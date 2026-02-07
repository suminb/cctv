import os
import subprocess
import time
import signal
from datetime import datetime, timedelta

# --- Configuration ---
RTSP_URL = os.environ.get("RTSP_URL")
ARCHIVE_PATH = os.environ.get("ARCHIVE_PATH", "/archive")
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", 90))
SEGMENT_TIME_SECONDS = 10
CLEANUP_INTERVAL_SECONDS = 3600  # 1 hour

ffmpeg_process = None
current_process_date = None
last_cleanup_time = time.time()


def get_current_date_str():
    """Returns the current date as YYYY-MM-DD."""
    return datetime.utcnow().strftime("%Y-%m-%d")


def start_ffmpeg_process(date_str):
    """Starts a new ffmpeg process for the given date."""
    global ffmpeg_process, current_process_date

    if not RTSP_URL:
        print("Error: RTSP_URL environment variable is not set. Exiting.")
        exit(1)

    # Ensure archive path exists
    os.makedirs(ARCHIVE_PATH, exist_ok=True)

    playlist_path = os.path.join(ARCHIVE_PATH, f"playlist_{date_str}.m3u8")
    segment_filename = os.path.join(ARCHIVE_PATH, f"{date_str}_segment_%05d.ts")

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
        "0",  # Keep all segments in the playlist for the day
        "-hls_segment_filename",
        segment_filename,
        playlist_path,
    ]

    print(f"DEBUG: FFMPEG command being executed: {command}")
    print(f"Starting ffmpeg for date {date_str}: {' '.join(command)}")
    # Start the process in a new process group
    ffmpeg_process = subprocess.Popen(command, preexec_fn=os.setsid)
    current_process_date = date_str


def stop_ffmpeg_process():
    """Gracefully stops the current ffmpeg process."""
    global ffmpeg_process
    if ffmpeg_process and ffmpeg_process.poll() is None:
        print(f"Gracefully stopping ffmpeg process (PID: {ffmpeg_process.pid})...")
        # Send SIGTERM to the entire process group
        os.killpg(os.getpgid(ffmpeg_process.pid), signal.SIGTERM)
        try:
            ffmpeg_process.wait(timeout=30)
            print("ffmpeg process stopped.")
        except subprocess.TimeoutExpired:
            print("ffmpeg process did not stop gracefully, killing.")
            os.killpg(os.getpgid(ffmpeg_process.pid), signal.SIGKILL)
    ffmpeg_process = None


def cleanup_old_files():
    """Deletes files older than the retention period."""
    global last_cleanup_time
    if time.time() - last_cleanup_time < CLEANUP_INTERVAL_SECONDS:
        return

    print("Running cleanup of old files...")
    now = datetime.utcnow()
    retention_delta = timedelta(days=RETENTION_DAYS)
    cutoff_date = now - retention_delta

    try:
        for filename in os.listdir(ARCHIVE_PATH):
            if filename.endswith(".ts") or filename.endswith(".m3u8"):
                file_path = os.path.join(ARCHIVE_PATH, filename)
                try:
                    file_mod_time = datetime.fromtimestamp(os.path.getmtime(file_path))
                    if file_mod_time < cutoff_date:
                        print(f"Deleting old file: {filename}")
                        os.remove(file_path)
                except OSError as e:
                    print(f"Error processing file {file_path}: {e}")
    except Exception as e:
        print(f"An error occurred during cleanup: {e}")

    last_cleanup_time = time.time()


def handle_shutdown_signal(signum, frame):
    """Handle termination signals to ensure clean shutdown."""
    print(f"Received signal {signum}. Shutting down.")
    stop_ffmpeg_process()
    exit(0)


def main():
    """Main application loop."""
    global ffmpeg_process

    signal.signal(signal.SIGINT, handle_shutdown_signal)
    signal.signal(signal.SIGTERM, handle_shutdown_signal)

    print("CCTV Archiver starting up.")

    while True:
        today_str = get_current_date_str()

        # --- Daily Rollover Logic ---
        if today_str != current_process_date:
            print(f"Date changed. Rolling over to {today_str}.")
            stop_ffmpeg_process()
            start_ffmpeg_process(today_str)

        # --- Crash Recovery Logic ---
        elif ffmpeg_process is None or ffmpeg_process.poll() is not None:
            if ffmpeg_process:  # It crashed
                print(
                    f"ffmpeg process crashed with exit code {ffmpeg_process.poll()}. Restarting."
                )
            else:  # Initial startup
                print("No ffmpeg process running. Starting for the first time.")

            stop_ffmpeg_process()  # Clean up just in case
            start_ffmpeg_process(today_str)

        # --- Periodic Cleanup ---
        cleanup_old_files()

        time.sleep(10)  # Check every 10 seconds


if __name__ == "__main__":
    main()
