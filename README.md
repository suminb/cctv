# CCTV Archiver

A Python application that archives RTSP video streams by:
- Capturing live RTSP streams and saving them as HLS segments
- Consolidating hourly segments into MP4 files using H.265 codec
- Automatically cleaning up old archived files based on retention period

## Features

- **Continuous Recording**: Captures RTSP streams 24/7 with automatic crash recovery
- **Hourly Consolidation**: Converts HLS segments to compressed MP4 files every hour
- **Automatic Cleanup**: Removes archived files older than the configured retention period
- **Graceful Shutdown**: Handles termination signals to ensure clean process shutdown

## Configuration

The application is configured using environment variables:

- `RTSP_URL` (required): The RTSP stream URL to capture
- `ARCHIVE_PATH` (optional): Directory path for storing archived files (default: `/archive`)
- `RETENTION_DAYS` (optional): Number of days to keep archived files (default: 90)

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
  -v /path/to/local/archive:/archive \
  --name cctv-archiver \
  cctv-archiver
```

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

Ensure you have Python 3 and ffmpeg installed, then run:

```bash
export RTSP_URL="rtsp://your-camera-url"
export ARCHIVE_PATH="/path/to/archive"
export RETENTION_DAYS=90
python3 app.py
```

## Maintenance Commands

### Purge Orphaned Files

Over time, orphaned HLS files (.ts segments and .m3u8 playlists) may accumulate if consolidation processes fail or are interrupted. These orphaned files don't have corresponding MP4 archives. You can manually clean them up using the `purge` command:

```bash
export ARCHIVE_PATH="/path/to/archive"
python3 app.py purge
```

This command will:
- Scan the archive directory for all MP4 files
- Identify HLS files (.ts and .m3u8) that don't have corresponding MP4 archives
- Delete the orphaned files and report how much space was freed

**Note**: The purge command only deletes HLS files that are truly orphaned (those WITHOUT corresponding MP4 archives). HLS files that have corresponding MP4 archives are not considered orphaned and will not be deleted.

## Testing

Run the unit tests:

```bash
python3 -m unittest test_app.py
```

## CI/CD

The repository includes a GitHub Actions CI pipeline that:
- Runs unit tests on every push and pull request
- Validates the code works on Python 3.x

Note: Docker image building and pushing is not automated in the CI pipeline because the target registry is in a private network. Images should be built and pushed manually from within the private network as shown above.