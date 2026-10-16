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
