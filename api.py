import os
import sys
import json
import re
import shutil
import tempfile
import threading
import subprocess
import configparser
from datetime import datetime
from flask import Flask, request, jsonify, render_template_string

app = Flask(__name__)

# Load configurations
CONFIG_FILE = 'settings.ini'
HISTORY_FILE = 'download_history.json'
TAGS_FILE = 'tags_history.json'
COOKIES_DIR = 'cookies'

ENV_COOKIE_PATH = None
if os.environ.get("YT_DLP_COOKIES"):
    try:
        tmp_env = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False)
        tmp_env.write(os.environ["YT_DLP_COOKIES"])
        tmp_env.close()
        ENV_COOKIE_PATH = tmp_env.name
        print(f"Loaded default cookies from YT_DLP_COOKIES environment variable.")
    except Exception as e:
        print(f"Failed to parse YT_DLP_COOKIES env: {e}")

config_parser = configparser.ConfigParser()
IS_RENDER = os.environ.get("RENDER") == "true"
if IS_RENDER:
    default_download = os.path.join(os.path.abspath(os.path.dirname(__file__)), "downloads")
else:
    default_download = os.path.join(os.path.expanduser("~"), "Downloads")

try:
    config_parser.read(CONFIG_FILE)
    DOWNLOAD_PATH = config_parser.get('Settings', 'download_path')
    YTDLP_PATH = config_parser.get('Settings', 'ytdlp_path')
except:
    DOWNLOAD_PATH = default_download
    YTDLP_PATH = "yt-dlp"

if IS_RENDER:
    DOWNLOAD_PATH = default_download

if not os.path.isabs(DOWNLOAD_PATH) or not os.path.exists(DOWNLOAD_PATH):
    DOWNLOAD_PATH = default_download
os.makedirs(DOWNLOAD_PATH, exist_ok=True)

IS_WIN = sys.platform == "win32"
_SUBPROCESS_FLAGS = {}
if IS_WIN and hasattr(subprocess, 'CREATE_NO_WINDOW'):
    _SUBPROCESS_FLAGS['creationflags'] = subprocess.CREATE_NO_WINDOW

def _run(*args, **kwargs):
    kwargs = {**_SUBPROCESS_FLAGS, **kwargs}
    return subprocess.run(*args, **kwargs)

def _popen(*args, **kwargs):
    kwargs = {**_SUBPROCESS_FLAGS, **kwargs}
    return subprocess.Popen(*args, **kwargs)

# In-memory task tracking
tasks = {}
task_counter = 0
tasks_lock = threading.Lock()

def add_to_history(url, title):
    try:
        history_data = []
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                history_data = json.load(f)
        
        # Avoid duplicate URLs in recent history
        for item in history_data:
            if item.get('url') == url:
                item['timestamp'] = datetime.now().isoformat()
                item['title'] = title or item.get('title', 'Unknown')
                with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
                    json.dump(history_data, f, ensure_ascii=False, indent=2)
                return
        
        history_data.append({
            'url': url,
            'title': title or 'Unknown',
            'timestamp': datetime.now().isoformat()
        })
        if len(history_data) > 1000:
            history_data = history_data[-1000:]
        with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(history_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving history: {e}")

def save_tags(tag_val):
    try:
        tags_data = []
        if os.path.exists(TAGS_FILE):
            with open(TAGS_FILE, 'r', encoding='utf-8') as f:
                tags_data = json.load(f)
        if tag_val not in tags_data:
            tags_data.append(tag_val)
            with open(TAGS_FILE, 'w', encoding='utf-8') as f:
                json.dump(tags_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving tags: {e}")

# Helper to parse progress from yt-dlp stdout
progress_re = re.compile(r"(\d+(?:\.\d+)?)%")
def extract_progress(line):
    match = progress_re.search(line)
    if match:
        return float(match.group(1))
    return None

def download_worker(task_id, url, download_opts):
    global tasks
    
    # 1. Fetch Title
    with tasks_lock:
        tasks[task_id]["status"] = "fetching_info"
        tasks[task_id]["logs"].append("Fetching video information...")
    
    title_cmd = [YTDLP_PATH, url, "--print", "%(title)s", "--no-download"]
    if download_opts.get("proxy"):
        title_cmd.extend(["--proxy", download_opts["proxy"]])
    if download_opts.get("cookie_file"):
        cookie_path = os.path.join(COOKIES_DIR, download_opts["cookie_file"])
        if os.path.exists(cookie_path):
            title_cmd.extend(["--cookies", cookie_path])
            
    video_title = "Unknown"
    try:
        res = _run(title_cmd, capture_output=True, text=True, timeout=20)
        if res.returncode == 0 and res.stdout.strip():
            video_title = res.stdout.strip()
            add_to_history(url, video_title)
            with tasks_lock:
                tasks[task_id]["title"] = video_title
                tasks[task_id]["logs"].append(f"Title: {video_title}")
    except Exception as e:
        with tasks_lock:
            tasks[task_id]["logs"].append(f"Warning: could not fetch title ({e})")
            
    # 2. Start Download
    cmd = [YTDLP_PATH, url]
    
    if download_opts.get("proxy"):
        cmd.extend(["--proxy", download_opts["proxy"]])
        
    cookie_used = False
    if download_opts.get("cookie_file"):
        cookie_path = os.path.join(COOKIES_DIR, download_opts["cookie_file"])
        if os.path.exists(cookie_path):
            # Create a safe temp copy just like the GUI does
            try:
                tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False)
                tmp.close()
                shutil.copy2(cookie_path, tmp.name)
                cmd.extend(["--cookies", tmp.name])
                cookie_used = True
            except Exception as e:
                with tasks_lock:
                    tasks[task_id]["logs"].append(f"Cookie copy failed: {e}")

    # Fallback to environment variable cookie
    if not cookie_used and ENV_COOKIE_PATH and os.path.exists(ENV_COOKIE_PATH):
        cmd.extend(["--cookies", ENV_COOKIE_PATH])

    # Format and Quality
    fmt = download_opts.get("format")
    quality = download_opts.get("quality")  # "best", "1080p", "720p", "480p", "360p"
    
    height = None
    if quality == "1080p":
        height = 1080
    elif quality == "720p":
        height = 720
    elif quality == "480p":
        height = 480
    elif quality == "360p":
        height = 360

    if fmt == "mp3":
        cmd.extend(["--extract-audio", "--audio-format", "mp3"])
    else:
        if fmt == "mp4":
            if height:
                f_str = f"bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]/best[height<={height}][ext=mp4]/best"
            else:
                f_str = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
            cmd.extend(["-f", f_str, "--merge-output-format", "mp4"])
        else:
            if height:
                f_str = f"bestvideo[height<={height}]+bestaudio/best[height<={height}]"
                cmd.extend(["-f", f_str])

    # Output naming & tags
    base_name = (download_opts.get("rename") or "").strip()
    tag_val = (download_opts.get("tag") or "").strip()
    
    if base_name or tag_val:
        name = base_name if base_name else "%(title)s"
        if tag_val:
            name = f"{name}#{tag_val}"
            save_tags(tag_val)
        for c in '<>:"/\\|?*':
            name = name.replace(c, '_')
        cmd.extend(["-o", f"{name}.%(ext)s"])
    else:
        cmd.extend(["-o", "%(title)s-%(id)s.%(ext)s"])

    cmd.extend(["-P", DOWNLOAD_PATH])
    
    with tasks_lock:
        tasks[task_id]["status"] = "downloading"
        tasks[task_id]["logs"].append(f"Command: {' '.join(cmd)}")
        
    try:
        proc = _popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, universal_newlines=True)
        with tasks_lock:
            tasks[task_id]["process"] = proc
            
        for line in iter(proc.stdout.readline, ''):
            clean_line = line.rstrip()
            if not clean_line:
                continue
            
            with tasks_lock:
                if tasks[task_id]["status"] == "stopped":
                    break
                tasks[task_id]["logs"].append(clean_line)
                
                # Check for progress percentage
                p = extract_progress(clean_line)
                if p is not None:
                    tasks[task_id]["progress"] = p
                    
        proc.stdout.close()
        return_code = proc.wait()
        
        with tasks_lock:
            # If manually stopped, status is already updated
            if tasks[task_id]["status"] != "stopped":
                if return_code in (0, 1):
                    tasks[task_id]["status"] = "completed"
                    tasks[task_id]["progress"] = 100.0
                    tasks[task_id]["logs"].append("Download completed successfully!")
                else:
                    tasks[task_id]["status"] = "failed"
                    tasks[task_id]["logs"].append(f"Failed with exit code: {return_code}")
                    
    except Exception as e:
        with tasks_lock:
            tasks[task_id]["status"] = "failed"
            tasks[task_id]["logs"].append(f"Error: {e}")
    finally:
        # Cleanup temp cookie file from command if created
        if "--cookies" in cmd:
            try:
                idx = cmd.index("--cookies")
                tmp_cookie_path = cmd[idx+1]
                if os.path.exists(tmp_cookie_path) and "tmp" in tmp_cookie_path:
                    os.remove(tmp_cookie_path)
            except:
                pass
        with tasks_lock:
            if "process" in tasks[task_id]:
                del tasks[task_id]["process"]

# --- API Endpoints ---

@app.route('/api/info', methods=['GET'])
def get_info():
    url = request.args.get('url')
    if not url:
        return jsonify({"error": "URL is required"}), 400
        
    proxy = request.args.get('proxy')
    cookie_file = request.args.get('cookie')
    
    cmd = [YTDLP_PATH, url, "--dump-json", "--no-download"]
    if proxy:
        cmd.extend(["--proxy", proxy])
    if cookie_file:
        cookie_path = os.path.join(COOKIES_DIR, cookie_file)
        if os.path.exists(cookie_path):
            cmd.extend(["--cookies", cookie_path])
            
    try:
        res = _run(cmd, capture_output=True, text=True, timeout=30)
        if res.returncode == 0:
            info = json.loads(res.stdout)
            
            # Extract clean, simplified summary along with raw info
            summary = {
                "title": info.get("title"),
                "id": info.get("id"),
                "duration": info.get("duration"),
                "uploader": info.get("uploader"),
                "description": info.get("description"),
                "thumbnail": info.get("thumbnail"),
                "view_count": info.get("view_count"),
                "formats": [
                    {
                        "format_id": f.get("format_id"),
                        "ext": f.get("ext"),
                        "resolution": f.get("resolution"),
                        "filesize": f.get("filesize") or f.get("filesize_approx"),
                        "note": f.get("format_note")
                    }
                    for f in info.get("formats", []) if f.get("format_id")
                ]
            }
            return jsonify({"summary": summary, "raw": info})
        else:
            return jsonify({"error": "Failed to fetch metadata", "details": res.stderr}), 500
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Request timed out"}), 504
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/download', methods=['POST'])
def start_download():
    global task_counter, tasks
    data = request.json or {}
    
    url = data.get('url', '').strip()
    if not url:
        return jsonify({"error": "URL is required"}), 400
        
    with tasks_lock:
        task_counter += 1
        task_id = str(task_counter)
        tasks[task_id] = {
            "id": task_id,
            "url": url,
            "title": "Unknown",
            "status": "pending",
            "progress": 0.0,
            "logs": [],
            "timestamp": datetime.now().isoformat()
        }
        
    # Start thread
    download_opts = {
        "format": data.get("format"),
        "quality": data.get("quality"),
        "rename": data.get("rename"),
        "tag": data.get("tag"),
        "proxy": data.get("proxy"),
        "cookie_file": data.get("cookie")
    }
    thread = threading.Thread(target=download_worker, args=(task_id, url, download_opts), daemon=True)
    thread.start()
    
    return jsonify({
        "message": "Download started",
        "task": {
            "id": task_id,
            "url": url,
            "status": "pending"
        }
    }), 201

@app.route('/yt', methods=['GET'])
def get_download():
    global task_counter, tasks
    
    url = request.args.get('url', '').strip()
    if not url:
        return jsonify({"error": "URL is required"}), 400
        
    with tasks_lock:
        task_counter += 1
        task_id = str(task_counter)
        tasks[task_id] = {
            "id": task_id,
            "url": url,
            "title": "Unknown",
            "status": "pending",
            "progress": 0.0,
            "logs": [],
            "timestamp": datetime.now().isoformat()
        }
        
    download_opts = {
        "format": request.args.get("format"),
        "quality": request.args.get("quality"),
        "rename": request.args.get("rename"),
        "tag": request.args.get("tag"),
        "proxy": request.args.get("proxy"),
        "cookie_file": request.args.get("cookie")
    }
    
    thread = threading.Thread(target=download_worker, args=(task_id, url, download_opts), daemon=True)
    thread.start()
    
    return jsonify({
        "message": "Download started",
        "task": {
            "id": task_id,
            "url": url,
            "status": "pending"
        }
    }), 201

@app.route('/api/tasks', methods=['GET'])
def get_tasks():
    with tasks_lock:
        # Create a copy of tasks metadata (excluding subprocess object)
        result = {}
        for tid, t in tasks.items():
            result[tid] = {k: v for k, v in t.items() if k != "process"}
        return jsonify(result)

@app.route('/api/tasks/<task_id>', methods=['GET'])
def get_task_details(task_id):
    with tasks_lock:
        if task_id not in tasks:
            return jsonify({"error": "Task not found"}), 404
        t = tasks[task_id]
        return jsonify({k: v for k, v in t.items() if k != "process"})

@app.route('/api/tasks/<task_id>/stop', methods=['POST'])
def stop_task(task_id):
    with tasks_lock:
        if task_id not in tasks:
            return jsonify({"error": "Task not found"}), 404
        t = tasks[task_id]
        if t["status"] not in ("pending", "downloading", "fetching_info"):
            return jsonify({"error": "Task is not active"}), 400
            
        proc = t.get("process")
        if proc:
            try:
                if IS_WIN:
                    _run(['taskkill', '/F', '/T', '/PID', str(proc.pid)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    proc.terminate()
                t["logs"].append("Download manually stopped by user.")
            except Exception as e:
                return jsonify({"error": f"Failed to terminate process: {e}"}), 500
        
        t["status"] = "stopped"
        return jsonify({"message": "Task stopped successfully"})

@app.route('/api/history', methods=['GET'])
def get_history():
    try:
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                return jsonify(json.load(f))
        return jsonify([])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/history/clear', methods=['POST'])
def clear_history():
    try:
        with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump([], f)
        return jsonify({"message": "History cleared successfully"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/cookies', methods=['GET'])
def list_cookies():
    try:
        if not os.path.exists(COOKIES_DIR):
            os.makedirs(COOKIES_DIR, exist_ok=True)
        files = [f for f in os.listdir(COOKIES_DIR) if f.endswith('.txt')]
        return jsonify(files)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/cookies', methods=['POST'])
def save_cookie():
    data = request.json or {}
    filename = data.get('filename', '').strip()
    content = data.get('content', '')
    
    if not filename:
        return jsonify({"error": "Filename is required"}), 400
        
    for char in '<>:"/\\|?*':
        filename = filename.replace(char, '_')
    if not filename.endswith('.txt'):
        filename += '.txt'
        
    try:
        os.makedirs(COOKIES_DIR, exist_ok=True)
        cookie_path = os.path.join(COOKIES_DIR, filename)
        
        # If no content is provided, initialize empty Netscape HTTP cookie structure
        if not content:
            content = "# Netscape HTTP Cookie File\n# Edit cookies below\n\n"
            
        with open(cookie_path, 'w', encoding='utf-8') as f:
            f.write(content)
            
        return jsonify({"message": f"Cookie file {filename} saved successfully", "filename": filename})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/cookies/<filename>', methods=['DELETE'])
def delete_cookie(filename):
    try:
        cookie_path = os.path.join(COOKIES_DIR, filename)
        if not os.path.exists(cookie_path):
            return jsonify({"error": "Cookie file not found"}), 404
        os.remove(cookie_path)
        return jsonify({"message": f"Cookie file {filename} deleted successfully"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/upgrade', methods=['POST'])
def trigger_upgrade():
    # Use standard yt-dlp update check via subprocess
    try:
        res = _run([YTDLP_PATH, "-U"], capture_output=True, text=True, timeout=120)
        output = (res.stdout.strip() + "\n" + res.stderr.strip()).strip()
        
        # Since exit code 100 on pip installs might be expected, we report back status code and output
        return jsonify({
            "returncode": res.returncode,
            "output": output,
            "message": "Upgrade command executed."
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>yt-dlp REST API Dashboard</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
    <style>
        body {
            font-family: 'Plus Jakarta Sans', sans-serif;
            background-color: #0b0f19;
            color: #f8fafc;
        }
        code, pre {
            font-family: 'JetBrains Mono', monospace;
        }
        .glass {
            background: rgba(15, 23, 42, 0.75);
            backdrop-filter: blur(16px);
            border: 1px solid rgba(255, 255, 255, 0.05);
        }
        .custom-scroll::-webkit-scrollbar {
            width: 6px;
            height: 6px;
        }
        .custom-scroll::-webkit-scrollbar-track {
            background: rgba(0, 0, 0, 0.1);
        }
        .custom-scroll::-webkit-scrollbar-thumb {
            background: rgba(255, 255, 255, 0.1);
            border-radius: 4px;
        }
        .custom-scroll::-webkit-scrollbar-thumb:hover {
            background: rgba(255, 255, 255, 0.2);
        }
    </style>
</head>
<body class="min-h-screen flex flex-col justify-between">
    <!-- Navbar -->
    <header class="glass sticky top-0 z-40 w-full backdrop-blur">
        <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
            <div class="flex items-center gap-3">
                <div class="w-8 h-8 rounded-lg bg-emerald-500/10 flex items-center justify-center border border-emerald-500/20 text-emerald-400">
                    <svg class="w-5 h-5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                        <path stroke-linecap="round" stroke-linejoin="round" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"></path>
                    </svg>
                </div>
                <div>
                    <h1 class="text-lg font-bold bg-gradient-to-r from-emerald-400 to-teal-300 bg-clip-text text-transparent">yt-dlp REST API</h1>
                    <p class="text-xs text-slate-400">Developer Dashboard</p>
                </div>
            </div>
            <div class="flex items-center gap-3">
                <span class="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-medium bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                    <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span>
                    API Active
                </span>
            </div>
        </div>
    </header>

    <!-- Main Content -->
    <main class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 flex-grow w-full">
        <div class="grid grid-cols-1 lg:grid-cols-12 gap-8">
            
            <!-- Left Side: Form & Cookies -->
            <div class="lg:col-span-5 flex flex-col gap-8">
                <!-- Download Card -->
                <div class="glass rounded-2xl p-6 shadow-xl">
                    <h2 class="text-lg font-semibold mb-4 flex items-center gap-2">
                        <svg class="w-5 h-5 text-emerald-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                            <path stroke-linecap="round" stroke-linejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z"></path>
                        </svg>
                        <span>New Download Task</span>
                    </h2>
                    <form id="download-form" class="space-y-4">
                        <div>
                            <label class="block text-xs font-semibold text-slate-400 mb-1.5">Video URL</label>
                            <div class="flex gap-2">
                                <input type="url" id="video-url" required class="flex-grow bg-slate-900 border border-slate-800 rounded-xl px-4 py-2 text-sm focus:outline-none focus:border-emerald-500 transition-colors" placeholder="https://www.youtube.com/watch?v=...">
                                <button type="button" onclick="cleanUrlParams()" class="px-3 bg-slate-800 hover:bg-slate-700 rounded-xl text-xs transition-colors" title="Clean trackers/parameters">Clean</button>
                            </div>
                        </div>

                        <!-- Format -->
                        <div>
                            <label class="block text-xs font-semibold text-slate-400 mb-1.5">Format Preset</label>
                            <div class="grid grid-cols-3 gap-2">
                                <label class="flex items-center justify-center border border-slate-800 rounded-xl p-2.5 cursor-pointer hover:bg-slate-805/50 transition-colors text-sm">
                                    <input type="radio" name="format" value="default" checked class="sr-only peer">
                                    <span class="peer-checked:text-emerald-400 font-medium">Default</span>
                                </label>
                                <label class="flex items-center justify-center border border-slate-800 rounded-xl p-2.5 cursor-pointer hover:bg-slate-805/50 transition-colors text-sm">
                                    <input type="radio" name="format" value="mp4" class="sr-only peer">
                                    <span class="peer-checked:text-emerald-400 font-medium">MP4 Video</span>
                                </label>
                                <label class="flex items-center justify-center border border-slate-800 rounded-xl p-2.5 cursor-pointer hover:bg-slate-805/50 transition-colors text-sm">
                                    <input type="radio" name="format" value="mp3" class="sr-only peer">
                                    <span class="peer-checked:text-emerald-400 font-medium">MP3 Audio</span>
                                </label>
                            </div>
                        </div>

                        <!-- Video Quality -->
                        <div id="quality-container" class="space-y-1.5">
                            <label class="block text-xs font-semibold text-slate-400 mb-1.5">Video Quality</label>
                            <div class="grid grid-cols-5 gap-1.5">
                                <label class="flex items-center justify-center border border-slate-800 rounded-xl p-2 cursor-pointer hover:bg-slate-805/50 transition-colors text-[11px] font-semibold">
                                    <input type="radio" name="quality" value="best" checked class="sr-only peer">
                                    <span class="peer-checked:text-emerald-400">Best</span>
                                </label>
                                <label class="flex items-center justify-center border border-slate-800 rounded-xl p-2 cursor-pointer hover:bg-slate-805/50 transition-colors text-[11px] font-semibold">
                                    <input type="radio" name="quality" value="1080p" class="sr-only peer">
                                    <span class="peer-checked:text-emerald-400">1080p</span>
                                </label>
                                <label class="flex items-center justify-center border border-slate-800 rounded-xl p-2 cursor-pointer hover:bg-slate-805/50 transition-colors text-[11px] font-semibold">
                                    <input type="radio" name="quality" value="720p" class="sr-only peer">
                                    <span class="peer-checked:text-emerald-400">720p</span>
                                </label>
                                <label class="flex items-center justify-center border border-slate-800 rounded-xl p-2 cursor-pointer hover:bg-slate-805/50 transition-colors text-[11px] font-semibold">
                                    <input type="radio" name="quality" value="480p" class="sr-only peer">
                                    <span class="peer-checked:text-emerald-400">480p</span>
                                </label>
                                <label class="flex items-center justify-center border border-slate-800 rounded-xl p-2 cursor-pointer hover:bg-slate-805/50 transition-colors text-[11px] font-semibold">
                                    <input type="radio" name="quality" value="360p" class="sr-only peer">
                                    <span class="peer-checked:text-emerald-400">360p</span>
                                </label>
                            </div>
                        </div>

                        <!-- Advanced Collapse -->
                        <details class="group border border-slate-800/50 rounded-xl p-2 bg-slate-900/20">
                            <summary class="list-none flex items-center justify-between cursor-pointer text-xs font-semibold text-slate-400 px-2 py-1 select-none">
                                <span>Advanced Options</span>
                                <span class="transition-transform group-open:rotate-180">▼</span>
                            </summary>
                            <div class="space-y-3 mt-3 px-2 pb-2">
                                <div>
                                    <label class="block text-[11px] font-semibold text-slate-500 mb-1">Custom Filename</label>
                                    <input type="text" id="rename" class="w-full bg-slate-900 border border-slate-800 rounded-lg px-3 py-1.5 text-xs focus:outline-none focus:border-emerald-500" placeholder="e.g. MyFavoriteVideo">
                                </div>
                                <div>
                                    <label class="block text-[11px] font-semibold text-slate-500 mb-1">Tag (Suffix)</label>
                                    <input type="text" id="tag" class="w-full bg-slate-900 border border-slate-800 rounded-lg px-3 py-1.5 text-xs focus:outline-none focus:border-emerald-500" placeholder="e.g. music">
                                </div>
                                <div>
                                    <label class="block text-[11px] font-semibold text-slate-500 mb-1">Proxy Server</label>
                                    <input type="text" id="proxy" class="w-full bg-slate-900 border border-slate-800 rounded-lg px-3 py-1.5 text-xs focus:outline-none focus:border-emerald-500" placeholder="e.g. 127.0.0.1:7890">
                                </div>
                                <div>
                                    <label class="block text-[11px] font-semibold text-slate-500 mb-1">Cookie File</label>
                                    <select id="cookie-select" class="w-full bg-slate-900 border border-slate-800 rounded-lg px-3 py-1.5 text-xs focus:outline-none focus:border-emerald-500">
                                        <option value="">None</option>
                                    </select>
                                </div>
                            </div>
                        </details>

                        <div class="grid grid-cols-2 gap-3 pt-2">
                            <button type="button" onclick="fetchMetadata()" class="px-4 py-2.5 bg-slate-800 hover:bg-slate-700 text-slate-300 font-semibold rounded-xl text-sm transition-colors flex items-center justify-center gap-1.5">
                                <svg class="w-4 h-4 mr-1" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                                    <path stroke-linecap="round" stroke-linejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"></path>
                                </svg>
                                <span>Get Info</span>
                            </button>
                            <button type="submit" class="px-4 py-2.5 bg-emerald-600 hover:bg-emerald-500 text-white font-bold rounded-xl text-sm transition-colors flex items-center justify-center gap-1.5">
                                <svg class="w-4 h-4 mr-1" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                                    <path stroke-linecap="round" stroke-linejoin="round" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"></path>
                                </svg>
                                <span>Download</span>
                            </button>
                        </div>
                    </form>
                </div>

                <!-- Cookies Card -->
                <div class="glass rounded-2xl p-6 shadow-xl">
                    <h2 class="text-lg font-semibold mb-4 flex items-center gap-2">
                        <svg class="w-5 h-5 text-emerald-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                            <path stroke-linecap="round" stroke-linejoin="round" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"></path>
                        </svg>
                        <span>Cookies Manager</span>
                    </h2>
                    
                    <div id="cookies-list" class="space-y-2 max-h-36 overflow-y-auto custom-scroll mb-4">
                        <!-- populated by JS -->
                    </div>

                    <details class="group border border-slate-800/50 rounded-xl p-2 bg-slate-900/20">
                        <summary class="list-none flex items-center justify-between cursor-pointer text-xs font-semibold text-slate-400 px-2 py-1 select-none">
                            <span>Add New Cookie File</span>
                            <span class="transition-transform group-open:rotate-180">▼</span>
                        </summary>
                        <div class="space-y-3 mt-3 px-2 pb-2">
                            <div>
                                <label class="block text-[11px] font-semibold text-slate-500 mb-1">File Name (without extension)</label>
                                <input type="text" id="cookie-name" class="w-full bg-slate-900 border border-slate-800 rounded-lg px-3 py-1.5 text-xs focus:outline-none" placeholder="e.g. youtube">
                            </div>
                            <div>
                                <label class="block text-[11px] font-semibold text-slate-500 mb-1">Netscape Format Cookie Content</label>
                                <textarea id="cookie-content" rows="4" class="w-full bg-slate-900 border border-slate-800 rounded-lg p-2 text-xs focus:outline-none custom-scroll font-mono" placeholder="# Netscape HTTP Cookie File..."></textarea>
                            </div>
                            <button type="button" onclick="createCookie()" class="w-full py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 font-semibold rounded-lg text-xs transition-colors">Create Cookie File</button>
                        </div>
                    </details>
                </div>
            </div>

            <!-- Right Side: Active Tasks, History, Console Logs -->
            <div class="lg:col-span-7 flex flex-col gap-8">
                <!-- Active Tasks -->
                <div class="glass rounded-2xl p-6 shadow-xl flex-grow">
                    <h2 class="text-lg font-semibold mb-4 flex items-center gap-2">
                        <svg class="w-5 h-5 text-emerald-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                            <path stroke-linecap="round" stroke-linejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 1121.27 15H18"></path>
                        </svg>
                        <span>Active Tasks</span>
                    </h2>
                    <div id="active-tasks-list" class="space-y-4">
                        <!-- populated by JS -->
                        <p class="text-sm text-slate-500 text-center py-6">No downloads in progress</p>
                    </div>
                </div>

                <!-- History -->
                <div class="glass rounded-2xl p-6 shadow-xl">
                    <div class="flex items-center justify-between mb-4">
                        <h2 class="text-lg font-semibold flex items-center gap-2">
                            <svg class="w-5 h-5 text-emerald-400" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                                <path stroke-linecap="round" stroke-linejoin="round" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                            </svg>
                            <span>Download History</span>
                        </h2>
                        <button onclick="clearHistory()" class="text-xs text-red-400 hover:underline">Clear History</button>
                    </div>
                    <div class="overflow-x-auto max-h-60 custom-scroll">
                        <table class="w-full text-left text-xs border-collapse">
                            <thead>
                                <tr class="border-b border-slate-800 text-slate-400">
                                    <th class="py-2 font-medium">Title</th>
                                    <th class="py-2 font-medium">URL</th>
                                    <th class="py-2 font-medium">Date</th>
                                </tr>
                            </thead>
                            <tbody id="history-table-body" class="divide-y divide-slate-800/50">
                                <!-- populated by JS -->
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>

        </div>
    </main>

    <!-- Console Drawer -->
    <div id="console-drawer" class="fixed bottom-0 left-0 w-full bg-slate-950 border-t border-slate-800 p-4 transform translate-y-full transition-transform duration-300 ease-in-out z-50 shadow-2xl">
        <div class="max-w-7xl mx-auto flex flex-col h-72">
            <div class="flex items-center justify-between pb-2 border-b border-slate-800 mb-2">
                <div class="flex items-center gap-2">
                    <span class="w-2.5 h-2.5 rounded-full bg-emerald-500 animate-ping"></span>
                    <span class="text-sm font-semibold text-slate-300" id="console-title">Console Logs</span>
                </div>
                <button onclick="toggleConsole(false)" class="text-slate-400 hover:text-slate-200 text-sm">Close Console [×]</button>
            </div>
            <pre id="console-log-area" class="flex-grow bg-slate-900 border border-slate-800 rounded-lg p-3 overflow-y-auto custom-scroll text-xs text-slate-300 select-text leading-relaxed whitespace-pre-wrap"></pre>
        </div>
    </div>

    <!-- Metadata Modal -->
    <div id="meta-modal" class="fixed inset-0 bg-slate-950/80 backdrop-blur-sm hidden items-center justify-center z-50 p-4">
        <div class="glass max-w-2xl w-full rounded-2xl p-6 max-h-[85vh] flex flex-col overflow-hidden shadow-2xl">
            <div class="flex items-center justify-between pb-3 border-b border-slate-800 mb-4">
                <h3 class="text-lg font-bold text-slate-200">Video Metadata</h3>
                <button onclick="toggleModal(false)" class="text-slate-400 hover:text-slate-200">×</button>
            </div>
            <div id="meta-modal-content" class="overflow-y-auto custom-scroll text-sm space-y-4 pr-1">
                <!-- populated by JS -->
            </div>
        </div>
    </div>

    <!-- Footer -->
    <footer class="text-center py-6 border-t border-slate-800/40 text-xs text-slate-500 max-w-7xl mx-auto w-full">
        yt-dlp REST API Dashboard &bull; Built with Python Flask &bull; Downloads to Downloads Directory
    </footer>

    <script>
        let activeTaskId = null;
        let consoleOpen = false;

        // Clean URL parameters
        function cleanUrlParams() {
            const urlInput = document.getElementById('video-url');
            let url = urlInput.value.trim();
            if (url.includes('?')) {
                url = url.split('?')[0];
                urlInput.value = url;
            }
        }

        // Populates elements
        async function loadCookies() {
            try {
                const response = await fetch('/api/cookies');
                const files = await response.json();
                
                // Populate select
                const select = document.getElementById('cookie-select');
                select.innerHTML = '<option value="">None</option>';
                files.forEach(f => {
                    select.innerHTML += `<option value="${f}">${f}</option>`;
                });

                // Populate list
                const list = document.getElementById('cookies-list');
                if (files.length === 0) {
                    list.innerHTML = '<p class="text-xs text-slate-500">No cookie files saved</p>';
                    return;
                }
                list.innerHTML = '';
                files.forEach(f => {
                    list.innerHTML += `
                        <div class="flex items-center justify-between bg-slate-900 border border-slate-800/60 px-3 py-1.5 rounded-lg text-xs">
                            <span class="font-mono text-slate-300">${f}</span>
                            <button onclick="deleteCookie('${f}')" class="text-red-400 hover:text-red-300 transition-colors">Delete</button>
                        </div>
                    `;
                });
            } catch (err) {
                console.error("Error loading cookies", err);
            }
        }

        async function deleteCookie(filename) {
            if (!confirm(`Are you sure you want to delete ${filename}?`)) return;
            try {
                await fetch(`/api/cookies/${filename}`, { method: 'DELETE' });
                loadCookies();
            } catch (err) {
                alert("Delete failed");
            }
        }

        async function createCookie() {
            const name = document.getElementById('cookie-name').value.trim();
            const content = document.getElementById('cookie-content').value;
            if (!name) return alert("Please enter filename");
            
            try {
                const res = await fetch('/api/cookies', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ filename: name, content: content })
                });
                if (res.ok) {
                    document.getElementById('cookie-name').value = '';
                    document.getElementById('cookie-content').value = '';
                    loadCookies();
                } else {
                    alert("Save failed");
                }
            } catch (err) {
                alert("Save failed");
            }
        }

        async function loadHistory() {
            try {
                const response = await fetch('/api/history');
                const history = await response.json();
                const tbody = document.getElementById('history-table-body');
                if (history.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="3" class="py-4 text-center text-slate-500">No download history</td></tr>';
                    return;
                }
                tbody.innerHTML = '';
                history.reverse().slice(0, 100).forEach(item => {
                    const date = new Date(item.timestamp).toLocaleString();
                    const shortUrl = item.url.length > 30 ? item.url.slice(0, 27) + '...' : item.url;
                    tbody.innerHTML += `
                        <tr class="hover:bg-slate-900/30 transition-colors">
                            <td class="py-2.5 font-medium text-slate-200 max-w-xs truncate">${item.title}</td>
                            <td class="py-2.5 text-slate-400 font-mono"><a href="${item.url}" target="_blank" class="hover:text-emerald-400 underline">${shortUrl}</a></td>
                            <td class="py-2.5 text-slate-500">${date}</td>
                        </tr>
                    `;
                });
            } catch (err) {
                console.error("Error loading history", err);
            }
        }

        async function clearHistory() {
            if (!confirm("Are you sure you want to clear all history?")) return;
            try {
                await fetch('/api/history/clear', { method: 'POST' });
                loadHistory();
            } catch (err) {
                alert("Failed to clear history");
            }
        }

        // Fetch Metadata
        async function fetchMetadata() {
            const url = document.getElementById('video-url').value.trim();
            if (!url) return alert("Please paste a video URL first.");
            
            const proxy = document.getElementById('proxy').value.trim();
            const cookie = document.getElementById('cookie-select').value;
            
            let query = `/api/info?url=${encodeURIComponent(url)}`;
            if (proxy) query += `&proxy=${encodeURIComponent(proxy)}`;
            if (cookie) query += `&cookie=${encodeURIComponent(cookie)}`;

            // UI state
            toggleModal(true, '<div class="text-center py-8"><svg class="animate-spin h-8 w-8 text-emerald-500 mx-auto mb-4" fill="none" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg><p class="text-slate-400 text-sm">Fetching metadata from video provider...</p></div>');

            try {
                const response = await fetch(query);
                const data = await response.json();
                
                if (!response.ok) {
                    toggleModal(true, `<div class="text-red-400 p-4 border border-red-500/20 bg-red-500/5 rounded-xl"><p class="font-bold">Error:</p><p class="text-sm font-mono mt-1">${data.error || 'Failed to parse metadata'}</p></div>`);
                    return;
                }
                
                const s = data.summary;
                const min = Math.floor(s.duration / 60);
                const sec = s.duration % 60;
                
                let content = `
                    <div class="flex flex-col md:flex-row gap-4 items-start">
                        ${s.thumbnail ? `<img src="${s.thumbnail}" class="w-full md:w-48 aspect-video object-cover rounded-xl border border-slate-800" alt="Thumbnail">` : ''}
                        <div class="flex-grow space-y-1">
                            <h4 class="text-base font-bold text-slate-100 leading-snug">${s.title}</h4>
                            <p class="text-xs text-slate-400">Uploader: <span class="font-medium text-slate-300">${s.uploader || 'Unknown'}</span></p>
                            <p class="text-xs text-slate-400">Duration: <span class="font-medium text-slate-300">${min}m ${sec}s</span></p>
                            <p class="text-xs text-slate-400">Views: <span class="font-medium text-slate-300">${(s.view_count || 0).toLocaleString()}</span></p>
                        </div>
                    </div>
                `;

                if (s.description) {
                    content += `
                        <div class="border-t border-slate-800 pt-3">
                            <label class="block text-xs font-semibold text-slate-500 mb-1">Description</label>
                            <p class="text-xs text-slate-300 line-clamp-3 overflow-hidden leading-relaxed">${s.description}</p>
                        </div>
                    `;
                }

                if (s.formats && s.formats.length > 0) {
                    content += `
                        <div class="border-t border-slate-800 pt-3">
                            <label class="block text-xs font-semibold text-slate-500 mb-1.5">Available Formats (${s.formats.length})</label>
                            <div class="grid grid-cols-1 sm:grid-cols-2 gap-2 max-h-48 overflow-y-auto custom-scroll pr-1 font-mono text-[11px]">
                                ${s.formats.map(f => `
                                    <div class="bg-slate-900 border border-slate-800/80 px-2 py-1 rounded flex justify-between">
                                        <span class="text-slate-300">${f.format_id} (${f.ext})</span>
                                        <span class="text-slate-400">${f.resolution || f.note || ''}</span>
                                    </div>
                                `).join('')}
                            </div>
                        </div>
                    `;
                }
                
                toggleModal(true, content);
            } catch (err) {
                toggleModal(true, `<div class="text-red-400 p-4 border border-red-500/20 bg-red-500/5 rounded-xl"><p class="font-bold">Error:</p><p class="text-sm font-mono mt-1">${err.message}</p></div>`);
            }
        }

        function toggleModal(show, content = '') {
            const modal = document.getElementById('meta-modal');
            const inner = document.getElementById('meta-modal-content');
            if (show) {
                if (content) inner.innerHTML = content;
                modal.classList.remove('hidden');
                modal.classList.add('flex');
            } else {
                modal.classList.remove('flex');
                modal.classList.add('hidden');
            }
        }

        // Download Form Submission
        document.getElementById('download-form').addEventListener('submit', async (e) => {
            e.preventDefault();
            const url = document.getElementById('video-url').value.trim();
            const format = document.querySelector('input[name="format"]:checked').value;
            const quality = document.querySelector('input[name="quality"]:checked').value;
            const rename = document.getElementById('rename').value.trim();
            const tag = document.getElementById('tag').value.trim();
            const proxy = document.getElementById('proxy').value.trim();
            const cookie = document.getElementById('cookie-select').value;

            const body = { url };
            if (format !== 'default') body.format = format;
            if (format !== 'mp3' && quality !== 'best') body.quality = quality;
            if (rename) body.rename = rename;
            if (tag) body.tag = tag;
            if (proxy) body.proxy = proxy;
            if (cookie) body.cookie = cookie;

            try {
                const response = await fetch('/api/download', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body)
                });
                const res = await response.json();
                if (response.ok) {
                    // Reset URL input
                    document.getElementById('video-url').value = '';
                    document.getElementById('rename').value = '';
                    document.getElementById('tag').value = '';
                    
                    // Show console logs drawer automatically for the started task
                    activeTaskId = res.task.id;
                    toggleConsole(true, `Task ${activeTaskId} Console Logs`);
                    
                    loadTasks();
                } else {
                    alert(res.error || "Failed to start download");
                }
            } catch (err) {
                alert("Failed to connect to API");
            }
        });

        // Load & Monitor Background Tasks
        async function loadTasks() {
            try {
                const response = await fetch('/api/tasks');
                const taskDict = await response.json();
                const container = document.getElementById('active-tasks-list');
                
                const taskIds = Object.keys(taskDict);
                if (taskIds.length === 0) {
                    container.innerHTML = '<p class="text-sm text-slate-500 text-center py-6">No downloads in progress</p>';
                    return;
                }

                // If active console task has finished downloading, reload history
                let logsContent = "";
                let consoleTitleStr = "Console Logs";

                container.innerHTML = '';
                taskIds.reverse().forEach(tid => {
                    const task = taskDict[tid];
                    const progress = parseFloat(task.progress).toFixed(1);
                    
                    let statusColor = "bg-slate-800 text-slate-400";
                    if (task.status === "downloading") statusColor = "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20";
                    if (task.status === "completed") statusColor = "bg-teal-500/10 text-teal-400 border border-teal-500/20";
                    if (task.status === "failed") statusColor = "bg-red-500/10 text-red-400 border border-red-500/20";
                    if (task.status === "stopped") statusColor = "bg-amber-500/10 text-amber-400 border border-amber-500/20";
                    if (task.status === "fetching_info") statusColor = "bg-blue-500/10 text-blue-400 border border-blue-500/20 border-pulse";

                    const isCurrentConsole = activeTaskId === tid;
                    if (isCurrentConsole) {
                        logsContent = task.logs.join('\\n');
                        consoleTitleStr = `Task ${tid} Logs (${task.status})`;
                    }

                    container.innerHTML += `
                        <div onclick="selectTaskForConsole('${tid}')" class="bg-slate-900/60 border ${isCurrentConsole ? 'border-emerald-500' : 'border-slate-800/80'} rounded-xl p-4 cursor-pointer hover:bg-slate-800/40 transition-all select-none">
                            <div class="flex items-center justify-between mb-2">
                                <h4 class="text-sm font-semibold truncate text-slate-200 max-w-[70%]">${task.title || 'Fetching metadata...'}</h4>
                                <div class="flex items-center gap-2">
                                    <span class="text-[10px] font-bold px-2 py-0.5 rounded-full ${statusColor}">${task.status.toUpperCase()}</span>
                                    ${task.status === "downloading" || task.status === "fetching_info" ? `
                                        <button onclick="event.stopPropagation(); stopTask('${tid}')" class="text-slate-400 hover:text-red-400 text-xs font-semibold px-2 py-0.5 rounded border border-slate-700 hover:border-red-400 transition-colors">Stop</button>
                                    ` : ''}
                                </div>
                            </div>
                            <div class="flex items-center gap-3">
                                <div class="flex-grow bg-slate-800 h-2 rounded-full overflow-hidden">
                                    <div class="bg-emerald-500 h-2 rounded-full transition-all duration-300" style="width: ${progress}%"></div>
                                </div>
                                <span class="text-xs font-semibold text-slate-300 font-mono w-10 text-right">${progress}%</span>
                            </div>
                        </div>
                    `;
                });

                // Update console contents dynamically if console is open
                if (consoleOpen && activeTaskId) {
                    const logsArea = document.getElementById('console-log-area');
                    const titleArea = document.getElementById('console-title');
                    titleArea.innerText = consoleTitleStr;
                    
                    const atBottom = logsArea.scrollHeight - logsArea.clientHeight <= logsArea.scrollTop + 50;
                    logsArea.textContent = logsContent;
                    if (atBottom) {
                        logsArea.scrollTop = logsArea.scrollHeight;
                    }
                }
            } catch (err) {
                console.error("Error fetching tasks", err);
            }
        }

        async function stopTask(tid) {
            if (!confirm(`Are you sure you want to stop task ${tid}?`)) return;
            try {
                await fetch(`/api/tasks/${tid}/stop`, { method: 'POST' });
                loadTasks();
            } catch (err) {
                alert("Failed to stop task");
            }
        }

        function selectTaskForConsole(tid) {
            activeTaskId = tid;
            toggleConsole(true);
            loadTasks();
        }

        function toggleConsole(show, title = "Console Logs") {
            const consoleEl = document.getElementById('console-drawer');
            consoleOpen = show;
            if (show) {
                consoleEl.classList.remove('translate-y-full');
            } else {
                consoleEl.classList.add('translate-y-full');
            }
        }

        // Periodically refresh active downloads and logs
        setInterval(loadTasks, 1500);
        setInterval(loadHistory, 8000);

        // Show/hide quality selector based on format selection
        document.querySelectorAll('input[name="format"]').forEach(radio => {
            radio.addEventListener('change', (e) => {
                const container = document.getElementById('quality-container');
                if (e.target.value === 'mp3') {
                    container.classList.add('hidden');
                } else {
                    container.classList.remove('hidden');
                }
            });
        });

        // Load initially
        loadCookies();
        loadHistory();
        loadTasks();
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

if __name__ == '__main__':
    # Listen on host/port from environment if running under Render or other systems
    host = '0.0.0.0' if (os.environ.get("RENDER") == "true" or os.environ.get("PORT")) else '127.0.0.1'
    port = int(os.environ.get("PORT", 5000))
    print(f"Starting yt-dlp Flask API server...")
    print(f"Downloads directory: {DOWNLOAD_PATH}")
    app.run(host=host, port=port, debug=(not IS_RENDER))
