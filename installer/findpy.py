# 若当前解释器可运行 LanKVM（Python 3.8+ 且带 tkinter），输出其路径；否则不输出。
import sys
try:
    import tkinter  # noqa: F401
    ok = sys.version_info >= (3, 8)
except Exception:
    ok = False
if ok:
    print(sys.executable)
