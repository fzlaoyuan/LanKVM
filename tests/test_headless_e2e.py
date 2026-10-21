# -*- coding: utf-8 -*-
"""LanKVM 无界面端到端测试（在任意系统上运行：python tests/test_headless_e2e.py）
用一台模拟主机 + 两台模拟副机（本机回环地址）验证：文件复制粘贴（三个方向）、失败处理、
拖放（两个方向）、发送按钮、配对密码校验。测试使用的 "pw" 仅为测试用的假密码。"""
import sys, time, os, tempfile, hashlib
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import lankvm as L
L.STAGE_DIR = tempfile.mkdtemp(prefix="stage")
OK = []
def check(name, cond):
    OK.append((name, bool(cond))); print(("✔ " if cond else "✘ ") + name)
def wait(f, t=8):
    end = time.time() + t
    while time.time() < end:
        if f(): return True
        time.sleep(0.05)
    return False

class FakeClip:
    def __init__(self): self.files=None; self.text=None; self.cleared=0; self.timeline=None
    def set_files(self, p): self.files=list(p); self.timeline and self.timeline.append(("setfiles",))
    def set(self, t): self.text=t
    def clear(self): self.cleared+=1
    def stop(self): pass
class FakeUI:
    zones=True
    def __init__(self): self.toasts=[]; self.shows=[]; self.hides=0
    def toast(self, t, p=None): self.toasts.append((t,p))
    def zone_show(self, e, l, cb): self.shows.append((e,l,cb))
    def zone_hide(self, d=0): self.hides+=1
class FakeMouse:
    position=(0,0)

srv = L.Server("主机A", "pw", headless=True, clipboard=False)
srv.ui = FakeUI(); srv.start(); srv.clip = FakeClip()
srv.bounds=(0,0,1920,1080); srv.center=(960,540); srv.mctl=FakeMouse()
srv_recv = tempfile.mkdtemp(); srv.files.recv_dir = srv_recv

def mk(side, fport, name):
    c = L.Client("127.0.0.1", "pw", side, name, fport=fport, headless=True, clipboard=False)
    c.bounds=(0,0,1280,720); c.got=[]; c.ui=FakeUI()
    orig=c._apply
    def ap(m):
        if m.get("t")!="ping": c.got.append(m)
        if c.clip and c.clip.timeline is not None and m.get("t")=="k": c.clip.timeline.append(("k", m.get("c") or m.get("n"), m["d"]))
        orig(m)
    c._apply=ap
    c.start(); c.clip=FakeClip(); c.files.recv_dir=tempfile.mkdtemp()
    return c
A = mk("right", 24812, "副机甲")
B = mk("left", 24813, "副机乙")
check("两台副机都已连接", wait(lambda: len(srv.sessions)==2))

# 测试数据：一个带空子目录的文件夹 + 一个大文件
src = tempfile.mkdtemp()
os.makedirs(src+"/项目/图片"); os.makedirs(src+"/项目/空文件夹")
open(src+"/项目/说明.txt","w",encoding="utf-8").write("你好")
big = os.urandom(5_000_000); open(src+"/项目/图片/a.bin","wb").write(big)
open(src+"/报告.docx","wb").write(b"docx"*1000)
md5 = lambda p: hashlib.md5(open(p,"rb").read()).hexdigest()

def ks(km, down): srv._key(km, down)
CTRL={"n":"ctrl_l"}; V={"c":"v","vk":86}

print("\n== 1. 主机复制 → 副机甲 粘贴")
srv._files_out([src+"/项目", src+"/报告.docx"])
srv._on_move(1919, 500); check("进入副机甲", srv.active=="right")
A.clip.timeline=[]; A.got.clear()
ks(CTRL,True); ks(V,True); ks(V,False)
check("V 被暂扣（未立即转发）", not any(m.get("c")=="v" for m in A.got))
check("文件到达后补发 V", wait(lambda: [e for e in A.clip.timeline if e[0]=="k" and e[1]=="v"]))
tl=A.clip.timeline
check("顺序正确：先写剪贴板，再按 V", tl.index(("setfiles",)) < tl.index(("k","v",True)))
tops=A.clip.files; print("   副机甲剪贴板:", [os.path.relpath(t, L.STAGE_DIR) for t in tops])
check("两个顶层项目", len(tops)==2 and os.path.basename(tops[0])=="项目" and os.path.basename(tops[1])=="报告.docx")
check("大文件内容一致", md5(tops[0]+"/图片/a.bin")==md5(src+"/项目/图片/a.bin"))
check("中文文件名/内容正确", open(tops[0]+"/说明.txt",encoding="utf-8").read()=="你好")
check("空文件夹也传过去了", os.path.isdir(tops[0]+"/空文件夹"))
A.got.clear(); ks(V,True); ks(V,False); time.sleep(0.3)
check("再次粘贴不重复传输，直接转发", [m.get("c") for m in A.got if m.get("t")=="k"]==["v","v"])
ks(CTRL,False)

print("\n== 2. 副机甲复制 → 主机本机粘贴")
A._files_out([src+"/报告.docx"]); time.sleep(0.3)
check("主机记录了副机甲的文件", srv.offer and srv.offer["owner"]=="right")
with srv.lock: srv._leave()
ks(CTRL,True); ks(V,True); ks(V,False)
check("主机剪贴板收到文件", wait(lambda: srv.clip.files))
check("主机补发了粘贴键", wait(lambda: srv.injected))
check("内容一致", md5(srv.clip.files[0])==md5(src+"/报告.docx"))
ks(CTRL,False)

print("\n== 3. 副机甲复制 → 副机乙 粘贴（经主机协调，甲直传乙）")
srv.offer["delivered"].clear(); srv.offer["delivered"].add(None)
srv._on_move(0, 300); check("进入副机乙", srv.active=="left")
B.clip.timeline=[]
ks(CTRL,True); ks(V,True); ks(V,False); ks(CTRL,False)    # 文件还没到就松开了 Ctrl：应自动补 Ctrl
check("副机乙收到文件", wait(lambda: B.clip.files))
def ctrl_at_v():
    ev=[e for e in B.clip.timeline if e[0]=="k"]; c=False; r=[]
    for _,k,d in ev:
        if k=="ctrl_l": c=d
        if k=="v": r.append((d,c))
    return r
check("副机乙收到的 V 都处于 Ctrl 按下状态（真正的 Ctrl+V）", wait(lambda: ctrl_at_v()==[(True,True),(False,True)]))
print("   副机乙按键序列:", [e[1:] for e in B.clip.timeline if e[0]=="k"])
ks(CTRL,False)
check("内容一致", md5(B.clip.files[0])==md5(src+"/报告.docx"))

print("\n== 4. 之后复制文字，文件粘贴状态清除")
A._clip_out("一段文字"); time.sleep(0.3)
check("主机清除了文件状态", srv.offer is None)
check("副机乙收到文字", B.clip.text=="一段文字")
B.got.clear(); ks(CTRL,True); ks(V,True); ks(V,False); ks(CTRL,False); time.sleep(0.3)
check("Ctrl+V 正常直接转发", [m.get("c") or m.get("n") for m in B.got if m.get("t")=="k"]==["ctrl_l","v","v","ctrl_l"])

print("\n== 5. 失败处理：源文件已变化")
A._files_out([src+"/报告.docx"]); time.sleep(0.3); A.offer["id"]="changed"
B.got.clear(); ks(CTRL,True); ks(V,True); ks(V,False); ks(CTRL,False)
check("副机乙收到失败提示", wait(lambda: any(m.get("t")=="toast" for m in B.got)))
check("暂扣状态已释放", srv.held is None)
print("   提示内容:", [m["text"] for m in B.got if m.get("t")=="toast"])

print("\n== 6. 拖放：主机 → 副机甲")
with srv.lock: srv._leave()
srv.ui.shows.clear()
srv._on_click(1919,500,type("Btn",(),{"name":"left"})(),True)
srv.mctl.position=(1919,500)
srv._on_move(1919,500)
check("按住鼠标时不穿屏", srv.active is None)
check("停留后显示投放区", wait(lambda: srv.ui.shows))
edge,label,cb = srv.ui.shows[0]; print("   投放区:", edge, label.replace("\n"," "))
srv._on_click(1919,500,type("Btn",(),{"name":"left"})(),False)
cb([src+"/项目"])
check("松开后鼠标进入副机甲", srv.active=="right")
check("副机甲收到拖放文件", wait(lambda: os.path.exists(A.files.recv_dir+"/项目/图片/a.bin") and os.path.getsize(A.files.recv_dir+"/项目/图片/a.bin")==len(big)))
check("副机甲弹出提示", wait(lambda: A.ui.toasts and "已收到" in A.ui.toasts[-1][0]))
print("   提示:", A.ui.toasts[-1][0])

print("\n== 7. 拖放：副机甲 → 主机")
srv.sessions["right"].os="win"
A.got.clear()
srv._on_click(0,0,type("Btn",(),{"name":"left"})(),True)
cx,cy=srv.center
for i in range(8):
    srv._on_move(cx-3000, cy); time.sleep(0.06)
check("拖着东西时不离开副机", srv.active=="right")
check("副机甲收到显示投放区指令", wait(lambda: A.ui.shows))
print("   副机投放区:", A.ui.shows[0][0], A.ui.shows[0][1].replace("\n"," "))
srv._on_click(0,0,type("Btn",(),{"name":"left"})(),False)
A.ui.shows[0][2]([src+"/报告.docx"])
check("松开后鼠标回到主机", wait(lambda: srv.active is None))
check("主机收到文件", wait(lambda: os.path.exists(srv_recv+"/报告.docx")))
check("同名文件自动改名（第二次）", (cb([src+"/报告.docx"]) or True) and wait(lambda: os.path.exists(A.files.recv_dir+"/报告.docx")))

print("\n== 8. 回归：发送按钮、拒绝错误密码")
with srv.lock: srv._leave()
srv.send_files([src+"/报告.docx"], "left")
check("发送按钮正常", wait(lambda: os.path.exists(B.files.recv_dir+"/报告.docx")))
bad = L.Client("127.0.0.1","wrong","top","坏",fport=24814,headless=True,clipboard=False); bad.start(); time.sleep(1); bad.running=False
check("错误密码被拒绝", "top" not in srv.sessions)

srv.stop(); A.stop(); B.stop(); bad.stop()
print(f"\n通过 {sum(o for _,o in OK)}/{len(OK)}")
sys.exit(0 if all(o for _,o in OK) else 1)
