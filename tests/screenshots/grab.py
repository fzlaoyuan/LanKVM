# -*- coding: utf-8 -*-
"""在 Linux/Xvfb 上按标记文件截屏（仅用于生成使用说明截图）
演示数据中的名称、密码 demo1234 均为虚构。"""
import os, time, json, sys
from PIL import ImageGrab
W=os.path.dirname(os.path.abspath(__file__)); SP=os.environ.get("SHOT_DIR", W)
names=json.loads(sys.argv[1]); done=set(); end=time.time()+float(sys.argv[2])
while time.time()<end and len(done)<len(names):
    for k,v in names.items():
        p=W+"/mark_"+k
        if k not in done and os.path.exists(p):
            time.sleep(0.3); m=json.load(open(p)); im=ImageGrab.grab(xdisplay=":99")
            if k=="s_toast": im.crop((1920-470,1080-190,1920,1080-50)).save(f"{SP}/shots/{v}.png")
            elif k=="s_zone": im.save(f"{SP}/shots/{v}.png")
            else: im.crop((m["x"],m["y"],m["x"]+m["w"],m["y"]+m["h"])).save(f"{SP}/shots/{v}.png")
            done.add(k); print("captured",v)
    time.sleep(0.1)
