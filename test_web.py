# test_web.py (new file, first test only)
import os
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
