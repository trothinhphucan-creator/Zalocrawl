"""
Script chẩn đoán v2: Tìm Zalo bằng nhiều cách khác nhau.
Chạy: python find_zalo.py
"""
import uiautomation as auto
import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

print("=" * 55)
print("  ZALO WINDOW FINDER v2")
print("=" * 55)

# ── 1. Kiểm tra process Zalo có đang chạy không ──
print("\n[PROCESS] Tìm process Zalo:")
try:
    result = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq Zalo.exe", "/FO", "CSV"],
        capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    lines = [l for l in result.stdout.strip().splitlines() if "Zalo" in l]
    if lines:
        for l in lines:
            print(f"  ✅ {l}")
    else:
        print("  ❌ Không tìm thấy process Zalo.exe!")
        print("     → Hãy mở Zalo PC trước khi chạy scraper.")
except Exception as e:
    print(f"  Lỗi tasklist: {e}")

# ── 2. Tìm cửa sổ bằng cách duyệt tất cả windows ──
print("\n[WINDOWS] Tất cả WindowControl đang hiển thị:")
desktop = auto.GetRootControl()
zalo_wins = []

def scan_windows(root, depth=0, max_depth=2):
    try:
        for ctrl in root.GetChildren():
            name  = ctrl.Name or ""
            cls   = ctrl.ClassName or ""
            ctype = ctrl.ControlTypeName or ""
            indent = "  " * depth
            if ctype in ("WindowControl", "PaneControl"):
                print(f"{indent}Name='{name[:50]}'  Class='{cls}'  Type={ctype}")
            if "zalo" in name.lower() or "zalo" in cls.lower():
                zalo_wins.append(ctrl)
            if depth < max_depth:
                scan_windows(ctrl, depth+1, max_depth)
    except Exception:
        pass

scan_windows(desktop)

# ── 3. Thử tìm theo từng ClassName phổ biến của Zalo ──
print("\n[SEARCH] Thử từng ClassName phổ biến của Zalo:")
possible_classes = [
    "ZaloMainWnd", "ZPCMainWnd", "ZaloPC",
    "Chrome_WidgetWin_1",  # Zalo mới dùng Electron (Chromium)
    "CefBrowserWindow",
    "ApplicationFrameWindow",
]
for cls in possible_classes:
    try:
        w = auto.WindowControl(ClassName=cls, searchDepth=1)
        exists = w.Exists(maxSearchSeconds=1)
        mark = "✅" if exists else "❌"
        print(f"  {mark}  ClassName='{cls}' → found={exists}  Name='{w.Name if exists else '—'}'")
        if exists:
            zalo_wins.append(w)
    except Exception as e:
        print(f"  ❌  ClassName='{cls}' → error: {e}")

# ── 4. Thử tìm theo SubName chứa "Zalo" ──
print("\n[SEARCH] WindowControl tên chứa 'Zalo':")
try:
    w = auto.WindowControl(SubName="Zalo", searchDepth=1)
    if w.Exists(maxSearchSeconds=2):
        print(f"  ✅  Name='{w.Name}'  Class='{w.ClassName}'")
        zalo_wins.append(w)
    else:
        print("  ❌  Không tìm thấy bằng SubName='Zalo'")
except Exception as e:
    print(f"  ❌  Lỗi: {e}")

# ── 5. Kết quả ──
print(f"\n[KẾT QUẢ] Tìm được {len(zalo_wins)} ứng viên Zalo window.")
for i, w in enumerate(zalo_wins):
    print(f"  [{i}] Name='{w.Name}'  Class='{w.ClassName}'  Type={w.ControlTypeName}")

print("\n" + "=" * 55)
