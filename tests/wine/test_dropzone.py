# -*- coding: utf-8 -*-
"""Windows 专项：拖放投放区不卡死测试。需在 Windows 或 Wine 中用 Windows 版 Python 运行：
    python tests/wine/test_dropzone.py new r_new      （新版原生投放区，应输出 NOT FROZEN）
结果写入同目录下的 <第二个参数>.log。"""
import sys, threading, time, os
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(HERE, '..', '..'))
LOG = open(os.path.join(HERE, sys.argv[2] + ".log"), "w", encoding="utf-8")
def P(*a):
    LOG.write(" ".join(str(x) for x in a) + "\n"); LOG.flush(); os.fsync(LOG.fileno())
P("start", sys.argv[1])
import lankvm as L
import tkinter as tk
class A: pass
app = A(); app.tk = tk; app.root = tk.Tk()
beats = [0]
def beat():
    beats[0] += 1; app.root.after(30, beat)
app.root.after(30, beat)
mode = sys.argv[1]
if mode in ("old", "new"):
    z = L.DropZone()
    P("zone ok", z.ok)
    app.root.after(300, lambda: (P("show zone"), z.show("right", "松开鼠标\n发送到\n副机", lambda f: P("dropped", f)), P("show returned")))
import ctypes, struct
def poke():
    # 模拟：鼠标在投放区上移动，然后松开投下文件
    time.sleep(1.0)
    w = L.win_api()
    u = ctypes.WinDLL("user32")
    u.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
    u.WindowFromPoint.argtypes = [L.win_api().POINT]; u.WindowFromPoint.restype = ctypes.c_void_p
    for i in range(20):
        w.SetCursorPos(1850, 300 + i * 10); time.sleep(0.02)
    pt = w.POINT(1850, 400); hwnd = u.WindowFromPoint(pt)
    P("window under cursor", hex(hwnd or 0))
    import time as _t; _t.sleep(0.3)
    files = ("C:\\windows\\win.ini\0\0").encode("utf-16-le")
    blob = struct.pack("<Iiiii", 20, 0, 0, 0, 1) + files
    cf = L.ClipFiles(); h = cf._galloc(blob)
    P("post WM_DROPFILES", u.PostMessageW(hwnd, 0x233, h, 0))
    time.sleep(0.5)
    P("after drop, window under cursor", hex(u.WindowFromPoint(pt) or 0))
if mode != "none": threading.Thread(target=poke, daemon=True).start()
def watch():
    last = -1
    for i in range(10):
        time.sleep(0.5)
        P(f"t={i*0.5+0.5:.1f}s heart={beats[0]}")
        if beats[0] == last:
            P("FROZEN"); os._exit(3)
        last = beats[0]
    P("NOT FROZEN"); os._exit(0)
threading.Thread(target=watch, daemon=True).start()
app.root.mainloop()
