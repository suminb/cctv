# web.py
import os
from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse
import app as archiver

def get_token() -> str:
    return os.environ.get('CCTV_TOKEN', '')

def get_archive_path() -> str:
    return archiver.ARCHIVE_PATH

app = FastAPI(title='cctv-viewer')

@app.middleware('http')
async def token_guard(request: Request, call_next):
    if request.url.path in ('/docs', '/openapi.json', '/redoc'):
        return await call_next(request)
    expected = get_token()
    if not expected:
        return await call_next(request)
    got = request.query_params.get('token') or request.headers.get('x-cctv-token')
    if got != expected:
        return JSONResponse({'detail': 'unauthorized'}, status_code=401)
    return await call_next(request)

@app.get('/api/live')
def api_live():
    hour = archiver.get_current_hour_identifier()
    playlist = os.path.join(get_archive_path(), f'playlist_{hour}.m3u8')
    if not os.path.exists(playlist):
        return JSONResponse({'detail': 'live not ready'}, status_code=404)
    return {'hour': hour, 'playlist_url': f'/hls/playlist_{hour}.m3u8'}

@app.get('/api/files')
def api_files(date: str | None = Query(default=None)):
    base = get_archive_path()
    try:
        names = os.listdir(base)
    except FileNotFoundError:
        return []
    out = []
    for n in names:
        if not (n.startswith('archive_') and n.endswith('.mp4')):
            continue
        if date and f'archive_{date}' not in n:
            continue
        p = os.path.join(base, n)
        try:
            out.append({'name': n, 'hour': n[8:-4], 'size': os.path.getsize(p), 'mtime': os.path.getmtime(p)})
        except OSError:
            continue
    out.sort(key=lambda f: f['name'], reverse=True)
    return out
