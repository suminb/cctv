import unittest
from unittest.mock import patch, MagicMock
import os
import time
import signal
import subprocess
from datetime import datetime, timedelta

# Import the functions from app.py
# For testing purposes, we'll import app as a module
import app

# Save original datetime for use in mocking
_original_datetime = datetime

class TestCCTVArchiver(unittest.TestCase):

    @patch('app.datetime')
    def test_get_current_hour_identifier(self, mock_datetime):
        mock_datetime.utcnow.return_value = datetime(2026, 2, 7, 10, 30, 0)
        self.assertEqual(app.get_current_hour_identifier(), "2026-02-07-10")

    @patch('app.os.makedirs')
    @patch('app.subprocess.Popen')
    @patch('app.RTSP_URL', "rtsp://test_url")
    @patch('app.ARCHIVE_PATH', "/test_archive")
    @patch('app.current_process_hour_identifier', None) # Ensure it's not set initially
    def test_start_ffmpeg_process_hls(self, mock_popen, mock_makedirs):
        app.ffmpeg_process = None # Reset global state for test
        app.start_ffmpeg_process("2026-02-07-10")

        mock_makedirs.assert_called_once_with("/test_archive", exist_ok=True)
        mock_popen.assert_called_once()

        expected_command = [
            "ffmpeg",
            "-i", "rtsp://test_url",
            "-c", "copy",
            "-map", "0",
            "-f", "hls",
            "-hls_time", "10",
            "-hls_list_size", "0",
            "-hls_segment_filename", "/test_archive/2026-02-07-10_segment_%05d.ts",
            "/test_archive/playlist_2026-02-07-10.m3u8",
        ]
        self.assertEqual(mock_popen.call_args[0][0], expected_command)
        self.assertIsNotNone(app.ffmpeg_process)
        self.assertEqual(app.current_process_hour_identifier, "2026-02-07-10")
        
    @patch('app.RTSP_URL', None) # Simulate missing RTSP_URL
    def test_start_ffmpeg_process_no_rtsp_url(self):
        with self.assertRaises(SystemExit) as cm:
            app.start_ffmpeg_process("2026-02-07-10")
        self.assertEqual(cm.exception.code, 1)

    @patch('app.os.killpg')
    @patch('app.os.getpgid', return_value=1234)
    def test_stop_ffmpeg_process_graceful(self, mock_getpgid, mock_killpg):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None # Process is still running
        mock_proc.wait.return_value = 0
        app.ffmpeg_process = mock_proc

        app.stop_ffmpeg_process()

        mock_killpg.assert_called_once_with(1234, signal.SIGTERM)
        mock_proc.wait.assert_called_once_with(timeout=30)
        self.assertIsNone(app.ffmpeg_process)

    @patch('app.os.killpg')
    @patch('app.os.getpgid', return_value=1234)
    def test_stop_ffmpeg_process_kill(self, mock_getpgid, mock_killpg):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None # Process is still running
        mock_proc.wait.side_effect = subprocess.TimeoutExpired(cmd="ffmpeg", timeout=30)
        app.ffmpeg_process = mock_proc

        app.stop_ffmpeg_process()

        mock_killpg.assert_any_call(1234, signal.SIGTERM)
        mock_killpg.assert_any_call(1234, signal.SIGKILL) # Should be called after timeout
        self.assertIsNone(app.ffmpeg_process)

    @patch('app.os.path.exists', return_value=True)
    @patch('app.subprocess.Popen')
    @patch('app.ARCHIVE_PATH', "/test_archive")
    def test_consolidate_hourly_archive(self, mock_popen, mock_exists):
        app.consolidate_hourly_archive("2026-02-07-09")

        mock_popen.assert_called_once()
        expected_command = [
            "ffmpeg",
            "-y",
            "-i", "/test_archive/playlist_2026-02-07-09.m3u8",
            "-c:v", "libx265",
            "-preset", "medium",
            "-crf", "26",
            "-c:a", "copy",
            "/test_archive/archive_2026-02-07-09.mp4",
        ]
        self.assertEqual(mock_popen.call_args[0][0], expected_command)
        self.assertIn("2026-02-07-09", app.consolidation_processes)

    @patch('app.os.listdir', return_value=[
        "2026-02-07-09_segment_00000.ts",
        "2026-02-07-09_segment_00001.ts",
        "playlist_2026-02-07-09.m3u8",
        "archive_2026-02-07-09.mp4", # Should not be deleted
        "other_file.txt", # Should not be deleted
    ])
    @patch('app.os.remove')
    def test_check_consolidation_status_success(self, mock_remove, mock_listdir):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0 # Process finished successfully
        mock_proc.returncode = 0 # Set the returncode attribute
        mock_proc.communicate.return_value = (b'stdout', b'stderr')
        app.consolidation_processes = {"2026-02-07-09": mock_proc}
        app.ARCHIVE_PATH = "/test_archive"

        app.check_consolidation_status()

        self.assertNotIn("2026-02-07-09", app.consolidation_processes)
        
        # Verify specific files are removed
        calls = mock_remove.call_args_list
        self.assertIn(unittest.mock.call("/test_archive/2026-02-07-09_segment_00000.ts"), calls)
        self.assertIn(unittest.mock.call("/test_archive/2026-02-07-09_segment_00001.ts"), calls)
        self.assertIn(unittest.mock.call("/test_archive/playlist_2026-02-07-09.m3u8"), calls)
        self.assertNotIn(unittest.mock.call("/test_archive/archive_2026-02-07-09.mp4"), calls)
        self.assertNotIn(unittest.mock.call("/test_archive/other_file.txt"), calls)
        
        self.assertEqual(mock_remove.call_count, 3) # Only 3 files should be deleted

    @patch('app.os.listdir', return_value=[
        "archive_2026-02-07-09.mp4",
        "archive_2026-02-06-09.mp4", # Recent file
        "archive_2025-11-01-09.mp4", # Very old file (should be deleted)
        "2026-02-07-09_segment_00001.ts", # Recent TS file (not subject to retention)
        "2025-11-01-09_segment_00001.ts", # Old TS file (not subject to retention)
        "playlist_2026-02-07-09.m3u8", # Recent playlist (not subject to retention)
        "playlist_2025-11-01-09.m3u8", # Old playlist (not subject to retention)
        "other_file.txt", # Should not be deleted
    ])
    @patch('app.os.path.getmtime')
    @patch('app.os.remove')
    @patch('app.datetime')
    def test_cleanup_old_files(self, mock_datetime, mock_remove, mock_getmtime, mock_listdir):
        # Mock current time to Feb 7, 2026, 10:00:00
        mock_datetime.utcnow.return_value = datetime(2026, 2, 7, 10, 0, 0)
        # Use the original (unmocked) datetime.fromtimestamp to avoid infinite recursion
        mock_datetime.fromtimestamp.side_effect = lambda ts: _original_datetime.fromtimestamp(ts)
        
        # Mock file modification times - only .mp4 files will be checked
        mock_getmtime.side_effect = [
            datetime(2026, 2, 7, 9, 0, 0).timestamp(),   # 2026-02-07-09.mp4 (recent)
            datetime(2026, 2, 6, 9, 0, 0).timestamp(),   # 2026-02-06-09.mp4 (recent)
            datetime(2025, 11, 1, 9, 0, 0).timestamp(),  # 2025-11-01-09.mp4 (old, should be deleted)
        ]
        
        app.ARCHIVE_PATH = "/test_archive"
        app.RETENTION_DAYS = 90
        app.last_cleanup_time = time.time() - app.CLEANUP_INTERVAL_SECONDS - 1 # Ensure cleanup runs

        app.cleanup_old_files()

        # Only old MP4 files should be removed by retention policy
        # TS and M3U8 files are deleted after consolidation, not by retention policy
        calls = mock_remove.call_args_list
        self.assertIn(unittest.mock.call("/test_archive/archive_2025-11-01-09.mp4"), calls)
        # TS and M3U8 files should NOT be removed by cleanup_old_files
        self.assertNotIn(unittest.mock.call("/test_archive/2025-11-01-09_segment_00001.ts"), calls)
        self.assertNotIn(unittest.mock.call("/test_archive/playlist_2025-11-01-09.m3u8"), calls)
        # Recent MP4 files should not be removed
        self.assertNotIn(unittest.mock.call("/test_archive/archive_2026-02-07-09.mp4"), calls)
        self.assertNotIn(unittest.mock.call("/test_archive/archive_2026-02-06-09.mp4"), calls)
        # Non-archive files should not be removed
        self.assertNotIn(unittest.mock.call("/test_archive/other_file.txt"), calls)
        # Should delete exactly 1 old MP4 file
        self.assertEqual(mock_remove.call_count, 1)

if __name__ == '__main__':
    unittest.main()
