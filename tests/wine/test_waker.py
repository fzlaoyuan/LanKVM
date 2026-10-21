# -*- coding: utf-8 -*-
"""Windows 专项：真实输入移动光标、唤醒、锁屏检测测试。需在 Windows 或 Wine 中运行，结果写入 wake.log。"""
import sys, os, time, ctypes
HERE=os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(HERE, '..', '..'))
LOG=open(os.path.join(HERE,"wake.log"),"w",encoding="utf-8")
def P(*a): LOG.write(" ".join(map(str,a))+"\n"); LOG.flush()
import lankvm as L
L.enable_dpi_awareness()
w = L.Waker(keep_awake=True); P("ok", w.ok, "INPUT size", ctypes.sizeof(w._INPUT))
api = L.win_api(); pt = api.POINT()
class LII(ctypes.Structure): _fields_=[("cbSize",ctypes.c_uint),("dwTime",ctypes.c_uint)]
u=ctypes.windll.user32; li=LII(8)
u.GetLastInputInfo(ctypes.byref(li)); t0=li.dwTime
time.sleep(0.3)
for x,y in [(100,100),(1919,1079),(960,540),(0,0)]:
    r = w.move_abs(x,y); time.sleep(0.05); api.GetCursorPos(ctypes.byref(pt)); P(f"move_abs({x},{y}) sent={r} -> cursor=({pt.x},{pt.y})")
u.GetLastInputInfo(ctypes.byref(li)); P("最后输入时间已刷新(说明被系统视为真实输入):", li.dwTime > t0)
w.wake(); P("wake ok")
P("is_locked:", w.is_locked())
w.stop(); P("done")
