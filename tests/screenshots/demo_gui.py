# -*- coding: utf-8 -*-
"""驱动真实 LanKVM 界面进入各状态（演示数据），供使用说明截图。在 Windows/Wine 中运行：python demo_gui.py server|client
演示数据中的名称、密码 demo1234 均为虚构。"""
# 驱动真实 LanKVM 界面进入不同状态，供使用说明截图（演示数据）
import sys, os, json
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(HERE, '..', '..'))
import lankvm as L
L.CONF_PATH = os.path.join(HERE, "demo_conf_%s.json" % sys.argv[1])
if os.path.exists(L.CONF_PATH): os.remove(L.CONF_PATH)
L.enable_dpi_awareness()
mode = sys.argv[1]
app = L.App()
r = app.root
r.geometry("900x860+40+40")
def mark(name):
    r.update_idletasks(); r.update()
    info = {"x": r.winfo_rootx(), "y": r.winfo_rooty(), "w": r.winfo_width(), "h": r.winfo_height()}
    open(os.path.join(HERE, "mark_" + name), "w").write(json.dumps(info))
steps = []
if mode == "server":
    app.v_name.set("演示主机-书房台式机"); app.v_pwd.set("demo1234")
    steps = [(1500, lambda: mark("s_idle")),
             (2500, lambda: app.toggle()),
             (9000, lambda: mark("s_connected")),
             (10000, lambda: (app.node.lock.acquire(), app.node._enter("right", 1919, 400), app.node.lock.release())),
             (11500, lambda: mark("s_active")),
             (12500, lambda: (app.node.lock.acquire(), app.node._leave(), app.node.lock.release())),
             (13000, lambda: app.show_toast("已收到 演示副机-客厅电脑 发来的 3 个文件（12.4 MB）", [r"C:\\x"])),
             (13600, lambda: mark("s_toast")),
             (14500, lambda: app.zone.show("right", "松开鼠标\n发送到\n演示副机-客厅电脑", lambda f: None)),
             (15500, lambda: mark("s_zone")),
             (16500, lambda: os._exit(0))]
else:
    app.v_role.set("client"); app._role_changed()
    app.v_name.set("演示副机-客厅电脑"); app.v_pwd.set("demo1234"); app.v_host.set("127.0.0.1")
    app.v_side.set("右侧"); app._redraw_map(force=True)
    steps = [(1500, lambda: mark("c_idle")),
             (2500, lambda: app.toggle()),
             (8000, lambda: mark("c_connected")),
             (9000, lambda: os._exit(0))]
for t, f in steps: r.after(t, f)
app.run()
