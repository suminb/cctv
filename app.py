import os
import subprocess
import tempfile
import time
import signal
import argparse
from datetime import datetime, timedelta

try:
    WEB_PORT = int(os.environ.get('WEB_PORT', '8080'))
except ValueError:
    print(f"Warning: invalid WEB_PORT={os.environ.get('WEB_PORT')!r}, defaulting to 8080")
    WEB_PORT = 8080

def start_web_server():
    try:
        import uvicorn
        import web as viewer
        uvicorn.run(viewer.app, host='0.0.0.0', port=WEB_PORT, log_level='info')
    except Exception as e:
        print(f'Web UI failed to start: {e}')

# --- Configuration ---
RTSP_URL = os.environ.get("RTSP_URL")
ARCHIVE_PATH = os.environ.get("ARCHIVE_PATH", "/archive")
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", 90))
SEGMENT_TIME_SECONDS = 10
CONSOLIDATION_CHECK_INTERVAL_SECONDS = 60  # Check every minute for hourly rollover
CLEANUP_INTERVAL_SECONDS = 3600  # 1 hour
BYTES_PER_MB = 1024 * 1024  # For file size conversions
try:
    MIN_CONSOLIDATED_DURATION_SECONDS = float(
        os.environ.get("MIN_CONSOLIDATED_DURATION_SECONDS", 3000)
    )
except ValueError:
    print(
        f"Warning: invalid MIN_CONSOLIDATED_DURATION_SECONDS="
        f"{os.environ.get('MIN_CONSOLIDATED_DURATION_SECONDS')!r}, defaulting to 3000"
    )
    MIN_CONSOLIDATED_DURATION_SECONDS = 3000.0

ffmpeg_process = None
current_process_hour_identifier = None  # YYYY-MM-DD-HH
last_cleanup_time = time.time()

# Store PIDs for consolidation tasks, if any
consolidation_processes = {}
consolidation_stderr_logs = {}  # identifier -> path of the consolidation stderr log


def get_current_hour_identifier():
    """Returns the current date and hour as YYYY-MM-DD-HH."""
    return datetime.utcnow().strftime("%Y-%m-%d-%H")


def get_next_segment_number(hour_identifier):
    """Returns one past the highest existing segment number for the hour.

    A fresh ffmpeg HLS process starts numbering at 0, which would overwrite
    segments recorded before a restart (pod redeploy, crash recovery). Resuming
    past the highest existing number — together with -hls_flags append_list —
    preserves pre-restart footage in the same hour.
    """
    prefix = f"{hour_identifier}_segment_"
    highest = -1
    try:
        names = os.listdir(ARCHIVE_PATH)
    except FileNotFoundError:
        return 0
    for n in names:
        if n.startswith(prefix) and n.endswith(".ts"):
            num = n[len(prefix):-len(".ts")]
            if num.isdigit():
                highest = max(highest, int(num))
    return highest + 1


def start_ffmpeg_process(hour_identifier):
    """Starts a new ffmpeg process for the given hour identifier."""
    global ffmpeg_process, current_process_hour_identifier

    if not RTSP_URL:
        print("Error: RTSP_URL environment variable is not set. Exiting.")
        exit(1)

    os.makedirs(ARCHIVE_PATH, exist_ok=True)

    playlist_path = os.path.join(ARCHIVE_PATH, f"playlist_{hour_identifier}.m3u8")
    segment_filename = os.path.join(ARCHIVE_PATH, f"{hour_identifier}_segment_%05d.ts")
    # Resume past existing segments so a restart appends to this hour instead
    # of overwriting it (see get_next_segment_number).
    next_number = get_next_segment_number(hour_identifier)

    command = [
        "ffmpeg",
        "-i",
        RTSP_URL,
        "-c",
        "copy",
        "-map",
        "0",
        "-f",
        "hls",
        "-hls_time",
        str(SEGMENT_TIME_SECONDS),
        "-hls_list_size",
        "0",
        "-hls_segment_filename",
        segment_filename,
        "-start_number",
        str(next_number),
        "-hls_flags",
        "append_list",
        playlist_path,
    ]

    print(f"DEBUG: FFMPEG command being executed for HLS: {command}")
    print(f"Starting ffmpeg for hour {hour_identifier}...")
    ffmpeg_process = subprocess.Popen(command, preexec_fn=os.setsid)
    current_process_hour_identifier = hour_identifier


def stop_ffmpeg_process():
    """Gracefully stops the current ffmpeg process."""
    global ffmpeg_process
    if ffmpeg_process and ffmpeg_process.poll() is None:
        print(f"Gracefully stopping ffmpeg process (PID: {ffmpeg_process.pid})...")
        os.killpg(os.getpgid(ffmpeg_process.pid), signal.SIGTERM)
        try:
            ffmpeg_process.wait(timeout=30)
            print("ffmpeg process stopped.")
        except subprocess.TimeoutExpired:
            print("ffmpeg process did not stop gracefully, killing.")
            os.killpg(os.getpgid(ffmpeg_process.pid), signal.SIGKILL)
    ffmpeg_process = None


def consolidate_hourly_archive(prev_hour_identifier):
    """
    Consolidates the HLS segments from the previous hour into a single MP4 file.
    Deletes the original HLS files after successful conversion.
    """
    print(f"Starting consolidation for hour: {prev_hour_identifier}")
    hourly_playlist = os.path.join(
        ARCHIVE_PATH, f"playlist_{prev_hour_identifier}.m3u8"
    )
    output_mp4 = os.path.join(ARCHIVE_PATH, f"archive_{prev_hour_identifier}.mp4")

    if not os.path.exists(hourly_playlist):
        print(
            f"Warning: Playlist {hourly_playlist} not found for consolidation. Skipping."
        )
        return

    command = [
        "ffmpeg",
        "-y",  # Automatically overwrite output files without prompting
        "-i",
        hourly_playlist,
        "-c:v",
        "libx264",  # Use H.264 video codec
        "-preset",
        "veryfast",  # Favor speed over compression
        "-crf",
        "26",  # Constant Rate Factor for quality (23-28 is common)
        "-c:a",
        "copy",  # Copy audio stream without re-encoding
        output_mp4,
    ]
    print(f"DEBUG: FFMPEG command being executed for MP4 consolidation: {command}")
    stderr_log = None
    try:
        # stderr goes to a file, not a pipe: ffmpeg writes progress output
        # continuously and an undrained pipe fills up (~64KB), blocking ffmpeg
        # forever (the process never exits, so HLS files never get cleaned up).
        # stdout from ffmpeg carries no useful output.
        stderr_log = tempfile.NamedTemporaryFile(
            prefix=f"consolidation_{prev_hour_identifier}_",
            suffix=".log",
            delete=False,
        )
        # Use a separate Popen call, don't block the main loop
        consolidation_proc = subprocess.Popen(
            command, stdout=subprocess.DEVNULL, stderr=stderr_log
        )
        stderr_log.close()
        consolidation_processes[prev_hour_identifier] = consolidation_proc
        consolidation_stderr_logs[prev_hour_identifier] = stderr_log.name
        print(
            f"Consolidation process for {prev_hour_identifier} started (PID: {consolidation_proc.pid})."
        )
    except Exception as e:
        if stderr_log is not None:
            try:
                stderr_log.close()
                os.remove(stderr_log.name)
            except OSError:
                pass
        print(f"Error starting consolidation for {prev_hour_identifier}: {e}")


def get_mp4_duration_seconds(mp4_path):
    """Returns the MP4 duration in seconds via ffprobe, or None if unknown.

    None (missing binary, timeout, unparsable output) is fail-safe: callers
    treat it as "cannot prove complete" and keep the HLS sources.
    """
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                mp4_path,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"Warning: ffprobe failed for {mp4_path}: {e}")
        return None
    if proc.returncode != 0:
        print(f"Warning: ffprobe exited {proc.returncode} for {mp4_path}")
        return None
    try:
        return float(proc.stdout.decode().strip())
    except ValueError:
        print(f"Warning: could not parse ffprobe output for {mp4_path}: {proc.stdout!r}")
        return None


def check_consolidation_status():
    """Checks the status of ongoing consolidation processes."""
    global consolidation_processes, consolidation_stderr_logs
    completed_identifiers = []
    for identifier, proc in consolidation_processes.items():
        if proc.poll() is not None:  # Process has finished
            proc.communicate()  # Reap the process (no pipes to drain)
            stderr_log_path = consolidation_stderr_logs.pop(identifier, None)
            try:
                if proc.returncode == 0:
                    output_mp4 = os.path.join(ARCHIVE_PATH, f"archive_{identifier}.mp4")
                    duration = get_mp4_duration_seconds(output_mp4)
                    if duration is not None and duration >= MIN_CONSOLIDATED_DURATION_SECONDS:
                        print(
                            f"Consolidation for {identifier} finished successfully "
                            f"({duration:.0f}s)."
                        )
                        # Delete HLS files for this hour
                        try:
                            for f in os.listdir(ARCHIVE_PATH):
                                if f.startswith(f"{identifier}_segment_") or f == f"playlist_{identifier}.m3u8":
                                    file_to_delete = os.path.join(ARCHIVE_PATH, f)
                                    os.remove(file_to_delete)
                                    print(f"Deleted HLS file: {file_to_delete}")
                        except Exception as e:
                            print(f"Error deleting HLS files for {identifier}: {e}")
                    else:
                        # Short or unverifiable archive (e.g. restarts ate most
                        # of the hour): keep the HLS sources for manual review
                        # instead of deleting the only evidence. A `.keep_*`
                        # marker also shields them from purge_orphaned_files.
                        # The short MP4 itself is kept (partial footage beats none).
                        if duration is None:
                            reason = "duration unknown (ffprobe failed)"
                        else:
                            reason = (
                                f"duration {duration:.0f}s below minimum "
                                f"{MIN_CONSOLIDATED_DURATION_SECONDS:.0f}s"
                            )
                        print(
                            f"Consolidation for {identifier} produced a short "
                            f"archive ({reason}). Keeping HLS sources."
                        )
                        marker = os.path.join(ARCHIVE_PATH, f".keep_{identifier}")
                        try:
                            open(marker, "w").close()
                        except OSError as e:
                            print(f"Error writing keep-marker {marker}: {e}")
                else:
                    print(
                        f"Consolidation for {identifier} failed with code {proc.returncode}."
                    )
                    if stderr_log_path and os.path.exists(stderr_log_path):
                        with open(stderr_log_path, "rb") as f:
                            f.seek(max(0, os.fstat(f.fileno()).st_size - 8192))
                            print(f"STDERR (tail):\n{f.read().decode(errors='replace')}")
            finally:
                if stderr_log_path:
                    try:
                        os.remove(stderr_log_path)
                    except OSError as e:
                        print(f"Error removing consolidation log {stderr_log_path}: {e}")
            completed_identifiers.append(identifier)

    for identifier in completed_identifiers:
        del consolidation_processes[identifier]


def cleanup_old_files():
    """Deletes archived MP4 files older than the retention period.
    
    Note: .ts segment files and .m3u8 playlists are deleted immediately after
    successful consolidation (see check_consolidation_status), not by retention policy.
    """
    global last_cleanup_time
    if time.time() - last_cleanup_time < CLEANUP_INTERVAL_SECONDS:
        return

    print("Running cleanup of old MP4 files...")
    # Compare raw POSIX timestamps: mixing datetime.fromtimestamp() (local
    # timezone) with datetime.utcnow() (UTC) shifts the cutoff by the
    # machine's UTC offset and delays (or skips) deletion of expired files.
    cutoff_ts = time.time() - (RETENTION_DAYS * 86400)

    try:
        for filename in os.listdir(ARCHIVE_PATH):
            if filename.endswith(".mp4"):  # Only target MP4 archived files
                file_path = os.path.join(ARCHIVE_PATH, filename)
                try:
                    if os.path.getmtime(file_path) < cutoff_ts:
                        print(f"Deleting old MP4 file: {filename}")
                        os.remove(file_path)
                except OSError as e:
                    print(f"Error processing file {file_path}: {e}")
    except Exception as e:
        print(f"An error occurred during MP4 cleanup: {e}")

    last_cleanup_time = time.time()


def purge_orphaned_files():
    """Manually delete .ts segment files and .m3u8 playlists that should have been auto-deleted.
    
    When consolidation succeeds, it creates an MP4 and should automatically delete the source
    HLS files (.ts segments and .m3u8 playlist). This automatic deletion happens in the
    check_consolidation_status() function. However, if that automatic deletion fails due to
    errors (file permissions, race conditions, etc.), these files remain in the archive
    directory alongside their MP4.
    
    This command identifies and deletes HLS files that have corresponding MP4 files, as these
    should have been deleted already but weren't (likely due to errors in the cleanup process).
    
    Files from recent hours (current + previous 2 hours) are excluded to avoid deleting files
    that are still being recorded or in the consolidation queue.
    
    Returns:
        tuple: (deleted_count, total_size_bytes) Number of files deleted and total size freed
    """
    if not os.path.exists(ARCHIVE_PATH):
        print(f"Archive path {ARCHIVE_PATH} does not exist.")
        return 0, 0
    
    print(f"Scanning {ARCHIVE_PATH} for HLS files that should have been deleted...")
    
    # Calculate recent hour identifiers to exclude (current + previous 2 hours)
    # These files are likely still being recorded or waiting for consolidation
    recent_hours = set()
    now = datetime.utcnow()
    for hours_ago in range(3):  # Current hour, 1 hour ago, 2 hours ago
        recent_time = now - timedelta(hours=hours_ago)
        recent_identifier = recent_time.strftime("%Y-%m-%d-%H")
        recent_hours.add(recent_identifier)
    
    print(f"Excluding recent hours from purge: {sorted(recent_hours)}")
    
    # First, find all MP4 files and extract their hour identifiers
    mp4_identifiers = set()
    try:
        for filename in os.listdir(ARCHIVE_PATH):
            if filename.endswith(".mp4") and filename.startswith("archive_"):
                # Extract YYYY-MM-DD-HH from archive_YYYY-MM-DD-HH.mp4
                identifier = filename[8:-4]  # Remove "archive_" prefix and ".mp4" suffix
                mp4_identifiers.add(identifier)
    except Exception as e:
        print(f"Error scanning for MP4 files: {e}")
        return 0, 0
    
    print(f"Found {len(mp4_identifiers)} MP4 archive(s)")
    
    # Now find HLS files that should have been deleted (have corresponding MP4s)
    files_to_delete = []
    total_size = 0
    
    try:
        for filename in os.listdir(ARCHIVE_PATH):
            should_delete = False
            identifier = None
            
            # Check if it's a segment file
            if filename.endswith(".ts") and "_segment_" in filename:
                # Extract YYYY-MM-DD-HH from YYYY-MM-DD-HH_segment_XXXXX.ts
                identifier = filename.split("_segment_")[0]
                # File should be deleted if it HAS an MP4 AND is not from a recent hour
                should_delete = (identifier in mp4_identifiers) and (identifier not in recent_hours)
            
            # Check if it's a playlist file
            elif filename.endswith(".m3u8") and filename.startswith("playlist_"):
                # Extract YYYY-MM-DD-HH from playlist_YYYY-MM-DD-HH.m3u8
                identifier = filename[9:-5]  # Remove "playlist_" prefix and ".m3u8" suffix
                # File should be deleted if it HAS an MP4 AND is not from a recent hour
                should_delete = (identifier in mp4_identifiers) and (identifier not in recent_hours)
            
            if should_delete:
                # Hours flagged incomplete by the duration gate (see
                # check_consolidation_status) keep their sources for manual
                # review — never purge them automatically.
                keep_marker = os.path.join(ARCHIVE_PATH, f".keep_{identifier}")
                if os.path.exists(keep_marker):
                    print(
                        f"Keeping HLS files for {identifier} "
                        f"(marked incomplete, remove {keep_marker} after review "
                        f"to allow purging)"
                    )
                    continue
                file_path = os.path.join(ARCHIVE_PATH, filename)
                try:
                    file_size = os.path.getsize(file_path)
                    files_to_delete.append((file_path, filename, file_size))
                    total_size += file_size
                except OSError as e:
                    # Skip files we can't access (permissions, etc.)
                    # They will not be included in the deletion list
                    print(f"Warning: Cannot access {file_path}: {e}")
    except Exception as e:
        print(f"Error scanning for files to delete: {e}")
        return 0, 0
    
    if not files_to_delete:
        print("No HLS files found that need to be deleted.")
        return 0, 0
    
    print(f"\nFound {len(files_to_delete)} HLS file(s) to delete ({total_size / BYTES_PER_MB:.2f} MB)")
    
    # Delete the files
    deleted_count = 0
    deleted_size = 0
    
    for file_path, filename, file_size in files_to_delete:
        try:
            os.remove(file_path)
            print(f"Deleted: {filename} ({file_size / BYTES_PER_MB:.2f} MB)")
            deleted_count += 1
            deleted_size += file_size
        except OSError as e:
            print(f"Error deleting {file_path}: {e}")
    
    print(f"\nPurge complete: Deleted {deleted_count} file(s), freed {deleted_size / BYTES_PER_MB:.2f} MB")
    return deleted_count, deleted_size


def handle_shutdown_signal(signum, frame):
    """Handle termination signals to ensure clean shutdown."""
    print(f"Received signal {signum}. Shutting down.")
    stop_ffmpeg_process()
    # Optionally, wait for consolidation processes to finish
    for identifier, proc in consolidation_processes.items():
        if proc.poll() is None:
            print(
                f"Stopping consolidation process {identifier} (PID: {proc.pid})..."
            )
            # Signal the process directly: consolidation processes are not
            # started with setsid, so signaling their process group would
            # also terminate this archiver.
            proc.terminate()
            try:
                proc.wait(timeout=30)
                print(f"Consolidation process {identifier} stopped.")
            except subprocess.TimeoutExpired:
                print(
                    f"Consolidation process {identifier} did not stop gracefully, killing."
                )
                proc.kill()
    exit(0)


def main():
    """Main application loop."""
    global ffmpeg_process, current_process_hour_identifier

    signal.signal(signal.SIGINT, handle_shutdown_signal)
    signal.signal(signal.SIGTERM, handle_shutdown_signal)

    import threading
    threading.Thread(target=start_web_server, daemon=True).start()
    print(f'Web viewer on :{WEB_PORT}')

    print("CCTV Archiver starting up with hourly MP4 consolidation.")

    while True:
        current_hour_id = get_current_hour_identifier()

        # --- Hourly Rollover Logic ---
        if current_hour_id != current_process_hour_identifier:
            print(f"Hour changed. Rolling over to {current_hour_id}.")
            if current_process_hour_identifier:  # Not the first run
                stop_ffmpeg_process()
                # Trigger consolidation for the hour that just finished
                consolidate_hourly_archive(current_process_hour_identifier)
            start_ffmpeg_process(current_hour_id)

        # --- Crash Recovery Logic for FFMPEG HLS Capture ---
        elif ffmpeg_process is None or ffmpeg_process.poll() is not None:
            if ffmpeg_process:
                print(
                    f"FFMPEG HLS capture process crashed with exit code {ffmpeg_process.poll()}. Restarting."
                )
            else:
                print(
                    "No FFMPEG HLS capture process running. Starting for the first time."
                )

            stop_ffmpeg_process()  # Ensure it's clean before starting
            start_ffmpeg_process(current_hour_id)

        # --- Check for finished consolidation tasks ---
        check_consolidation_status()

        # --- Periodic Cleanup ---
        cleanup_old_files()
        purge_orphaned_files()

        time.sleep(
            CONSOLIDATION_CHECK_INTERVAL_SECONDS
        )  # Check for rollover/status every minute


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="CCTV Archiver - Archive RTSP streams to MP4 files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  (none)    Start the archiver in continuous recording mode (default)
  purge     Delete orphaned HLS files (.ts and .m3u8) that don't have corresponding MP4 archives

Environment Variables:
  RTSP_URL         RTSP stream URL to capture (required for recording mode)
  ARCHIVE_PATH     Directory for archived files (default: /archive)
  RETENTION_DAYS   Number of days to keep archived files (default: 90)

Examples:
  # Start continuous recording
  python3 app.py
  
  # Purge orphaned files
  python3 app.py purge
        """
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=["purge"],
        help="Command to execute (omit for normal recording mode)"
    )
    
    args = parser.parse_args()
    
    if args.command == "purge":
        purge_orphaned_files()
    else:
        main()
