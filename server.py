"""
Flask Backend — Zalo Scraper Dashboard v2
Thêm: SQLite local storage, Review/Filter API, Sync to AgentSee CRM
"""

from flask import Flask, jsonify, request, Response, send_from_directory
from flask_cors import CORS
import threading
import queue
import time
import json
import os
import re
import sqlite3
import subprocess
import sys
import logging
import requests as http_requests
from datetime import datetime
from collections import deque
from contextlib import contextmanager

# ──────────────────────────────────────────────
app = Flask(__name__, static_folder="static", template_folder="templates")
CORS(app)

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "zalocrawl.db")

# ──────────────────────────────────────────────
#  DATABASE — SQLite
# ──────────────────────────────────────────────

def init_db():
    """Tạo bảng SQLite nếu chưa có."""
    with _get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                customer    TEXT    NOT NULL,
                logs        TEXT    NOT NULL DEFAULT '[]',
                msg_count   INTEGER DEFAULT 0,
                scraped_at  TEXT,
                status      TEXT    DEFAULT 'pending',
                synced      INTEGER DEFAULT 0,
                synced_at   TEXT,
                zalo_uid    TEXT    DEFAULT NULL,
                zalo_name   TEXT    DEFAULT NULL
            )
        """)
        # Migration: thêm cột nếu đang dùng DB cũ chưa có zalo_uid
        try:
            conn.execute("ALTER TABLE conversations ADD COLUMN zalo_uid  TEXT DEFAULT NULL")
        except Exception:
            pass  # cột đã tồn tại
        try:
            conn.execute("ALTER TABLE conversations ADD COLUMN bot_msgs       TEXT DEFAULT '[]'")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE conversations ADD COLUMN account_senders TEXT DEFAULT '[]'")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE conversations ADD COLUMN user_msg_count  INTEGER DEFAULT 0")
        except Exception:
            pass
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sync_config (
                key   TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        # Mặc định sync config
        conn.execute("""
            INSERT OR IGNORE INTO sync_config (key, value) VALUES
                ('agentsee_url',    'http://your-agentsee-server/api/import-chat'),
                ('agentsee_secret', ''),
                ('agentsee_method', 'POST')
        """)


@contextmanager
def _get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def db_save_conversation(customer: str, logs: list, account_senders: list = None) -> int:
    """
    Lưu conversation vào DB.
    logs: [{sender, message, role}] — tự tách USER vs BOT.
    """
    user_logs = [m for m in logs if m.get("role") == "USER"]
    bot_logs  = [m for m in logs if m.get("role") == "BOT"]
    with _get_db() as conn:
        cur = conn.execute(
            """INSERT INTO conversations
               (customer, logs, bot_msgs, account_senders,
                msg_count, user_msg_count, scraped_at, status, synced)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', 0)""",
            (
                customer,
                json.dumps(logs, ensure_ascii=False),
                json.dumps(bot_logs, ensure_ascii=False),
                json.dumps(account_senders or [], ensure_ascii=False),
                len(logs),
                len(user_logs),
                datetime.now().isoformat(),
            )
        )
        return cur.lastrowid


def db_get_conversations(status: str = "all", limit: int = 200) -> list:
    with _get_db() as conn:
        if status == "all":
            rows = conn.execute(
                "SELECT * FROM conversations ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM conversations WHERE status=? ORDER BY id DESC LIMIT ?",
                (status, limit)
            ).fetchall()
    return [dict(r) for r in rows]


def db_set_status(conv_id: int, status: str):
    with _get_db() as conn:
        conn.execute(
            "UPDATE conversations SET status=? WHERE id=?", (status, conv_id)
        )


def db_delete(conv_id: int):
    with _get_db() as conn:
        conn.execute("DELETE FROM conversations WHERE id=?", (conv_id,))


def db_get_sync_config() -> dict:
    with _get_db() as conn:
        rows = conn.execute("SELECT key, value FROM sync_config").fetchall()
    return {r["key"]: r["value"] for r in rows}


def db_set_sync_config(cfg: dict):
    with _get_db() as conn:
        for k, v in cfg.items():
            conn.execute(
                "INSERT OR REPLACE INTO sync_config (key, value) VALUES (?,?)", (k, v)
            )


# ──────────────────────────────────────────────
#  TRẠNG THÁI TOÀN CỤC
# ──────────────────────────────────────────────
class ScraperState:
    def __init__(self):
        self.is_running    = False
        self.process       = None
        self.thread        = None
        self.start_time    = None
        self.total_scraped = 0
        self.total_pushed  = 0
        self.total_failed  = 0
        self.current_name  = ""
        self.scraped_list  = []
        self.log_queue     = queue.Queue()
        self.log_history   = deque(maxlen=500)

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
            "level":   level,
            "message": message,
        }
        self.log_history.append(entry)
        self.log_queue.put(entry)

    def elapsed(self) -> str:
        if not self.start_time: return "—"
        delta = datetime.now() - datetime.fromisoformat(self.start_time)
        h, rem = divmod(int(delta.total_seconds()), 3600)
        m, s   = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"


STATE = ScraperState()

# ──────────────────────────────────────────────
#  SCRAPER SUBPROCESS
# ──────────────────────────────────────────────
_pending: dict = {"name": "", "logs": 0, "log_data": []}


def _parse_log_line(line: str):
    global _pending

    if "tiến độ" in line.lower() or "đã cào" in line.lower():
        m = re.search(r"(\d+)/\d+ người", line)
        if m:
            STATE.total_scraped = int(m.group(1))

    for pat in [r"Contact: '(.+?)'", r"Click vào: '(.+?)'"]:
        m = re.search(pat, line)
        if m:
            name = m.group(1).strip()
            _pending = {"name": name, "logs": 0, "log_data": []}
            STATE.current_name = name
            break

    # Parse "Parse được N tin nhắn — Khách: 'Tên' | BOT: Bé Mầm"
    m = re.search(r"Parse được (\d+) tin nhắn.*Khách: '(.+?)'", line)
    if m:
        _pending["logs"] = int(m.group(1))
        _pending["name"] = m.group(2).strip()
        STATE.current_name = _pending["name"]
    else:
        m2 = re.search(r"Parse được (\d+) tin nhắn", line)
        if m2:
            _pending["logs"] = int(m2.group(1))

    # Parse account_senders từ "| BOT: Bé Mầm, Tinni Store"
    m = re.search(r"\| BOT: (.+)$", line)
    if m and "(Bạn)" not in m.group(1):
        _pending["account_senders"] = [s.strip() for s in m.group(1).split(",") if s.strip()]

    # Khi lưu local thành công → scraper log "[SAVE] ✅ Đã lưu local!"
    # Lúc này api_ingest đã tự lưu DB + cập nhật STATE → không cần làm gì thêm ở đây.
    # Chỉ cần track để hiển thị log.

    if "[SAVE] ❌" in line:
        STATE.total_failed += 1
        name = _pending["name"] or STATE.current_name or "Unknown"
        STATE.scraped_list.append({
            "name":   name,
            "logs":   _pending["logs"],
            "status": "error",
            "time":   datetime.now().strftime("%H:%M:%S"),
        })
        _pending = {"name": "", "logs": 0, "log_data": [], "account_senders": []}

    # Backward compat: nếu còn log cũ dùng [PUSH]
    if "✅ Thành công" in line and "[PUSH]" in line:
        # Chỉ update counter nếu api_ingest chưa làm (lần chạy cũ)
        pass  # api_ingest đã xử lý

    if "❌" in line and "[PUSH]" in line:
        STATE.total_failed += 1
        _pending = {"name": "", "logs": 0, "log_data": [], "account_senders": []}





def _run_scraper_process(config: dict):
    limit   = config.get("limit", 100)
    api_url = config.get("apiEndpoint", "http://localhost:3000/api/crm/import-chats")
    secret  = config.get("apiSecret",   "antigravity_secret_2026")

    STATE.reset_stats()
    STATE.is_running = True
    STATE.push_log("INFO", f"🚀 Bắt đầu scraper — limit={limit}")
    STATE.push_log("INFO", f"📡 API: {api_url}")

    _write_runtime_config(limit, api_url, secret)

    try:
        sub_env = {
            **os.environ,
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8":       "1",
            "SCRAPER_LIMIT":    str(limit),
            "SCRAPER_API_URL":  api_url,
            "SCRAPER_SECRET":   secret,
        }
        proc = subprocess.Popen(
            [sys.executable, "-u", "zalo_scraper.py"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=False, bufsize=0,
            cwd=os.path.dirname(os.path.abspath(__file__)),
            env=sub_env,
        )
        STATE.process = proc

        for raw_line in proc.stdout:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            _parse_log_line(line)
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
        STATE.is_running = False
        STATE.process    = None
        STATE.push_log("SUCCESS", "✅ Scraper đã dừng.")


def _write_runtime_config(limit, api_url, secret):
    cfg = {"limit": limit, "api_endpoint": api_url, "api_secret": secret}
    with open("runtime_config.json", "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ──────────────────────────────────────────────
#  REST API ENDPOINTS — Dashboard
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
        "scrapedList":  STATE.scraped_list[-50:],
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


@app.route("/api/start-by-list", methods=["POST"])
def api_start_by_list():
    """
    Khởi động scraper theo danh sách tên khách hàng (từ ZCA-js hoặc nhập tay).
    Payload: { names: ["Nguyễn Thị A", "Trần Văn B", ...] }
    """
    if STATE.is_running:
        return jsonify({"ok": False, "error": "Scraper đang chạy rồi!"}), 400

    body  = request.get_json(silent=True) or {}
    names = body.get("names", [])

    # Chuẩn hóa: loại bỏ dòng trống, strip whitespace
    names = [n.strip() for n in names if isinstance(n, str) and n.strip()]

    if not names:
        return jsonify({"ok": False, "error": "Danh sách tên trống"}), 400

    STATE.log_history.clear()
    # Lưu danh sách vào file tạm để subprocess đọc
    name_list_file = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "scraper_name_list.json"
    )
    with open(name_list_file, "w", encoding="utf-8") as f:
        json.dump(names, f, ensure_ascii=False, indent=2)

    t = threading.Thread(
        target=_run_scraper_by_list,
        args=(names,),
        daemon=True
    )
    STATE.thread = t
    t.start()
    return jsonify({
        "ok": True,
        "message": f"Đang cào {len(names)} người theo danh sách.",
        "count": len(names),
        "names": names,
    })


def _run_scraper_by_list(names: list):
    """Chạy zalo_scraper.py ở mode danh sách trong subprocess."""
    STATE.reset_stats()
    STATE.is_running = True
    STATE.push_log("INFO", f"📋 Mode DANH SÁCH — {len(names)} người")
    for i, n in enumerate(names[:10], 1):
        STATE.push_log("INFO", f"  {i}. {n}")
    if len(names) > 10:
        STATE.push_log("INFO", f"  ... và {len(names)-10} người khác")

    name_list_file = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "scraper_name_list.json"
    )
    try:
        sub_env = {
            **os.environ,
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8":       "1",
            "SCRAPER_MODE":     "list",
            "SCRAPER_LIST_FILE": name_list_file,
        }
        proc = subprocess.Popen(
            [sys.executable, "-u", "zalo_scraper.py"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=False, bufsize=0,
            cwd=os.path.dirname(os.path.abspath(__file__)),
            env=sub_env,
        )
        STATE.process = proc

        for raw_line in proc.stdout:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            _parse_log_line(line)
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

    except Exception as e:
        STATE.push_log("ERROR", f"❌ Lỗi: {e}")
    finally:
        STATE.is_running = False
        STATE.process    = None
        STATE.push_log("SUCCESS", "✅ Scraper đã dừng.")


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
    def event_stream():
        for entry in list(STATE.log_history)[-50:]:
            yield f"data: {json.dumps(entry, ensure_ascii=False)}\n\n"
        while True:
            try:
                entry = STATE.log_queue.get(timeout=1.0)
                yield f"data: {json.dumps(entry, ensure_ascii=False)}\n\n"
            except queue.Empty:
                yield ": heartbeat\n\n"

    return Response(
        event_stream(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                 "Access-Control-Allow-Origin": "*"},
    )


# ──────────────────────────────────────────────
#  REST API ENDPOINTS — Conversations (SQLite)
# ──────────────────────────────────────────────

# ──────────────────────────────────────────────
#  INGEST — Scraper → Local SQLite (không push CRM)
# ──────────────────────────────────────────────

@app.route("/api/conversations/ingest", methods=["POST"])
def api_ingest():
    """
    Endpoint nội bộ — nhận data từ zalo_scraper.py và lưu vào SQLite.
    KHÔNG gửi đi đâu cả. Người dùng review trong dashboard rồi mới Sync.

    Payload: {
      secret, customerName, logs: [{sender, message, role}],
      accountSenders: [...]
    }
    """
    body = request.get_json(silent=True) or {}

    # Basic auth bằng secret
    if body.get("secret") != "antigravity_secret_2026":
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    customer      = (body.get("customerName") or "").strip()
    logs          = body.get("logs", [])
    acc_senders   = body.get("accountSenders", [])

    if not customer:
        return jsonify({"ok": False, "error": "customerName trống"}), 400
    if not logs:
        return jsonify({"ok": False, "error": "logs trống"}), 400

    try:
        conv_id = db_save_conversation(customer, logs, acc_senders)
        user_cnt = sum(1 for m in logs if m.get("role") == "USER")
        bot_cnt  = len(logs) - user_cnt
        STATE.push_log("SUCCESS",
            f"[DB] Đã lưu '{customer}' — {len(logs)} msgs (khách: {user_cnt}, shop: {bot_cnt})")
        STATE.total_pushed += 1
        STATE.scraped_list.append({
            "name":   customer,
            "logs":   len(logs),
            "status": "ok",
            "time":   datetime.now().strftime("%H:%M:%S"),
        })
        return jsonify({
            "ok":        True,
            "id":        conv_id,
            "customer":  customer,
            "msgCount":  len(logs),
            "userMsgs":  user_cnt,
            "botMsgs":   bot_cnt,
            "status":    "pending",   # luôn pending, chờ review
        })
    except Exception as e:
        STATE.push_log("ERROR", f"[DB] Lỗi lưu '{customer}': {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/conversations")
def api_conversations():
    status = request.args.get("status", "all")
    rows = db_get_conversations(status=status, limit=300)
    # Parse logs JSON string → list
    for r in rows:
        try:
            r["logs_parsed"] = json.loads(r["logs"])
        except Exception:
            r["logs_parsed"] = []
    return jsonify({"ok": True, "data": rows, "total": len(rows)})


@app.route("/api/conversations/<int:conv_id>/status", methods=["PUT"])
def api_set_status(conv_id):
    body   = request.get_json(silent=True) or {}
    status = body.get("status", "")
    if status not in ("pending", "approved", "rejected"):
        return jsonify({"ok": False, "error": "Status không hợp lệ"}), 400
    db_set_status(conv_id, status)
    return jsonify({"ok": True, "id": conv_id, "status": status})


@app.route("/api/conversations/<int:conv_id>", methods=["DELETE"])
def api_delete_conversation(conv_id):
    db_delete(conv_id)
    return jsonify({"ok": True, "deleted": conv_id})


# ──────────────────────────────────────────────
#  SYNC CONFIG
# ──────────────────────────────────────────────

@app.route("/api/sync/config", methods=["GET"])
def api_get_sync_config():
    return jsonify({"ok": True, "config": db_get_sync_config()})


@app.route("/api/sync/config", methods=["POST"])
def api_set_sync_config():
    cfg = request.get_json(silent=True) or {}
    allowed = {"agentsee_url", "agentsee_secret", "agentsee_method"}
    filtered = {k: v for k, v in cfg.items() if k in allowed}
    db_set_sync_config(filtered)
    return jsonify({"ok": True, "saved": filtered})


# ──────────────────────────────────────────────
#  SYNC TO AGENTSEE CRM
# ──────────────────────────────────────────────

@app.route("/api/sync", methods=["POST"])
def api_sync():
    """
    Đẩy các conversation approved + chưa sync lên AgentSee CRM.
    Gọi: POST /api/sync
    Body (optional): { "ids": [1, 2, 3] }  — sync ID cụ thể
    """
    cfg = db_get_sync_config()
    url    = cfg.get("agentsee_url", "").strip()
    secret = cfg.get("agentsee_secret", "").strip()

    if not url or url == "http://your-agentsee-server/api/import-chat":
        return jsonify({
            "ok": False,
            "error": "Chưa cấu hình AgentSee URL. Vào tab Duyệt → Cài đặt Sync."
        }), 400

    body = request.get_json(silent=True) or {}
    specific_ids = body.get("ids", [])

    # Lấy conversations cần sync
    with _get_db() as conn:
        if specific_ids:
            placeholders = ",".join("?" * len(specific_ids))
            rows = conn.execute(
                f"SELECT * FROM conversations WHERE id IN ({placeholders}) AND synced=0",
                specific_ids
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM conversations WHERE status='approved' AND synced=0"
            ).fetchall()

    if not rows:
        return jsonify({"ok": True, "synced": 0, "message": "Không có conversation nào cần sync."})

    synced_ok  = []
    synced_err = []

    for row in rows:
        row = dict(row)
        try:
            logs = json.loads(row["logs"])
        except Exception:
            logs = []

        payload = {
            "secret":       secret,
            "customerName": row["customer"],
            "logs":         logs,
            "source":       "zalocrawl",
            "scrapedAt":    row["scraped_at"],
        }

        try:
            resp = http_requests.post(url, json=payload, timeout=15,
                                      headers={"Content-Type": "application/json"})
            resp.raise_for_status()
            # Đánh dấu đã sync
            with _get_db() as conn:
                conn.execute(
                    "UPDATE conversations SET synced=1, synced_at=? WHERE id=?",
                    (datetime.now().isoformat(), row["id"])
                )
            synced_ok.append(row["id"])
            STATE.push_log("SUCCESS",
                f"[SYNC] ✅ '{row['customer']}' → AgentSee OK")
        except Exception as e:
            synced_err.append({"id": row["id"], "error": str(e)})
            STATE.push_log("ERROR",
                f"[SYNC] ❌ '{row['customer']}' lỗi: {e}")

    return jsonify({
        "ok":        True,
        "synced":    len(synced_ok),
        "failed":    len(synced_err),
        "syncedIds": synced_ok,
        "errors":    synced_err,
    })



# ──────────────────────────────────────────────
#  ZCA-JS INTEGRATION ENDPOINTS
# ──────────────────────────────────────────────

@app.route("/api/zca/import-friends", methods=["POST"])
def api_zca_import_friends():
    """
    Nhận friend list từ zca-js, match theo tên với conversations trong DB.
    zca-js gọi:
      const friends = await zca.getFriendList();
      fetch('http://localhost:5000/api/zca/import-friends', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ friends: friends.data })
      })
    Friend object mẫu: { userId, zaloName, alias, ... }
    """
    body = request.get_json(silent=True) or {}
    friends: list = body.get("friends", [])

    if not friends:
        return jsonify({"ok": False, "error": "friends list trống"}), 400

    # Build lookup: cạnh thường hóa tên để match mềm
    import unicodedata
    def norm(s: str) -> str:
        s = (s or "").lower().strip()
        # Bỏ dấu tiếng Việt cho fuzzy match
        nfkd = unicodedata.normalize('NFKD', s)
        return "".join(c for c in nfkd if not unicodedata.combining(c))

    friend_map = {}  # norm_name -> {userId, zaloName}
    for f in friends:
        uid  = str(f.get("userId", "") or f.get("uid", "") or f.get("id", ""))
        name = f.get("zaloName", "") or f.get("name", "") or f.get("alias", "")
        if uid and name:
            friend_map[norm(name)] = {"userId": uid, "zaloName": name}

    # Match với tất cả conversations chưa có zalo_uid
    matched = []
    unmatched = []

    with _get_db() as conn:
        rows = conn.execute(
            "SELECT id, customer FROM conversations WHERE zalo_uid IS NULL"
        ).fetchall()

        for row in rows:
            key = norm(row["customer"])
            if key in friend_map:
                f_info = friend_map[key]
                conn.execute(
                    "UPDATE conversations SET zalo_uid=?, zalo_name=? WHERE id=?",
                    (f_info["userId"], f_info["zaloName"], row["id"])
                )
                matched.append({
                    "id": row["id"],
                    "customer": row["customer"],
                    "userId": f_info["userId"],
                })
            else:
                unmatched.append(row["customer"])

    STATE.push_log("SUCCESS",
        f"[ZCA] Import {len(friends)} friends → matched {len(matched)}/{len(rows)} contacts")

    return jsonify({
        "ok":        True,
        "total_friends": len(friends),
        "matched":   len(matched),
        "unmatched": len(unmatched),
        "matchedContacts": matched,
        "unmatchedNames":  unmatched[:20],  # giới hạn để tránh payload to
    })


@app.route("/api/zca/update-uid", methods=["POST"])
def api_zca_update_uid():
    """
    Cập nhật zalo_uid thủ công cho 1 contact:
    POST { id: 123, zalo_uid: "123456789", zalo_name: "Nguyễn Văn A" }
    """
    body = request.get_json(silent=True) or {}
    conv_id  = body.get("id")
    zalo_uid = str(body.get("zalo_uid", "")).strip()
    zalo_name = body.get("zalo_name", "").strip()
    if not conv_id or not zalo_uid:
        return jsonify({"ok": False, "error": "Thiếu id hoặc zalo_uid"}), 400

    with _get_db() as conn:
        conn.execute(
            "UPDATE conversations SET zalo_uid=?, zalo_name=? WHERE id=?",
            (zalo_uid, zalo_name or None, conv_id)
        )
    return jsonify({"ok": True, "id": conv_id, "zalo_uid": zalo_uid})


@app.route("/api/zca/remarketing-list")
def api_zca_remarketing_list():
    """
    Trả về danh sách contacts đã approve + có zalo_uid để zca-js gửi tin.

    zca-js gọi:
      const res  = await fetch('http://localhost:5000/api/zca/remarketing-list');
      const data = await res.json();
      for (const c of data.contacts) {
        await zca.sendMessage({ msg: data.template.replace('{name}', c.name) },
                               Number(c.userId), ThreadType.User);
      }
    """
    with _get_db() as conn:
        rows = conn.execute("""
            SELECT id, customer, zalo_uid, zalo_name, msg_count, scraped_at
            FROM conversations
            WHERE status='approved' AND zalo_uid IS NOT NULL AND zalo_uid != ''
            ORDER BY id DESC
        """).fetchall()

    contacts = [{
        "id":        r["id"],
        "name":      r["zalo_name"] or r["customer"],
        "userId":    r["zalo_uid"],
        "msgCount":  r["msg_count"],
        "scrapedAt": r["scraped_at"],
    } for r in rows]

    return jsonify({
        "ok":       True,
        "total":    len(contacts),
        "contacts": contacts,
        # Template remarketing mẫu — zca-js thay {name} bằng tên thực
        "template": "Chào {name}, cảm ơn bạn đã nhắn tin với chúng mình! 😊",
    })


@app.route("/api/zca/stats")
def api_zca_stats():
    """Thống kê nhanh trạng thái ZCA matching."""
    with _get_db() as conn:
        total    = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        matched  = conn.execute("SELECT COUNT(*) FROM conversations WHERE zalo_uid IS NOT NULL").fetchone()[0]
        approved = conn.execute("SELECT COUNT(*) FROM conversations WHERE status='approved' AND zalo_uid IS NOT NULL").fetchone()[0]
        ready    = approved  # sẵn sàng gửi remarketing

    return jsonify({
        "ok": True,
        "total": total, "matched": matched,
        "approved": approved, "readyToSend": ready,
    })


# ──────────────────────────────────────────────
if __name__ == "__main__":
    init_db()
    print("=" * 55)
    print("  🤖 ZALO SCRAPER DASHBOARD v2")
    print("  📍 http://localhost:5000")
    print(f"  🗄  Database: {DB_PATH}")
    print("=" * 55)
    app.run(debug=False, port=5000, threaded=True)
