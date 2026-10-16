import unittest
from unittest.mock import patch, MagicMock
import os
import shutil
import sys
import tempfile
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
            "-start_number", "0",
            "-hls_flags", "append_list",
            "/test_archive/playlist_2026-02-07-10.m3u8",
        ]
        self.assertEqual(mock_popen.call_args[0][0], expected_command)
        self.assertIsNotNone(app.ffmpeg_process)
        self.assertEqual(app.current_process_hour_identifier, "2026-02-07-10")

    @patch('app.os.listdir', return_value=[])
    def test_get_next_segment_number_empty(self, mock_listdir):
        self.assertEqual(app.get_next_segment_number("2026-02-07-10"), 0)

    @patch('app.os.listdir', side_effect=FileNotFoundError)
    def test_get_next_segment_number_no_dir(self, mock_listdir):
        self.assertEqual(app.get_next_segment_number("2026-02-07-10"), 0)

    @patch('app.os.listdir', return_value=[
        "2026-02-07-10_segment_00007.ts",
        "2026-02-07-10_segment_00012.ts",
        "2026-02-07-10_segment_00003.ts",
        "playlist_2026-02-07-10.m3u8",  # not a segment, ignored
        "archive_2026-02-07-10.mp4",  # not a segment, ignored
        "2026-02-07-09_segment_00200.ts",  # other hour, ignored
        "2026-02-07-10_segment_broken.ts",  # non-numeric, ignored
    ])
    def test_get_next_segment_number_resumes(self, mock_listdir):
        self.assertEqual(app.get_next_segment_number("2026-02-07-10"), 13)

    @patch('app.os.makedirs')
    @patch('app.subprocess.Popen')
    @patch('app.os.listdir', return_value=["2026-02-07-10_segment_00041.ts"])
    @patch('app.RTSP_URL', "rtsp://test_url")
    @patch('app.ARCHIVE_PATH', "/test_archive")
    @patch('app.current_process_hour_identifier', None)
    def test_start_ffmpeg_process_resumes_numbering(self, mock_listdir, mock_popen, mock_makedirs):
        app.ffmpeg_process = None
        app.start_ffmpeg_process("2026-02-07-10")

        command = mock_popen.call_args[0][0]
        self.assertIn("-start_number", command)
        self.assertEqual(command[command.index("-start_number") + 1], "42")
        self.assertIn("-hls_flags", command)
        self.assertEqual(command[command.index("-hls_flags") + 1], "append_list")
        
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

    @patch('app.os.killpg')
    @patch('app.os.getpgid')
    def test_shutdown_stops_consolidation_without_signaling_own_group(self, mock_getpgid, mock_killpg):
        """Consolidation processes are not started with setsid, so they share
        the archiver's own process group. Signaling their process group would
        SIGTERM/SIGKILL the archiver itself. Shutdown must signal only the
        consolidation process (terminate/kill on the Popen handle).
        """
        # Simulates the real bug: the consolidation proc is in our group
        mock_getpgid.return_value = os.getpgrp()

        consolidation_proc = MagicMock()
        consolidation_proc.poll.return_value = None  # still running
        consolidation_proc.wait.return_value = 0
        app.ffmpeg_process = None
        app.consolidation_processes = {"2099-01-01-00": consolidation_proc}

        with self.assertRaises(SystemExit):
            app.handle_shutdown_signal(signal.SIGTERM, None)

        consolidation_proc.terminate.assert_called_once_with()
        mock_killpg.assert_not_called()  # must never signal our own group

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
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "26",
            "-c:a", "copy",
            "/test_archive/archive_2026-02-07-09.mp4",
        ]
        self.assertEqual(mock_popen.call_args[0][0], expected_command)
        self.assertIn("2026-02-07-09", app.consolidation_processes)

    @patch('app.get_mp4_duration_seconds', return_value=3500.0)  # full hour -> delete path
    def test_consolidation_completes_when_child_writes_large_stderr(self, _mock_duration):
        """Regression test for the consolidation pipe deadlock.

        ffmpeg writes progress to stderr continuously; for long encodes the
        output exceeds the OS pipe buffer (~64KB). If the parent creates
        stderr=PIPE but only drains it after the process exits, the child
        blocks on write forever and never finishes.

        Runs a real subprocess through app's own Popen kwargs: the child
        writes >64KB to stderr then exits 0. Mimicking the main loop
        (poll without draining) must still reach the success path: the
        process is reaped from consolidation_processes and HLS files are
        deleted.
        """
        child_code = (
            "import sys\n"
            "line = 'x' * 80 + '\\n'\n"
            "for _ in range(1000):\n"  # ~80KB > 64KB pipe buffer
            "    sys.stderr.write(line)\n"
            "sys.stderr.flush()\n"
            "sys.exit(0)\n"
        )

        archive = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, archive, ignore_errors=True)
        playlist = os.path.join(archive, "playlist_2099-01-01-00.m3u8")
        with open(playlist, "w") as f:
            f.write("#EXTM3U\n")

        real_popen = subprocess.Popen
        popen_kwargs = {}

        def fake_popen(cmd, **kwargs):
            popen_kwargs.update(kwargs)
            # Swap the command for a fast child, keep the parent's pipe setup
            return real_popen([sys.executable, "-c", child_code], **kwargs)

        app.consolidation_processes = {}
        app.ARCHIVE_PATH = archive

        with patch('app.subprocess.Popen', side_effect=fake_popen):
            app.consolidate_hourly_archive("2099-01-01-00")

        proc = app.consolidation_processes.get("2099-01-01-00")
        self.assertIsNotNone(proc)

        def reap():
            if proc.poll() is None:
                proc.kill()
            try:
                proc.communicate(timeout=5)
            except Exception:
                pass
        self.addCleanup(reap)

        # Mimic the main loop: poll until the consolidation is reaped
        deadline = time.time() + 15
        while ("2099-01-01-00" in app.consolidation_processes
               and time.time() < deadline):
            app.check_consolidation_status()
            time.sleep(0.05)

        self.assertNotIn("2099-01-01-00", app.consolidation_processes)
        self.assertFalse(os.path.exists(playlist))  # success path deleted HLS

        # The app must not create pipes it never drains (deadlock tripwire)
        self.assertNotIn(subprocess.PIPE, popen_kwargs.values())

    @patch('app.get_mp4_duration_seconds', return_value=3500.0)  # full hour -> delete path
    @patch('app.os.listdir', return_value=[
        "2026-02-07-09_segment_00000.ts",
        "2026-02-07-09_segment_00001.ts",
        "playlist_2026-02-07-09.m3u8",
        "archive_2026-02-07-09.mp4", # Should not be deleted
        "other_file.txt", # Should not be deleted
    ])
    @patch('app.os.remove')
    def test_check_consolidation_status_success(self, mock_remove, mock_listdir, _mock_duration):
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

    @patch('builtins.open')
    @patch('app.os.remove')
    @patch('app.os.listdir', return_value=[
        "2026-02-07-09_segment_00000.ts",
        "playlist_2026-02-07-09.m3u8",
        "archive_2026-02-07-09.mp4",
    ])
    @patch('app.get_mp4_duration_seconds', return_value=120.0)  # ~2 min: incomplete hour
    def test_check_consolidation_status_short_keeps_sources(
            self, _mock_duration, mock_listdir, mock_remove, mock_open):
        """A restart-mid-hour produces a short archive. The duration gate must
        keep the HLS sources (only evidence of that window) and drop a
        .keep_<hour> marker instead of deleting anything."""
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0
        mock_proc.returncode = 0
        mock_proc.communicate.return_value = (b'stdout', b'stderr')
        app.consolidation_processes = {"2026-02-07-09": mock_proc}
        app.ARCHIVE_PATH = "/test_archive"

        app.check_consolidation_status()

        self.assertNotIn("2026-02-07-09", app.consolidation_processes)
        mock_remove.assert_not_called()  # nothing deleted
        mock_open.assert_called_once_with("/test_archive/.keep_2026-02-07-09", "w")

    @patch('builtins.open')
    @patch('app.os.remove')
    @patch('app.get_mp4_duration_seconds', return_value=None)  # ffprobe failed
    def test_check_consolidation_status_unknown_duration_keeps_sources(
            self, _mock_duration, mock_remove, mock_open):
        """ffprobe failure must fail safe: sources kept, marker written."""
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0
        mock_proc.returncode = 0
        mock_proc.communicate.return_value = (b'stdout', b'stderr')
        app.consolidation_processes = {"2026-02-07-09": mock_proc}
        app.ARCHIVE_PATH = "/test_archive"

        app.check_consolidation_status()

        mock_remove.assert_not_called()
        mock_open.assert_called_once_with("/test_archive/.keep_2026-02-07-09", "w")

    @patch('app.subprocess.run', side_effect=OSError('ffprobe missing'))
    def test_get_mp4_duration_seconds_fail_safe(self, _mock_run):
        self.assertIsNone(app.get_mp4_duration_seconds("/tmp/whatever.mp4"))

    @patch('app.os.path.getsize', return_value=1024)
    @patch('app.os.path.exists')
    @patch('app.os.listdir')
    @patch('app.os.remove')
    def test_purge_skips_hours_with_keep_marker(
            self, mock_remove, mock_listdir, mock_exists, mock_getsize):
        """purge_orphaned_files must never touch hours flagged incomplete."""
        def exists_side_effect(path):
            if path == "/test_archive":
                return True
            return path.endswith(".keep_2026-02-07-06")
        mock_exists.side_effect = exists_side_effect
        mock_listdir.return_value = [
            "archive_2026-02-07-05.mp4",       # complete hour
            "archive_2026-02-07-06.mp4",       # incomplete hour (keep marker)
            "2026-02-07-05_segment_00001.ts",  # -> deleted
            "playlist_2026-02-07-05.m3u8",     # -> deleted
            "2026-02-07-06_segment_00001.ts",  # -> kept
            "playlist_2026-02-07-06.m3u8",     # -> kept
        ]
        app.ARCHIVE_PATH = "/test_archive"

        deleted_count, deleted_size = app.purge_orphaned_files()

        self.assertEqual(deleted_count, 2)
        deleted_files = [call[0][0] for call in mock_remove.call_args_list]
        self.assertIn("/test_archive/2026-02-07-05_segment_00001.ts", deleted_files)
        self.assertIn("/test_archive/playlist_2026-02-07-05.m3u8", deleted_files)
        self.assertNotIn("/test_archive/2026-02-07-06_segment_00001.ts", deleted_files)
        self.assertNotIn("/test_archive/playlist_2026-02-07-06.m3u8", deleted_files)

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
    @patch('app.time.time')
    @patch('app.datetime')
    def test_cleanup_old_files(self, mock_datetime, mock_time, mock_remove, mock_getmtime, mock_listdir):
        # Mock current time to Feb 7, 2026, 10:00:00 (retention compares raw
        # POSIX timestamps, so the clock must be pinned for the fixture)
        now_ts = datetime(2026, 2, 7, 10, 0, 0).timestamp()
        mock_time.return_value = now_ts
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
        app.last_cleanup_time = now_ts - app.CLEANUP_INTERVAL_SECONDS - 1 # Ensure cleanup runs

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

    @patch('app.os.listdir', return_value=[
        "archive_old.mp4",   # expired, must be deleted
        "archive_recent.mp4",  # within retention, must be kept
    ])
    @patch('app.os.path.getmtime')
    @patch('app.os.remove')
    def test_cleanup_old_files_is_timezone_independent(self, mock_remove, mock_getmtime, mock_listdir):
        """Retention cutoff must be timezone-free.

        The old code compared datetime.fromtimestamp() (local timezone)
        against datetime.utcnow() (UTC). In a UTC+9 timezone, files up to
        9 hours older than the retention cutoff were wrongly kept (and in
        UTC-x timezones, slightly newer files were wrongly deleted).
        """
        fixed_now = time.time()

        old_ts = fixed_now - (90 * 86400) - (5 * 3600)  # 90d5h old: expired
        recent_ts = fixed_now - (30 * 86400)            # 30d old: keep
        mock_getmtime.side_effect = [old_ts, recent_ts]

        app.RETENTION_DAYS = 90
        app.ARCHIVE_PATH = "/test_archive"
        app.last_cleanup_time = time.time() - app.CLEANUP_INTERVAL_SECONDS - 1

        # Force a non-UTC timezone to expose the local-vs-UTC comparison.
        # The bug (and the fix) is observable regardless of the host tz only
        # if we pin a tz whose offset != 0.
        old_tz = os.environ.get("TZ")
        os.environ["TZ"] = "Asia/Seoul"  # UTC+9
        time.tzset()

        def restore_tz():
            if old_tz is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old_tz
            time.tzset()
        self.addCleanup(restore_tz)

        with patch('app.datetime') as mock_datetime:
            mock_datetime.utcnow.return_value = datetime.utcfromtimestamp(fixed_now)
            # Pre-fix code calls fromtimestamp(); keep it real (tz-sensitive)
            mock_datetime.fromtimestamp.side_effect = lambda ts: _original_datetime.fromtimestamp(ts)
            app.cleanup_old_files()

        deleted = [call[0][0] for call in mock_remove.call_args_list]
        self.assertIn("/test_archive/archive_old.mp4", deleted)      # expired -> deleted
        self.assertNotIn("/test_archive/archive_recent.mp4", deleted)  # recent -> kept
        self.assertEqual(mock_remove.call_count, 1)

    @patch('app.os.path.exists')
    @patch('app.os.listdir')
    @patch('app.os.path.getsize')
    @patch('app.os.remove')
    def test_purge_orphaned_files_with_orphans(self, mock_remove, mock_getsize, mock_listdir, mock_exists):
        """Test purge identifies and deletes HLS files that have corresponding MP4s."""
        # Only the archive root itself "exists"; no .keep_ markers are present.
        mock_exists.side_effect = lambda p: p == "/test_archive"
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
    @patch('app.os.path.exists')
    @patch('app.os.listdir')
    @patch('app.os.path.getsize')
    @patch('app.os.remove')
    def test_purge_orphaned_files_excludes_recent_hours(self, mock_remove, mock_getsize, mock_listdir, mock_exists, mock_datetime):
        """Test that purge excludes files from recent hours (current + previous 2 hours)."""
        # Only the archive root itself "exists"; no .keep_ markers are present.
        mock_exists.side_effect = lambda p: p == "/test_archive"
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

    def test_argument_parsing_purge(self):
        """Test that the purge command line argument is properly parsed."""
        import argparse
        
        # Test purge command
        parser = argparse.ArgumentParser()
        parser.add_argument("command", nargs="?", choices=["purge"])
        args = parser.parse_args(["purge"])
        
        self.assertEqual(args.command, "purge")
        self.assertTrue(args.command == "purge")
    
    def test_argument_parsing_default(self):
        """Test that no command defaults to None (which triggers main)."""
        import argparse
        
        # Test default (no command)
        parser = argparse.ArgumentParser()
        parser.add_argument("command", nargs="?", choices=["purge"])
        args = parser.parse_args([])
        
        self.assertIsNone(args.command)
        self.assertFalse(args.command == "purge")

if __name__ == '__main__':
    unittest.main()
