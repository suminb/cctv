# python:3.11-slim: Debian ffmpeg bundles libx264 (needed for hourly
# consolidation) and stream-copy HLS needs no extra libs. This replaces
# jrottenberg/ffmpeg:5.1-alpine whose apk Python 3.8 cannot satisfy
# modern fastapi/uvicorn/starlette/pydantic (>=3.10, pip backtracking).
FROM python:3.11-slim

# 1. Install ffmpeg (includes libx264 encoder)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# 2. Set up the working directory
WORKDIR /app

# 3. Copy the application script into the container
COPY app.py web.py ./
COPY static/ ./static/
RUN pip install --no-cache-dir "fastapi>=0.110" "uvicorn[standard]>=0.29" "httpx>=0.27"

EXPOSE 8080

# 4. Set the command to run the Python script
# Using -u for unbuffered output, which is good for logging in containers
ENTRYPOINT ["python3", "-u", "app.py"]
