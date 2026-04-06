"""
debug_search_coords.py
Di chuyển chuột đến tọa độ tính toán để kiểm tra xem có đúng không.
Chạy: python debug_search_coords.py

Sau 3 giây, chuột sẽ di chuyển lần lượt đến:
  1. safe_click_y  → nên rơi vào header/tiêu đề Zalo
  2. search_y      → nên rơi vào ô tìm kiếm
  3. first_result_y → nên rơi vào kết quả đầu tiên
"""
import sys, time, uiautomation as auto

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SIDEBAR_WIDTH_RATIO = 0.32
CONTACT_HEIGHT_PX   = 72
HEADER_HEIGHT_PX    = 220

def find_zalo(ctrl, depth=0):
    if depth > 2: return None
    try:
        for child in ctrl.GetChildren():
            name = (child.Name or "").strip()
            cls  = child.ClassName or ""
            if name == "Zalo" and "Chrome_WidgetWin" in cls:
                return child
            r = find_zalo(child, depth+1)
            if r: return r
    except Exception:
        pass
    return None

win = find_zalo(auto.GetRootControl())
if not win:
    print("❌ Không tìm thấy cửa sổ Zalo!")
    sys.exit(1)

r = win.BoundingRectangle
win_w = r.right - r.left
sidebar_left  = r.left
sidebar_right = r.left + int(win_w * SIDEBAR_WIDTH_RATIO)
sidebar_mid_x = (sidebar_left + sidebar_right) // 2

# Tọa độ tính toán
safe_click_y   = r.top + 40
search_y       = r.top + 110
first_result_y = search_y + 44 + CONTACT_HEIGHT_PX // 2

print(f"Zalo Window: ({r.left},{r.top}) → ({r.right},{r.bottom})  size={win_w}x{r.bottom-r.top}")
print(f"sidebar_mid_x   = {sidebar_mid_x}")
print()
print("Sau 3 giây chuột sẽ di chuyển kiểm tra từng tọa độ...")
print("Quan sát xem chuột có rơi đúng vị trí không:")
time.sleep(3)

coords = [
    (sidebar_mid_x, safe_click_y,   "1. safe_click_y  → Header/tiêu đề Zalo"),
    (sidebar_mid_x, search_y,       "2. search_y       → Ô tìm kiếm"),
    (sidebar_mid_x, first_result_y, "3. first_result_y → Kết quả đầu tiên"),
]

for x, y, label in coords:
    print(f"  {label}  (x={x}, y={y})")
    auto.MoveTo(x, y)
    time.sleep(2)

print("\n✅ Hoàn tất kiểm tra tọa độ.")
print("Nếu tọa độ nào sai, điều chỉnh trong ZaloLayout.__init__() trong zalo_scraper.py:")
print("  self.search_y = r.top + ???  (thay 110 bằng giá trị phù hợp)")
