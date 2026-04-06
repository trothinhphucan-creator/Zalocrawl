# ZALOCRAWL — BỘ NHỚ TRUNG TÂM (System Context)

---

# 1. Tổng quan dự án (Project Overview)

**Zalocrawl** — Bot RPA tự động cào toàn bộ lịch sử hội thoại từ **Zalo PC (Windows Electron)**, lưu vào SQLite local, cho phép review trên web dashboard trước khi đồng bộ lên CRM (AgentSee).

Mục tiêu cốt lõi: Thu thập dữ liệu chat khách hàng từ Zalo để phục vụ remarketing và phân tích hội thoại.

---

# 2. Công nghệ & Kiến trúc (Tech Stack & Architecture)

## Tech Stack

| Layer | Công nghệ | Vai trò |
|---|---|---|
| RPA Engine | `uiautomation` (Win32 UIA) | Tìm cửa sổ Zalo, click tọa độ pixel, cuộn sidebar, Ctrl+A/C clipboard |
| Clipboard | `pyperclip` | Đọc/ghi Windows clipboard (lấy nội dung chat) |
| HTTP Client | `requests` | Gửi data từ scraper → local Flask server |
| Backend | `Flask` + `flask-cors` | REST API + SSE log stream + serve dashboard |
| Database | **SQLite** (file `zalocrawl.db`) | Lưu conversations local, sync config, trạng thái review |
| Dashboard | Single-page HTML (dark-mode, glassmorphism) | Điều khiển scraper, xem log realtime, duyệt conversations |
| Remarketing | `zca-js` (Node.js, file tham khảo) | Gửi tin nhắn Zalo tự động qua ZCA API |

## Kiến trúc — 3 tầng đơn giản

```
┌─────────────────────────────────────────────────────────────┐
│  TẦNG 1: RPA (zalo_scraper.py)                              │
│  - Chạy như subprocess của Flask                             │
│  - 2 mode: sidebar (duyệt lần lượt) | list (tìm theo tên)  │
│  - Output: POST JSON → localhost:5000/api/conversations/ingest │
└──────────────────────────┬──────────────────────────────────┘
                           │ HTTP POST (loopback)
┌──────────────────────────▼──────────────────────────────────┐
│  TẦNG 2: Flask Backend (server.py)                           │
│  - Subprocess manager (start/stop scraper)                   │
│  - SSE log streaming (realtime)                              │
│  - SQLite CRUD (conversations, sync_config)                  │
│  - Sync endpoint → push approved data lên AgentSee CRM      │
│  - ZCA-js integration (import friends, remarketing list)     │
└──────────────────────────┬──────────────────────────────────┘
                           │ HTTP (serve static)
┌──────────────────────────▼──────────────────────────────────┐
│  TẦNG 3: Dashboard (templates/index.html)                    │
│  - Single HTML file (~2000 dòng, inline CSS + JS)            │
│  - Tabs: Điều khiển | Duyệt conversations | Log stream      │
│  - Upload JSON danh sách tên → start-by-list mode            │
└─────────────────────────────────────────────────────────────┘
```

## Luồng dữ liệu chính

```
Zalo PC window
  → [uiautomation click + clipboard read]
  → zalo_scraper.py parse text → JSON {customerName, logs[{sender, message, role}]}
  → POST /api/conversations/ingest (Flask local)
  → SQLite (status=pending)
  → Dashboard review (approve/reject)
  → POST /api/sync → AgentSee CRM (chỉ approved + chưa synced)
```

## Database Schema (conversations table)

| Column | Type | Mô tả |
|---|---|---|
| id | INTEGER PK | Auto-increment |
| customer | TEXT | Tên khách hàng |
| logs | TEXT (JSON) | Toàn bộ tin nhắn [{sender, message, role}] |
| bot_msgs | TEXT (JSON) | Chỉ tin BOT |
| account_senders | TEXT (JSON) | Tên các tài khoản shop |
| msg_count | INTEGER | Tổng tin nhắn |
| user_msg_count | INTEGER | Tin nhắn của khách |
| status | TEXT | pending / approved / rejected |
| synced | INTEGER | 0 hoặc 1 |
| synced_at | TEXT | ISO datetime |
| zalo_uid | TEXT | UID Zalo (từ ZCA-js matching) |
| zalo_name | TEXT | Tên Zalo chính xác |

## 2 Scraping Modes

1. **Sidebar mode** (`main_scraper`): Duyệt lần lượt contact list bằng click tọa độ pixel + cuộn sidebar. Không biết trước tên khách → auto-detect từ parsed text. Dedup bằng `slot_index + scroll_round`.

2. **List mode** (`scrape_by_name_list`): Nhận danh sách tên → Ctrl+F search box → click kết quả đầu tiên. Biết trước customer_name → phân loại USER/BOT chính xác 100%.

---

# 3. Trạng thái hiện tại (Current Status)

## [Hoàn thành] — Chạy ổn định

- **zalo_scraper.py** — Cả 2 mode (sidebar + list) đều functional. Parse text → JSON logs với role USER/BOT. Lưu qua local API.
- **server.py** — Flask backend đầy đủ: subprocess manager, SSE streaming, SQLite CRUD, sync to CRM, ZCA-js integration endpoints.
- **templates/index.html** — Dashboard dark-mode hoàn chỉnh (~2000 dòng): điều khiển scraper, upload JSON name list, review conversations, xem log realtime.
- **SQLite schema** — Bảng `conversations` + `sync_config` với migration tự động (ALTER TABLE try/except).
- **ZCA-js integration** — 3 endpoints: import-friends (match UID), update-uid (thủ công), remarketing-list (trả danh sách approved + có UID).
- **zca-remarketing.js** — Script Node.js mẫu đầy đủ workflow: sync friends → check stats → send messages.

## [Đang dở dang / Cần cải thiện]

- **Coordinate hardcoding** — `HEADER_HEIGHT_PX=220`, `CONTACT_HEIGHT_PX=72`, `search_y = win_top + 110` là magic numbers phụ thuộc Zalo version và DPI. Nếu Zalo update UI hoặc chạy trên màn hình khác DPI → cần cận chỉnh lại.
- **Sidebar mode dedup** — Dùng positional key `slot_idx + scroll_round`, không detect trùng tên thực sự. Nếu Zalo sidebar thay đổi thứ tự (tin nhắn mới đẩy lên) → có thể cào trùng.
- **DB migration pattern** — Dùng try/except ALTER TABLE. Chưa có migration versioning. Nếu thêm nhiều cột trong tương lai sẽ khó quản lý.
- **Secret hardcoded** — `antigravity_secret_2026` xuất hiện cứng trong cả scraper và server. Chưa dùng env var nhất quán (server check hardcode, scraper đọc từ env nhưng fallback hardcode).
- **Error recovery** — Nếu scraper crash giữa chừng (Zalo bị minimize, mất focus), không có retry logic hoặc resume từ contact cuối.
- **Dashboard single HTML** — ~2000 dòng inline CSS+JS. Chưa tách component. Khó maintain nếu tiếp tục mở rộng.
- **static/ folder trống** — Có thư mục nhưng không có file nào, CSS/JS đều inline trong HTML.

---

# 4. Quy tắc phát triển (Guardrails & Handoff Notes)

## Nguyên tắc bất di bất dịch

### 1. KHÔNG thay đổi thư viện RPA core
`uiautomation` là nền tảng duy nhất hoạt động với Zalo Electron (Chrome_WidgetWin_1). KHÔNG được tự ý đổi sang pyautogui, pywinauto, selenium, hay bất kỳ thư viện nào khác. Zalo PC không expose DOM — chỉ có coordinate-based + clipboard mới hoạt động.

### 2. Scraper LUÔN lưu local trước, KHÔNG push thẳng CRM
Luồng bắt buộc: `scraper → POST /api/conversations/ingest → SQLite (status=pending) → Dashboard review → Sync CRM`. Mọi thay đổi PHẢI giữ nguyên luồng này. Không được bypass bước review (đã từng gặp lỗi push data rác lên CRM).

### 3. KHÔNG tách dashboard HTML thành nhiều file
`templates/index.html` là single-file intentional design — inline CSS + JS. Lý do: deploy đơn giản (chỉ cần Flask serve 1 file), không cần build step. Nếu cần thêm tính năng dashboard, thêm vào file hiện tại, KHÔNG tạo file .js/.css riêng.

### 4. Giữ nguyên cấu trúc 2-process (Flask + subprocess)
Flask server quản lý scraper qua `subprocess.Popen`. KHÔNG chuyển scraper thành thread trong Flask process hoặc async task. Lý do: scraper dùng `uiautomation` blocking calls + clipboard manipulation — chạy cùng process với Flask sẽ block toàn bộ HTTP server và gây race condition clipboard.

### 5. Mọi thay đổi tọa độ UI phải test thủ công với Zalo PC đang mở
Các hằng số layout (`HEADER_HEIGHT_PX`, `CONTACT_HEIGHT_PX`, `SIDEBAR_WIDTH_RATIO`, `search_y offset`) là kết quả calibrate thủ công. KHÔNG được thay đổi dựa trên giả định — phải mở Zalo PC, chạy `debug_search_coords.py` hoặc `find_zalo.py` để verify.

---

## Lưu ý bổ sung cho AI thợ code

- **Encoding**: Toàn bộ project xử lý tiếng Việt. Mọi file I/O phải dùng `encoding="utf-8"`. stdout/stderr đã force UTF-8 trong scraper.
- **Database**: SQLite file-based (`zalocrawl.db`). Dùng context manager `_get_db()` cho mọi truy vấn. KHÔNG tạo connection pool hoặc dùng ORM.
- **Environment variables**: Scraper nhận config qua env vars (`SCRAPER_MODE`, `SCRAPER_LIMIT`, `SCRAPER_API_URL`, `SCRAPER_SECRET`, `SCRAPER_LIST_FILE`). Flask ghi `runtime_config.json` trước khi spawn subprocess.
- **Log parsing**: Flask đọc stdout của subprocess và parse bằng regex trong `_parse_log_line()`. Nếu thay đổi format log trong scraper → PHẢI update regex tương ứng trong server.py.
- **Windows-only**: Dự án chỉ chạy trên Windows 10/11. `uiautomation` dùng Win32 UIA API. Không cần xử lý cross-platform.
