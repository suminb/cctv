# web.py
import os
from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse
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
    # `/` (UI shell) is public so the token prompt loads;
    # `/api/*`, `/hls/*`, `/video/*` still require token.
    if request.method == 'GET' and request.url.path == '/':
        return await call_next(request)
    expected = get_token()
    if not expected:
        return await call_next(request)
    got = (request.query_params.get('token')
           or request.headers.get('x-cctv-token')
           or request.cookies.get('cctv_token'))
    # Cookie branch exists so HLS segment requests authenticate: hls.js (and
    # native <video>) fetch the .ts URLs straight from the playlist body,
    # which carries no query string or headers. Cookies go along automatically
    # on same-origin requests; the UI mirrors the token into one on entry.
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

STATIC_DIR = os.path.join(os.path.dirname(__file__), 'static')

@app.get('/hls/{filename}')
def serve_hls(filename: str):
    base = os.path.basename(filename)
    if base != filename or not (base.endswith('.m3u8') or base.endswith('.ts')):
        return JSONResponse({'detail': 'not found'}, status_code=404)
    if '_segment_' not in base and not base.startswith('playlist_'):
        return JSONResponse({'detail': 'not found'}, status_code=404)
    p = os.path.join(get_archive_path(), base)
    if not os.path.exists(p):
        return JSONResponse({'detail': 'not found'}, status_code=404)
    media = 'application/vnd.apple.mpegurl' if base.endswith('.m3u8') else 'video/mp2t'
    return FileResponse(p, media_type=media)

@app.get('/video/{name}')
def serve_video(name: str):
    base = os.path.basename(name)
    if base != name or not (base.startswith('archive_') and base.endswith('.mp4')):
        return JSONResponse({'detail': 'not found'}, status_code=404)
    p = os.path.join(get_archive_path(), base)
    if not os.path.exists(p):
        return JSONResponse({'detail': 'not found'}, status_code=404)
    return FileResponse(p, media_type='video/mp4')

@app.get('/')
def index():
    p = os.path.join(STATIC_DIR, 'index.html')
    if not os.path.exists(p):
        return JSONResponse({'detail': 'not found'}, status_code=404)
    return FileResponse(p, media_type='text/html')
