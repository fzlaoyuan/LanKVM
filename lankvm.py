#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LanKVM —— 局域网键鼠共享 / 穿屏 / 剪贴板同步 / 文件互传（纯软件，无需外置硬件）

  主机(Server)：接着真实键盘鼠标的电脑，鼠标移出屏幕边缘即进入副机。
  副机(Client)：被控制的电脑，接收键鼠事件并注入。

  文件复制粘贴：在任意一台电脑上 Ctrl+C 复制文件/文件夹，到另一台电脑 Ctrl+V 即可粘贴。
  拖放发送：按住文件拖到屏幕边缘停留片刻，在出现的蓝色区域松开，即发送到另一台电脑。

用法：
  python lankvm.py                      # 图形界面
  python lankvm.py server -p 密码        # 命令行主机
  python lankvm.py client -p 密码 -s right [-H 主机IP]
  python lankvm.py send -H 对方IP -p 密码 文件或文件夹...
"""
import argparse
import hashlib
import hmac
import json
import os
import pathlib
import platform
import queue
import secrets
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse

VERSION = "1.2.0"
AUTHOR = "老白玩AI"
WEBSITE = "https://laobaiai.com/"
CTRL_PORT = 24800
DISC_PORT = 24801
FILE_PORT = 24802
OS_NAME = {"Windows": "win", "Darwin": "mac"}.get(platform.system(), "linux")
RECV_DIR = os.path.join(os.path.expanduser("~"), "Downloads", "LanKVM")
STAGE_DIR = os.path.join(tempfile.gettempdir(), "LanKVM", "clip")
CONF_PATH = os.path.join(os.path.expanduser("~"), ".lankvm.json")

SIDES = {"right": "右侧", "left": "左侧", "top": "上方", "bottom": "下方"}
OPPOSITE = {"right": "left", "left": "right", "top": "bottom", "bottom": "top"}
CTRL_NAMES = {"ctrl", "ctrl_l", "ctrl_r"}
ALT_NAMES = {"alt", "alt_l", "alt_r", "alt_gr"}
CMD_NAMES = {"cmd", "cmd_l", "cmd_r"}
PASTE_MODS = CTRL_NAMES | CMD_NAMES
CTRL_CMD_SWAP = {"ctrl": "cmd", "ctrl_l": "cmd_l", "ctrl_r": "cmd_r",
                 "cmd": "ctrl", "cmd_l": "ctrl_l", "cmd_r": "ctrl_r"}
FONT = {"win": "Microsoft YaHei UI", "mac": "PingFang SC"}.get(OS_NAME, "Noto Sans CJK SC")

# ---------------------------------------------------------------- 日志
LOG_Q = queue.Queue()


def log(msg):
    line = time.strftime("%H:%M:%S ") + str(msg)
    try:
        if sys.stdout:
            print(line, flush=True)
    except Exception:
        pass
    LOG_Q.put(line)


# ---------------------------------------------------------------- 平台工具
def enable_dpi_awareness():
    """Windows 高分屏下取得真实像素坐标。"""
    if OS_NAME != "win":
        return
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def screen_bounds():
    """返回 (x, y, w, h)。Windows 取多显示器组成的虚拟屏幕。"""
    if OS_NAME == "win":
        import ctypes
        u = ctypes.windll.user32
        return (u.GetSystemMetrics(76), u.GetSystemMetrics(77),
                u.GetSystemMetrics(78), u.GetSystemMetrics(79))
    if OS_NAME == "mac":
        try:
            from Quartz import CGDisplayBounds, CGMainDisplayID
            b = CGDisplayBounds(CGMainDisplayID())
            return (0, 0, int(b.size.width), int(b.size.height))
        except Exception:
            pass
    else:
        try:
            from Xlib import display
            s = display.Display().screen()
            return (0, 0, s.width_in_pixels, s.height_in_pixels)
        except Exception:
            pass
    try:
        import tkinter
        r = tkinter.Tk()
        r.withdraw()
        w, h = r.winfo_screenwidth(), r.winfo_screenheight()
        r.destroy()
        return (0, 0, w, h)
    except Exception:
        return (0, 0, 1920, 1080)


def primary_center():
    if OS_NAME == "win":
        import ctypes
        u = ctypes.windll.user32
        return (u.GetSystemMetrics(0) // 2, u.GetSystemMetrics(1) // 2)
    x, y, w, h = screen_bounds()
    return (x + w // 2, y + h // 2)


def make_mac(password, nonce):
    return hmac.new(password.encode("utf-8"), nonce.encode("utf-8"), hashlib.sha256).hexdigest()


def local_ips():
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except Exception:
        pass
    ips.discard("127.0.0.1")
    return sorted(ips) or ["127.0.0.1"]


def open_folder(path):
    os.makedirs(path, exist_ok=True)
    if OS_NAME == "win":
        os.startfile(path)
    elif OS_NAME == "mac":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def reveal_files(paths):
    """在文件管理器中打开并选中第一个文件。"""
    if not paths:
        return
    p = paths[0]
    try:
        if OS_NAME == "win":
            subprocess.Popen(f'explorer /select,"{p}"')
        elif OS_NAME == "mac":
            subprocess.Popen(["open", "-R", p])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(p)])
    except Exception:
        pass


def describe(names, n):
    names = [str(x) for x in names][:2]
    s = "、".join(names)
    return f"{s} 等 {n} 项" if n > len(names) else s


# ---------------------------------------------------------------- 按键序列化
def key_to_msg(key):
    """把 pynput 的按键转成跨平台描述：特殊键用名字，普通键用字符(+同系统时的 vk)。"""
    from pynput import keyboard
    if isinstance(key, keyboard.Key):
        return {"n": key.name}
    d = {}
    vk = getattr(key, "vk", None)
    ch = getattr(key, "char", None)
    if vk is not None:
        d["vk"] = vk
    if OS_NAME == "win" and vk is not None and (0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A):
        ch = chr(vk).lower()                       # 按住 Ctrl 时 char 会变成控制字符
    elif ch and len(ch) == 1 and ord(ch) < 32:
        ch = chr(ord(ch) + 96)                     # Ctrl+C -> 'c'
    if ch and len(ch) == 1 and ch.isascii() and ch.isalpha():
        ch = ch.lower()                            # Shift 由修饰键单独传递
    if ch:
        d["c"] = ch
    return d


# ---------------------------------------------------------------- 网络连接
class Conn:
    """4 字节长度前缀 + JSON 的消息连接；post() 走后台发送线程，绝不阻塞输入钩子。"""

    def __init__(self, sock, writer=True):
        self.sock = sock
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        except OSError:
            pass
        self.wlock = threading.Lock()
        self.closed = False
        self.q = queue.Queue()
        if writer:
            threading.Thread(target=self._writer, daemon=True).start()

    def send(self, obj):
        data = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        with self.wlock:
            self.sock.sendall(struct.pack(">I", len(data)) + data)

    def post(self, obj):
        if not self.closed:
            self.q.put(obj)

    def _writer(self):
        while True:
            obj = self.q.get()
            if obj is None or self.closed:
                break
            try:
                self.send(obj)
            except Exception:
                self.close()
                break

    def recv(self):
        n = struct.unpack(">I", self.recvn(4))[0]
        if n > 64 * 1024 * 1024:
            raise ValueError("消息过大")
        return json.loads(self.recvn(n).decode("utf-8"))

    def recvn(self, n):
        buf = bytearray()
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("连接已断开")
            buf += chunk
        return bytes(buf)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.q.put(None)
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


# ---------------------------------------------------------------- Windows API（按需加载）
_W = None


def win_api():
    global _W
    if _W is not None:
        return _W
    import ctypes
    from ctypes import wintypes as wt

    class NS:
        pass

    w = NS()
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    sh = ctypes.WinDLL("shell32")
    vp = ctypes.c_void_p

    def fn(lib, name, res, *args):
        f = getattr(lib, name)
        f.restype = res
        f.argtypes = list(args)
        return f

    w.OpenClipboard = fn(u32, "OpenClipboard", wt.BOOL, wt.HWND)
    w.CloseClipboard = fn(u32, "CloseClipboard", wt.BOOL)
    w.EmptyClipboard = fn(u32, "EmptyClipboard", wt.BOOL)
    w.GetClipboardData = fn(u32, "GetClipboardData", vp, wt.UINT)
    w.SetClipboardData = fn(u32, "SetClipboardData", vp, wt.UINT, vp)
    w.IsClipboardFormatAvailable = fn(u32, "IsClipboardFormatAvailable", wt.BOOL, wt.UINT)
    w.GetClipboardSequenceNumber = fn(u32, "GetClipboardSequenceNumber", wt.DWORD)
    w.RegisterClipboardFormatW = fn(u32, "RegisterClipboardFormatW", wt.UINT, wt.LPCWSTR)
    w.CreateWindowExW = fn(u32, "CreateWindowExW", wt.HWND, wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                           ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                           wt.HWND, wt.HMENU, wt.HINSTANCE, vp)
    w.DestroyWindow = fn(u32, "DestroyWindow", wt.BOOL, wt.HWND)
    w.GetAsyncKeyState = fn(u32, "GetAsyncKeyState", ctypes.c_short, ctypes.c_int)
    w.SetCursorPos = fn(u32, "SetCursorPos", wt.BOOL, ctypes.c_int, ctypes.c_int)
    w.GetCursorPos = fn(u32, "GetCursorPos", wt.BOOL, ctypes.POINTER(wt.POINT))
    w.CallWindowProcW = fn(u32, "CallWindowProcW", ctypes.c_ssize_t, vp, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
    setlp = "SetWindowLongPtrW" if ctypes.sizeof(vp) == 8 else "SetWindowLongW"
    w.SetWindowLongPtrW = fn(u32, setlp, vp, wt.HWND, ctypes.c_int, vp)
    w.ChangeWindowMessageFilterEx = fn(u32, "ChangeWindowMessageFilterEx", wt.BOOL, wt.HWND, wt.UINT, wt.DWORD, vp)
    w.GlobalAlloc = fn(k32, "GlobalAlloc", vp, wt.UINT, ctypes.c_size_t)
    w.GlobalLock = fn(k32, "GlobalLock", vp, vp)
    w.GlobalUnlock = fn(k32, "GlobalUnlock", wt.BOOL, vp)
    w.GlobalFree = fn(k32, "GlobalFree", vp, vp)
    w.DragQueryFileW = fn(sh, "DragQueryFileW", wt.UINT, vp, wt.UINT, wt.LPWSTR, wt.UINT)
    w.DragAcceptFiles = fn(sh, "DragAcceptFiles", None, wt.HWND, wt.BOOL)
    w.DragFinish = fn(sh, "DragFinish", None, vp)
    w.WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
    w.POINT = wt.POINT
    w.ctypes = ctypes
    _W = w
    return w


class Waker:
    """被控制的电脑：用真实输入移动鼠标（能退出屏保、点亮熄灭的屏幕），
    进入时主动唤醒屏幕，可选防止自动睡眠，并检测是否处于锁屏界面。"""

    def __init__(self, keep_awake=True):
        self.ok = False
        self._stop = threading.Event()
        self._proc = None
        try:
            if OS_NAME == "win":
                self._win_init()
            self.ok = True
        except Exception as e:
            log(f"唤醒功能初始化失败：{e}")
        if keep_awake:
            threading.Thread(target=self._keep_loop, daemon=True).start()

    # ---- Windows
    def _win_init(self):
        import ctypes
        from ctypes import wintypes as wt
        ulp = ctypes.c_size_t

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                        ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ulp)]

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                        ("time", wt.DWORD), ("dwExtraInfo", ulp)]

        class HARDWAREINPUT(ctypes.Structure):
            _fields_ = [("uMsg", wt.DWORD), ("wParamL", wt.WORD), ("wParamH", wt.WORD)]

        class _U(ctypes.Union):
            _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", wt.DWORD), ("u", _U)]

        u = ctypes.WinDLL("user32", use_last_error=True)
        k = ctypes.WinDLL("kernel32")
        u.SendInput.argtypes = [wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
        u.SendInput.restype = wt.UINT
        u.GetSystemMetrics.argtypes = [ctypes.c_int]
        u.OpenInputDesktop.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
        u.OpenInputDesktop.restype = ctypes.c_void_p
        u.CloseDesktop.argtypes = [ctypes.c_void_p]
        u.GetUserObjectInformationW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p,
                                                wt.DWORD, ctypes.POINTER(wt.DWORD)]
        k.SetThreadExecutionState.argtypes = [wt.DWORD]
        k.SetThreadExecutionState.restype = wt.DWORD
        self._ct, self._wt, self._u, self._k = ctypes, wt, u, k
        self._INPUT, self._MOUSEINPUT = INPUT, MOUSEINPUT

    def _send_mouse(self, dx, dy, flags):
        inp = self._INPUT(type=0)                   # INPUT_MOUSE
        inp.u.mi = self._MOUSEINPUT(dx, dy, 0, flags, 0, 0)
        return self._u.SendInput(1, self._ct.byref(inp), self._ct.sizeof(self._INPUT))

    def move_abs(self, x, y):
        """Windows：用 SendInput 把光标移到虚拟桌面坐标 (x, y)。"""
        u = self._u
        vx, vy = u.GetSystemMetrics(76), u.GetSystemMetrics(77)
        vw, vh = max(2, u.GetSystemMetrics(78)), max(2, u.GetSystemMetrics(79))
        nx = int(round((x - vx) * 65535 / (vw - 1)))
        ny = int(round((y - vy) * 65535 / (vh - 1)))
        # MOUSEEVENTF_MOVE | MOUSEEVENTF_VIRTUALDESK | MOUSEEVENTF_ABSOLUTE
        return self._send_mouse(max(0, min(65535, nx)), max(0, min(65535, ny)), 0x0001 | 0x4000 | 0x8000)

    def wake(self):
        """点亮屏幕、退出屏保。"""
        try:
            if OS_NAME == "win":
                self._k.SetThreadExecutionState(0x00000001 | 0x00000002)   # SYSTEM | DISPLAY_REQUIRED
                self._send_mouse(1, 0, 0x0001)                              # 相对移动 1 像素再移回
                self._send_mouse(-1, 0, 0x0001)
            elif OS_NAME == "mac":
                subprocess.Popen(["caffeinate", "-u", "-t", "2"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                for cmd in (["xset", "dpms", "force", "on"], ["xdg-screensaver", "reset"]):
                    if shutil.which(cmd[0]):
                        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            log(f"唤醒屏幕失败：{e}")

    def is_locked(self):
        """Windows：当前输入桌面不是普通桌面（锁屏 / 登录界面 / UAC）时返回 True。"""
        if OS_NAME != "win":
            return False
        try:
            h = self._u.OpenInputDesktop(0, False, 0x0001)      # DESKTOP_READOBJECTS
            if not h:
                return True
            try:
                buf = self._ct.create_unicode_buffer(64)
                need = self._wt.DWORD(0)
                self._u.GetUserObjectInformationW(h, 2, buf, self._ct.sizeof(buf), self._ct.byref(need))
                return buf.value.lower() != "default"
            finally:
                self._u.CloseDesktop(h)
        except Exception:
            return False

    def _keep_loop(self):
        """防止电脑在被共享期间自动睡眠（允许屏幕照常熄灭）。"""
        try:
            if OS_NAME == "win":
                self._k.SetThreadExecutionState(0x80000000 | 0x00000001)   # CONTINUOUS | SYSTEM_REQUIRED
                self._stop.wait()
                self._k.SetThreadExecutionState(0x80000000)
            elif OS_NAME == "mac":
                self._proc = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())],
                                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            log(f"防睡眠设置失败：{e}")

    def stop(self):
        self._stop.set()
        if self._proc:
            try:
                self._proc.terminate()
            except Exception:
                pass


def _hdrop_files(w, hdrop):
    n = w.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)
    out = []
    for i in range(n):
        ln = w.DragQueryFileW(hdrop, i, None, 0)
        buf = w.ctypes.create_unicode_buffer(ln + 1)
        w.DragQueryFileW(hdrop, i, buf, ln + 1)
        if buf.value:
            out.append(buf.value)
    return out


class _WinClip:
    """打开 Windows 剪贴板（带重试；用临时窗口作为所有者，保证 SetClipboardData 可用）。"""

    def __init__(self):
        self.w = win_api()
        self.hwnd = None

    def __enter__(self):
        w = self.w
        self.hwnd = w.CreateWindowExW(0, "STATIC", None, 0, 0, 0, 0, 0, None, None, None, None)
        for _ in range(25):
            if w.OpenClipboard(self.hwnd):
                return self
            time.sleep(0.02)
        if self.hwnd:
            w.DestroyWindow(self.hwnd)
        raise OSError("剪贴板正被其他程序占用")

    def __exit__(self, *exc):
        self.w.CloseClipboard()
        if self.hwnd:
            self.w.DestroyWindow(self.hwnd)


# ---------------------------------------------------------------- 剪贴板中的文件
class ClipFiles:
    """读写系统剪贴板里的“文件列表”（资源管理器/访达里复制的文件）。"""
    CF_HDROP = 15

    def __init__(self):
        self.ok = False
        try:
            if OS_NAME == "win":
                self.w = win_api()
                self.fmt_effect = self.w.RegisterClipboardFormatW("Preferred DropEffect")
                self.ok = True
            elif OS_NAME == "mac":
                import AppKit
                self.ak = AppKit
                self.pb = AppKit.NSPasteboard.generalPasteboard()
                self.ok = True
            else:
                self.xclip = shutil.which("xclip")
                self.ok = bool(self.xclip)
        except Exception as e:
            log(f"文件剪贴板不可用：{e}")

    def signature(self):
        """剪贴板变化计数；返回 None 表示不支持（每次都读取）。"""
        if OS_NAME == "win":
            return int(self.w.GetClipboardSequenceNumber())
        if OS_NAME == "mac":
            return int(self.pb.changeCount())
        return None

    def get(self):
        if OS_NAME == "win":
            w = self.w
            if not w.IsClipboardFormatAvailable(self.CF_HDROP):
                return None
            with _WinClip():
                h = w.GetClipboardData(self.CF_HDROP)
                return (_hdrop_files(w, h) or None) if h else None
        if OS_NAME == "mac":
            ak = self.ak
            urls = self.pb.readObjectsForClasses_options_(
                [ak.NSURL], {ak.NSPasteboardURLReadingFileURLsOnlyKey: True})
            return [str(u.path()) for u in urls] if urls else None
        r = subprocess.run([self.xclip, "-selection", "clipboard", "-t", "TARGETS", "-o"],
                           capture_output=True, timeout=2)
        targets = r.stdout.decode("utf-8", "ignore").split()
        for t in ("text/uri-list", "x-special/gnome-copied-files"):
            if t in targets:
                out = subprocess.run([self.xclip, "-selection", "clipboard", "-t", t, "-o"],
                                     capture_output=True, timeout=2).stdout.decode("utf-8", "ignore")
                files = [urllib.parse.unquote(urllib.parse.urlparse(x.strip()).path)
                         for x in out.splitlines() if x.strip().startswith("file://")]
                return files or None
        return None

    def set(self, paths):
        paths = [os.path.abspath(p) for p in paths]
        if OS_NAME == "win":
            w = self.w
            files = ("\0".join(paths) + "\0\0").encode("utf-16-le")
            h = self._galloc(struct.pack("<Iiiii", 20, 0, 0, 0, 1) + files)   # DROPFILES, fWide=1
            eff = self._galloc(struct.pack("<I", 1))                          # DROPEFFECT_COPY
            with _WinClip():
                w.EmptyClipboard()
                if not w.SetClipboardData(self.CF_HDROP, h):
                    w.GlobalFree(h)
                    w.GlobalFree(eff)
                    raise OSError("写入剪贴板失败")
                if not w.SetClipboardData(self.fmt_effect, eff):
                    w.GlobalFree(eff)
            return
        if OS_NAME == "mac":
            self.pb.clearContents()
            self.pb.writeObjects_([self.ak.NSURL.fileURLWithPath_(p) for p in paths])
            return
        data = "\n".join(pathlib.Path(p).as_uri() for p in paths).encode("utf-8")
        subprocess.Popen([self.xclip, "-selection", "clipboard", "-t", "text/uri-list", "-i"],
                         stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL).communicate(data, timeout=5)

    def clear(self):
        if OS_NAME == "win":
            with _WinClip():
                self.w.EmptyClipboard()
        elif OS_NAME == "mac":
            self.pb.clearContents()
        else:
            subprocess.Popen([self.xclip, "-selection", "clipboard", "-i"], stdin=subprocess.PIPE,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).communicate(b"", timeout=5)

    def _galloc(self, data):
        w = self.w
        h = w.GlobalAlloc(0x0042, len(data))          # GMEM_MOVEABLE | GMEM_ZEROINIT
        if not h:
            raise MemoryError("GlobalAlloc 失败")
        p = w.GlobalLock(h)
        w.ctypes.memmove(p, data, len(data))
        w.GlobalUnlock(h)
        return h


def _norm_files(files):
    return frozenset(os.path.normcase(os.path.abspath(f)) for f in files) if files else None


# ---------------------------------------------------------------- 剪贴板同步（文本 + 文件）
class ClipSync:
    MAX = 1_000_000

    def __init__(self, on_text, on_files=None):
        self.on_text, self.on_files = on_text, on_files
        self.last = None
        self.last_files = None
        self.ours = None
        self.running = False
        try:
            import pyperclip
            self.pc = pyperclip
        except Exception:
            self.pc = None
        self.fb = None
        if on_files:
            fb = ClipFiles()
            self.fb = fb if fb.ok else None

    def start(self):
        if not self.pc and not self.fb:
            log("剪贴板同步不可用（缺少 pyperclip）")
            return
        if not self.pc:
            log("未安装 pyperclip，文本剪贴板同步已关闭")
        if not self.fb and self.on_files:
            log("本机不支持文件复制粘贴" + ("（需安装 xclip）" if OS_NAME == "linux" else ""))
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self.running = False

    def _loop(self):
        if self.pc:
            try:
                self.last = self.pc.paste()
            except Exception as e:
                log(f"文本剪贴板不可用：{e}")
                self.pc = None
        if self.fb:
            try:
                self.last_files = _norm_files(self.fb.get())
            except Exception:
                pass
        sig = None
        while self.running:
            time.sleep(0.35)
            if self.fb:
                try:
                    s = self.fb.signature()
                    if s is not None and s == sig:
                        continue                    # 剪贴板没有任何变化
                    sig = s
                    files = self.fb.get()
                    if files:
                        key = _norm_files(files)
                        if key != self.last_files:
                            self.last_files = key
                            self.last = None        # 之后再复制同样的文字也算新内容
                            if key != self.ours:
                                self.on_files(files)
                        continue
                    self.last_files = None
                except Exception:
                    pass
            if not self.pc:
                continue
            try:
                txt = self.pc.paste()
            except Exception:
                continue
            if txt and txt != self.last and len(txt) <= self.MAX:
                self.last = txt
                self.on_text(txt)

    def set(self, txt):
        if not self.pc or not txt:
            return
        self.last = txt
        try:
            self.pc.copy(txt)
        except Exception as e:
            log(f"写入剪贴板失败：{e}")

    def set_files(self, paths):
        if not self.fb:
            raise RuntimeError("本机不支持文件剪贴板")
        self.ours = _norm_files(paths)
        self.last_files = self.ours
        self.last = None
        self.fb.set(paths)

    def clear(self):
        self.last = ""
        try:
            if self.fb:
                self.fb.clear()
            elif self.pc:
                self.pc.copy("")
        except Exception:
            pass


# ---------------------------------------------------------------- 文件互传
_BAD_CHARS = '<>:"|?*' if OS_NAME == "win" else ""


def _safe_parts(name):
    parts = [p for p in str(name).replace("\\", "/").split("/") if p not in ("", ".", "..")]
    out = []
    for p in parts:
        p = "".join("_" if (c in _BAD_CHARS or ord(c) < 32) else c for c in p).rstrip(" .") or "_"
        out.append(p)
    return out or ["file"]


def _unique(path):
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    if os.path.isdir(path):
        base, ext = path, ""
    i = 1
    while os.path.exists(f"{base} ({i}){ext}"):
        i += 1
    return f"{base} ({i}){ext}"


def _expand(paths):
    """展开文件/文件夹 -> [(本地路径或 None(目录), 相对名称, 大小)]"""
    out = []
    for p in paths:
        p = os.path.abspath(p)
        if os.path.isdir(p):
            root = os.path.dirname(p.rstrip("/\\")) or p
            for dp, _, fs in os.walk(p):
                out.append((None, os.path.relpath(dp, root).replace("\\", "/"), 0))
                for f in fs:
                    full = os.path.join(dp, f)
                    try:
                        size = os.path.getsize(full)
                    except OSError:
                        continue
                    out.append((full, os.path.relpath(full, root).replace("\\", "/"), size))
        elif os.path.isfile(p):
            out.append((p, os.path.basename(p), os.path.getsize(p)))
    return out


class FileService:
    """接收端：每台机器都运行。
    mode=recv/drop 存入 ~/Downloads/LanKVM；mode=clip 存入临时目录供粘贴。"""

    def __init__(self, password, port=FILE_PORT, recv_dir=RECV_DIR, on_event=None):
        self.password, self.port, self.recv_dir = password, port, recv_dir
        self.on_event = on_event
        self.sock = None

    def start(self):
        self._cleanup()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("", self.port))
        self.sock.listen(8)
        threading.Thread(target=self._accept, daemon=True).start()

    def stop(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass

    @staticmethod
    def _cleanup():
        try:
            for d in os.listdir(STAGE_DIR):
                p = os.path.join(STAGE_DIR, d)
                if time.time() - os.path.getmtime(p) > 3 * 86400:
                    shutil.rmtree(p, ignore_errors=True)
        except OSError:
            pass

    def _accept(self):
        while True:
            try:
                s, addr = self.sock.accept()
            except OSError:
                break
            threading.Thread(target=self._handle, args=(s, addr), daemon=True).start()

    def _emit(self, *args):
        if self.on_event:
            try:
                self.on_event(*args)
            except Exception as e:
                log(f"文件事件处理失败：{e}")

    def _handle(self, sock, addr):
        conn = Conn(sock, writer=False)
        mode, tag = "recv", ""
        authed = False
        try:
            sock.settimeout(30)
            nonce = secrets.token_hex(16)
            conn.send({"t": "challenge", "nonce": nonce})
            a = conn.recv()
            if not hmac.compare_digest(str(a.get("mac", "")), make_mac(self.password, nonce)):
                conn.send({"t": "reject", "reason": "配对密码错误"})
                log(f"拒绝来自 {addr[0]} 的文件（密码错误）")
                return
            authed = True
            conn.send({"t": "ok"})
            hdr = conn.recv()
            count = int(hdr.get("count", 0))
            mode = hdr.get("mode", "recv")
            tag = str(hdr.get("tag", ""))[:64]
            sender = str(hdr.get("from") or addr[0])
            if mode == "clip":
                base = os.path.join(STAGE_DIR, "".join(_safe_parts(tag or "x")) + "-" + secrets.token_hex(3))
            else:
                base = self.recv_dir
            os.makedirs(base, exist_ok=True)
            self._emit("start", mode, tag, [], sender, int(hdr.get("size", 0)), count)
            topmap, tops = {}, []

            def place(rel, is_dir):
                parts = _safe_parts(rel)
                if parts[0] not in topmap:
                    d = _unique(os.path.join(base, parts[0]))
                    topmap[parts[0]] = d
                    tops.append(d)
                if len(parts) == 1:
                    return topmap[parts[0]]
                p = os.path.join(topmap[parts[0]], *parts[1:])
                return p if is_dir else _unique(p)

            nfiles, total = 0, 0
            for _ in range(count):
                meta = conn.recv()
                if meta.get("dir"):
                    os.makedirs(place(meta["name"], True), exist_ok=True)
                    continue
                size = int(meta["size"])
                path = place(meta["name"], False)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                remaining = size
                with open(path, "wb") as f:
                    while remaining:
                        chunk = sock.recv(min(1 << 20, remaining))
                        if not chunk:
                            raise ConnectionError("传输中断")
                        f.write(chunk)
                        remaining -= len(chunk)
                nfiles += 1
                total += size
            conn.send({"t": "done"})
            where = "临时目录（供粘贴）" if mode == "clip" else self.recv_dir
            log(f"收到 {sender} 发来的 {nfiles} 个文件（{total / 1048576:.1f} MB）→ {where}")
            self._emit("done", mode, tag, tops, sender, total, nfiles)
        except Exception as e:
            log(f"接收文件失败：{e}")
            if authed:
                self._emit("fail", mode, tag, [], "", 0, 0)
        finally:
            conn.close()


def send_files(host, port, password, paths, progress=None, mode="recv", tag="", sender=""):
    items = _expand(paths)
    if not items:
        raise ValueError("没有可发送的文件")
    total = sum(sz for _, _, sz in items)
    sock = socket.create_connection((host, port), timeout=10)
    conn = Conn(sock, writer=False)
    try:
        ch = conn.recv()
        conn.send({"t": "auth", "mac": make_mac(password, ch["nonce"])})
        r = conn.recv()
        if r.get("t") != "ok":
            raise PermissionError(r.get("reason", "被拒绝"))
        conn.send({"t": "files", "count": len(items), "mode": mode, "tag": tag,
                   "from": sender, "size": total})
        sent, nfiles = 0, 0
        for path, rel, size in items:
            if path is None:
                conn.send({"name": rel, "dir": True})
                continue
            conn.send({"name": rel, "size": size})
            with open(path, "rb") as f:
                remaining = size
                while remaining:
                    chunk = f.read(min(1 << 20, remaining))
                    if not chunk:
                        raise IOError(f"读取失败（文件正在变化？）：{path}")
                    sock.sendall(chunk)
                    remaining -= len(chunk)
                    sent += len(chunk)
                    if progress:
                        progress(sent / (total or 1))
            nfiles += 1
        sock.settimeout(300)
        conn.recv()
        return nfiles, total
    finally:
        conn.close()


# ---------------------------------------------------------------- 公共
class Node:
    ui = None
    headless = False

    def _toast(self, text, paths=None, quiet=False):
        if not quiet:
            log(text)
        if self.ui:
            self.ui.toast(text, paths)

    def _received(self, mode, tops, sender, size, n):
        self._toast(f"已收到 {sender} 发来的 {n} 个文件（{size / 1048576:.1f} MB）", tops, quiet=True)
        if mode == "drop" and not self.headless:
            reveal_files(tops)


# ---------------------------------------------------------------- 主机
class Session:
    def __init__(self, conn, addr, hello):
        self.conn = conn
        self.ip = addr[0]
        self.name = hello.get("name") or addr[0]
        self.os = hello.get("os", "?")
        self.side = hello["side"]
        self.bounds = tuple(int(v) for v in hello["bounds"])
        self.fport = int(hello.get("fport", FILE_PORT))


class Server(Node):
    role = "server"

    def __init__(self, name, password, port=CTRL_PORT, fport=FILE_PORT, clipboard=True, headless=False):
        self.name, self.password, self.port, self.fport = name, password, port, fport
        self.clipboard, self.headless = clipboard, headless
        self.lock = threading.RLock()
        self.sessions = {}
        self.active = None
        self.locked = False
        self.mods = set()
        self.vx = self.vy = 0
        self.running = False
        self.ml = self.kl = None
        self.lsock = None
        self.files = None
        self.clip = None
        self.kctl = None
        # 文件复制粘贴
        self.offer = None              # 当前剪贴板里的文件：{"id","owner"(None=本机),"paths","names","n","delivered"}
        self.held = None               # 等待文件到达的那次 Ctrl+V
        self._v_held_remote = False
        self._v_swallow = False
        self.injected = []             # 无界面测试用
        # 拖放
        self.local_btn, self.remote_btn = set(), set()
        self.zone = None               # 本机边缘投放区所在侧
        self.zone_remote = None        # 副机边缘投放区所在侧
        self._zone_t0 = None
        self._zone_pending = False

    # ---- 生命周期
    def start(self):
        self.bounds = screen_bounds()
        self.center = primary_center()
        self.running = True
        self.lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.lsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.lsock.bind(("", self.port))
        self.lsock.listen(8)
        threading.Thread(target=self._accept, daemon=True).start()
        threading.Thread(target=self._beacon, daemon=True).start()
        self.files = FileService(self.password, self.fport, on_event=self._file_event)
        self.files.start()
        if self.clipboard:
            self.clip = ClipSync(self._clip_out, self._files_out)
            self.clip.start()
        if not self.headless:
            from pynput import keyboard, mouse
            self.mctl = mouse.Controller()
            self.kctl = keyboard.Controller()
            self.switch_q = queue.Queue()
            threading.Thread(target=self._switcher, daemon=True).start()
            self.switch_q.put(False)
        log(f"主机已启动：{self.name}  IP {', '.join(local_ips())}  端口 {self.port}  屏幕 {self.bounds}")
        log("等待副机连接…  热键：Ctrl+Alt+H 立即回到本机，Ctrl+Alt+L 锁定/解锁本机")

    def stop(self):
        with self.lock:
            self._leave()
            self._hide_local_zone(0)
        self.running = False
        for s in list(self.sessions.values()):
            s.conn.close()
        try:
            self.lsock.close()
        except Exception:
            pass
        if self.files:
            self.files.stop()
        if self.clip:
            self.clip.stop()
        log("主机已停止")

    def status(self):
        with self.lock:
            return {"role": "server", "active": self.active, "locked": self.locked,
                    "pasting": bool(self.held),
                    "peers": [(s.side, s.name, s.ip, s.os) for s in self.sessions.values()]}

    def _peer_name(self, side):
        if side is None:
            return self.name
        s = self.sessions.get(side)
        return s.name if s else SIDES.get(side, side)

    # ---- 网络
    def _beacon(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        msg = json.dumps({"app": "lankvm", "name": self.name, "port": self.port}).encode()
        while self.running:
            try:
                s.sendto(msg, ("255.255.255.255", DISC_PORT))
            except OSError:
                pass
            time.sleep(2)
        s.close()

    def _accept(self):
        while self.running:
            try:
                sock, addr = self.lsock.accept()
            except OSError:
                break
            threading.Thread(target=self._handle, args=(sock, addr), daemon=True).start()

    def _handle(self, sock, addr):
        conn = Conn(sock)
        s = None
        try:
            sock.settimeout(10)
            nonce = secrets.token_hex(16)
            conn.send({"t": "challenge", "nonce": nonce, "v": VERSION, "name": self.name})
            hello = conn.recv()
            if hello.get("t") != "hello" or not hmac.compare_digest(
                    str(hello.get("mac", "")), make_mac(self.password, nonce)):
                conn.send({"t": "reject", "reason": "配对密码错误"})
                log(f"拒绝 {addr[0]}：配对密码错误")
                return
            side = hello.get("side")
            with self.lock:
                if side not in SIDES:
                    conn.send({"t": "reject", "reason": "位置参数无效"})
                    return
                if side in self.sessions:
                    conn.send({"t": "reject", "reason": f"主机{SIDES[side]}已有副机"})
                    log(f"拒绝 {addr[0]}：{SIDES[side]}已被占用")
                    return
                s = Session(conn, addr, hello)
                self.sessions[side] = s
            conn.send({"t": "welcome", "name": self.name, "os": OS_NAME, "fport": self.fport})
            sock.settimeout(None)
            log(f"副机已连接：{s.name}（{s.ip}，{s.os}）位于主机{SIDES[side]}，屏幕 {s.bounds[2]}x{s.bounds[3]}")
            while self.running:
                msg = conn.recv()
                t = msg.get("t")
                if t == "clip":
                    self._clip_in(msg.get("text", ""), s)
                elif t == "cfiles":
                    self._offer_remote(s, msg)
                elif t == "cready":
                    self._paste_ready(s.side, str(msg.get("id")))
                elif t == "cfail":
                    self._paste_fail(str(msg.get("id")), msg.get("err", "失败"))
                elif t == "dropped":
                    with self.lock:
                        if self.active == s.side:
                            self._leave()
                elif t == "toast":
                    self._toast(str(msg.get("text", ""))[:300])
        except Exception as e:
            if self.running and s:
                log(f"副机 {s.name} 断开：{e}")
        finally:
            if s:
                with self.lock:
                    if self.active == s.side:
                        self._leave()
                    if self.sessions.get(s.side) is s:
                        del self.sessions[s.side]
                    if self.offer and self.offer["owner"] == s.side:
                        self.offer = None
                    h = self.held
                if h and h["target"] == s.side:
                    self._paste_fail(h["id"], "目标电脑已断开")
            conn.close()

    # ---- 文本剪贴板
    def _clip_out(self, txt):
        with self.lock:
            self.offer = None
        for s in list(self.sessions.values()):
            s.conn.post({"t": "clip", "text": txt})

    def _clip_in(self, txt, src):
        with self.lock:
            self.offer = None
        if self.clip:
            self.clip.set(txt)
        for s in list(self.sessions.values()):
            if s is not src:
                s.conn.post({"t": "clip", "text": txt})

    # ---- 文件复制粘贴
    def _files_out(self, files):
        names = [os.path.basename(f.rstrip("/\\")) or f for f in files]
        with self.lock:
            self.offer = {"id": secrets.token_hex(6), "owner": None, "paths": list(files),
                          "names": names, "n": len(files), "delivered": set()}
        if self.sessions:
            log(f"本机复制了 {describe(names, len(files))}，可在其他电脑上 Ctrl+V 粘贴")

    def _offer_remote(self, s, msg):
        names = [str(x) for x in msg.get("names", [])]
        n = int(msg.get("n", len(names)))
        with self.lock:
            self.offer = {"id": str(msg.get("id")), "owner": s.side, "paths": None,
                          "names": names, "n": n, "delivered": set()}
        log(f"{s.name} 复制了 {describe(names, n)}，可在其他电脑上 Ctrl+V 粘贴")
        if OS_NAME != "win" and self.clip:
            self.clip.clear()          # 非 Windows 主机无法拦截单个按键：先清空，避免粘贴出旧内容

    def _begin_paste(self, target, mod, vkm):
        """目标电脑按下了粘贴键。若剪贴板里是别的电脑的文件，则开始拉取并返回 True（暂扣这次粘贴）。"""
        with self.lock:
            o = self.offer
            if not o or o["owner"] == target or target in o["delivered"]:
                return False
            if self.held:
                return self.held["target"] == target     # 同一台电脑重复按：继续暂扣；其他电脑照常
            if target is not None and target not in self.sessions:
                return False
            if o["owner"] is not None and o["owner"] not in self.sessions:
                self.offer = None
                return False
            h = {"id": o["id"], "target": target, "mod": mod, "vkm": vkm}
            self.held = h
            o = dict(o)
        threading.Thread(target=self._pull, args=(o, target, h), daemon=True).start()
        return True

    def _pull(self, o, target, h):
        log(f"粘贴：正在把 {describe(o['names'], o['n'])} 从 {self._peer_name(o['owner'])} "
            f"传到 {self._peer_name(target)} …")
        try:
            if o["owner"] is None:
                s = self.sessions[target]
                send_files(s.ip, s.fport, self.password, o["paths"], mode="clip", tag=o["id"], sender=self.name)
            else:
                own = self.sessions[o["owner"]]
                if target is None:
                    host, port = "", self.fport
                else:
                    t = self.sessions[target]
                    host, port = t.ip, t.fport
                own.conn.post({"t": "cpull", "id": o["id"], "host": host, "port": port})
        except Exception as e:
            self._paste_fail(o["id"], str(e))
            return
        for _ in range(1800):                    # 最长等 15 分钟
            time.sleep(0.5)
            if self.held is not h:
                return
        self._paste_fail(o["id"], "等待超时")

    def _paste_ready(self, target, oid):
        act = None
        with self.lock:
            o = self.offer
            if o and o["id"] == oid:
                o["delivered"].add(target)
            h = self.held
            if h and h["id"] == oid and h["target"] == target:
                self.held = None
                act = h
        if act:
            log(f"粘贴：文件已到达 {self._peer_name(target)}")
            threading.Thread(target=self._do_paste, args=(act,), daemon=True).start()

    def _do_paste(self, h):
        time.sleep(0.05)
        mod = h["mod"]
        held_now = bool(self.mods & PASTE_MODS)
        if h["target"] is None:
            if self.headless:
                self.injected.append(("paste", held_now))
                return
            from pynput import keyboard
            v = keyboard.KeyCode.from_char("v")
            mkey = keyboard.Key.cmd if mod in CMD_NAMES else keyboard.Key.ctrl
            try:
                if held_now:
                    self.kctl.press(v)
                    self.kctl.release(v)
                else:
                    with self.kctl.pressed(mkey):
                        self.kctl.press(v)
                        self.kctl.release(v)
            except Exception as e:
                log(f"模拟粘贴失败：{e}")
            return
        s = self.sessions.get(h["target"])
        if not s:
            return
        vkm = h.get("vkm") or {"c": "v"}
        seq = []
        if not held_now:
            seq.append({"t": "k", "d": True, "os": OS_NAME, "n": mod})
        seq += [{"t": "k", "d": True, "os": OS_NAME, **vkm}, {"t": "k", "d": False, "os": OS_NAME, **vkm}]
        if not held_now:
            seq.append({"t": "k", "d": False, "os": OS_NAME, "n": mod})
        for m in seq:
            s.conn.post(m)

    def _paste_fail(self, oid, err):
        tgt = "?"
        with self.lock:
            h = self.held
            if h and h["id"] == oid:
                self.held = None
                tgt = h["target"]
        if tgt == "?":
            return
        text = f"粘贴文件失败：{err}"
        if tgt is None:
            self._toast(text)
        else:
            log(text)
            s = self.sessions.get(tgt)
            if s:
                s.conn.post({"t": "toast", "text": text})

    def _file_event(self, event, mode, tag, tops, sender, size, n):
        if mode == "clip":
            if event == "start" and size > 20 * 1048576:
                self._toast(f"正在从 {sender} 获取 {n} 项（{size / 1048576:.0f} MB）…", quiet=True)
            elif event == "done":
                try:
                    if not self.clip:
                        raise RuntimeError("本机未开启剪贴板同步")
                    self.clip.set_files(tops)
                    self._paste_ready(None, tag)
                except Exception as e:
                    self._paste_fail(tag, str(e))
            elif event == "fail":
                self._paste_fail(tag, "传输中断")
            return
        if event == "start" and mode == "drop":
            self._toast(f"正在接收 {sender} 拖过来的文件…", quiet=True)
        elif event == "done":
            self._received(mode, tops, sender, size, n)

    def send_files(self, paths, side=None, progress=None):
        with self.lock:
            s = self.sessions.get(side) if side else None
            if not s:
                s = self.sessions.get(self.active) if self.active else None
            if not s and len(self.sessions) == 1:
                s = next(iter(self.sessions.values()))
        if not s:
            raise RuntimeError("请先选择要发送到的副机")
        n, total = send_files(s.ip, s.fport, self.password, paths, progress, sender=self.name)
        log(f"已发送 {n} 个文件（{total / 1048576:.1f} MB）到 {s.name}")

    def toggle_lock(self):
        with self.lock:
            self.locked = not self.locked
            log("已锁定在本机（不会穿屏）" if self.locked else "已解除锁定")

    # ---- 拖放
    def _local_zone_try(self, side):
        if self.zone or self._zone_pending or not self.ui or not getattr(self.ui, "zones", False):
            return
        self._zone_pending = True

        def check():
            try:
                time.sleep(0.3)
                with self.lock:
                    if not self.local_btn or self.active is not None or self.zone:
                        return
                    try:
                        x, y = self.mctl.position
                    except Exception:
                        return
                    if self._edge_hit(x, y, 3) != side or side not in self.sessions:
                        return
                    self.zone = side
                    name = self.sessions[side].name
                self.ui.zone_show(side, f"松开鼠标\n发送到\n{name}", lambda files: self.drop_local(files, side))
            finally:
                self._zone_pending = False
        threading.Thread(target=check, daemon=True).start()

    def _hide_local_zone(self, delay):
        if self.zone:
            self.zone = None
            if self.ui:
                self.ui.zone_hide(delay)

    def drop_local(self, files, side):
        """在本机边缘投放区松开了文件：发送到该侧副机，并让鼠标进入那台电脑。"""
        with self.lock:
            s = self.sessions.get(side)
            self.zone = None
            if not s:
                return
            if self.active is None and not self.locked:
                try:
                    x, y = self.mctl.position
                except Exception:
                    x, y = self.center
                self._enter(side, x, y)
        threading.Thread(target=self._send_drop, args=(s, files), daemon=True).start()

    def _send_drop(self, s, files):
        try:
            n, total = send_files(s.ip, s.fport, self.password, files, mode="drop", sender=self.name)
            log(f"拖放：已发送 {n} 个文件（{total / 1048576:.1f} MB）到 {s.name}")
        except Exception as e:
            self._toast(f"拖放发送失败：{e}")

    # ---- 输入监听（进入副机时切换为“拦截模式”）
    def _switcher(self):
        from pynput import keyboard, mouse
        cur = None
        while self.running:
            try:
                sup = self.switch_q.get(timeout=0.5)
            except queue.Empty:
                continue
            while not self.switch_q.empty():
                sup = self.switch_q.get_nowait()
            if sup == cur:
                continue
            self._stop_listeners()
            extra = {"win32_event_filter": self._kfilter} if OS_NAME == "win" and not sup else {}
            self.ml = mouse.Listener(on_move=self._on_move, on_click=self._on_click,
                                     on_scroll=self._on_scroll, suppress=sup)
            self.kl = keyboard.Listener(on_press=self._on_press, on_release=self._on_release,
                                        suppress=sup, **extra)
            self.ml.start()
            self.kl.start()
            cur = sup
        self._stop_listeners()

    def _stop_listeners(self):
        for lis in (self.ml, self.kl):
            if lis:
                try:
                    lis.stop()
                except Exception:
                    pass
        self.ml = self.kl = None

    def _kfilter(self, msg, data):
        """Windows 本机键盘钩子过滤器：本机粘贴别的电脑的文件时，先吞掉这次 V，等文件到了再补发。"""
        if self._kdecide(msg, data):
            self.kl.suppress_event()
        return True

    def _kdecide(self, msg, data):
        try:
            if data.vkCode != 0x56 or (data.flags & 0x12):      # 只看 V；忽略程序注入的按键
                return False
            if msg in (0x100, 0x104):                           # 按下
                if self._v_swallow:
                    return True                                 # 按住不放的自动重复
                w = win_api()
                if (w.GetAsyncKeyState(0x11) & 0x8000) and not (w.GetAsyncKeyState(0x12) & 0x8000):
                    if self.active is None and self._begin_paste(None, "ctrl", None):
                        self._v_swallow = True
                        return True
                return False
            if self._v_swallow:                                 # 对应的抬起
                self._v_swallow = False
                return True
        except Exception:
            pass
        return False

    def _set_local_pos(self, pos):
        try:
            self.mctl.position = pos
        except Exception:
            pass

    def _edge_hit(self, x, y, margin=1):
        bx, by, bw, bh = self.bounds
        for side in self.sessions:
            if ((side == "right" and x >= bx + bw - margin) or (side == "left" and x <= bx + margin - 1) or
                    (side == "bottom" and y >= by + bh - margin) or (side == "top" and y <= by + margin - 1)):
                return side
        return None

    def _enter(self, side, x, y):
        s = self.sessions[side]
        _, _, cw, ch = s.bounds
        bx, by, bw, bh = self.bounds
        fx = min(1.0, max(0.0, (x - bx) / max(1, bw - 1)))
        fy = min(1.0, max(0.0, (y - by) / max(1, bh - 1)))
        self.vx, self.vy = {
            "right": (1, int(fy * (ch - 1))),
            "left": (cw - 2, int(fy * (ch - 1))),
            "bottom": (int(fx * (cw - 1)), 1),
            "top": (int(fx * (cw - 1)), ch - 2),
        }[side]
        self.active = side
        self.remote_btn = set()
        self.zone_remote = self._zone_t0 = None
        self._hide_local_zone(0)
        self._set_local_pos(self.center)
        s.conn.post({"t": "enter", "x": self.vx, "y": self.vy})
        if not self.headless:
            self.switch_q.put(True)
        log(f"→ 进入副机 {s.name}")

    def _leave(self):
        side = self.active
        if side is None:
            return
        s = self.sessions.get(side)
        self.active = None
        self.remote_btn = set()
        self.zone_remote = self._zone_t0 = None
        self._v_held_remote = False
        bx, by, bw, bh = self.bounds
        pos = self.center
        if s:
            s.conn.post({"t": "leave"})
            _, _, cw, ch = s.bounds
            fx = min(1.0, max(0.0, self.vx / max(1, cw - 1)))
            fy = min(1.0, max(0.0, self.vy / max(1, ch - 1)))
            pos = {
                "right": (bx + bw - 3, by + int(fy * (bh - 1))),
                "left": (bx + 2, by + int(fy * (bh - 1))),
                "bottom": (bx + int(fx * (bw - 1)), by + bh - 3),
                "top": (bx + int(fx * (bw - 1)), by + 2),
            }[side]
        self._set_local_pos(pos)
        if not self.headless:
            self.switch_q.put(False)
        log("← 回到本机")

    def _on_move(self, x, y, injected=False):
        with self.lock:
            if self.active is None:
                if self.locked or injected:
                    return
                side = self._edge_hit(x, y)
                if side:
                    if self.local_btn:              # 按着鼠标（拖东西）时不穿屏，改为显示投放区
                        self._local_zone_try(side)
                        return
                    self._enter(side, x, y)
                return
            s = self.sessions.get(self.active)
            if not s:
                self._leave()
                return
            cx, cy = self.center
            dx, dy = int(x - cx), int(y - cy)
            if dx == 0 and dy == 0:
                return
            self._set_local_pos(self.center)          # 本机光标始终拉回中心，只取位移量
            self.vx += dx
            self.vy += dy
            _, _, cw, ch = s.bounds
            side = self.active
            if ((side == "right" and self.vx < 0) or (side == "left" and self.vx >= cw) or
                    (side == "bottom" and self.vy < 0) or (side == "top" and self.vy >= ch)):
                if self.remote_btn and s.os == "win":     # 在副机上拖着东西碰到回程边缘
                    self.vx = max(0, min(cw - 1, self.vx))
                    self.vy = max(0, min(ch - 1, self.vy))
                    s.conn.post({"t": "m", "x": self.vx, "y": self.vy})
                    if not self.zone_remote:
                        now = time.time()
                        if self._zone_t0 is None:
                            self._zone_t0 = now
                        elif now - self._zone_t0 > 0.25:
                            self.zone_remote = side
                            s.conn.post({"t": "zone", "edge": OPPOSITE[side],
                                         "label": f"松开鼠标\n发送到\n{self.name}"})
                    return
                self._leave()
                return
            if not self.zone_remote:
                self._zone_t0 = None
            self.vx = max(0, min(cw - 1, self.vx))
            self.vy = max(0, min(ch - 1, self.vy))
            s.conn.post({"t": "m", "x": self.vx, "y": self.vy})

    def _remote(self):
        return self.sessions.get(self.active) if self.active else None

    def _on_click(self, x, y, button, pressed, injected=False):
        name = getattr(button, "name", str(button))
        with self.lock:
            s = self._remote()
            if s:
                (self.remote_btn.add if pressed else self.remote_btn.discard)(name)
                s.conn.post({"t": "b", "b": name, "d": pressed})
                if not self.remote_btn:
                    self.zone_remote = self._zone_t0 = None
                return
            if injected:
                return
            (self.local_btn.add if pressed else self.local_btn.discard)(name)
            if not self.local_btn:
                self._hide_local_zone(0.8)          # 稍等片刻，让“松开即投放”先被处理

    def _on_scroll(self, x, y, dx, dy, injected=False):
        s = self._remote()
        if s:
            s.conn.post({"t": "w", "dx": dx, "dy": dy})

    def _on_press(self, key, injected=False):
        self._key(key_to_msg(key), True, injected)

    def _on_release(self, key, injected=False):
        self._key(key_to_msg(key), False, injected)

    def _key(self, km, down, injected=False):
        n = km.get("n")
        if n:
            (self.mods.add if down else self.mods.discard)(n)
        if down:
            hot = self.mods & CTRL_NAMES and self.mods & ALT_NAMES
            if hot and km.get("c") == "h" and self.active:
                with self.lock:
                    self._leave()
                return
            if hot and km.get("c") == "l" and not self.active:
                self.toggle_lock()
                return
        if km.get("c") == "v" and not n and not injected:
            pm = self.mods & PASTE_MODS
            if down and pm and not (self.mods & ALT_NAMES):
                target = self.active
                vkm = {k: km[k] for k in ("c", "vk") if k in km}
                if target is not None:
                    if self._begin_paste(target, sorted(pm)[0], vkm):
                        self._v_held_remote = True
                        return
                elif OS_NAME != "win":           # Windows 本机由钩子过滤器处理
                    self._begin_paste(None, sorted(pm)[0], None)
            elif not down and self._v_held_remote:
                self._v_held_remote = False
                return
        s = self._remote()
        if s:
            s.conn.post({"t": "k", "d": down, "os": OS_NAME, **km})


# ---------------------------------------------------------------- 副机
class Client(Node):
    role = "client"

    def __init__(self, host, password, side, name, port=CTRL_PORT, fport=FILE_PORT,
                 clipboard=True, map_ctrl_cmd=False, headless=False, keep_awake=True):
        self.host, self.password, self.side, self.name = (host or "").strip(), password, side, name
        self.keep_awake = keep_awake
        self.waker = None
        self._last_wake = 0.0
        self.port, self.fport, self.clipboard = port, fport, clipboard
        self.map_ctrl_cmd, self.headless = map_ctrl_cmd, headless
        self.bounds = screen_bounds()
        self.running = False
        self.conn = None
        self.server_ip = self.server_name = None
        self.server_fport = FILE_PORT
        self.controlled = False
        self.pressed_keys, self.pressed_btns = set(), set()
        self.files = self.clip = None
        self.offer = None
        self.zone_on = False

    def start(self):
        self.running = True
        self.files = FileService(self.password, self.fport, on_event=self._file_event)
        self.files.start()
        if self.clipboard:
            self.clip = ClipSync(self._clip_out, self._files_out)
            self.clip.start()
        if not self.headless:
            from pynput import keyboard, mouse
            self.mctl, self.kctl = mouse.Controller(), keyboard.Controller()
            self.waker = Waker(self.keep_awake)
        threading.Thread(target=self._run, daemon=True).start()
        log(f"副机已启动：{self.name}（位于主机{SIDES[self.side]}）屏幕 {self.bounds}  IP {', '.join(local_ips())}")

    def stop(self):
        self.running = False
        if self.conn:
            self.conn.close()
        if self.files:
            self.files.stop()
        if self.clip:
            self.clip.stop()
        self._zone_off(0)
        self._release_all()
        if self.waker:
            self.waker.stop()
        log("副机已停止")

    def status(self):
        return {"role": "client", "server": self.server_name, "ip": self.server_ip,
                "connected": bool(self.conn and not self.conn.closed), "controlled": self.controlled}

    def _run(self):
        while self.running:
            host = self.host or self._discover()
            if not host:
                continue
            try:
                self._session(host)
            except Exception as e:
                if self.running:
                    log(f"连接主机 {host} 失败/断开：{e}")
            self._release_all()
            self.conn, self.controlled = None, False
            if self.running:
                time.sleep(3)

    def _discover(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("", DISC_PORT))
            s.settimeout(3)
            data, addr = s.recvfrom(4096)
            j = json.loads(data.decode())
            if j.get("app") == "lankvm":
                self.port = int(j.get("port", self.port))
                log(f"发现主机 {j.get('name')}（{addr[0]}）")
                return addr[0]
        except socket.timeout:
            pass
        except Exception as e:
            log(f"自动发现失败：{e}（可手动填写主机 IP）")
            time.sleep(3)
        finally:
            s.close()
        return None

    def _session(self, host):
        sock = socket.create_connection((host, self.port), timeout=5)
        conn = Conn(sock)
        try:
            ch = conn.recv()
            conn.send({"t": "hello", "name": self.name, "os": OS_NAME, "side": self.side,
                       "bounds": list(self.bounds), "fport": self.fport, "v": VERSION,
                       "mac": make_mac(self.password, ch["nonce"])})
            r = conn.recv()
            if r.get("t") != "welcome":
                raise PermissionError("被主机拒绝：" + r.get("reason", ""))
            sock.settimeout(None)
            self.conn, self.server_ip = conn, host
            self.server_name, self.server_fport = r.get("name", host), int(r.get("fport", FILE_PORT))
            log(f"已连接主机 {self.server_name}（{host}）")
            threading.Thread(target=self._pinger, args=(conn,), daemon=True).start()
            while self.running:
                self._apply(conn.recv())
        finally:
            conn.close()

    def _pinger(self, conn):
        while not conn.closed and self.running:
            conn.post({"t": "ping"})
            time.sleep(5)

    def _post(self, obj):
        if self.conn:
            self.conn.post(obj)

    # ---- 剪贴板
    def _clip_out(self, txt):
        self.offer = None
        self._post({"t": "clip", "text": txt})

    def _files_out(self, files):
        names = [os.path.basename(f.rstrip("/\\")) or f for f in files]
        self.offer = {"id": secrets.token_hex(6), "paths": list(files)}
        self._post({"t": "cfiles", "id": self.offer["id"], "names": names[:20], "n": len(files)})

    def _serve_pull(self, msg):
        o = self.offer
        oid = str(msg.get("id"))
        if not o or o["id"] != oid:
            self._post({"t": "cfail", "id": oid, "err": "复制的内容已变化，请重新复制"})
            return
        host = msg.get("host") or self.server_ip
        try:
            send_files(host, int(msg["port"]), self.password, o["paths"], mode="clip", tag=oid, sender=self.name)
        except Exception as e:
            self._post({"t": "cfail", "id": oid, "err": str(e)})

    def _file_event(self, event, mode, tag, tops, sender, size, n):
        if mode == "clip":
            if event == "start" and size > 20 * 1048576:
                self._toast(f"正在从 {sender} 获取 {n} 项（{size / 1048576:.0f} MB）…", quiet=True)
            elif event == "done":
                try:
                    if not self.clip:
                        raise RuntimeError("副机未开启剪贴板同步")
                    self.clip.set_files(tops)
                    self._post({"t": "cready", "id": tag})
                except Exception as e:
                    self._post({"t": "cfail", "id": tag, "err": str(e)})
            elif event == "fail":
                self._post({"t": "cfail", "id": tag, "err": "传输中断"})
            return
        if event == "start" and mode == "drop":
            self._toast(f"正在接收 {sender} 拖过来的文件…", quiet=True)
        elif event == "done":
            self._received(mode, tops, sender, size, n)

    def send_files(self, paths, progress=None):
        if not self.server_ip:
            raise RuntimeError("尚未连接主机")
        n, total = send_files(self.server_ip, self.server_fport, self.password, paths, progress, sender=self.name)
        log(f"已发送 {n} 个文件（{total / 1048576:.1f} MB）到 {self.server_name}")

    # ---- 拖放
    def _zone_off(self, delay):
        if self.zone_on:
            self.zone_on = False
            if self.ui:
                self.ui.zone_hide(delay)

    def drop_files(self, files):
        """在副机边缘投放区松开了文件：发给主机，鼠标回到主机。"""
        self.zone_on = False
        self._post({"t": "dropped"})
        threading.Thread(target=self._send_drop, args=(files,), daemon=True).start()

    def _send_drop(self, files):
        try:
            n, total = send_files(self.server_ip, self.server_fport, self.password, files,
                                  mode="drop", sender=self.name)
            log(f"拖放：已发送 {n} 个文件（{total / 1048576:.1f} MB）到 {self.server_name}")
        except Exception as e:
            self._toast(f"拖放发送失败：{e}")

    # ---- 唤醒
    def _wake_up(self):
        if not self.waker or time.time() - self._last_wake < 2:
            return
        self._last_wake = time.time()
        w = self.waker

        def work():
            w.wake()
            time.sleep(0.6)
            if w.is_locked():
                self._post({"t": "toast", "text":
                            f"{self.name} 正处于锁屏/登录界面。出于安全限制，Windows 不允许程序代为输入密码，"
                            f"请在该电脑上解锁一次；若希望屏保后不再锁屏，可在其屏保设置里取消“在恢复时显示登录屏幕”。"})
        threading.Thread(target=work, daemon=True).start()

    # ---- 注入
    def _resolve_key(self, msg):
        from pynput import keyboard
        n = msg.get("n")
        if n:
            if self.map_ctrl_cmd:
                n = CTRL_CMD_SWAP.get(n, n)
            return getattr(keyboard.Key, n, None)
        if msg.get("os") == OS_NAME and msg.get("vk") is not None:
            return keyboard.KeyCode.from_vk(msg["vk"])
        c = msg.get("c")
        return keyboard.KeyCode.from_char(c) if c else None

    def _apply(self, msg):
        t = msg.get("t")
        if t == "ping":
            return
        if t == "clip":
            if self.clip:
                self.offer = None
                self.clip.set(msg.get("text", ""))
            return
        if t == "cpull":
            threading.Thread(target=self._serve_pull, args=(msg,), daemon=True).start()
            return
        if t == "toast":
            self._toast(msg.get("text", ""))
            return
        if t == "zone":
            self.zone_on = True
            if self.ui and getattr(self.ui, "zones", False):
                self.ui.zone_show(msg.get("edge", "left"), msg.get("label", ""), self.drop_files)
            return
        if t == "enter":
            self.controlled = True
            self._wake_up()
        elif t == "leave":
            self.controlled = False
            self._zone_off(0)
            self._release_all()
            return
        elif t == "b" and not msg.get("d"):
            self._zone_off(0.8)
        if self.headless:
            return
        bx, by = self.bounds[0], self.bounds[1]
        try:
            if t in ("m", "enter"):
                x, y = bx + msg["x"], by + msg["y"]
                if self.waker and self.waker.ok and OS_NAME == "win":
                    self.waker.move_abs(x, y)        # 真实输入：能退出屏保、点亮屏幕
                else:
                    self.mctl.position = (x, y)
            elif t == "b":
                from pynput import mouse
                btn = getattr(mouse.Button, msg["b"], None)
                if btn:
                    if msg["d"]:
                        self.mctl.press(btn)
                        self.pressed_btns.add(btn)
                    else:
                        self.mctl.release(btn)
                        self.pressed_btns.discard(btn)
            elif t == "w":
                self.mctl.scroll(msg.get("dx", 0), msg.get("dy", 0))
            elif t == "k":
                key = self._resolve_key(msg)
                if key is not None:
                    if msg["d"]:
                        self.kctl.press(key)
                        self.pressed_keys.add(key)
                    else:
                        self.kctl.release(key)
                        self.pressed_keys.discard(key)
        except Exception as e:
            log(f"注入事件失败 {t}：{e}")

    def _release_all(self):
        if self.headless:
            self.pressed_keys.clear()
            self.pressed_btns.clear()
            return
        for k in list(self.pressed_keys):
            try:
                self.kctl.release(k)
            except Exception:
                pass
        for b in list(self.pressed_btns):
            try:
                self.mctl.release(b)
            except Exception:
                pass
        self.pressed_keys.clear()
        self.pressed_btns.clear()


# ---------------------------------------------------------------- 图形界面
def load_conf():
    try:
        with open(CONF_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_conf(d):
    try:
        with open(CONF_PATH, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


class UIBridge:
    """供后台线程调用的界面接口：所有操作排队到 Tk 线程执行。"""

    def __init__(self, app):
        self.app = app
        self.zones = OS_NAME == "win"

    def toast(self, text, paths=None):
        self.app.uiq.put(("toast", text, paths))

    def zone_show(self, edge, label, cb):
        if self.app.zone:
            self.app.zone.show(edge, label, cb)

    def zone_hide(self, delay=0):
        if self.app.zone:
            self.app.zone.hide(delay)


class DropZone:
    """屏幕边缘的蓝色投放区（Windows）：接收资源管理器拖来的文件。
    使用独立线程里的原生 Win32 窗口，不经过 Tk —— 避免与 Tk 的消息循环互相锁死。
    所有方法都可以从任意线程调用。"""
    W = 150
    BG = 0xE66E1C                                 # COLORREF(0x00BBGGRR) = #1c6ee6
    WM_SHOW, WM_HIDE = 0x8001, 0x8002             # WM_APP + n

    def __init__(self):
        self.ok = False
        self.hwnd = None
        self.cb = None
        self.label = ""
        self.edge = "right"
        self.gen = 0
        self._ready = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()
        self._ready.wait(5)

    # ---- 对外接口
    def show(self, edge, label, cb):
        if not self.ok:
            return
        self.gen += 1
        self.edge, self.label, self.cb = edge, label, cb
        self._u.PostMessageW(self.hwnd, self.WM_SHOW, 0, 0)

    def hide(self, delay=0):
        if not self.ok:
            return
        g = self.gen

        def do():
            if self.gen == g:
                self.cb = None
                self._u.PostMessageW(self.hwnd, self.WM_HIDE, 0, 0)
        if delay:
            t = threading.Timer(delay, do)
            t.daemon = True
            t.start()
        else:
            do()

    # ---- 窗口线程
    def _api(self):
        import ctypes
        from ctypes import wintypes as wt
        w = win_api()
        u = ctypes.WinDLL("user32", use_last_error=True)
        g = ctypes.WinDLL("gdi32")
        k = ctypes.WinDLL("kernel32")
        vp, lres = ctypes.c_void_p, ctypes.c_ssize_t

        class WNDCLASSEXW(ctypes.Structure):
            _fields_ = [("cbSize", wt.UINT), ("style", wt.UINT), ("lpfnWndProc", w.WNDPROC),
                        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int), ("hInstance", vp),
                        ("hIcon", vp), ("hCursor", vp), ("hbrBackground", vp),
                        ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR), ("hIconSm", vp)]

        class PAINTSTRUCT(ctypes.Structure):
            _fields_ = [("hdc", vp), ("fErase", wt.BOOL), ("rcPaint", wt.RECT), ("fRestore", wt.BOOL),
                        ("fIncUpdate", wt.BOOL), ("rgbReserved", ctypes.c_byte * 32)]

        def fn(lib, name, res, *args):
            f = getattr(lib, name)
            f.restype, f.argtypes = res, list(args)
            return f
        u.RegisterClassExW = fn(u, "RegisterClassExW", wt.ATOM, ctypes.POINTER(WNDCLASSEXW))
        u.DefWindowProcW = fn(u, "DefWindowProcW", lres, vp, wt.UINT, wt.WPARAM, wt.LPARAM)
        u.CreateWindowExW = fn(u, "CreateWindowExW", vp, wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                               ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, vp, vp, vp, vp)
        u.GetMessageW = fn(u, "GetMessageW", wt.BOOL, ctypes.POINTER(wt.MSG), vp, wt.UINT, wt.UINT)
        u.TranslateMessage = fn(u, "TranslateMessage", wt.BOOL, ctypes.POINTER(wt.MSG))
        u.DispatchMessageW = fn(u, "DispatchMessageW", lres, ctypes.POINTER(wt.MSG))
        u.PostMessageW = fn(u, "PostMessageW", wt.BOOL, vp, wt.UINT, wt.WPARAM, wt.LPARAM)
        u.ShowWindow = fn(u, "ShowWindow", wt.BOOL, vp, ctypes.c_int)
        u.SetWindowPos = fn(u, "SetWindowPos", wt.BOOL, vp, ctypes.c_ssize_t, ctypes.c_int, ctypes.c_int,
                            ctypes.c_int, ctypes.c_int, wt.UINT)
        u.SetLayeredWindowAttributes = fn(u, "SetLayeredWindowAttributes", wt.BOOL, vp, wt.DWORD, wt.BYTE, wt.DWORD)
        u.BeginPaint = fn(u, "BeginPaint", vp, vp, ctypes.POINTER(PAINTSTRUCT))
        u.EndPaint = fn(u, "EndPaint", wt.BOOL, vp, ctypes.POINTER(PAINTSTRUCT))
        u.GetClientRect = fn(u, "GetClientRect", wt.BOOL, vp, ctypes.POINTER(wt.RECT))
        u.FillRect = fn(u, "FillRect", ctypes.c_int, vp, ctypes.POINTER(wt.RECT), vp)
        u.DrawTextW = fn(u, "DrawTextW", ctypes.c_int, vp, wt.LPCWSTR, ctypes.c_int, ctypes.POINTER(wt.RECT), wt.UINT)
        u.InvalidateRect = fn(u, "InvalidateRect", wt.BOOL, vp, vp, wt.BOOL)
        u.LoadCursorW = fn(u, "LoadCursorW", vp, vp, vp)
        g.CreateSolidBrush = fn(g, "CreateSolidBrush", vp, wt.DWORD)
        g.CreateFontW = fn(g, "CreateFontW", vp, *([ctypes.c_int] * 5), *([wt.DWORD] * 8), wt.LPCWSTR)
        g.SelectObject = fn(g, "SelectObject", vp, vp, vp)
        g.SetTextColor = fn(g, "SetTextColor", wt.DWORD, vp, wt.DWORD)
        g.SetBkMode = fn(g, "SetBkMode", ctypes.c_int, vp, ctypes.c_int)
        k.GetModuleHandleW = fn(k, "GetModuleHandleW", vp, wt.LPCWSTR)
        return ctypes, wt, w, u, g, k, WNDCLASSEXW, PAINTSTRUCT

    def _run(self):
        try:
            ctypes, wt, w, u, g, k, WNDCLASSEXW, PAINTSTRUCT = self._api()
            self._u = u
            hinst = k.GetModuleHandleW(None)
            self._brush = g.CreateSolidBrush(self.BG)
            self._font = g.CreateFontW(-24, 0, 0, 0, 700, 0, 0, 0, 1, 0, 0, 5, 0, FONT)
            self._proc = w.WNDPROC(self._wndproc)       # 保持引用，防止被回收
            wc = WNDCLASSEXW()
            wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
            wc.lpfnWndProc = self._proc
            wc.hInstance = hinst
            wc.hCursor = u.LoadCursorW(None, 32512)     # IDC_ARROW
            wc.lpszClassName = "LanKVMDropZone"
            u.RegisterClassExW(ctypes.byref(wc))
            ex = 0x00000008 | 0x00000080 | 0x00000010 | 0x08000000 | 0x00080000
            # WS_EX_TOPMOST | TOOLWINDOW | ACCEPTFILES | NOACTIVATE | LAYERED
            hwnd = u.CreateWindowExW(ex, "LanKVMDropZone", "LanKVM", 0x80000000,   # WS_POPUP
                                     0, 0, self.W, 300, None, None, hinst, None)
            if not hwnd:
                raise OSError(f"创建投放区窗口失败（{ctypes.get_last_error()}）")
            u.SetLayeredWindowAttributes(hwnd, 0, 235, 2)   # LWA_ALPHA
            for m in (0x0233, 0x004A, 0x0049):            # 允许从非管理员程序拖入
                w.ChangeWindowMessageFilterEx(hwnd, m, 1, None)
            w.DragAcceptFiles(hwnd, True)
            self.hwnd = hwnd
            self._ps_t, self._wt, self._ct = PAINTSTRUCT, wt, ctypes
            self._g = g
            self.ok = True
        except Exception as e:
            log(f"投放区不可用：{e}")
            self._ready.set()
            return
        self._ready.set()
        msg = wt.MSG()
        while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            u.TranslateMessage(ctypes.byref(msg))
            u.DispatchMessageW(ctypes.byref(msg))

    def _wndproc(self, hwnd, msg, wp, lp):
        try:
            if msg == self.WM_SHOW:
                self._do_show(hwnd)
                return 0
            if msg == self.WM_HIDE:
                self._u.ShowWindow(hwnd, 0)                # SW_HIDE
                return 0
            if msg == 0x0233:                              # WM_DROPFILES
                w = win_api()
                files = _hdrop_files(w, wp)
                w.DragFinish(wp)
                self._u.ShowWindow(hwnd, 0)
                cb, self.cb = self.cb, None
                if cb and files:
                    threading.Thread(target=cb, args=(files,), daemon=True).start()
                return 0
            if msg == 0x000F:                              # WM_PAINT
                self._paint(hwnd)
                return 0
            if msg == 0x0014:                              # WM_ERASEBKGND
                return 1
            if msg == 0x0021:                              # WM_MOUSEACTIVATE
                return 3                                   # MA_NOACTIVATE
        except Exception as e:
            log(f"投放区消息处理失败：{e}")
        return self._u.DefWindowProcW(hwnd, msg, wp, lp)

    def _do_show(self, hwnd):
        bx, by, bw, bh = screen_bounds()
        W = self.W
        x, y, cw, ch = {"right": (bx + bw - W, by, W, bh), "left": (bx, by, W, bh),
                        "bottom": (bx, by + bh - W, bw, W), "top": (bx, by, bw, W)}.get(
            self.edge, (bx + bw - W, by, W, bh))
        self._u.SetWindowPos(hwnd, -1, x, y, cw, ch, 0x0010 | 0x0040)   # TOPMOST, NOACTIVATE|SHOWWINDOW
        self._u.InvalidateRect(hwnd, None, True)
        # 轻推一下光标，让资源管理器的拖放过程立即识别到新出现的投放区
        w = win_api()
        pt = w.POINT()
        if w.GetCursorPos(w.ctypes.byref(pt)):
            dx, dy = {"right": (-3, 0), "left": (3, 0), "bottom": (0, -3), "top": (0, 3)}.get(self.edge, (0, 0))
            w.SetCursorPos(pt.x + dx, pt.y + dy)
            time.sleep(0.03)
            w.SetCursorPos(pt.x + 2 * dx, pt.y + 2 * dy)

    def _paint(self, hwnd):
        ct, wt, u, g = self._ct, self._wt, self._u, self._g
        ps = self._ps_t()
        hdc = u.BeginPaint(hwnd, ct.byref(ps))
        try:
            rc = wt.RECT()
            u.GetClientRect(hwnd, ct.byref(rc))
            u.FillRect(hdc, ct.byref(rc), self._brush)
            g.SetBkMode(hdc, 1)                            # TRANSPARENT
            g.SetTextColor(hdc, 0xFFFFFF)
            old = g.SelectObject(hdc, self._font)
            text = "▼\n\n" + self.label
            flags = 0x0001 | 0x0010                        # DT_CENTER | DT_WORDBREAK
            calc = wt.RECT(rc.left + 8, 0, rc.right - 8, 0)
            u.DrawTextW(hdc, text, -1, ct.byref(calc), flags | 0x0400)   # DT_CALCRECT
            h = calc.bottom - calc.top
            top = max(0, (rc.bottom - rc.top - h) // 2)
            r2 = wt.RECT(rc.left + 8, top, rc.right - 8, top + h)
            u.DrawTextW(hdc, text, -1, ct.byref(r2), flags)
            g.SelectObject(hdc, old)
        finally:
            u.EndPaint(hwnd, ct.byref(ps))


class App:
    # 配色
    INK = "#1E2A3A"        # 文字 / 标题栏
    PAPER = "#EEF2F6"      # 窗口底色
    PANEL = "#FFFFFF"      # 面板
    DESK = "#E3E9F0"       # 布局图桌面
    LINE = "#D3DBE5"       # 分隔线 / 空位描边
    MUTED = "#6F7D8E"      # 次要文字
    BLUE = "#1C6EE6"       # 品牌 / 已连接
    GREEN = "#17A673"      # 正在控制
    BEZEL = "#2B3A4D"      # 显示器边框

    def __init__(self):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk = tk, ttk
        self.node = None
        self.uiq = queue.Queue()
        self.bridge = UIBridge(self)
        self._toast_win = None
        self._map_key = None
        self._log_open = True
        c = load_conf()
        r = self.root = tk.Tk()
        r.title("LanKVM 局域网键鼠共享")
        r.geometry("900x860")
        r.minsize(820, 780)
        r.configure(bg=self.PAPER)
        here = os.path.dirname(os.path.abspath(__file__))
        ico, png = os.path.join(here, "lankvm.ico"), os.path.join(here, "lankvm.png")
        try:
            if OS_NAME == "win" and os.path.exists(ico):
                r.iconbitmap(ico)
            elif os.path.exists(png):
                self._icon = tk.PhotoImage(file=png)
                r.iconphoto(True, self._icon)
        except Exception:
            pass
        self.zone = DropZone() if OS_NAME == "win" else None
        self.bridge.zones = bool(self.zone and self.zone.ok)
        self._styles()

        self.v_role = tk.StringVar(value=c.get("role", "server"))
        self.v_name = tk.StringVar(value=c.get("name", socket.gethostname()))
        self.v_pwd = tk.StringVar(value=c.get("password", ""))
        self.v_side = tk.StringVar(value=c.get("side", "右侧"))
        self.v_host = tk.StringVar(value=c.get("host", ""))
        self.v_clip = tk.BooleanVar(value=c.get("clipboard", True))
        self.v_swap = tk.BooleanVar(value=c.get("map_ctrl_cmd", False))
        self.v_awake = tk.BooleanVar(value=c.get("keep_awake", True))
        self.v_target = tk.StringVar()
        self.v_status = tk.StringVar(value="")

        self._build_header()
        self._build_footer()                   # 先占住底部，窗口偏矮时由运行记录让出空间
        body = tk.Frame(r, bg=self.PAPER)
        body.pack(fill="both", expand=True, padx=16, pady=(14, 0))
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)
        self._build_settings(body)
        self._build_map(body)
        self._build_files()
        self._build_log()

        self._role_changed()
        r.protocol("WM_DELETE_WINDOW", self._quit)
        r.after(200, self._poll)
        r.after(30, self._poll_ui)
        log(f"本机 IP：{', '.join(local_ips())}")

    # ---- 样式
    def _styles(self):
        st = self.ttk.Style(self.root)
        try:
            st.theme_use("clam")
        except Exception:
            pass
        f = (FONT, 10)
        st.configure(".", font=f, background=self.PANEL, foreground=self.INK)
        st.configure("TEntry", fieldbackground="#FFFFFF", bordercolor=self.LINE, lightcolor=self.LINE,
                     darkcolor=self.LINE, padding=(8, 5))
        st.map("TEntry", bordercolor=[("focus", self.BLUE)], lightcolor=[("focus", self.BLUE)])
        st.configure("TCombobox", fieldbackground="#FFFFFF", bordercolor=self.LINE, lightcolor=self.LINE,
                     darkcolor=self.LINE, padding=(8, 4), arrowsize=14)
        st.map("TCombobox", fieldbackground=[("readonly", "#FFFFFF"), ("disabled", "#F3F5F8")],
               foreground=[("disabled", self.MUTED)], bordercolor=[("focus", self.BLUE)])
        st.configure("TCheckbutton", background=self.PANEL, foreground=self.INK, padding=(0, 1),
                     indicatorbackground="#FFFFFF", indicatorforeground=self.BLUE)
        st.map("TCheckbutton", foreground=[("disabled", self.MUTED)],
               background=[("disabled", self.PANEL), ("active", self.PANEL)],
               indicatorbackground=[("selected", "#FFFFFF")])
        st.configure("Primary.TButton", background=self.BLUE, foreground="#FFFFFF", bordercolor=self.BLUE,
                     lightcolor=self.BLUE, darkcolor=self.BLUE, font=(FONT, 11, "bold"), padding=(16, 9))
        st.map("Primary.TButton", background=[("pressed", "#1557B8"), ("active", "#2A7BF0")],
               lightcolor=[("active", "#2A7BF0")], darkcolor=[("active", "#2A7BF0")])
        st.configure("Stop.TButton", background="#FFFFFF", foreground="#C0392B", bordercolor="#E7B8B2",
                     lightcolor="#FFFFFF", darkcolor="#FFFFFF", font=(FONT, 11, "bold"), padding=(16, 9))
        st.map("Stop.TButton", background=[("active", "#FDF1EF")])
        st.configure("Soft.TButton", background="#F1F5FA", foreground=self.INK, bordercolor=self.LINE,
                     lightcolor="#F1F5FA", darkcolor="#F1F5FA", padding=(12, 6))
        st.map("Soft.TButton", background=[("active", "#E4ECF6"), ("disabled", "#F6F8FA")],
               foreground=[("disabled", self.MUTED)])
        st.configure("Horizontal.TProgressbar", troughcolor="#E6ECF3", background=self.BLUE,
                     bordercolor="#E6ECF3", lightcolor=self.BLUE, darkcolor=self.BLUE, thickness=6)

    def _label(self, parent, text, size=10, bold=False, fg=None, bg=None, **kw):
        return self.tk.Label(parent, text=text, font=(FONT, size, "bold") if bold else (FONT, size),
                             fg=fg or self.INK, bg=bg or self.PANEL, **kw)

    # ---- 标题栏
    def _build_header(self):
        tk = self.tk
        h = tk.Frame(self.root, bg=self.INK, height=64)
        h.pack(fill="x")
        h.pack_propagate(False)
        logo = tk.Canvas(h, width=44, height=34, bg=self.INK, highlightthickness=0)
        logo.pack(side="left", padx=(18, 10))
        for x0, col in ((2, "#3FA2FF"), (24, "#7BD39A")):
            logo.create_rectangle(x0, 4, x0 + 18, 18, fill=col, outline="#FFFFFF", width=2)
            logo.create_line(x0 + 9, 18, x0 + 9, 23, fill="#FFFFFF", width=2)
        logo.create_line(12, 29, 34, 29, fill="#FFFFFF", width=2, arrow="last", arrowshape=(6, 7, 3))
        box = tk.Frame(h, bg=self.INK)
        box.pack(side="left")
        self._label(box, "LanKVM 局域网键鼠共享", 15, True, "#FFFFFF", self.INK).pack(anchor="w")
        self._label(box, "一套键盘鼠标，控制局域网里的多台电脑，文件和剪贴板随手互通", 9, False,
                    "#A9B6C6", self.INK).pack(anchor="w")
        self.pill = tk.Label(h, text="未启动", font=(FONT, 10, "bold"), fg="#C9D3DF", bg="#33445A",
                             padx=14, pady=4)
        self.pill.pack(side="right", padx=18)

    # ---- 左侧：连接设置
    def _build_settings(self, body):
        tk, ttk = self.tk, self.ttk
        p = tk.Frame(body, bg=self.PANEL, highlightthickness=1, highlightbackground=self.LINE)
        p.grid(row=0, column=0, sticky="nsw", padx=(0, 14))
        inner = tk.Frame(p, bg=self.PANEL)
        inner.pack(fill="both", expand=True, padx=18, pady=16)
        self.settings_widgets = []

        self._label(inner, "这台电脑是", 9, fg=self.MUTED).pack(anchor="w")
        seg = tk.Frame(inner, bg=self.LINE)
        seg.pack(fill="x", pady=(4, 10))
        self.seg = {}
        for val, text in (("server", "主机\n键鼠接在这台"), ("client", "副机\n被主机控制")):
            b = tk.Label(seg, text=text, font=(FONT, 10), justify="center", padx=10, pady=6, cursor="hand2")
            b.pack(side="left", fill="x", expand=True, padx=1, pady=1)
            b.bind("<Button-1>", lambda _e, v=val: self._set_role(v))
            self.seg[val] = b

        def field(label, var, show=None):
            self._label(inner, label, 9, fg=self.MUTED).pack(anchor="w")
            e = ttk.Entry(inner, textvariable=var, width=26, show=show or "")
            e.pack(fill="x", pady=(2, 8))
            self.settings_widgets.append(e)
            return e
        field("本机名称（显示在其他电脑上）", self.v_name)
        self.e_pwd = field("配对密码（所有电脑填写相同的密码）", self.v_pwd, "•")

        self.cbox = tk.Frame(inner, bg=self.PANEL)
        self.cbox.pack(fill="x")
        self._label(self.cbox, "这台副机摆在主机的", 9, fg=self.MUTED).pack(anchor="w")
        self.cb_side = ttk.Combobox(self.cbox, textvariable=self.v_side, values=list(SIDES.values()),
                                    state="readonly", width=24)
        self.cb_side.pack(fill="x", pady=(2, 8))
        self.cb_side.bind("<<ComboboxSelected>>", lambda _e: self._redraw_map(force=True))
        self._label(self.cbox, "主机 IP（留空则自动查找）", 9, fg=self.MUTED).pack(anchor="w")
        self.e_host = ttk.Entry(self.cbox, textvariable=self.v_host, width=26)
        self.e_host.pack(fill="x", pady=(2, 4))
        self.ck_swap = ttk.Checkbutton(self.cbox, text="Ctrl 与 Cmd 互换（Windows 控制 Mac 时）",
                                       variable=self.v_swap)
        self.ck_swap.pack(anchor="w")
        self.ck_awake = ttk.Checkbutton(self.cbox, text="防止这台电脑自动睡眠", variable=self.v_awake)
        self.ck_awake.pack(anchor="w")
        self.settings_widgets += [self.cb_side, self.e_host, self.ck_swap, self.ck_awake]

        self.ck_clip = ttk.Checkbutton(inner, text="同步剪贴板（文字和文件）", variable=self.v_clip)
        self.ck_clip.pack(anchor="w", pady=(4, 0))
        self.settings_widgets.append(self.ck_clip)

        self.btn_start = ttk.Button(inner, text="开始共享", style="Primary.TButton", command=self.toggle)
        self.btn_start.pack(fill="x", side="bottom", pady=(14, 0))

    def _set_role(self, val):
        if self.node:
            return
        self.v_role.set(val)
        self._role_changed()

    def _role_changed(self):
        role = self.v_role.get()
        for val, b in self.seg.items():
            on = val == role
            b.configure(bg=self.BLUE if on else "#F5F7FA", fg="#FFFFFF" if on else self.INK)
        if role == "client":
            self.cbox.pack(fill="x", before=self.ck_clip)
        else:
            self.cbox.pack_forget()
        self._redraw_map(force=True)

    # ---- 右侧：设备布局图
    def _build_map(self, body):
        tk, ttk = self.tk, self.ttk
        p = tk.Frame(body, bg=self.PANEL, highlightthickness=1, highlightbackground=self.LINE)
        p.grid(row=0, column=1, sticky="nsew")
        top = tk.Frame(p, bg=self.PANEL)
        top.pack(fill="x", padx=18, pady=(14, 0))
        self._label(top, "设备布局", 12, True).pack(side="left")
        self.btn_lock = ttk.Button(top, text="锁定在本机", style="Soft.TButton", command=self._lock)
        self.btn_lock.pack(side="right")
        self.canvas = tk.Canvas(p, bg=self.DESK, highlightthickness=0, height=320)
        self.canvas.pack(fill="both", expand=True, padx=18, pady=10)
        self.canvas.bind("<Configure>", lambda _e: self._redraw_map(force=True))
        self.lbl_status = self._label(p, "", 10, fg=self.INK, anchor="w", justify="left", wraplength=520)
        self.lbl_status.pack(fill="x", padx=18, pady=(0, 14))
        self.lbl_status.configure(textvariable=self.v_status)
        p.bind("<Configure>", lambda e: self.lbl_status.configure(wraplength=max(200, e.width - 40)))

    def _map_state(self):
        role = self.v_role.get()
        st = self.node.status() if self.node else None
        if role == "server":
            peers = {p[0]: (p[1], p[2]) for p in (st["peers"] if st else [])}
            return ("server", bool(st), self.v_name.get() or "本机", tuple(sorted(peers.items())),
                    st["active"] if st else None, bool(st and st["locked"]))
        side = {v: k for k, v in SIDES.items()}.get(self.v_side.get(), "right")
        conn = bool(st and st["connected"])
        return ("client", bool(st), (st["server"] if conn else None), side, conn,
                bool(st and st["controlled"]), self.v_name.get() or "本机")

    def _redraw_map(self, force=False):
        key = self._map_state()
        if key == self._map_key and not force:
            return
        self._map_key = key
        cv = self.canvas
        cv.delete("all")
        W, H = max(cv.winfo_width(), 420), max(cv.winfo_height(), 260)
        cx, cy = W // 2, H // 2
        # 按画布大小整体缩放，保证上下左右四个位置互不重叠
        k = min(1.0, H / 330.0, W / 560.0)
        hw, hh = int(150 * k), int(92 * k)     # 主机
        sw, sh = int(128 * k), int(80 * k)     # 副机
        gap, vgap = int(34 * k), int(30 * k)
        def place(cx, cy):
            return {"right": (cx + hw // 2 + gap + sw // 2, cy), "left": (cx - hw // 2 - gap - sw // 2, cy),
                    "top": (cx, cy - hh // 2 - vgap - sh // 2), "bottom": (cx, cy + hh // 2 + vgap + sh // 2)}
        pos = place(cx, cy)
        if key[0] == "client":                 # 副机视图只有两台，整体居中
            px, py = pos[key[3]]
            cx, cy = cx - (px - cx) // 2, cy - (py - cy) // 2
            pos = place(cx, cy)
        self._k = k

        def link(side, color, width, dash=None):
            x, y = pos[side]
            if side in ("left", "right"):
                sgn = 1 if side == "right" else -1
                a, b = (cx + sgn * hw // 2, cy), (x - sgn * sw // 2, y)
            else:
                sgn = 1 if side == "bottom" else -1
                a, b = (cx, cy + sgn * (hh // 2 + 10)), (x, y - sgn * (sh // 2 + 10))
            cv.create_line(*a, *b, fill=color, width=width, dash=dash, capstyle="round")

        if key[0] == "server":
            _, running, name, peers, active, locked = key
            peers = dict(peers)
            for side in ("right", "left", "top", "bottom"):
                if side in peers:
                    link(side, self.GREEN if side == active else "#9FB3C8", 3 if side == active else 2)
                elif not running or len(peers) < 4:
                    link(side, self.LINE, 1, dash=(3, 4))
            self._monitor(cx, cy, hw, hh, "本机（主机）", name, self.GREEN if running and not active else "#3C4C60",
                          note=("鼠标在这里" if running and not active else ("已锁定" if locked else "")),
                          note_xy=(cx, cy - hh // 2 - vgap // 2))
            for side in ("right", "left", "top", "bottom"):
                x, y = pos[side]
                if side in peers:
                    n, ip = peers[side]
                    on = side == active
                    nxy = {"top": (x, y + sh // 2 + vgap // 2 + 4), "bottom": (x + sw // 2 + 44, y)}.get(side)
                    self._monitor(x, y, sw, sh, n, ip, self.GREEN if on else self.BLUE,
                                  note="鼠标在这里" if on else "", note_xy=nxy)
                else:
                    self._slot(x, y, sw, sh, SIDES[side] + "空位")
            if not running:
                tip = "选择“主机”，设置配对密码后点“开始共享”。副机启动后会自动出现在这里。"
            elif not peers:
                tip = "等待副机连接。在其他电脑上运行 LanKVM，选择“副机”并填写同样的配对密码。"
            elif active:
                tip = f"正在控制「{peers[active][0]}」。往回移出屏幕边缘，或按 Ctrl+Alt+H 回到本机。"
            else:
                sides = "、".join(f"{SIDES[s]}边缘进入「{peers[s][0]}」" for s in peers)
                tip = f"已连接{len(peers)}台副机。把鼠标移出屏幕{sides}。"
            if locked:
                tip = "已锁定在本机，鼠标不会移到其他电脑。再次点“解除锁定”或按 Ctrl+Alt+L 恢复。"
            self.v_status.set(tip)
        else:
            _, running, server, side, conn, controlled, me = key
            link(side, self.GREEN if controlled else ("#9FB3C8" if conn else self.LINE),
                 3 if controlled else 2, dash=None if conn else (3, 4))
            if conn:
                self._monitor(cx, cy, hw, hh, "主机", server, "#3C4C60")
            else:
                self._slot(cx, cy, hw, hh, "主机（尚未连接）")
            x, y = pos[side]
            nxy = {"top": (x, y + sh // 2 + vgap // 2 + 4), "bottom": (x + sw // 2 + 44, y)}.get(side)
            self._monitor(x, y, sw, sh, "本机（副机）", me, self.GREEN if controlled else self.BLUE,
                          note="鼠标在这里" if controlled else "", note_xy=nxy)
            if not running:
                tip = f"这台电脑将作为副机，摆在主机的{SIDES[side]}。填写和主机相同的配对密码，点“开始共享”。"
            elif not conn:
                tip = "正在局域网里查找主机……如果一直找不到，请在左侧填写主机的IP地址，并确认两台电脑连在同一个网络。"
            elif controlled:
                tip = f"主机「{server}」正在控制这台电脑。"
            else:
                tip = f"已连接主机「{server}」。在主机上把鼠标移出屏幕{SIDES[side]}边缘即可控制这台电脑。"
            self.v_status.set(tip)

    def _rrect(self, x0, y0, x1, y1, r, **kw):
        pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1, x1 - r, y1,
               x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
        return self.canvas.create_polygon(pts, smooth=True, **kw)

    def _monitor(self, x, y, w, h, title, sub, screen, note="", note_xy=None):
        cv = self.canvas
        x0, y0, x1, y1 = x - w // 2, y - h // 2, x + w // 2, y + h // 2
        self._rrect(x0, y0, x1, y1, 8, fill=self.BEZEL, outline="")
        self._rrect(x0 + 5, y0 + 5, x1 - 5, y1 - 5, 4, fill=screen, outline="")
        k = getattr(self, "_k", 1.0)
        st = int(10 * k)
        cv.create_polygon(x - 10, y1, x + 10, y1, x + 14, y1 + st, x - 14, y1 + st, fill="#8A99AB", outline="")
        cv.create_line(x - 24, y1 + st + 1, x + 24, y1 + st + 1, fill="#8A99AB", width=3, capstyle="round")
        f1 = (FONT, 10 if k > 0.85 else 9, "bold")
        f2 = (FONT, 9 if k > 0.85 else 8)
        cv.create_text(x, y - int(9 * k) - 1, text=self._fit(title, f1, w - 16), fill="#FFFFFF", font=f1)
        cv.create_text(x, y + int(12 * k) + 1, text=self._fit(str(sub), f2, w - 14), fill="#DCE6F2", font=f2)
        if note:
            nx, ny = note_xy or (x, y1 + st + 14)
            cv.create_text(nx, ny, text=note, fill=self.GREEN if "鼠标" in note else self.MUTED,
                           font=(FONT, 9, "bold"))

    def _fit(self, text, font, maxw):
        """文字超出显示器宽度时截断并加省略号。"""
        import tkinter.font as tkfont
        fo = self._fonts.get(font) if hasattr(self, "_fonts") else None
        if fo is None:
            self._fonts = getattr(self, "_fonts", {})
            fo = self._fonts[font] = tkfont.Font(root=self.root, family=font[0], size=font[1],
                                                 weight=font[2] if len(font) > 2 else "normal")
        if fo.measure(text) <= maxw:
            return text
        while text and fo.measure(text + "…") > maxw:
            text = text[:-1]
        return text + "…"

    def _slot(self, x, y, w, h, text):
        self._rrect(x - w // 2, y - h // 2, x + w // 2, y + h // 2, 8, fill="", outline="#B4C1D0",
                    dash=(4, 4), width=1)
        self.canvas.create_text(x, y, text=text, fill=self.MUTED, font=(FONT, 9), width=w - 12)

    # ---- 文件传输
    def _build_files(self):
        tk, ttk = self.tk, self.ttk
        p = tk.Frame(self.root, bg=self.PANEL, highlightthickness=1, highlightbackground=self.LINE)
        p.pack(fill="x", padx=16, pady=(14, 0))
        row = tk.Frame(p, bg=self.PANEL)
        row.pack(fill="x", padx=18, pady=(12, 6))
        self._label(row, "传文件", 12, True).pack(side="left", padx=(0, 14))
        ttk.Button(row, text="发送文件", style="Soft.TButton", command=lambda: self.send(False)).pack(side="left")
        ttk.Button(row, text="发送文件夹", style="Soft.TButton", command=lambda: self.send(True)).pack(
            side="left", padx=6)
        self._label(row, "发往", 10, fg=self.MUTED).pack(side="left", padx=(10, 4))
        self.cb_target = ttk.Combobox(row, textvariable=self.v_target, state="readonly", width=26)
        self.cb_target.pack(side="left")
        ttk.Button(row, text="打开接收文件夹", style="Soft.TButton",
                   command=lambda: open_folder(RECV_DIR)).pack(side="right")
        self.prog = ttk.Progressbar(p, maximum=1.0, style="Horizontal.TProgressbar")
        self.prog.pack(fill="x", padx=18, pady=(2, 6))
        self._label(p, "也可以直接复制粘贴：在一台电脑上按 Ctrl+C 复制文件，到另一台电脑按 Ctrl+V。"
                       "或者按住文件拖到屏幕边缘，停一下，在出现的蓝色区域松开。",
                    9, fg=self.MUTED, anchor="w", justify="left", wraplength=800).pack(fill="x", padx=18,
                                                                                        pady=(0, 12))

    # ---- 日志
    def _build_log(self):
        tk = self.tk
        p = tk.Frame(self.root, bg=self.PAPER)
        p.pack(fill="both", expand=False, padx=16, pady=(10, 0))
        bar = tk.Frame(p, bg=self.PAPER)
        bar.pack(fill="x")
        self.log_toggle = self._label(bar, "运行记录  ▼", 9, True, self.MUTED, self.PAPER, cursor="hand2")
        self.log_toggle.pack(side="left")
        self.log_toggle.bind("<Button-1>", lambda _e: self._toggle_log())
        self.txt = tk.Text(p, height=5, wrap="word", state="disabled", bg="#F8FAFC", fg="#44546A",
                           relief="flat", font=(FONT, 9), highlightthickness=1,
                           highlightbackground=self.LINE, padx=10, pady=6)
        self.txt.pack(fill="both", expand=True, pady=(4, 0))

    def _toggle_log(self):
        self._log_open = not self._log_open
        if self._log_open:
            self.txt.pack(fill="both", expand=True, pady=(4, 0))
            self.log_toggle.configure(text="运行记录  ▼")
        else:
            self.txt.pack_forget()
            self.log_toggle.configure(text="运行记录  ▶")

    # ---- 页脚
    def _build_footer(self):
        tk = self.tk
        ft = tk.Frame(self.root, bg=self.PAPER)
        ft.pack(fill="x", side="bottom", padx=16, pady=8)
        self._label(ft, f"LanKVM v{VERSION}    作者：{AUTHOR}", 9, fg=self.MUTED, bg=self.PAPER).pack(side="left")
        link = tk.Label(ft, text="官网 laobaiai.com", fg=self.BLUE, bg=self.PAPER, cursor="hand2",
                        font=(FONT, 9, "underline"))
        link.pack(side="right")
        link.bind("<Button-1>", lambda _e: self._open_site())

    # ---- 行为
    def _conf(self):
        side = {v: k for k, v in SIDES.items()}.get(self.v_side.get(), "right")
        d = {"role": self.v_role.get(), "name": self.v_name.get().strip() or socket.gethostname(),
             "password": self.v_pwd.get(), "side": self.v_side.get(), "host": self.v_host.get().strip(),
             "clipboard": self.v_clip.get(), "map_ctrl_cmd": self.v_swap.get(),
             "keep_awake": self.v_awake.get()}
        save_conf(d)
        return d, side

    def _set_running(self, running):
        for w in self.settings_widgets:
            try:
                if isinstance(w, self.ttk.Combobox):
                    w.configure(state="disabled" if running else "readonly")
                else:
                    w.state(["disabled"] if running else ["!disabled"])
            except Exception:
                pass
        for b in self.seg.values():
            b.configure(cursor="arrow" if running else "hand2")
        self.btn_start.configure(text="停止共享" if running else "开始共享",
                                 style="Stop.TButton" if running else "Primary.TButton")

    def toggle(self):
        from tkinter import messagebox
        if self.node:
            self.node.stop()
            self.node = None
            self._set_running(False)
            self._redraw_map(force=True)
            return
        d, side = self._conf()
        if not d["password"]:
            if not messagebox.askyesno("没有设置配对密码",
                                       "不设密码时，局域网里的任何电脑都能连接并控制这台电脑。\n仍要继续吗？"):
                return
        try:
            if d["role"] == "server":
                self.node = Server(d["name"], d["password"], clipboard=d["clipboard"])
            else:
                self.node = Client(d["host"], d["password"], side, d["name"], clipboard=d["clipboard"],
                                   map_ctrl_cmd=d["map_ctrl_cmd"], keep_awake=d["keep_awake"])
            self.node.ui = self.bridge
            self.node.start()
            self._set_running(True)
        except Exception as e:
            self.node = None
            messagebox.showerror("无法开始共享", f"{e}\n\n可能是端口被占用、防火墙拦截，或者缺少依赖。")

    @staticmethod
    def _open_site():
        import webbrowser
        try:
            webbrowser.open(WEBSITE)
        except Exception:
            pass

    def _lock(self):
        if isinstance(self.node, Server):
            self.node.toggle_lock()

    def send(self, folder):
        from tkinter import filedialog, messagebox
        if not self.node:
            messagebox.showinfo("还没有开始共享", "请先点“开始共享”，等其他电脑连接后再发送文件。")
            return
        if folder:
            p = filedialog.askdirectory()
            paths = [p] if p else []
        else:
            paths = list(filedialog.askopenfilenames())
        if not paths:
            return
        target = None
        if isinstance(self.node, Server):
            sel = self.v_target.get()
            target = self._targets.get(sel)

        def prog(v):
            self.root.after(0, lambda: self.prog.configure(value=v))

        def work():
            try:
                if isinstance(self.node, Server):
                    self.node.send_files(paths, target, prog)
                else:
                    self.node.send_files(paths, prog)
            except Exception as e:
                log(f"发送失败：{e}")
            prog(0)
        threading.Thread(target=work, daemon=True).start()

    # ---- 提示气泡
    def show_toast(self, text, paths=None):
        tk = self.tk
        if self._toast_win is not None:
            try:
                self._toast_win.destroy()
            except Exception:
                pass
        t = tk.Toplevel(self.root)
        t.overrideredirect(True)
        t.attributes("-topmost", True)
        bg = self.INK
        fr = tk.Frame(t, bg=bg, padx=16, pady=12, highlightthickness=0)
        fr.pack()
        bar = tk.Frame(fr, bg=self.BLUE, width=4)
        bar.pack(side="left", fill="y", padx=(0, 12))
        col = tk.Frame(fr, bg=bg)
        col.pack(side="left")
        widgets = [fr, col,
                   tk.Label(col, text="LanKVM", bg=bg, fg="#8FB8F5", font=(FONT, 9, "bold")),
                   tk.Label(col, text=text, bg=bg, fg="white", font=(FONT, 10), wraplength=340, justify="left")]
        if paths:
            widgets.append(tk.Label(col, text="点击打开所在文件夹", bg=bg, fg="#9AA8B8", font=(FONT, 9)))
        for wdg in widgets[2:]:
            wdg.pack(anchor="w")
        t.update_idletasks()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        t.geometry(f"+{sw - t.winfo_width() - 24}+{sh - t.winfo_height() - 72}")

        def close(_e=None):
            try:
                t.destroy()
            except Exception:
                pass

        def click(_e=None):
            if paths:
                reveal_files(paths)
            close()
        for wdg in widgets + [t]:
            wdg.bind("<Button-1>", click)
        t.after(6000, close)
        self._toast_win = t

    def _poll_ui(self):
        try:
            while True:
                item = self.uiq.get_nowait()
                try:
                    if item[0] == "toast":
                        self.show_toast(item[1], item[2])
                except Exception as e:
                    log(f"界面操作失败：{e}")
        except queue.Empty:
            pass
        self.root.after(30, self._poll_ui)

    def _poll(self):
        lines = []
        while not LOG_Q.empty():
            lines.append(LOG_Q.get_nowait())
        if lines:
            self.txt.configure(state="normal")
            self.txt.insert("end", "\n".join(lines) + "\n")
            self.txt.see("end")
            self.txt.configure(state="disabled")
        n = self.node
        self._targets = getattr(self, "_targets", {})
        if n:
            st = n.status()
            if st["role"] == "server":
                self._targets = {f"{p[1]}（{SIDES[p[0]]}）": p[0] for p in st["peers"]}
                vals = list(self._targets)
                self.cb_target.configure(values=vals)
                if vals and self.v_target.get() not in vals:
                    self.v_target.set(vals[0])
                elif not vals:
                    self.v_target.set("")
                if st["locked"]:
                    self.pill.configure(text="已锁定在本机", bg="#7A5A12", fg="#FFE7A8")
                elif st.get("pasting"):
                    self.pill.configure(text="正在传输文件", bg=self.BLUE, fg="#FFFFFF")
                elif st["active"]:
                    self.pill.configure(text="正在控制副机", bg=self.GREEN, fg="#FFFFFF")
                else:
                    self.pill.configure(text=f"主机运行中，{len(vals)}台副机" if vals else "等待副机连接",
                                        bg="#2E5E9E" if vals else "#33445A", fg="#FFFFFF")
                self.btn_lock.configure(text="解除锁定" if st["locked"] else "锁定在本机")
            else:
                label = f"主机 {st['server']}（{st['ip']}）" if st["connected"] else ""
                self.cb_target.configure(values=[label] if label else [])
                self.v_target.set(label)
                if not st["connected"]:
                    self.pill.configure(text="正在查找主机", bg="#33445A", fg="#FFFFFF")
                elif st["controlled"]:
                    self.pill.configure(text="正在被主机控制", bg=self.GREEN, fg="#FFFFFF")
                else:
                    self.pill.configure(text="已连接主机", bg="#2E5E9E", fg="#FFFFFF")
        else:
            self.pill.configure(text="未启动", bg="#33445A", fg="#C9D3DF")
        try:
            self.btn_lock.state(["!disabled"] if isinstance(n, Server) else ["disabled"])
        except Exception:
            pass
        self._redraw_map()
        self.root.after(200, self._poll)

    def _quit(self):
        if self.node:
            self.node.stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


# ---------------------------------------------------------------- 入口
def main():
    enable_dpi_awareness()
    ap = argparse.ArgumentParser(description=f"LanKVM 局域网键鼠共享（作者：{AUTHOR}  {WEBSITE}）")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("server", help="作为主机运行（共享本机键鼠）")
    s.add_argument("-n", "--name", default=socket.gethostname())
    s.add_argument("-p", "--password", default="")
    s.add_argument("--port", type=int, default=CTRL_PORT)
    s.add_argument("--no-clip", action="store_true")
    c = sub.add_parser("client", help="作为副机运行（被控制）")
    c.add_argument("-H", "--host", default="", help="主机 IP，留空自动发现")
    c.add_argument("-s", "--side", choices=list(SIDES), default="right", help="本机位于主机哪一侧")
    c.add_argument("-n", "--name", default=socket.gethostname())
    c.add_argument("-p", "--password", default="")
    c.add_argument("--port", type=int, default=CTRL_PORT)
    c.add_argument("--map-ctrl-cmd", action="store_true", help="Ctrl 与 Cmd 互换")
    c.add_argument("--no-clip", action="store_true")
    f = sub.add_parser("send", help="向对方发送文件/文件夹")
    f.add_argument("-H", "--host", required=True)
    f.add_argument("-p", "--password", default="")
    f.add_argument("--port", type=int, default=FILE_PORT)
    f.add_argument("paths", nargs="+")
    a = ap.parse_args()

    if a.cmd is None:
        App().run()
        return
    if a.cmd == "send":
        n, total = send_files(a.host, a.port, a.password, a.paths,
                              lambda v: print(f"\r发送中 {v * 100:5.1f}%", end="", flush=True),
                              sender=socket.gethostname())
        print(f"\n完成：{n} 个文件，{total / 1048576:.1f} MB")
        return
    node = (Server(a.name, a.password, a.port, clipboard=not a.no_clip) if a.cmd == "server" else
            Client(a.host, a.password, a.side, a.name, a.port, clipboard=not a.no_clip,
                   map_ctrl_cmd=a.map_ctrl_cmd))
    node.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        node.stop()


if __name__ == "__main__":
    main()
