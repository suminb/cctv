import os
import subprocess
import time
import signal
import argparse
from datetime import datetime, timedelta

# --- Configuration ---
RTSP_URL = os.environ.get("RTSP_URL")
ARCHIVE_PATH = os.environ.get("ARCHIVE_PATH", "/archive")
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", 90))
SEGMENT_TIME_SECONDS = 10
CONSOLIDATION_CHECK_INTERVAL_SECONDS = 60  # Check every minute for hourly rollover
CLEANUP_INTERVAL_SECONDS = 3600  # 1 hour
BYTES_PER_MB = 1024 * 1024  # For file size conversions

ffmpeg_process = None
current_process_hour_identifier = None  # YYYY-MM-DD-HH
last_cleanup_time = time.time()

# Store PIDs for consolidation tasks, if any
consolidation_processes = {}


def get_current_hour_identifier():
    """Returns the current date and hour as YYYY-MM-DD-HH."""
    return datetime.utcnow().strftime("%Y-%m-%d-%H")


def start_ffmpeg_process(hour_identifier):
    """Starts a new ffmpeg process for the given hour identifier."""
    global ffmpeg_process, current_process_hour_identifier

    if not RTSP_URL:
        print("Error: RTSP_URL environment variable is not set. Exiting.")
        exit(1)

    os.makedirs(ARCHIVE_PATH, exist_ok=True)

    playlist_path = os.path.join(ARCHIVE_PATH, f"playlist_{hour_identifier}.m3u8")
    segment_filename = os.path.join(ARCHIVE_PATH, f"{hour_identifier}_segment_%05d.ts")

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
        "libx265",  # Use H.265 video codec
        "-preset",
        "medium",  # Medium speed/compression balance
        "-crf",
        "26",  # Constant Rate Factor for quality (23-28 is common)
        "-c:a",
        "copy",  # Copy audio stream without re-encoding
        output_mp4,
    ]
    print(f"DEBUG: FFMPEG command being executed for MP4 consolidation: {command}")
    try:
        # Use a separate Popen call, don't block the main loop
        consolidation_proc = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        consolidation_processes[prev_hour_identifier] = consolidation_proc
        print(
            f"Consolidation process for {prev_hour_identifier} started (PID: {consolidation_proc.pid})."
        )
    except Exception as e:
        print(f"Error starting consolidation for {prev_hour_identifier}: {e}")


def check_consolidation_status():
    """Checks the status of ongoing consolidation processes."""
    global consolidation_processes
    completed_identifiers = []
    for identifier, proc in consolidation_processes.items():
        if proc.poll() is not None:  # Process has finished
            stdout, stderr = proc.communicate()
            if proc.returncode == 0:
                print(f"Consolidation for {identifier} finished successfully.")
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
                print(
                    f"Consolidation for {identifier} failed with code {proc.returncode}."
                )
                print(f"STDOUT:\n{stdout.decode()}")
                print(f"STDERR:\n{stderr.decode()}")
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
    now = datetime.utcnow()
    retention_delta = timedelta(days=RETENTION_DAYS)
    cutoff_date = now - retention_delta

    try:
        for filename in os.listdir(ARCHIVE_PATH):
            if filename.endswith(".mp4"):  # Only target MP4 archived files
                file_path = os.path.join(ARCHIVE_PATH, filename)
                try:
                    file_mod_time = datetime.fromtimestamp(os.path.getmtime(file_path))
                    if file_mod_time < cutoff_date:
                        print(f"Deleting old MP4 file: {filename}")
                        os.remove(file_path)
                except OSError as e:
                    print(f"Error processing file {file_path}: {e}")
    except Exception as e:
        print(f"An error occurred during MP4 cleanup: {e}")

    last_cleanup_time = time.time()


def purge_orphaned_files():
    """Manually delete orphaned .ts segment files and .m3u8 playlists.
    
    Orphaned files are HLS segments and playlists that don't have a corresponding
    MP4 file. This happens when consolidation fails or never completes.
    
    Returns:
        tuple: (deleted_count, total_size_bytes) Number of files deleted and total size freed
    """
    if not os.path.exists(ARCHIVE_PATH):
        print(f"Archive path {ARCHIVE_PATH} does not exist.")
        return 0, 0
    
    print(f"Scanning {ARCHIVE_PATH} for orphaned HLS files...")
    
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
    
    # Now find orphaned .ts and .m3u8 files
    orphaned_files = []
    total_size = 0
    
    try:
        for filename in os.listdir(ARCHIVE_PATH):
            is_orphaned = False
            identifier = None
            
            # Check if it's a segment file
            if filename.endswith(".ts") and "_segment_" in filename:
                # Extract YYYY-MM-DD-HH from YYYY-MM-DD-HH_segment_XXXXX.ts
                identifier = filename.split("_segment_")[0]
                is_orphaned = identifier not in mp4_identifiers
            
            # Check if it's a playlist file
            elif filename.endswith(".m3u8") and filename.startswith("playlist_"):
                # Extract YYYY-MM-DD-HH from playlist_YYYY-MM-DD-HH.m3u8
                identifier = filename[9:-5]  # Remove "playlist_" prefix and ".m3u8" suffix
                is_orphaned = identifier not in mp4_identifiers
            
            if is_orphaned:
                file_path = os.path.join(ARCHIVE_PATH, filename)
                try:
                    file_size = os.path.getsize(file_path)
                    orphaned_files.append((file_path, filename, file_size))
                    total_size += file_size
                except OSError as e:
                    # Skip files we can't access (permissions, etc.)
                    # They will not be included in the deletion list
                    print(f"Warning: Cannot access {file_path}: {e}")
    except Exception as e:
        print(f"Error scanning for orphaned files: {e}")
        return 0, 0
    
    if not orphaned_files:
        print("No orphaned files found.")
        return 0, 0
    
    print(f"\nFound {len(orphaned_files)} orphaned file(s) ({total_size / BYTES_PER_MB:.2f} MB)")
    
    # Delete orphaned files
    deleted_count = 0
    deleted_size = 0
    
    for file_path, filename, file_size in orphaned_files:
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
                f"Waiting for consolidation process {identifier} to finish (PID: {proc.pid})..."
            )
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                print(
                    f"Consolidation process {identifier} did not stop gracefully, killing."
                )
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    exit(0)


def main():
    """Main application loop."""
    global ffmpeg_process, current_process_hour_identifier

    signal.signal(signal.SIGINT, handle_shutdown_signal)
    signal.signal(signal.SIGTERM, handle_shutdown_signal)

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
