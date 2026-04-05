"""
Flask Backend — Zalo Scraper Dashboard
Cung cấp REST API + Server-Sent Events (SSE) cho giao diện web.
"""

from flask import Flask, jsonify, request, Response, send_from_directory
from flask_cors import CORS
import threading
import queue
import time
import json
import os
import subprocess
import sys
import logging
from datetime import datetime
from collections import deque

# ──────────────────────────────────────────────
app = Flask(__name__, static_folder="static", template_folder="templates")
CORS(app)

# ──────────────────────────────────────────────
#  TRẠNG THÁI TOÀN CỤC
# ──────────────────────────────────────────────
class ScraperState:
    """Quản lý trạng thái của scraper process."""
    def __init__(self):
        self.is_running   = False
        self.process      = None          # subprocess.Popen
        self.thread       = None          # thread đọc stdout
        self.start_time   = None
        self.total_scraped = 0
        self.total_pushed  = 0
        self.total_failed  = 0
        self.current_name  = ""
        self.scraped_list  = []           # [{name, logs, time}]
        self.log_queue     = queue.Queue()
        self.log_history   = deque(maxlen=500)  # giữ 500 dòng log gần nhất

    def reset_stats(self):
        self.total_scraped = 0
        self.total_pushed  = 0
        self.total_failed  = 0
        self.current_name  = ""
        self.scraped_list  = []
        self.start_time    = datetime.now().isoformat()

    def push_log(self, level: str, message: str):
        entry = {
            "time":    datetime.now().strftime("%H:%M:%S"),
            "level":   level,      # INFO | WARNING | ERROR | SUCCESS
            "message": message,
        }
        self.log_history.append(entry)
        self.log_queue.put(entry)

    def elapsed(self) -> str:
        if not self.start_time:
            return "—"
        start = datetime.fromisoformat(self.start_time)
        delta = datetime.now() - start
        h, rem = divmod(int(delta.total_seconds()), 3600)
        m, s   = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"


STATE = ScraperState()

# ──────────────────────────────────────────────
#  SCRAPER RUNNER (subprocess)
# ──────────────────────────────────────────────
def _run_scraper_process(config: dict):
    """Chạy zalo_scraper.py như subprocess và đọc log từ stdout."""
    limit   = config.get("limit", 100)
    api_url = config.get("apiEndpoint", "http://localhost:3000/api/crm/import-chats")
    secret  = config.get("apiSecret",   "antigravity_secret_2026")

    STATE.reset_stats()
    STATE.is_running = True
    STATE.push_log("INFO", f"🚀 Bắt đầu scraper — limit={limit}")
    STATE.push_log("INFO", f"📡 API: {api_url}")

    # Viết file config tạm thời để scraper đọc
    _write_runtime_config(limit, api_url, secret)

    try:
        # Ép Python subprocess xuất ra UTF-8 (tránh lỗi cp1252 trên Windows)
        sub_env = {
            **os.environ,
            "PYTHONIOENCODING": "utf-8",        # stdout/stderr của child process
            "PYTHONUTF8":       "1",              # Python 3.7+ UTF-8 mode
            "SCRAPER_LIMIT":    str(limit),
            "SCRAPER_API_URL":  api_url,
            "SCRAPER_SECRET":   secret,
        }
        proc = subprocess.Popen(
            [sys.executable, "-u", "zalo_scraper.py"],  # -u = unbuffered
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=False,          # đọc bytes thô, decode thủ công bên dưới
            bufsize=0,
            cwd=os.path.dirname(os.path.abspath(__file__)),
            env=sub_env,
        )
        STATE.process = proc

        # Đọc từng dòng bytes → decode UTF-8, bỏ qua byte lỗi
        for raw_line in proc.stdout:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue

            # Phân tích dòng log để cập nhật stats
            _parse_log_line(line)

            # Xác định level để highlight
            level = "INFO"
            if "ERROR" in line or "❌" in line:
                level = "ERROR"
            elif "WARNING" in line or "⚠" in line:
                level = "WARNING"
            elif "✅" in line or "Thành công" in line or "HOÀN TẤT" in line:
                level = "SUCCESS"

            STATE.push_log(level, line)

        proc.wait()
        exit_code = proc.returncode
        STATE.push_log("INFO" if exit_code == 0 else "ERROR",
                       f"Process kết thúc (exit code: {exit_code})")

    except FileNotFoundError:
        STATE.push_log("ERROR", "❌ Không tìm thấy zalo_scraper.py")
    except Exception as e:
        STATE.push_log("ERROR", f"❌ Lỗi nghiêm trọng: {e}")
    finally:
        STATE.is_running  = False
        STATE.process     = None
        STATE.push_log("SUCCESS", "✅ Scraper đã dừng.")


def _write_runtime_config(limit, api_url, secret):
    """Ghi config ra file runtime_config.json để scraper đọc nếu cần."""
    cfg = {"limit": limit, "api_endpoint": api_url, "api_secret": secret}
    with open("runtime_config.json", "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def _parse_log_line(line: str):
    """Phân tích log line để cập nhật stats của STATE."""
    if "đã cào" in line.lower() or "tiến độ" in line.lower():
        import re
        m = re.search(r"(\d+)/\d+ người", line)
        if m:
            STATE.total_scraped = int(m.group(1))
    if "Click vào:" in line:
        import re
        m = re.search(r"Click vào: '(.+?)'", line)
        if m:
            STATE.current_name = m.group(1)
    if "✅ Thành công" in line:
        STATE.total_pushed += 1
    if "❌" in line and "[PUSH]" in line:
        STATE.total_failed += 1


# ──────────────────────────────────────────────
#  REST API ENDPOINTS
# ──────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory("templates", "index.html")


@app.route("/api/status")
def api_status():
    return jsonify({
        "isRunning":    STATE.is_running,
        "startTime":    STATE.start_time,
        "elapsed":      STATE.elapsed(),
        "totalScraped": STATE.total_scraped,
        "totalPushed":  STATE.total_pushed,
        "totalFailed":  STATE.total_failed,
        "currentName":  STATE.current_name,
        "scrapedList":  STATE.scraped_list[-50:],  # trả 50 gần nhất
    })


@app.route("/api/start", methods=["POST"])
def api_start():
    if STATE.is_running:
        return jsonify({"ok": False, "error": "Scraper đang chạy rồi!"}), 400

    config = request.get_json(silent=True) or {}
    STATE.log_history.clear()

    t = threading.Thread(target=_run_scraper_process, args=(config,), daemon=True)
    STATE.thread = t
    t.start()

    return jsonify({"ok": True, "message": "Scraper đã được khởi động."})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    if not STATE.is_running:
        return jsonify({"ok": False, "error": "Scraper không đang chạy."}), 400

    if STATE.process:
        STATE.process.terminate()
        STATE.push_log("WARNING", "⛔ Người dùng yêu cầu dừng scraper.")

    return jsonify({"ok": True, "message": "Đã gửi lệnh dừng."})


@app.route("/api/logs/history")
def api_logs_history():
    return jsonify(list(STATE.log_history))


@app.route("/api/logs/stream")
def api_logs_stream():
    """Server-Sent Events — đẩy log real-time về client."""
    def event_stream():
        # Gửi backlog trước
        for entry in list(STATE.log_history)[-50:]:
            yield f"data: {json.dumps(entry, ensure_ascii=False)}\n\n"

        while True:
            try:
                entry = STATE.log_queue.get(timeout=1.0)
                yield f"data: {json.dumps(entry, ensure_ascii=False)}\n\n"
            except queue.Empty:
                # Heartbeat mỗi giây để giữ kết nối SSE
                yield ": heartbeat\n\n"

    return Response(
        event_stream(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control":               "no-cache",
            "X-Accel-Buffering":           "no",
            "Access-Control-Allow-Origin": "*",
        },
    )


# ──────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 55)
    print("  🤖 ZALO SCRAPER DASHBOARD")
    print("  📍 http://localhost:5000")
    print("=" * 55)
    app.run(debug=False, port=5000, threaded=True)
