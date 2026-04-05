"""
Dump toàn bộ UI tree bên trong cửa sổ Zalo để tìm Sidebar + Contacts.
Chạy: python dump_zalo_tree.py > tree.txt
"""
import sys, uiautomation as auto

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── Tìm cửa sổ Zalo ──────────────────────────────────────
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

zalo = find_zalo(auto.GetRootControl())
if not zalo:
    print("KHÔNG TÌM THẤY CỬA SỔ ZALO"); sys.exit(1)

print(f"ZALO WINDOW: Name='{zalo.Name}'  Class='{zalo.ClassName}'  Type={zalo.ControlTypeName}")
rect = zalo.BoundingRectangle
print(f"  Rect=({rect.left},{rect.top},{rect.right},{rect.bottom})")
print()

# ── Dump toàn bộ cây UI ─────────────────────────────────
def dump(ctrl, depth=0, max_depth=6):
    if depth > max_depth: return
    indent = "  " * depth
    try:
        children = ctrl.GetChildren()
    except Exception:
        children = []

    for ch in children:
        name   = (ch.Name or "").strip()[:60]
        cls    = ch.ClassName or ""
        ctype  = ch.ControlTypeName
        rect   = ch.BoundingRectangle
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        print(f"{indent}[{ctype}] Name='{name}'  Class='{cls}'  Size={w}x{h}")
        dump(ch, depth+1, max_depth)

dump(zalo, max_depth=5)
print("\n=== XONG ===")
