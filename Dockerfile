# Use official lightweight Python image
FROM python:3.12-slim

# Install system dependencies (ffmpeg and ffprobe are needed by yt-dlp to merge video and audio formats)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg nodejs && \
    rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and install python packages
COPY requirments.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application files
COPY . .

# Expose port 10000 (Render's default)
EXPOSE 10000

# Set dynamic environment variables
ENV RENDER=true
ENV PORT=10000

# Run the app with gunicorn
CMD ["gunicorn", "api:app", "--bind", "0.0.0.0:10000", "--workers", "1", "--threads", "4", "--timeout", "120"]
