# CCTV Archiver + Viewer

A Python application that archives RTSP video streams and serves them through a web UI:
- Capturing live RTSP streams and saving them as HLS segments
- Consolidating hourly segments into MP4 files using H.264 codec
- Automatically cleaning up old archived files based on retention period
- Serving a web viewer: live HLS feed by default, archived MP4 playback on selection

All timestamps / hour buckets (`YYYY-MM-DD-HH`) are **UTC**.

## Features

- **Continuous Recording**: Captures RTSP streams 24/7 with automatic crash recovery
- **Hourly Consolidation**: Converts HLS segments to compressed MP4 files every hour
- **Automatic Cleanup**: Removes archived files older than the configured retention period
- **Graceful Shutdown**: Handles termination signals to ensure clean process shutdown
- **Web Viewer**: Single-page UI on `:8080` — plays the live feed (current hour's HLS `.ts` segments via hls.js) when nothing is selected, plays `archive_*.mp4` when a file is selected, with a date filter and flat newest-first file list

## Configuration

The application is configured using environment variables:

- `RTSP_URL` (required): The RTSP stream URL to capture
- `ARCHIVE_PATH` (optional): Directory path for storing archived files (default: `/archive`)
- `RETENTION_DAYS` (optional): Number of days to keep archived files (default: 90)
- `CCTV_TOKEN` (optional): shared password for the web viewer. Empty = open. Set via secret.
- `WEB_PORT` (optional): viewer port (default: 8080).

## Web Viewer

The viewer is embedded in the archiver container (FastAPI + uvicorn on `$WEB_PORT`, default `8080`, started as a daemon thread next to the recorder loop).

- Open `http://localhost:8080` (via port-forward below). Nothing selected = **LIVE** (current hour's `playlist_*.m3u8` + 10s `.ts` segments, 10–30s delay). Click an MP4 row = play that file. Click **🔴 LIVE** = back to live.
- File list: `archive_*.mp4` newest-first with an optional date picker. Hours shown are UTC (parsed from filenames).
- Hour rollover: while watching live the player follows into the new hour (polled every 60s); while watching an MP4 it keeps playing uninterrupted.
- Restart caveat: restarting the pod mid-hour restarts that hour's HLS segment numbering from `00000`, so the consolidated MP4 for that hour only covers footage since the last restart.

### Access

Port-forward (service or deployment both work):

```bash
kubectl -n cctv port-forward svc/cctv-viewer 80:80
# then open http://localhost:80, or
kubectl -n cctv port-forward deploy/cctv-archiver 8080:8080
# then open http://localhost:8080
```

Enter the token when prompted if `CCTV_TOKEN` is set (stored in browser `localStorage`).

### Auth

Set `CCTV_TOKEN` to require a shared token (query `?token=` or `X-CCTV-Token` header). Empty = open access (fine behind port-forward on a private network).
`/` (UI shell) is public so the token prompt loads; `/api/*`, `/hls/*`, `/video/*` require the token.

```bash
kubectl -n cctv create secret generic cctv-rtsp-secret \
  --from-literal=RTSP_URL='rtsp://...' \
  --from-literal=CCTV_TOKEN='...' --dry-run=client -o yaml | kubectl apply -f -
```

### API

- `GET /` — viewer UI (`text/html`, public)
- `GET /api/live` — `{"hour": "YYYY-MM-DD-HH", "playlist_url": "/hls/playlist_....m3u8"}` (404 while the first playlist is being written)
- `GET /api/files?date=YYYY-MM-DD` — `[{"name", "hour", "size", "mtime"}]` newest-first
- `GET /hls/{playlist,m3u8,ts}` — HLS files (`application/vnd.apple.mpegurl` / `video/mp2t`)
- `GET /video/{archive_*.mp4}` — MP4 with range/seek support

### Kubernetes notes

- `k8s/deployment.yaml` uses `strategy: Recreate` (not RollingUpdate): the archive PVC is `ReadWriteOnce`, so a replacement pod on another node can never mount while the old pod holds the volume — RollingUpdate deadlocks. Recreate stops the old pod first (brief recording gap).
- Probes hit `GET /` (public by design) on port `8080`.

## Running the Application

### Using Docker

Build the Docker image:

```bash
docker build -t cctv-archiver .
```

Run the container:

```bash
docker run -d \
  -e RTSP_URL="rtsp://your-camera-url" \
  -e ARCHIVE_PATH="/archive" \
  -e RETENTION_DAYS=90 \
  -e CCTV_TOKEN="change-me" \
  -p 8080:8080 \
  -v /path/to/local/archive:/archive \
  --name cctv-archiver \
  cctv-archiver
```

Then open `http://localhost:8080`.

### Pushing to Private Registry

If you need to push the image to a private Docker registry (e.g., `zot.whiterabbit.co.kr`):

```bash
# Build the image
docker build -t zot.whiterabbit.co.kr/app/cctv:latest .

# Tag with specific version/commit
docker tag zot.whiterabbit.co.kr/app/cctv:latest zot.whiterabbit.co.kr/app/cctv:$(git rev-parse --short HEAD)

# Log in to the registry
docker login zot.whiterabbit.co.kr

# Push the images
docker push zot.whiterabbit.co.kr/app/cctv:latest
docker push zot.whiterabbit.co.kr/app/cctv:$(git rev-parse --short HEAD)
```

### Running Locally (without Docker)

Ensure you have Python 3.11+ and ffmpeg installed, then run:

```bash
pip install fastapi "uvicorn[standard]" httpx
export RTSP_URL="rtsp://your-camera-url"
export ARCHIVE_PATH="/path/to/archive"
export RETENTION_DAYS=90
export CCTV_TOKEN=""  # empty = open access for local dev
python3 app.py
# viewer at http://localhost:8080
```

## Maintenance Commands

### Purge Leftover HLS Files

When an MP4 archive is successfully created from HLS segments, the source .ts and .m3u8 files are automatically deleted. However, if this automatic cleanup fails (e.g., due to file permissions or other errors), these files may remain in the archive directory.

The `purge` command cleans up HLS files that should have been deleted after successful consolidation:

```bash
export ARCHIVE_PATH="/path/to/archive"
python3 app.py purge
```

This command will:
- Scan the archive directory for all MP4 files
- Identify HLS files (.ts and .m3u8) that **have** corresponding MP4 archives (meaning they should have been deleted already)
- **Exclude files from the current hour and previous 2 hours** (to protect actively recording or consolidating files)
- Delete the leftover HLS files and report how much space was freed

**Note**: The purge command only deletes HLS files that have corresponding MP4 archives AND are older than 3 hours. This ensures that:
- Files still being recorded are never deleted
- Files in the consolidation queue are protected
- Only leftover files from failed cleanup operations are removed

## Testing

Run the unit tests:

```bash
python3 -m unittest test_app test_web
```

## CI/CD

The repository includes a GitHub Actions CI pipeline that:
- Runs unit tests on every push and pull request
- Validates the code works on Python 3.x

Note: Docker image building and pushing is not automated in the CI pipeline because the target registry is in a private network. Images should be built and pushed manually from within the private network as shown above.