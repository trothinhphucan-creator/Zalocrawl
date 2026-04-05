# Zalo PC Scraper 🤖

Bot tự động cào dữ liệu hội thoại từ **Zalo PC (Windows)** và đẩy lên server CRM nội bộ.  
Xây dựng bằng `uiautomation` + `requests` + `Flask` dashboard.

## 🗂️ Cấu trúc

```
Zalocrawl/
├── zalo_scraper.py      # Bot RPA chính — click tọa độ sidebar, đọc clipboard
├── server.py            # Flask backend + SSE log stream cho dashboard
├── templates/
│   └── index.html       # Web dashboard dark-mode (glassmorphism)
├── requirements.txt     # Dependencies
└── find_zalo.py         # Diagnostic — tìm ClassName cửa sổ Zalo
```

## ⚙️ Cài đặt

```bash
pip install -r requirements.txt
```

## 🚀 Chạy

**1. Mở Zalo PC** (đảm bảo không minimize)

**2. Khởi động Dashboard:**
```bash
python server.py
```

**3. Mở trình duyệt:** `http://localhost:5000`

**4. Nhấn "Bắt đầu"** trên dashboard để chạy scraper.

## 📡 API Payload

```json
{
  "secret": "antigravity_secret_2026",
  "customerName": "Tên khách hàng",
  "logs": [
    { "sender": "Khách", "message": "Xin chào shop" },
    { "sender": "Bé Mầm", "message": "Dạ chào bạn" }
  ]
}
```

## 🔧 Cấu hình

Chỉnh trong `zalo_scraper.py`:

| Biến | Mặc định | Mô tả |
|---|---|---|
| `API_ENDPOINT` | `localhost:3000/...` | URL server CRM |
| `API_SECRET` | `antigravity_secret_2026` | Secret key |
| `HEADER_HEIGHT_PX` | `220` | Khoảng bỏ qua toolbar Zalo |
| `CONTACT_HEIGHT_PX` | `72` | Chiều cao mỗi contact item |
| `CLICK_PAUSE` | `2.0s` | Chờ sau khi click contact |

## ⚠️ Lưu ý

- Zalo PC phiên bản mới dùng **Electron (Chromium)** — ClassName: `Chrome_WidgetWin_1`
- Không di chuột khi bot đang chạy
- Chỉ hỗ trợ **Windows 10/11** (uiautomation dùng Win32 UIA)
- Chạy với quyền **Administrator** nếu gặp lỗi accessibility

## 📦 Dependencies

- `uiautomation` — Windows UI Automation
- `requests` — HTTP push lên server
- `flask` + `flask-cors` — Dashboard backend
- `pyperclip` — Đọc clipboard cross-platform
