# test_web.py (new file, first test only)
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
import web

class TestWebLive(unittest.TestCase):
    def test_live_returns_current_hour(self):
        with patch.object(web, 'get_archive_path', return_value='/tmp'), \
             patch('app.get_current_hour_identifier', return_value='2026-10-16-14'), \
             patch('os.path.exists', return_value=True):
            client = TestClient(web.app)
            r = client.get('/api/live')
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json(), {
                'hour': '2026-10-16-14',
                'playlist_url': '/hls/playlist_2026-10-16-14.m3u8',
            })

    def test_live_404_when_no_playlist(self):
        with patch.object(web, 'get_archive_path', return_value='/tmp'), \
             patch('app.get_current_hour_identifier', return_value='2026-10-16-14'), \
             patch('os.path.exists', return_value=False):
            client = TestClient(web.app)
            r = client.get('/api/live')
            self.assertEqual(r.status_code, 404)

    def test_auth_rejects_bad_token(self):
        with patch.object(web, 'get_token', return_value='secret123'):
            client = TestClient(web.app)
            r = client.get('/api/live', headers={'X-CCTV-Token': 'wrong'})
            self.assertEqual(r.status_code, 401)

    def test_auth_allows_valid_header_token(self):
        with patch.object(web, 'get_token', return_value='secret123'), \
             patch('app.get_current_hour_identifier', return_value='2026-10-16-14'), \
             patch('os.path.exists', return_value=True):
            c = TestClient(web.app)
            r = c.get('/api/live', headers={'X-CCTV-Token': 'secret123'})
            self.assertEqual(r.status_code, 200)

    def test_auth_open_when_no_token_set(self):
        with patch.object(web, 'get_token', return_value=''), \
             patch('app.get_current_hour_identifier', return_value='2026-10-16-14'), \
             patch('os.path.exists', return_value=True):
            c = TestClient(web.app)
            r = c.get('/api/live')
            self.assertEqual(r.status_code, 200)


class TestWebFiles(unittest.TestCase):
    def test_files_sorted_newest_first(self):
        fake = ['archive_2026-10-16-12.mp4', 'archive_2026-10-16-14.mp4', 'archive_2026-10-15-10.mp4', 'playlist_2026-10-16-14.m3u8', 'other.txt']
        with patch('os.listdir', return_value=fake), \
             patch('os.path.getsize', return_value=100), \
             patch('os.path.getmtime', return_value=1700000000.0), \
             patch.object(web, 'get_token', return_value=''):
            c = TestClient(web.app)
            r = c.get('/api/files')
            self.assertEqual(r.status_code, 200)
            names = [f['name'] for f in r.json()]
            self.assertEqual(names, ['archive_2026-10-16-14.mp4', 'archive_2026-10-16-12.mp4', 'archive_2026-10-15-10.mp4'])

    def test_files_date_filter(self):
        fake = ['archive_2026-10-16-14.mp4', 'archive_2026-10-15-10.mp4']
        with patch('os.listdir', return_value=fake), \
             patch('os.path.getsize', return_value=100), \
             patch('os.path.getmtime', return_value=1700000000.0), \
             patch.object(web, 'get_token', return_value=''):
            c = TestClient(web.app)
            r = c.get('/api/files?date=2026-10-15')
            self.assertEqual([f['name'] for f in r.json()], ['archive_2026-10-15-10.mp4'])


class TestWebStatic(unittest.TestCase):
    def test_hls_rejects_traversal(self):
        import web as _web
        from fastapi.testclient import TestClient as _TC
        with patch.object(_web, 'get_token', return_value=''):
            c = _TC(_web.app)
            r = c.get('/hls/../app.py')
            self.assertIn(r.status_code, (400, 404))

    def test_video_404_for_missing(self):
        import web as _web
        from fastapi.testclient import TestClient as _TC
        with patch.object(_web, 'get_token', return_value=''), \
             patch('os.path.exists', return_value=False):
            c = _TC(_web.app)
            r = c.get('/video/archive_2026-10-16-14.mp4')
            self.assertEqual(r.status_code, 404)

    def test_hls_guard_direct(self):
        import web as _web
        r = _web.serve_hls('../app.py')
        self.assertEqual(r.status_code, 404)
        r = _web.serve_hls('evil.mp4')
        self.assertEqual(r.status_code, 404)
        r = _web.serve_video('other.txt')
        self.assertEqual(r.status_code, 404)

    def test_hls_happy_path_mime(self):
        import os as _os
        import tempfile as _tf
        import web as _web
        from fastapi.testclient import TestClient as _TC
        with _tf.TemporaryDirectory() as tmp:
            m3u8 = 'playlist_2026-10-16-14.m3u8'
            ts = 'playlist_2026-10-16-14_segment_00000.ts'
            mp4 = 'archive_2026-10-16-14.mp4'
            for name, data in ((m3u8, b'#EXTM3U\n'), (ts, b'\x47' * 188), (mp4, b'\x00\x00\x00\x18ftyp')):
                with open(_os.path.join(tmp, name), 'wb') as f:
                    f.write(data)
            with patch.object(_web, 'get_token', return_value=''), \
                 patch.object(_web, 'get_archive_path', return_value=tmp):
                c = _TC(_web.app)
                r = c.get(f'/hls/{m3u8}')
                self.assertEqual(r.status_code, 200)
                self.assertIn('vnd.apple.mpegurl', r.headers.get('content-type', ''))
                r = c.get(f'/hls/{ts}')
                self.assertEqual(r.status_code, 200)
                self.assertIn('mp2t', r.headers.get('content-type', ''))
                r = c.get(f'/video/{mp4}')
                self.assertEqual(r.status_code, 200)
                self.assertIn('mp4', r.headers.get('content-type', ''))


class TestWebIndex(unittest.TestCase):
    def test_index_serves_html(self):
        with patch.object(web, 'get_token', return_value=''):
            c = TestClient(web.app)
            r = c.get('/')
            self.assertEqual(r.status_code, 200)
            self.assertIn('<video', r.text)
