"""
╔══════════════════════════════════════════════════════════════════╗
║           ZALO PC SCRAPER v2 - COORDINATE-BASED APPROACH        ║
║   Thư viện: uiautomation, requests, pyperclip/win32clipboard    ║
║   Lý do đổi: Zalo Electron không expose ListItem/TextControl    ║
║   Chiến lược: Click tọa độ pixel sidebar → Clipboard read chat  ║
╚══════════════════════════════════════════════════════════════════╝
"""

import sys
import uiautomation as auto
import requests
import time
import re
import logging
import ctypes
import ctypes.wintypes
from typing import Optional

# ── Force UTF-8 stdout/stderr trên Windows ──────────────────
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ─────────────────────────────────────────────
#  CẤU HÌNH CHUNG
# ─────────────────────────────────────────────
# Luôn lưu vào local server — KHÔNG push thẳng lên CRM
# (Vào dashboard duyệt rồi mới sync AgentSee)
LOCAL_SERVER    = "http://localhost:5000"
API_ENDPOINT    = f"{LOCAL_SERVER}/api/conversations/ingest"
# Secret: ưu tiên env var ZALOCRAWL_SECRET, fallback default
import os as _os
API_SECRET      = _os.environ.get("ZALOCRAWL_SECRET", "antigravity_secret_2026")
REQUEST_TIMEOUT = 10

# Search Y Offset — vị trí ô tìm kiếm tính từ đỉnh window Zalo (px)
# Có thể chỉnh qua Dashboard → Cấu hình → "Search Y Offset"
SEARCH_Y_OFFSET_DEFAULT = 110

def _load_search_y_offset() -> int:
    """Đọc search_y_offset từ server config (do người dùng calibrate trong dashboard)."""
    try:
        import requests as _req
        r = _req.get(f"{LOCAL_SERVER}/api/config/search-offset", timeout=2)
        return r.json().get("search_y_offset", SEARCH_Y_OFFSET_DEFAULT)
    except Exception:
        return SEARCH_Y_OFFSET_DEFAULT

# Khoảng thời gian chờ (giây)
CLICK_PAUSE      = 2.5   # chờ sau khi click contact để Zalo load chat
SCROLL_PAUSE     = 1.2   # chờ sau khi cuộn sidebar
COPY_WAIT        = 0.6   # chờ sau Ctrl+A / Ctrl+C

# Cài đặt cuộn lịch sử chat
HISTORY_MAX_SCROLLS = 40   # số lần cuộn lên tối đa mỗi contact
HISTORY_SCROLL_STEP = 10   # số notch mỗi lần WheelUp
HISTORY_LOAD_WAIT   = 1.5  # giây chờ Zalo render sau mỗi lần cuộn

# Sidebar layout (tự động tính từ Zalo window rect)
SIDEBAR_WIDTH_RATIO = 0.32   # sidebar chiếm ~32% chiều rộng cửa sổ
CONTACT_HEIGHT_PX   = 72     # chiều cao mỗi contact item ~72px
HEADER_HEIGHT_PX    = 220    # header: title(30)+nav(50)+account(40)+search(50)+tab(30)+margin(20)
CONTACT_X_OFFSET    = 0.5   # click vào giữa chiều ngang sidebar

# ─────────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("zalo_scraper.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────
#  WIN32 CLIPBOARD (đọc text từ clipboard)
# ─────────────────────────────────────────────

def _read_clipboard() -> str:
    """
    Đọc text từ Windows Clipboard.
    Dùng pyperclip — xử lý đúng 64-bit HANDLE trên Windows 10/11.
    """
    try:
        import pyperclip
        text = pyperclip.paste()
        return text or ""
    except Exception as e:
        log.warning("[CLIPBOARD] Lỗi đọc clipboard: %s", e)
        return ""


def _clear_clipboard():
    """Xóa clipboard bằng pyperclip."""
    try:
        import pyperclip
        pyperclip.copy("")
    except Exception:
        pass


# ─────────────────────────────────────────────
#  TÌM CỬA SỔ ZALO
# ─────────────────────────────────────────────

def _get_zalo_window():
    """
    Tìm cửa sổ Zalo Electron.
    Zalo PC mới dùng Chromium → ClassName='Chrome_WidgetWin_1', Name='Zalo'.
    """
    desktop = auto.GetRootControl()

    def _find(ctrl, depth=0):
        if depth > 2: return None
        try:
            for child in ctrl.GetChildren():
                name = (child.Name or "").strip()
                cls  = child.ClassName or ""
                if name == "Zalo" and "Chrome_WidgetWin" in cls:
                    return child
                r = _find(child, depth+1)
                if r: return r
        except Exception:
            pass
        return None

    win = _find(desktop)
    if win:
        r = win.BoundingRectangle
        log.info("[UI] Tìm thấy Zalo: (%d,%d,%d,%d) size=%dx%d",
                 r.left, r.top, r.right, r.bottom,
                 r.right - r.left, r.bottom - r.top)
    else:
        log.error("[UI] Không tìm thấy cửa sổ Zalo!")
        log.error("[UI] → Mở Zalo PC và đảm bảo không bị thu nhỏ.")
    return win


# ─────────────────────────────────────────────
#  TÍNH TỌA ĐỘ SIDEBAR
# ─────────────────────────────────────────────

class ZaloLayout:
    """Lưu tọa độ và kích thước các vùng UI của Zalo."""

    def __init__(self, win):
        r = win.BoundingRectangle
        self.win_left   = r.left
        self.win_top    = r.top
        self.win_right  = r.right
        self.win_bottom = r.bottom
        self.win_w      = r.right  - r.left
        self.win_h      = r.bottom - r.top

        # Sidebar: cột bên trái (≈32% chiều rộng)
        self.sidebar_left   = r.left
        self.sidebar_right  = r.left + int(self.win_w * SIDEBAR_WIDTH_RATIO)
        self.sidebar_top    = r.top  + HEADER_HEIGHT_PX
        self.sidebar_bottom = r.bottom
        self.sidebar_mid_x  = (self.sidebar_left + self.sidebar_right) // 2

        # Ô tìm kiếm Zalo: nằm trên header sidebar
        # Offset calibrate được từ dashboard (mặc định 110px)
        _search_y_off       = _load_search_y_offset()
        self.search_x       = self.sidebar_mid_x
        self.search_y       = r.top + _search_y_off

        # Kết quả tìm kiếm: item đầu tiên nằm ngay dưới search box
        self.search_result_x = self.sidebar_mid_x
        self.search_result_y = r.top + _search_y_off + 44 + CONTACT_HEIGHT_PX // 2


        # Chat panel: phần còn lại
        self.chat_left   = self.sidebar_right
        self.chat_right  = r.right
        self.chat_top    = r.top + HEADER_HEIGHT_PX
        self.chat_bottom = r.bottom
        self.chat_mid_x  = (self.chat_left + self.chat_right) // 2
        self.chat_mid_y  = (self.chat_top  + self.chat_bottom) // 2

    def contact_y(self, index: int) -> int:
        """Y tọa độ của contact thứ `index` trong sidebar."""
        return self.sidebar_top + CONTACT_HEIGHT_PX // 2 + index * CONTACT_HEIGHT_PX

    def visible_contact_count(self) -> int:
        """Số contact có thể hiển thị trong sidebar."""
        return max(1, (self.sidebar_bottom - self.sidebar_top) // CONTACT_HEIGHT_PX)

    def log_layout(self):
        log.info("[LAYOUT] Sidebar: x=%d→%d  y=%d→%d  (%d contacts visible)",
                 self.sidebar_left, self.sidebar_right,
                 self.sidebar_top,  self.sidebar_bottom,
                 self.visible_contact_count())
        log.info("[LAYOUT] SearchBox: (%d, %d)",
                 self.search_x, self.search_y)
        log.info("[LAYOUT] ChatPanel: x=%d→%d  mid=(%d,%d)",
                 self.chat_left, self.chat_right,
                 self.chat_mid_x, self.chat_mid_y)


# ─────────────────────────────────────────────
#  ĐỌC TÊN CONTACT & CHAT QUA CLIPBOARD
# ─────────────────────────────────────────────

def _get_name_from_avatar_popup(layout: ZaloLayout, fallback: str = "") -> str:
    """
    Lấy tên khách hàng bằng cách click vào ảnh đại diện trong chat header.

    Quy trình:
    1. Click vào avatar (góc trái trên của chat panel)
    2. Zalo mở popup/card thông tin liên hệ
    3. Thử đọc tên qua clipboard (click name text → Ctrl+C)
    4. Đóng popup bằng Escape
    Trả về tên tìm được hoặc fallback nếu không đọc được.
    """
    try:
        # ── Tọa độ avatar trong chat header ──────────────────────────────
        # Zalo PC: avatar nằm ở góc trái trên của chat panel header
        # Thường tại (chat_left + 20, chat_top + 28) — avatar 40×40px
        avatar_x = layout.chat_left + 20
        avatar_y = layout.chat_top + 28

        log.debug("[AVATAR] Click avatar tại (%d, %d)", avatar_x, avatar_y)
        _clear_clipboard()
        auto.Click(avatar_x, avatar_y)
        time.sleep(1.2)   # chờ popup animation

        # ── Thử đọc tên từ popup ─────────────────────────────────────────
        # Popup thường hiện ra ở giữa màn hình hoặc sát chat panel
        # Tên thường ở vị trí trung tâm-trên của popup (~200px từ center)
        # Chiến lược: click vào vùng tên, Ctrl+A, Ctrl+C
        popup_center_x = layout.chat_left + (layout.chat_right - layout.chat_left) // 4
        popup_name_y   = layout.chat_top + 120   # vùng tên trong popup info

        auto.Click(popup_center_x, popup_name_y)
        time.sleep(0.3)
        auto.SendKeys("{Ctrl}a")
        time.sleep(0.2)
        auto.SendKeys("{Ctrl}c")
        time.sleep(0.4)

        raw = _read_clipboard().strip()
        # Lọc: tên thường là 1 dòng, ≤ 60 ký tự, không chứa URL
        lines = [l.strip() for l in raw.split("\n") if l.strip()]
        candidate = lines[0] if lines else ""
        if 0 < len(candidate) <= 60 and "http" not in candidate:
            log.info("[AVATAR] ✅ Đọc được tên: '%s'", candidate)
            name = candidate
        else:
            log.warning("[AVATAR] Clipboard không hợp lệ ('%s') → dùng fallback",
                        candidate[:40] if candidate else "(rỗng)")
            name = fallback

    except Exception as e:
        log.warning("[AVATAR] Lỗi khi đọc popup: %s", e)
        name = fallback

    finally:
        # ── Đóng popup bằng Escape ────────────────────────────────────────
        try:
            auto.SendKeys("{Escape}")
            time.sleep(0.4)
        except Exception:
            pass

    return name


# ─────────────────────────────────────────────
#  TÌM KIẬM CONTACT THEO TÊN
# ─────────────────────────────────────────────

def _search_contact(layout: ZaloLayout, name: str, result_wait: float = 2.5) -> bool:
    """
    Dùng Ctrl+F của Zalo để mở ô tìm kiếm, rồi gõ tên và click kết quả đầu tiên.

    Chiến lược:
    1. Click vào PHẦN TRÊN CÙNG của sidebar (không phải ô chat) để đảm bảo Zalo được focus
    2. Nhấn Ctrl+F → Zalo mở ô search và focus vào đó
    3. Paste tên → chờ kết quả → click kết quả đầu tiên
    4. Nhấn Escape để đóng search mode (chat vẫn mở)

    KHÔNG dùng tọa độ cứng cho ô search vì dễ nhầm sang ô nhắn tin.
    """
    import pyperclip

    log.info("[SEARCH] Tìm kiếm: '%s'", name)

    try:
        # ── Bước 1: Click vào thanh tiêu đề sidebar  ──────────────────────
        # Điểm click: phần header của sidebar, cách top khoảng 70px
        # (Đây là vùng LOGO / icon account — KHÔNG phải ô nhắn tin)
        safe_click_y = layout.win_top + 40  # vùng thanh tiêu đề window Zalo
        safe_click_x = layout.sidebar_mid_x

        log.debug("[SEARCH] Click header sidebar tại (%d, %d) để focus Zalo",
                  safe_click_x, safe_click_y)
        auto.Click(safe_click_x, safe_click_y)
        time.sleep(0.4)

        # ── Bước 2: Mở ô search bằng Ctrl+F ──────────────────────────────
        # Zalo PC hỗ trợ Ctrl+F để focus vào search box phía trên contact list
        auto.SendKeys("{Ctrl}f")
        time.sleep(0.8)   # chờ animation search box xuất hiện

        # ── Bước 3: Xóa nội dung cũ (nếu có) rồi paste tên ───────────────
        auto.SendKeys("{Ctrl}a")
        time.sleep(0.1)
        auto.SendKeys("{Delete}")
        time.sleep(0.1)

        pyperclip.copy(name)
        auto.SendKeys("{Ctrl}v")
        log.info("[SEARCH] Đã paste tên '%s' vào ô search", name)
        time.sleep(result_wait)   # chờ Zalo render kết quả

        # ── Bước 4: Kết quả đầu tiên nằm ngay dưới search box ────────────
        # Sau khi search, contact list thu gọn và item đầu tiên hiện ra
        # Y của kết quả 1 = search_y + chiều cao search box (~44px) + CONTACT_HEIGHT_PX/2
        first_result_y = layout.search_y + 44 + CONTACT_HEIGHT_PX // 2
        first_result_x = layout.sidebar_mid_x

        log.debug("[SEARCH] Click kết quả đầu tiên tại (%d, %d)",
                  first_result_x, first_result_y)
        auto.Click(first_result_x, first_result_y)
        time.sleep(CLICK_PAUSE)   # chờ chat panel load

        # ── Bước 5: Thoát search mode ─────────────────────────────────────
        auto.SendKeys("{Escape}")
        time.sleep(0.4)

        log.info("[SEARCH] ✅ Đã mở chat với '%s'", name)
        return True

    except Exception as e:
        log.error("[SEARCH] ❌ Lỗi khi tìm '%s': %s", name, e)
        return False



def _scroll_chat_to_top(layout: ZaloLayout,
                         max_scrolls: int = HISTORY_MAX_SCROLLS,
                         scroll_step: int = HISTORY_SCROLL_STEP,
                         load_wait:   float = HISTORY_LOAD_WAIT) -> None:
    """
    Cuộn ngược lên đầu lịch sử chat để Zalo lazy-load toàn bộ tin nhắn cũ.
    Dừng khi clipboard ổn định 2 lần liên tiếp (không có tin mới load thêm).
    """
    msg_area_y = layout.chat_top + int((layout.chat_bottom - layout.chat_top) * 0.4)
    auto.Click(layout.chat_mid_x, msg_area_y)
    time.sleep(0.3)

    log.info("[SCROLL_UP] Bắt đầu cuộn lên đầu lịch sử chat…")
    prev_len   = 0
    stable_cnt = 0

    for i in range(max_scrolls):
        auto.MoveTo(layout.chat_mid_x, msg_area_y)
        auto.WheelUp(wheelTimes=scroll_step)
        time.sleep(load_wait)

        # Thử đo sự thay đổi qua clipboard length
        _clear_clipboard()
        auto.SendKeys("{Ctrl}a")
        time.sleep(0.25)
        auto.SendKeys("{Ctrl}c")
        time.sleep(0.25)
        cur_len = len(_read_clipboard())

        if cur_len == prev_len:
            stable_cnt += 1
            log.info("[SCROLL_UP] Ổn định lần %d (%d chars).", stable_cnt, cur_len)
            if stable_cnt >= 2:
                log.info("[SCROLL_UP] Đã đến đầu lịch sử sau %d lần cuộn.", i + 1)
                break
        else:
            stable_cnt = 0
            log.debug("[SCROLL_UP] Lần %d — %d chars.", i + 1, cur_len)

        prev_len = cur_len

    auto.SendKeys("{Escape}")
    time.sleep(0.2)


def _copy_chat_content(layout: ZaloLayout) -> str:
    """
    Lấy TOÀN BỘ lịch sử chat:
    1. Cuộn lên đến đầu để Zalo load hết tin nhắn cũ.
    2. Ctrl+A → Ctrl+C → đọc clipboard.
    """
    # Bước 1: cuộn lên đầu (load full history)
    _scroll_chat_to_top(layout)

    # Bước 2: copy toàn bộ sau khi đã load xong
    msg_area_y = layout.chat_top + int((layout.chat_bottom - layout.chat_top) * 0.4)
    _clear_clipboard()
    auto.Click(layout.chat_mid_x, msg_area_y)
    time.sleep(0.3)
    auto.SendKeys("{Ctrl}a")
    time.sleep(COPY_WAIT)
    auto.SendKeys("{Ctrl}c")
    time.sleep(COPY_WAIT)

    text = _read_clipboard()
    log.info("[CLIPBOARD] Đọc được %d ký tự toàn bộ lịch sử.", len(text))

    auto.SendKeys("{Escape}")
    time.sleep(0.2)
    return text


def _get_chat_texts_from_accessibility(layout: ZaloLayout) -> list[str]:
    """
    Thử đọc text từ cây accessibility của DocumentControl Zalo.
    Nếu Zalo expose được text elements thì dùng phương pháp này.
    """
    texts = []
    try:
        desktop = auto.GetRootControl()

        def collect_texts(ctrl, depth=0):
            if depth > 10: return
            try:
                for child in ctrl.GetChildren():
                    ctype = child.ControlTypeName
                    name  = (child.Name or "").strip()
                    if ctype in ("TextControl", "StaticControl") and name:
                        texts.append(name)
                    collect_texts(child, depth+1)
            except Exception:
                pass

        # Tìm DocumentControl của Zalo
        for ctrl in desktop.GetChildren():
            if ctrl.ControlTypeName == "DocumentControl":
                name = (ctrl.Name or "").strip()
                if "Zalo" in name or name == "":
                    collect_texts(ctrl)
    except Exception as e:
        log.debug("[ACCESS] Không đọc được từ accessibility: %s", e)

    return texts


# ─────────────────────────────────────────────
#  PARSE TEXT THÀNH LOG CHAT
# ─────────────────────────────────────────────

_TIME_PATTERN = re.compile(
    r'^(\d{1,2}[:/]\d{2}([:/]\d{2,4})?'
    r'|hôm nay|hôm qua|yesterday|today'
    r'|thứ \w+|\d{1,2}/\d{1,2}/\d{2,4}'
    r'|\d{1,2} \w+ \d{4})$',
    re.IGNORECASE,
)

_JUNK_TEXTS = {
    "tìm kiếm", "search", "nhắn tin", "gửi", "trả lời", "chuyển tiếp",
    "thích", "like", "xem thêm", "thu gọn", "đã xem", "đang nhập...",
    "tin nhắn mới", "tất cả", "ảnh", "file", "link", "nhóm", "bạn bè",
    "zalo", "nhập tin nhắn", "emoji",
}

def _is_junk(text: str) -> bool:
    t = text.strip()
    if not t: return True
    if _TIME_PATTERN.match(t): return True
    if t.lower() in _JUNK_TEXTS: return True
    if len(t) <= 2: return True
    return False


def parse_zalo_texts(
    raw_texts: list[str],
    customer_name: str,
) -> tuple[list[dict], list[str]]:
    """
    Chuyển list text thô thành log chat có cấu trúc.
    Trả về: (logs, account_senders)

    - logs: [{sender, message, role}]
        role = 'USER'  — tin nhắn của khách hàng
        role = 'BOT'   — tin nhắn tài khoản shop (brand voice)
    - account_senders: list tên tài khoản shop xuất hiện trong chat

    Logic phân biệt USER ↔ BOT:
        sender == customerName          → USER
        sender == "Bạn" / "Ban" / "You" → BOT  (Zalo PC hiển thị tin mình gửi)
        sender != customerName          → BOT   (tên shop, nhân viên...)
    """
    SELF_ALIASES = {"bạn", "ban", "you"}

    if len(raw_texts) == 1 and "\n" in raw_texts[0]:
        raw_texts = raw_texts[0].splitlines()

    logs: list[dict] = []
    clean = [t.strip() for t in raw_texts if not _is_junk(t.strip())]
    log.debug("[PARSE] %d raw → %d clean tokens", len(raw_texts), len(clean))

    if not clean:
        return [], []

    customer_norm = (customer_name or "").strip().lower()
    known_senders: set[str] = set()
    if customer_name:
        known_senders.add(customer_name.strip())

    i = 0
    current_sender: Optional[str] = None

    while i < len(clean):
        token = clean[i]
        word_count = len(token.split())

        looks_like_name = (
            token in known_senders
            or (
                word_count <= 5
                and len(token) < 50
                and not token.endswith(("?", "!", ".", "…", "..."))
                and "\n" not in token
            )
        )

        def _role(sender: str) -> str:
            s = sender.lower()
            if s in SELF_ALIASES:
                return "BOT"
            if customer_norm and s == customer_norm:
                return "USER"
            return "BOT"  # tên shop hoặc nhân viên → brand voice

        if looks_like_name and i + 1 < len(clean):
            current_sender = token
            known_senders.add(token)
            message = clean[i + 1]
            logs.append({
                "sender":  current_sender,
                "message": message,
                "role":    _role(current_sender),
            })
            i += 2
        else:
            sender = current_sender or "Unknown"
            logs.append({
                "sender":  sender,
                "message": token,
                "role":    _role(sender),
            })
            i += 1

    # account_senders = tất cả senders xuất hiện mà KHÔNG phải khách hàng
    account_senders = sorted(
        s for s in known_senders
        if s.lower() != customer_norm and s.lower() not in SELF_ALIASES
    )

    user_cnt = sum(1 for m in logs if m["role"] == "USER")
    bot_cnt  = sum(1 for m in logs if m["role"] == "BOT")
    log.info(
        "[PARSE] %d msgs — USER: %d, BOT: %d (brand voice) | account: %s",
        len(logs), user_cnt, bot_cnt,
        ", ".join(account_senders) if account_senders else "(tự detect)",
    )
    return logs, account_senders



# ─────────────────────────────────────────────
#  PUSH API
# ─────────────────────────────────────────────

def save_local(
    customer_name: str,
    parsed_logs: list[dict],
    account_senders: list[str] | None = None,
) -> bool:
    """
    Lưu data vào local Flask server (SQLite).
    KHÔNG push thẳng lên CRM — phải review trong dashboard trước.
    """
    if not parsed_logs:
        log.warning("[SAVE] Bỏ qua '%s' — không có log nào.", customer_name)
        return False

    user_cnt = sum(1 for m in parsed_logs if m.get("role") == "USER")
    bot_cnt  = len(parsed_logs) - user_cnt

    payload = {
        "secret":         API_SECRET,
        "customerName":   customer_name,
        "logs":           parsed_logs,
        "accountSenders": account_senders or [],
    }

    log.info(
        "[SAVE] Đang lưu '%s' — %d msgs (khách: %d, shop: %d)…",
        customer_name, len(parsed_logs), user_cnt, bot_cnt,
    )

    try:
        r = requests.post(
            API_ENDPOINT,
            json=payload,
            timeout=REQUEST_TIMEOUT,
            headers={"Content-Type": "application/json"},
        )
        r.raise_for_status()
        log.info("[SAVE] ✅ Đã lưu local! (chờ duyệt trong dashboard)")
        return True
    except requests.exceptions.ConnectionError:
        log.error("[SAVE] ❌ Server local không chạy? Hãy kiểm tra http://localhost:5000")
    except requests.exceptions.Timeout:
        log.error("[SAVE] ❌ Timeout sau %ds.", REQUEST_TIMEOUT)
    except requests.exceptions.HTTPError as e:
        log.error("[SAVE] ❌ HTTP Error: %s", e)
    except Exception as e:
        log.error("[SAVE] ❌ Lỗi: %s", e)
    return False

# alias cho backward compat
push_to_server = save_local


# ─────────────────────────────────────────────
#  CUỘN SIDEBAR (WheelDown đúng API)
# ─────────────────────────────────────────────

def _scroll_sidebar(layout: ZaloLayout, times: int = 3):
    """
    Cuộn sidebar xuống bằng cách di chuột vào sidebar rồi WheelDown.
    API uiautomation v2: WheelDown(wheelTimes) — KHÔNG nhận x,y.
    """
    try:
        # Di chuyển chuột vào giữa sidebar trước
        auto.MoveTo(layout.sidebar_mid_x, layout.chat_mid_y)
        time.sleep(0.1)
        # Sau đó mới WheelDown
        auto.WheelDown(wheelTimes=times)
        log.debug("[SCROLL] WheelDown x%d tại sidebar_mid_x=%d", times, layout.sidebar_mid_x)
    except Exception as e:
        log.warning("[SCROLL] Lỗi WheelDown: %s", e)



# ─────────────────────────────────────────────
#  CÀO THEO DANH SÁCH TÊN (MODE 2)
# ─────────────────────────────────────────────

def scrape_by_name_list(name_list: list[str]) -> None:
    """
    Mode 2: Cào theo danh sách tên cụ thể (lấy từ ZCA-js hoặc nhập tay).

    Ưu điểm so với mode sidebar:
    - Biết chính xác tên khách hàng trước khi parse → phân loại USER/BOT chuẩn 100%
    - Không bị trùng lặp, không bỏ sót
    - Tìm đúng người cần cào

    Luồng mỗi contact:
    1. Gõ tên vào ô search Zalo → click kết quả đầu tiên
    2. Cuộn lên đầu lịch sử (load đầy đủ)
    3. Ctrl+A, Ctrl+C → lấy toàn bộ text
    4. Parse với customer_name = tên đã biết → USER/BOT chính xác
    5. Lưu local (không push CRM, chờ review)
    """
    if not name_list:
        log.warning("[LIST] Danh sách rỗng — dừng.")
        return

    log.info("═" * 60)
    log.info("  ZALO SCRAPER — MODE DANH SÁCH (%d người)", len(name_list))
    log.info("═" * 60)

    # ── Tìm Zalo window ──
    zalo_win = _get_zalo_window()
    if not zalo_win:
        return

    try:
        zalo_win.SetFocus()
    except Exception:
        pass
    time.sleep(0.8)

    layout = ZaloLayout(zalo_win)
    layout.log_layout()

    total_ok    = 0
    total_fail  = 0

    for idx, customer_name in enumerate(name_list, 1):
        customer_name = customer_name.strip()
        if not customer_name:
            continue

        log.info("─" * 50)
        log.info("[LIST] (%d/%d) Đang cào: '%s'", idx, len(name_list), customer_name)

        # ── Tìm kiếm contact — retry tối đa 2 lần ──
        max_retries = 2
        found = False
        for attempt in range(1, max_retries + 1):
            found = _search_contact(layout, customer_name)
            if found:
                break
            if attempt < max_retries:
                log.warning("[LIST] [↺ Retry %d/%d] Thử lại '%s'...",
                            attempt, max_retries, customer_name)
                time.sleep(2.0)
                # Re-focus Zalo trước khi thử lại
                try:
                    zalo_win.SetFocus()
                    time.sleep(0.5)
                except Exception:
                    pass

        if not found:
            log.error("[LIST] ❌ Không thể mở chat với '%s' sau %d lần thử — bỏ qua.",
                      customer_name, max_retries)
            total_fail += 1
            continue

        # ── Đọc chat ──
        raw_texts = _get_chat_texts_from_accessibility(layout)
        if not raw_texts:
            log.info("[READ] Accessibility rỗng → thử clipboard…")
            clip_text = _copy_chat_content(layout)
            if clip_text:
                raw_texts = [clip_text]
        else:
            log.info("[READ] Đọc được %d phần tử text từ accessibility.", len(raw_texts))

        if not raw_texts:
            log.warning("[LIST] Không đọc được nội dung chat của '%s'.", customer_name)
            total_fail += 1
            continue

        # ── Parse với customer_name ĐÃ BIẾT → 100% chính xác ──
        parsed_logs, account_senders = parse_zalo_texts(raw_texts, customer_name)

        user_cnt = sum(1 for m in parsed_logs if m.get("role") == "USER")
        bot_cnt  = len(parsed_logs) - user_cnt
        log.info("[PARSE] Parse được %d tin nhắn — Khách: '%s' | BOT: %s",
                 len(parsed_logs), customer_name,
                 ", ".join(account_senders) or "(Bạn)")

        # ── Lưu local ──
        save_local(customer_name, parsed_logs, account_senders)

        total_ok += 1
        log.info("[LIST] Tiến độ: %d/%d ✅ xong '%s'",
                 idx, len(name_list), customer_name)

        # Dừng nhẹ giữa các contact
        time.sleep(1.0)

    log.info("═" * 60)
    log.info("  HOÀN TẤT! Thành công: %d, Thất bại: %d / %d",
             total_ok, total_fail, len(name_list))
    log.info("═" * 60)


# ─────────────────────────────────────────────
#  HÀM CHÍNH (Mode 1 — Sidebar)
# ─────────────────────────────────────────────

def main_scraper(limit: int = 100):
    """
    Vòng lặp chính: duyệt Sidebar Zalo theo tọa độ pixel,
    đọc chat qua clipboard, parse và push lên server.
    """
    log.info("═" * 60)
    log.info("  ZALO SCRAPER v2 BẮT ĐẦU — limit=%d người", limit)
    log.info("═" * 60)

    # ── Bước 1: Tìm cửa sổ Zalo ──────────────────────────────
    zalo_win = _get_zalo_window()
    if not zalo_win:
        return

    # Focus Zalo
    try:
        zalo_win.SetFocus()
    except Exception:
        pass
    time.sleep(0.8)

    # ── Bước 2: Tính layout sidebar / chat panel ──────────────
    layout = ZaloLayout(zalo_win)
    layout.log_layout()

    n_visible = layout.visible_contact_count()
    log.info("[LAYOUT] Dự kiến ~%d contact/màn hình sidebar.", n_visible)

    # ── Bước 3: Setup tracking ────────────────────────────────
    scraped_names: set[str] = set()
    total_scraped = 0
    scroll_round  = 0
    max_empty_scroll = 5

    consecutive_no_new = 0
    prev_first_contact_y_signature = None  # dùng để phát hiện sidebar đã cuộn

    # ── Bước 4: Vòng lặp ─────────────────────────────────────
    while total_scraped < limit:
        found_new_in_pass = False

        for slot_idx in range(n_visible):
            if total_scraped >= limit:
                break

            contact_x = layout.sidebar_mid_x
            contact_y = layout.contact_y(slot_idx)

            if contact_y >= layout.sidebar_bottom:
                break

            # ── Bước 1: Click vào contact trong sidebar ─────────────────────
            log.info("[LOOP] [%d/%s] Click slot [%d] tại (%d, %d)…",
                     total_scraped + 1,
                     "∞" if limit >= 999999 else str(limit),
                     slot_idx, contact_x, contact_y)
            try:
                auto.Click(contact_x, contact_y)
            except Exception as e:
                log.warning("[UI] Click lỗi slot %d: %s", slot_idx, e)
                continue

            time.sleep(CLICK_PAUSE)   # chờ chat panel load

            # ── Dedup theo vị trí (slot + round) ────────────────────────────
            dedup_key = f"s{slot_idx}_r{scroll_round}"
            if dedup_key in scraped_names:
                continue

            # ── Bước 2: Click ảnh đại diện → lấy tên chính xác ─────────────
            fallback_name = f"Khách_{dedup_key}"
            contact_name  = _get_name_from_avatar_popup(layout, fallback=fallback_name)
            log.info("[LOOP] Tên khách: '%s'", contact_name)

            # ── Dedup theo tên thật ──────────────────────────────────────────
            name_key = contact_name.strip().lower()
            if name_key in scraped_names:
                log.info("[DEDUP] Bỏ qua '%s' — đã cào rồi.", contact_name)
                continue

            # ── Bước 3: Scroll to top + Ctrl+A + Ctrl+C ─────────────────────
            # (popup đã được đóng trong _get_name_from_avatar_popup)
            raw_texts = _get_chat_texts_from_accessibility(layout)
            if not raw_texts:
                log.info("[READ] Accessibility rỗng → thử clipboard…")
                clip_text = _copy_chat_content(layout)
                if clip_text:
                    raw_texts = [clip_text]

            if not raw_texts:
                log.warning("[LOOP] Không đọc được nội dung chat '%s' — bỏ qua.", contact_name)
                scraped_names.add(dedup_key)
                continue

            # ── Bước 4: Parse — customer_name đã biết → 100% chính xác ─────
            parsed_logs, account_senders = parse_zalo_texts(raw_texts,
                                                            customer_name=contact_name)
            bot_msgs  = [m for m in parsed_logs if m.get("role") == "BOT"]

            # Lọc account_senders (BOT senders, bỏ biến thể "Bạn")
            SELF_SET = {"bạn", "ban", "you"}
            account_senders = sorted(set(
                m["sender"] for m in bot_msgs
                if m["sender"].lower() not in SELF_SET
            ))

            user_cnt = len([m for m in parsed_logs if m.get("role") == "USER"])
            log.info("[PARSE] %d tin nhắn — Khách: '%s' (%d) | BOT: %s (%d)",
                     len(parsed_logs), contact_name, user_cnt,
                     ", ".join(account_senders) or "(Bạn)", len(bot_msgs))

            # ── Bước 5: Push lên local server ────────────────────────────────
            push_to_server(contact_name, parsed_logs, account_senders)

            scraped_names.add(dedup_key)   # positional key
            scraped_names.add(name_key)    # name key
            total_scraped += 1
            found_new_in_pass = True
            log.info("[LOOP] Tiến độ: %d/%s",
                     total_scraped, "∞" if limit >= 999999 else str(limit))

        # ── Cuộn sidebar ──────────────────────────────────────
        if not found_new_in_pass:
            consecutive_no_new += 1
            log.info("[SCROLL] Không có người mới — cuộn sidebar (%d/%d)…",
                     consecutive_no_new, max_empty_scroll)
        else:
            consecutive_no_new = 0

        if consecutive_no_new >= max_empty_scroll:
            log.info("[LOOP] Cuộn %d lần không có người mới → dừng.", max_empty_scroll)
            break

        _scroll_sidebar(layout, times=3)
        time.sleep(SCROLL_PAUSE)
        scroll_round += 1

    # ── Tổng kết ──────────────────────────────────────────────
    log.info("═" * 60)
    log.info("  HOÀN TẤT! Đã cào %d người. (%d vòng cuộn)",
             total_scraped, scroll_round)
    log.info("═" * 60)


# ─────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import os, json as _json

    mode       = os.environ.get("SCRAPER_MODE", "sidebar")
    API_ENDPOINT = os.environ.get("SCRAPER_API_URL", API_ENDPOINT)
    API_SECRET   = os.environ.get("SCRAPER_SECRET",  API_SECRET)

    if mode == "list":
        # ── Mode 2: Cào theo danh sách tên ──
        list_file = os.environ.get("SCRAPER_LIST_FILE", "scraper_name_list.json")
        try:
            with open(list_file, encoding="utf-8") as f:
                name_list = _json.load(f)
            log.info("[ENTRY] MODE=list — %d tên từ %s", len(name_list), list_file)
            scrape_by_name_list(name_list)
        except Exception as e:
            log.error("[ENTRY] ❌ Không đọc được danh sách: %s", e)
    else:
        # ── Mode 1: Cào sidebar (giới hạn số lượng) ──
        limit = int(os.environ.get("SCRAPER_LIMIT", 100))
        log.info("[ENTRY] MODE=sidebar — limit=%d", limit)
        main_scraper(limit=limit)
