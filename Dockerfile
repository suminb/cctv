# Use a minimal base image with a static ffmpeg build
FROM jrottenberg/ffmpeg:5.1-alpine

# 1. Install Python
RUN apk add --no-cache python3

# 2. Set up the working directory
WORKDIR /app

# 3. Copy the application script into the container
COPY app.py .

# 4. Set the command to run the Python script
# Using -u for unbuffered output, which is good for logging in containers
ENTRYPOINT ["python3", "-u", "app.py"]
