# yt-dlp REST API & Dashboard

A clean, modern, and lightweight REST API and browser-based developer dashboard for `yt-dlp` built using Flask.

Features:
* **Background Downloads**: Queues and streams downloads asynchronously using thread-safe background workers.
* **Metadata Extraction**: Fetches full video info and available resolution/size presets without downloading.
* **Progress Tracking**: Scans terminal outputs to calculate download percentages in real-time.
* **Interactive UI Dashboard**: Visually manage downloads, live console logs, and Netscape cookies from the browser at `http://127.0.0.1:5000/`.
* **Flexible GET Endpoint**: Trigger downloads via a simple browser search query.
* **Docker Ready**: Pre-configured with a `Dockerfile` for easy deployment to cloud services like Render (including system-wide `ffmpeg` setup).

---

## Getting Started

### Prerequisites
Make sure you have `yt-dlp` and `ffmpeg` installed on your system.

### Running Locally
1. Install Python requirements:
   ```bash
   pip install -r requirments.txt
   ```
2. Start the API server:
   ```bash
   python api.py
   ```
3. Open your browser and navigate to **`http://127.0.0.1:5000/`** to access the dashboard.

---

## API Endpoints Reference

### 1. Trigger Download (GET /yt)
Trigger a download task directly from any browser URL query:
* **Endpoint**: `GET http://127.0.0.1:5000/yt`
* **Query Parameters**:
  * `url` (required) - Video URL to download.
  * `format` (optional) - Preset format: `"mp4"`, `"mp3"`.
  * `quality` (optional) - Maximum height: `"1080p"`, `"720p"`, `"480p"`, `"360p"`.
  * `rename` (optional) - Custom output filename.
  * `tag` (optional) - Optional suffix name tag.
  * `proxy` (optional) - Proxy IP/address.
  * `cookie` (optional) - Cookie filename saved under `/cookies`.

### 2. Trigger Download (POST /api/download)
* **Endpoint**: `POST http://127.0.0.1:5000/api/download`
* **Content-Type**: `application/json`
* **Payload**:
  ```json
  {
    "url": "https://www.youtube.com/watch?v=ThLEU0qlw6E",
    "format": "mp4",
    "quality": "720p"
  }
  ```

### 3. Check Tasks (GET /api/tasks)
* **Endpoint**: `GET http://127.0.0.1:5000/api/tasks`
* **Response**: Lists all tasks in this session with their status, logs, and progress.

### 4. Check Single Task (GET /api/tasks/<task_id>)
* **Endpoint**: `GET http://127.0.0.1:5000/api/tasks/1`

### 5. Stop Task (POST /api/tasks/<task_id>/stop)
* **Endpoint**: `POST http://127.0.0.1:5000/api/tasks/1/stop`

---

## Deployment to Render
The project is container-ready. When deploying to **Render**:
1. Connect your GitHub repository to a new Render **Web Service**.
2. Select **Docker** as the environment runtime. Render will automatically read the `Dockerfile`, install Python, `ffmpeg`, and start the app with Gunicorn.
3. (Optional) To make your downloads persistent, add a **Persistent Disk** mounted at path `/app/downloads`.
