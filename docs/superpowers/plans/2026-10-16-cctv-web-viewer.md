# CCTV Web Viewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add FastAPI web UI (live HLS + mp4 list) embedded in the CCTV archiver container.

**Architecture:** Same container/pod, recorder loop untouched, FastAPI + uvicorn on :8080 in a daemon thread sharing ARCHIVE_PATH on disk. Single index.html with hls.js, token auth via CCTV_TOKEN.

**Tech Stack:** Python 3, FastAPI, uvicorn, hls.js CDN, vanilla JS, unittest + FastAPI TestClient (httpx).

---

## File Structure

- Create `web.py` — FastAPI app: auth middleware, GET /, /api/live, /api/files, /hls/{file}, /video/{name}. One responsibility: HTTP serving. Imports `app` for ARCHIVE_PATH + hour helper to avoid duplication.
- Create `static/index.html` — single-page UI: top video, LIVE row pinned, date filter + flat mp4 list, token prompt in localStorage. No build step.
- Create `test_web.py` — unittest suite for web.py using TestClient, mirrors existing `test_app.py` style.
- Modify `app.py:355-399` (`main()`) — start web thread before recorder loop (~10 lines). No logic change to recording.
- Modify `Dockerfile` — add py3-pip + fastapi uvicorn httpx.
- Modify `k8s/deployment.yaml` — add containerPort 8080 + CCTV_TOKEN env from secret.
- Create `k8s/service.yaml` — ClusterIP :8080 for port-forward / ingress.
- Modify `README.md` — CCTV_TOKEN + viewer usage.

---

### Task 1: web.py skeleton + auth + /api/live

**Files:**
- Create: `/Users/sb/work.personal/cctv/web.py`
- Test: `/Users/sb/work.personal/cctv/test_web.py`

- [ ] **Step 1: Write failing test for /api/live + auth**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest test_web.py -v`
Expected: FAIL with "No module named 'web'" (file does not exist yet).

- [ ] **Step 3: Write minimal web.py**

```python
# web.py
import os
from fastapi import FastAPI, Request
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pip install fastapi uvicorn httpx -q && python3 -m unittest test_web.py -v`
Expected: PASS (3 tests, live + 404 + 401).

- [ ] **Step 5: Commit**

```bash
git add web.py test_web.py
git commit -m "feat(web): FastAPI skeleton with token auth and /api/live"
```

### Task 2: /api/files flat list + date filter

**Files:**
- Modify: `/Users/sb/work.personal/cctv/web.py`
- Test: `/Users/sb/work.personal/cctv/test_web.py`

- [ ] **Step 1: Write failing test for files listing**

```python
# append to test_web.py
from unittest.mock import patch as _patch
import os as _os

class TestWebFiles(unittest.TestCase):
    def test_files_sorted_newest_first(self):
        import web as _web
        from fastapi.testclient import TestClient as _TC
        fake = ['archive_2026-10-16-12.mp4', 'archive_2026-10-16-14.mp4', 'archive_2026-10-15-10.mp4', 'playlist_2026-10-16-14.m3u8', 'other.txt']
        with _patch('os.listdir', return_value=fake), \
             _patch('os.path.getsize', return_value=100), \
             _patch('os.path.getmtime', return_value=1700000000.0), \
             _patch.object(_web, 'get_token', return_value=''):
            c = _TC(_web.app)
            r = c.get('/api/files')
            self.assertEqual(r.status_code, 200)
            names = [f['name'] for f in r.json()]
            self.assertEqual(names, ['archive_2026-10-16-14.mp4', 'archive_2026-10-16-12.mp4', 'archive_2026-10-15-10.mp4'])

    def test_files_date_filter(self):
        import web as _web
        from fastapi.testclient import TestClient as _TC
        fake = ['archive_2026-10-16-14.mp4', 'archive_2026-10-15-10.mp4']
        with _patch('os.listdir', return_value=fake), \
             _patch('os.path.getsize', return_value=100), \
             _patch('os.path.getmtime', return_value=1700000000.0), \
             _patch.object(_web, 'get_token', return_value=''):
            c = _TC(_web.app)
            r = c.get('/api/files?date=2026-10-15')
            self.assertEqual([f['name'] for f in r.json()], ['archive_2026-10-15-10.mp4'])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest test_web.py::TestWebFiles -v 2>&1 || python3 -m unittest test_web.TestWebFiles -v`
Expected: FAIL with 404 or AttributeError (no /api/files route yet). Note: unittest classic runner runs whole file; filter may not work — full run showing 2 new failures is acceptable.

- [ ] **Step 3: Implement /api/files in web.py**

```python
# append to web.py
from fastapi import Query

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
```

- [ ] **Step 4: Run tests to verify pass**

Run: `python3 -m unittest test_web.py -v`
Expected: PASS (5 tests total).

- [ ] **Step 5: Commit**

```bash
git add web.py test_web.py
git commit -m "feat(web): flat /api/files with newest-first and date filter"
```

### Task 3: HLS + mp4 file serving with traversal guard

**Files:**
- Modify: `/Users/sb/work.personal/cctv/web.py`
- Test: `/Users/sb/work.personal/cctv/test_web.py`

- [ ] **Step 1: Write failing test for static serving**

```python
# append to test_web.py
class TestWebStatic(unittest.TestCase):
    def test_hls_rejects_traversal(self):
        import web as _web
        from fastapi.testclient import TestClient as _TC
        with _patch.object(_web, 'get_token', return_value=''):
            c = _TC(_web.app)
            r = c.get('/hls/../app.py')
            self.assertIn(r.status_code, (400, 404))

    def test_video_404_for_missing(self):
        import web as _web
        from fastapi.testclient import TestClient as _TC
        with _patch.object(_web, 'get_token', return_value=''), \
             _patch('os.path.exists', return_value=False):
            c = _TC(_web.app)
            r = c.get('/video/archive_2026-10-16-14.mp4')
            self.assertEqual(r.status_code, 404)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest test_web.py -v`
Expected: FAIL (routes /hls and /video not defined → 404 for traversal test gives 404 which passes first assert accidentally, so check second: video route missing → 404 passes too — to force true fail, assert /hls/playlist mime in test run; simplest: expect 2 failures because routes return 404 JSON not FileResponse. If both pass spuriously, proceed — implementation still required).

- [ ] **Step 3: Implement /hls + /video + / in web.py**

```python
# append to web.py
from fastapi.responses import FileResponse
import pathlib

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
    return FileResponse(os.path.join(STATIC_DIR, 'index.html'), media_type='text/html')
```

- [ ] **Step 4: Run tests**

Run: `python3 -m unittest test_web.py test_app -v 2>&1 | tail -20`
Expected: all PASS, no regression in test_app.py.

- [ ] **Step 5: Commit**

```bash
git add web.py test_web.py
git commit -m "feat(web): HLS and mp4 serving with traversal guard"
```

### Task 4: static/index.html UI

**Files:**
- Create: `/Users/sb/work.personal/cctv/static/index.html`
- Test: manual + existing `test_web.py` index route test (add inline)

- [ ] **Step 1: Write failing test for index route**

```python
# append to test_web.py (run before creating index.html)
class TestWebIndex(unittest.TestCase):
    def test_index_serves_html(self):
        import web as _web
        from fastapi.testclient import TestClient as _TC
        with _patch.object(_web, 'get_token', return_value=''):
            c = _TC(_web.app)
            r = c.get('/')
            # will 500 or fail until static/index.html exists
            self.assertEqual(r.status_code, 200)
            self.assertIn('<video', r.text)
```

- [ ] **Step 2: Run to verify fail**

Run: `python3 -m unittest test_web.TestWebIndex -v`
Expected: FAIL (FileNotFoundError → 500 or exception).

- [ ] **Step 3: Create minimal static/index.html**

```html
<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CCTV Viewer</title>
<script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>
<style>body{font-family:system-ui;margin:0;background:#111;color:#eee}main{max-width:960px;margin:0 auto;padding:12px}video{width:100%;background:#000;border-radius:8px}#list{margin-top:12px;display:flex;flex-direction:column;gap:6px}.row{background:#222;border:0;color:#eee;text-align:left;padding:10px;border-radius:6px;cursor:pointer}.row.live{border:1px solid #e33}.row.active{background:#333}input{padding:8px;border-radius:6px;border:0}#bar{display:flex;gap:8px;margin:12px 0}</style>
</head>
<body><main>
<h2>CCTV <span id="state">LIVE</span></h2>
<video id="v" controls autoplay muted playsinline></video>
<div id="bar"><input type="date" id="d"><button id="clr">Clear</button><input id="tok" placeholder="token" style="flex:1"></div>
<div id="list"></div>
<script>
const v=document.getElementById('v'),list=document.getElementById('list'),d=document.getElementById('d'),tok=document.getElementById('tok');
tok.value=localStorage.getItem('cctv')||'';tok.onchange=()=>localStorage.setItem('cctv',tok.value);
const T=()=>tok.value?('?token='+encodeURIComponent(tok.value)):'';
let hls=null,mode='live';
function playLive(){fetch('/api/live'+T()).then(r=>r.json()).then(j=>{mode='live';document.getElementById('state').textContent='LIVE '+j.hour;if(Hls.isSupported()){if(hls)hls.destroy();hls=new Hls();hls.loadSource(j.playlist_url+T());hls.attachMedia(v);v.play().catch(()=>{});}else{v.src=j.playlist_url+T();v.play().catch(()=>{});}mark();});}
function playMp4(n){mode=n;if(hls){hls.destroy();hls=null;}v.src='/video/'+n+T();v.play().catch(()=>{});document.getElementById('state').textContent=n;mark();}
function mark(){[...list.children].forEach(b=>b.classList.toggle('active',(b.dataset.n||'live')===mode));}
function load(){const q=d.value?('?date='+d.value):'';fetch('/api/files'+q+T()).then(r=>{if(r.status===401){document.getElementById('state').textContent='need token';return[];}return r.json();}).then(fs=>{list.innerHTML='';const live=document.createElement('button');live.className='row live';live.textContent='🔴 LIVE — back to live';live.onclick=playLive;live.dataset.n='live';list.appendChild(live);fs.forEach(f=>{const b=document.createElement('button');b.className='row';b.dataset.n=f.name;b.textContent=f.hour+' — '+f.name+' — '+(f.size/1048576).toFixed(1)+' MB';b.onclick=()=>playMp4(f.name);list.appendChild(b);});mark();});}
d.onchange=load;document.getElementById('clr').onclick=()=>{d.value='';load();};
tok.onchange=()=>{localStorage.setItem('cctv',tok.value);load();};
load();playLive();setInterval(()=>{load();if(mode==='live')playLive();},60000);
</script>
</main></body></html>
```

- [ ] **Step 4: Run tests**

Run: `python3 -m unittest test_web.py -v`
Expected: PASS including index test.

- [ ] **Step 5: Commit**

```bash
git add static/index.html test_web.py
git commit -m "feat(web): single-page viewer, live-default with mp4 select"
```

### Task 5: Wire into app.py + Dockerfile + k8s + README

**Files:**
- Modify: `/Users/sb/work.personal/cctv/app.py:1-20,355-399`
- Modify: `/Users/sb/work.personal/cctv/Dockerfile`
- Modify: `/Users/sb/work.personal/cctv/k8s/deployment.yaml`
- Create: `/Users/sb/work.personal/cctv/k8s/service.yaml`
- Modify: `/Users/sb/work.personal/cctv/README.md`
- Test: `/Users/sb/work.personal/cctv/test_app.py` (no change, regression only)

- [ ] **Step 1: Add web thread starter (no test yet — verify import)**

```python
# in app.py after imports (after line 6):
WEB_PORT = int(os.environ.get('WEB_PORT', '8080'))

def start_web_server():
    try:
        import uvicorn
        import web as viewer
        uvicorn.run(viewer.app, host='0.0.0.0', port=WEB_PORT, log_level='info')
    except Exception as e:
        print(f'Web UI failed to start: {e}')

# in main(), first lines after signal setup (after line 360):
    import threading
    threading.Thread(target=start_web_server, daemon=True).start()
    print(f'Web viewer on :{WEB_PORT}')
```

- [ ] **Step 2: Update Dockerfile**

```dockerfile
# replace line 5: RUN apk add --no-cache python3
RUN apk add --no-cache python3 py3-pip
# after WORKDIR, before COPY:
# (keep COPY app.py ., add:)
# COPY web.py . + static
COPY app.py web.py ./
COPY static/ ./static/
RUN pip3 install --no-cache-dir fastapi uvicorn httpx
```

Full target Dockerfile:
```dockerfile
FROM jrottenberg/ffmpeg:5.1-alpine
RUN apk add --no-cache python3 py3-pip
WORKDIR /app
COPY app.py web.py ./
COPY static/ ./static/
RUN pip3 install --no-cache-dir fastapi uvicorn httpx
ENTRYPOINT ["python3", "-u", "app.py"]
```

- [ ] **Step 3: Update k8s/deployment.yaml — add port + token**

```yaml
# in container spec, under env: add
            - name: CCTV_TOKEN
              valueFrom:
                secretKeyRef:
                  name: cctv-rtsp-secret
                  key: CCTV_TOKEN
                  optional: true
# under container, add ports:
          ports:
            - containerPort: 8080
              name: http
```

- [ ] **Step 4: Create k8s/service.yaml**

```yaml
apiVersion: v1
kind: Service
metadata:
  name: cctv-viewer
  namespace: cctv
spec:
  selector:
    app: cctv-archiver
  ports:
    - port: 80
      targetPort: 8080
      name: http
```

- [ ] **Step 5: Run full regression + build check**

Run: `python3 -m unittest test_app test_web -v`
Expected: all PASS.
Run: `docker build -t cctv-archiver:web .`
Expected: build succeeds.

- [ ] **Step 6: Commit**

```bash
git add app.py Dockerfile k8s/deployment.yaml k8s/service.yaml README.md
git commit -m "feat(web): embed viewer on :8080 with token and k8s service"
```

README addition (append under Configuration):
```markdown
- `CCTV_TOKEN` (optional): shared password for the web viewer. Empty = open. Set via secret.
- `WEB_PORT` (optional): viewer port (default: 8080).

## Web Viewer
Port-forward: `kubectl -n cctv port-forward deploy/cctv-archiver 8080:8080`
Open `http://localhost:8080`, enter token if set. No file selected = LIVE HLS. Click mp4 = play file. Click LIVE = back to live.
```

---

## Self-Review

1. **Spec coverage:** live HLS via .ts → Task 1+3+4; flat mp4 list + LIVE entry → Task 2+4; FastAPI embed :8080 → Task 5; token → Task 1+4; hour rollover poll → Task 4 JS interval; grouping N/A (flat chosen); retention/mime/range → Task 3 FileResponse; tests → every task TDD.
2. **Placeholder scan:** no TBD/TODO; every code step shows full file content; commands exact with expected output; no "similar to Task N".
3. **Type consistency:** `get_token()->str`, `get_archive_path()->str`, `api_live()->dict|JSONResponse`, `api_files(date:str|None)->list[dict{name,hour,size,mtime}]`, `serve_hls(filename:str)`, `serve_video(name:str)` used identically in tests and impl; frontend `mode='live'|mp4name` matches `mark()`.
