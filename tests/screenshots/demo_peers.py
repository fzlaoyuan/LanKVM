# -*- coding: utf-8 -*-
"""配合截图的演示对端（无界面）：python demo_peers.py clients|server <秒数>
演示数据中的名称、密码 demo1234 均为虚构。"""
import sys, time
import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import lankvm as L
mode = sys.argv[1]
if mode == "clients":
    a = L.Client("127.0.0.1", "demo1234", "right", "演示副机-客厅电脑", fport=24812, headless=True, clipboard=False); a.bounds=(0,0,1920,1080)
    b = L.Client("127.0.0.1", "demo1234", "left", "演示笔记本-卧室", fport=24813, headless=True, clipboard=False); b.bounds=(0,0,1366,768)
    a.start(); b.start()
else:
    s = L.Server("演示主机-书房台式机", "demo1234", fport=24812, headless=True, clipboard=False); s.start()
time.sleep(float(sys.argv[2]))
