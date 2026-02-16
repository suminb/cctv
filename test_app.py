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

    @patch('app.os.path.exists', return_value=True)
    @patch('app.os.listdir')
    @patch('app.os.path.getsize')
    @patch('app.os.remove')
    def test_purge_orphaned_files_with_orphans(self, mock_remove, mock_getsize, mock_listdir, mock_exists):
        """Test purge identifies and deletes HLS files that have corresponding MP4s."""
        # Setup: MP4s exist for hours 05, 06, and 07 (all old enough to delete HLS files)
        # HLS files for these hours should be deleted
        # HLS files for hour 08 without MP4 should NOT be deleted
        mock_listdir.return_value = [
            "archive_2026-02-07-05.mp4",  # Hour 05 MP4
            "archive_2026-02-07-06.mp4",  # Hour 06 MP4
            "archive_2026-02-07-07.mp4",  # Hour 07 MP4
            "2026-02-07-05_segment_00001.ts",  # Should be deleted (has MP4, old enough)
            "playlist_2026-02-07-05.m3u8",  # Should be deleted (has MP4, old enough)
            "2026-02-07-06_segment_00001.ts",  # Should be deleted (has MP4, old enough)
            "2026-02-07-06_segment_00002.ts",  # Should be deleted (has MP4, old enough)
            "playlist_2026-02-07-06.m3u8",  # Should be deleted (has MP4, old enough)
            "2026-02-07-07_segment_00001.ts",  # Should be deleted (has MP4, old enough)
            "2026-02-07-07_segment_00002.ts",  # Should be deleted (has MP4, old enough)
            "playlist_2026-02-07-07.m3u8",  # Should be deleted (has MP4, old enough)
            "2026-02-07-08_segment_00001.ts",  # Should NOT be deleted (no MP4 yet)
            "playlist_2026-02-07-08.m3u8",  # Should NOT be deleted (no MP4 yet)
            "other_file.txt",  # Not an HLS file
        ]
        
        # Mock file sizes (1 MB each for simplicity)
        mock_getsize.return_value = 1024 * 1024
        
        app.ARCHIVE_PATH = "/test_archive"
        
        deleted_count, deleted_size = app.purge_orphaned_files()
        
        # Should delete 8 files (1 ts + 1 m3u8 from hour 05, 2 ts + 1 m3u8 from hour 06, 2 ts + 1 m3u8 from hour 07)
        self.assertEqual(deleted_count, 8)
        self.assertEqual(deleted_size, 8 * 1024 * 1024)
        
        # Verify correct files were deleted
        deleted_files = [call[0][0] for call in mock_remove.call_args_list]
        self.assertIn("/test_archive/2026-02-07-05_segment_00001.ts", deleted_files)
        self.assertIn("/test_archive/playlist_2026-02-07-05.m3u8", deleted_files)
        self.assertIn("/test_archive/2026-02-07-06_segment_00001.ts", deleted_files)
        self.assertIn("/test_archive/2026-02-07-06_segment_00002.ts", deleted_files)
        self.assertIn("/test_archive/playlist_2026-02-07-06.m3u8", deleted_files)
        self.assertIn("/test_archive/2026-02-07-07_segment_00001.ts", deleted_files)
        self.assertIn("/test_archive/2026-02-07-07_segment_00002.ts", deleted_files)
        self.assertIn("/test_archive/playlist_2026-02-07-07.m3u8", deleted_files)
        
        # Verify files that should NOT be deleted
        self.assertNotIn("/test_archive/2026-02-07-08_segment_00001.ts", deleted_files)  # No MP4
        self.assertNotIn("/test_archive/playlist_2026-02-07-08.m3u8", deleted_files)  # No MP4
        self.assertNotIn("/test_archive/archive_2026-02-07-05.mp4", deleted_files)  # MP4 files
        self.assertNotIn("/test_archive/archive_2026-02-07-06.mp4", deleted_files)  # MP4 files
        self.assertNotIn("/test_archive/archive_2026-02-07-07.mp4", deleted_files)  # MP4 files
        self.assertNotIn("/test_archive/other_file.txt", deleted_files)  # Not HLS

    @patch('app.os.path.exists', return_value=True)
    @patch('app.os.listdir')
    def test_purge_orphaned_files_no_orphans(self, mock_listdir, mock_exists):
        """Test purge when there are no HLS files that need deletion."""
        # Scenario: HLS files exist but no MP4s yet (still being recorded/consolidated)
        mock_listdir.return_value = [
            "2026-02-07-10_segment_00001.ts",  # No MP4 - should not delete
            "playlist_2026-02-07-10.m3u8",  # No MP4 - should not delete
        ]
        
        app.ARCHIVE_PATH = "/test_archive"
        
        deleted_count, deleted_size = app.purge_orphaned_files()
        
        self.assertEqual(deleted_count, 0)
        self.assertEqual(deleted_size, 0)

    @patch('app.os.path.exists', return_value=False)
    def test_purge_orphaned_files_no_archive_path(self, mock_exists):
        """Test purge when archive path doesn't exist."""
        app.ARCHIVE_PATH = "/nonexistent"
        
        deleted_count, deleted_size = app.purge_orphaned_files()
        
        self.assertEqual(deleted_count, 0)
        self.assertEqual(deleted_size, 0)

    @patch('app.datetime')
    @patch('app.os.path.exists', return_value=True)
    @patch('app.os.listdir')
    @patch('app.os.path.getsize')
    @patch('app.os.remove')
    def test_purge_orphaned_files_excludes_recent_hours(self, mock_remove, mock_getsize, mock_listdir, mock_exists, mock_datetime):
        """Test that purge excludes files from recent hours (current + previous 2 hours)."""
        # Mock current time to Feb 16, 2026, 13:00:00
        mock_datetime.utcnow.return_value = datetime(2026, 2, 16, 13, 0, 0)
        
        # Setup: Files from hours 13, 12, 11 (recent) should NOT be deleted even if they have MP4s
        # Files from hour 10 and earlier WITH MP4s should be deleted
        mock_listdir.return_value = [
            "archive_2026-02-16-13.mp4",  # Current hour MP4
            "archive_2026-02-16-12.mp4",  # 1 hour ago MP4
            "archive_2026-02-16-11.mp4",  # 2 hours ago MP4
            "archive_2026-02-16-10.mp4",  # 3 hours ago MP4
            "archive_2026-02-16-09.mp4",  # 4 hours ago MP4
            "2026-02-16-13_segment_00001.ts",  # Current hour - should NOT be deleted (recent)
            "playlist_2026-02-16-13.m3u8",  # Current hour - should NOT be deleted (recent)
            "2026-02-16-12_segment_00001.ts",  # 1 hour ago - should NOT be deleted (recent)
            "playlist_2026-02-16-12.m3u8",  # 1 hour ago - should NOT be deleted (recent)
            "2026-02-16-11_segment_00001.ts",  # 2 hours ago - should NOT be deleted (recent)
            "playlist_2026-02-16-11.m3u8",  # 2 hours ago - should NOT be deleted (recent)
            "2026-02-16-10_segment_00001.ts",  # 3 hours ago, has MP4 - SHOULD be deleted
            "playlist_2026-02-16-10.m3u8",  # 3 hours ago, has MP4 - SHOULD be deleted
            "2026-02-16-09_segment_00001.ts",  # 4 hours ago, has MP4 - SHOULD be deleted
            "playlist_2026-02-16-09.m3u8",  # 4 hours ago, has MP4 - SHOULD be deleted
        ]
        
        mock_getsize.return_value = 1024 * 1024  # 1 MB
        
        app.ARCHIVE_PATH = "/test_archive"
        
        deleted_count, deleted_size = app.purge_orphaned_files()
        
        # Should delete files from hours 10 and 09 (4 files: 2 ts + 2 m3u8)
        self.assertEqual(deleted_count, 4)
        self.assertEqual(deleted_size, 4 * 1024 * 1024)
        
        # Verify correct files were deleted
        deleted_files = [call[0][0] for call in mock_remove.call_args_list]
        self.assertIn("/test_archive/2026-02-16-10_segment_00001.ts", deleted_files)
        self.assertIn("/test_archive/playlist_2026-02-16-10.m3u8", deleted_files)
        self.assertIn("/test_archive/2026-02-16-09_segment_00001.ts", deleted_files)
        self.assertIn("/test_archive/playlist_2026-02-16-09.m3u8", deleted_files)
        
        # Verify recent hour files were NOT deleted (even though they have MP4s)
        self.assertNotIn("/test_archive/2026-02-16-13_segment_00001.ts", deleted_files)
        self.assertNotIn("/test_archive/playlist_2026-02-16-13.m3u8", deleted_files)
        self.assertNotIn("/test_archive/2026-02-16-12_segment_00001.ts", deleted_files)
        self.assertNotIn("/test_archive/playlist_2026-02-16-12.m3u8", deleted_files)
        self.assertNotIn("/test_archive/2026-02-16-11_segment_00001.ts", deleted_files)
        self.assertNotIn("/test_archive/playlist_2026-02-16-11.m3u8", deleted_files)

if __name__ == '__main__':
    unittest.main()
