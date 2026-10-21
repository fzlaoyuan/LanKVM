#!/bin/bash
# LanKVM 局域网键鼠共享 —— macOS 一键安装
# 作者：老白玩AI   官网：https://laobaiai.com/
#
# 已有合适的 Python（3.8+ 且带 Tk 8.6）时直接使用；否则使用安装包内置的免安装版 Python 3.12。
# 所有文件都装在 LanKVM.app 内部，不修改系统 Python。
# （LANKVM_APPDIR / LANKVM_PKGS / LANKVM_CHECK 三个环境变量仅供开发自测使用）

cd "$(dirname "$0")" || exit 1
SRC="$(pwd)"
VER="1.2.0"

fail() {
    echo
    echo "  安装未完成：$1"
    echo "  如需帮助，请截图本窗口。"
    read -n 1 -s -r -p "  按任意键关闭…"
    echo
    exit 1
}
step() { echo; echo "$1"; }
info() { echo "      $1"; }

clear 2>/dev/null
echo "============================================"
echo "  LanKVM 局域网键鼠共享 v$VER   macOS 安装程序"
echo "  作者：老白玩AI   官网：https://laobaiai.com/"
echo "============================================"

case "$(uname -m)" in
    arm64) PBS=aarch64 ;;
    x86_64) PBS=x86_64 ;;
    *) fail "不支持的处理器：$(uname -m)" ;;
esac

APPDIR="${LANKVM_APPDIR:-/Applications}"
if [ ! -w "$APPDIR" ]; then
    APPDIR="$HOME/Applications"
    mkdir -p "$APPDIR" || fail "无法创建 $APPDIR"
fi
APP="$APPDIR/LanKVM.app"
RES="$APP/Contents/Resources"
SITE="$RES/site-packages"
PKGS="${LANKVM_PKGS:-pynput pyperclip six pyobjc-core pyobjc-framework-Cocoa pyobjc-framework-Quartz pyobjc-framework-ApplicationServices pyobjc-framework-CoreText}"
CHECK="${LANKVM_CHECK:-import tkinter, pynput, pyperclip, AppKit, Quartz; tkinter.Tcl()}"

check_py() {
    "$1" -c 'import sys, tkinter; assert sys.version_info >= (3, 8) and tkinter.TkVersion >= 8.6; tkinter.Tcl()' \
        >/dev/null 2>&1
}

install_deps() {   # $1 = python
    rm -rf "$SITE"
    "$1" -m pip install --no-index --find-links "$SRC/wheels" --target "$SITE" $PKGS \
        --disable-pip-version-check -q >/dev/null 2>&1 &&
        PYTHONPATH="$SITE" PYTHONNOUSERSITE=1 "$1" -c "$CHECK" >/dev/null 2>&1
}

# ---------------------------------------------------------------------------
step "[1/5] 检测本机 Python …"
FOUND=""
# 注意：不调用 /usr/bin/python3 —— 未装开发者工具时它会弹出安装窗口，且自带的 Tk 版本过旧
for c in /Library/Frameworks/Python.framework/Versions/3.*/bin/python3 \
         /opt/homebrew/bin/python3 /usr/local/bin/python3; do
    if [ -x "$c" ] && check_py "$c"; then
        FOUND="$c"
        break
    fi
done

# ---------------------------------------------------------------------------
step "[2/5] 准备程序目录 …"
pkill -f "LanKVM.app/Contents/Resources/lankvm.py" >/dev/null 2>&1
sleep 0.5
rm -rf "$APP" || fail "无法删除旧版本：$APP"
mkdir -p "$APP/Contents/MacOS" "$RES" || fail "无法创建 $APP"
cp "$SRC/app/lankvm.py" "$SRC/app/README.md" "$SRC/app/lankvm.icns" "$SRC/app/lankvm.png" "$RES/" \
    || fail "复制程序文件失败"
cp "$SRC/卸载LanKVM.command" "$RES/" 2>/dev/null
info "安装位置：$APP"

# ---------------------------------------------------------------------------
step "[3/5] 准备 Python 运行环境 …"
PY=""
if [ -n "$FOUND" ]; then
    info "检测到已安装的 Python：$FOUND（跳过安装）"
    info "安装运行依赖（离线）…"
    if install_deps "$FOUND"; then
        PY="$FOUND"
        PYCMD="$FOUND"
        info "依赖安装完成。"
    else
        info "该 Python 无法安装依赖，改用内置 Python。"
    fi
else
    info "未检测到可用的 Python。"
fi
if [ -z "$PY" ]; then
    info "正在安装内置 Python 3.12（免安装版，只放在 LanKVM.app 内部）…"
    tar -xzf "$SRC/runtime/python-$PBS.tar.gz" -C "$RES" || fail "解压内置 Python 失败"
    PY="$RES/python/bin/python3"
    PYCMD='$RES/python/bin/python3'          # 写入启动器时使用相对路径，移动 App 也能运行
    install_deps "$PY" || fail "内置 Python 安装依赖或自检失败"
    info "内置 Python 安装完成。"
fi

# ---------------------------------------------------------------------------
step "[4/5] 生成 LanKVM 应用 …"
cat > "$APP/Contents/MacOS/LanKVM" <<EOF
#!/bin/bash
RES="\$(cd "\$(dirname "\$0")/../Resources" && pwd)"
export PYTHONPATH="\$RES/site-packages"
export PYTHONNOUSERSITE=1
exec "$PYCMD" "\$RES/lankvm.py" "\$@"
EOF
chmod +x "$APP/Contents/MacOS/LanKVM"

cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>LanKVM</string>
    <key>CFBundleDisplayName</key><string>LanKVM 局域网键鼠共享</string>
    <key>CFBundleIdentifier</key><string>com.laobaiai.lankvm</string>
    <key>CFBundleExecutable</key><string>LanKVM</string>
    <key>CFBundleIconFile</key><string>lankvm</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>$VER</string>
    <key>CFBundleVersion</key><string>$VER</string>
    <key>LSMinimumSystemVersion</key><string>11.0</string>
    <key>NSHighResolutionCapable</key><true/>
    <key>NSHumanReadableCopyright</key><string>作者：老白玩AI  https://laobaiai.com/</string>
</dict>
</plist>
EOF

xattr -dr com.apple.quarantine "$APP" >/dev/null 2>&1
codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || info "（本地签名未完成，不影响使用）"
info "已生成：$APP"

# ---------------------------------------------------------------------------
step "[5/5] 系统授权 …"
echo
echo "  macOS 要求你手动允许 LanKVM 控制键盘鼠标。接下来会打开两个设置页面："
echo "    ① 隐私与安全性 → 辅助功能：打开 LanKVM 的开关"
echo "    ② 隐私与安全性 → 输入监控：打开 LanKVM 的开关"
echo "  若列表里没有 LanKVM，请先运行一次 LanKVM，或点“+”添加：$APP"
echo "  （列表里显示为 python3 时，打开它的开关也可以）"
echo "  授权后请退出并重新打开 LanKVM。首次运行若弹出“接受传入网络连接”，请选“允许”。"
echo
read -n 1 -s -r -p "  按任意键打开设置页面并启动 LanKVM…"
echo
open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility" >/dev/null 2>&1
sleep 1
open "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent" >/dev/null 2>&1
open "$APP" >/dev/null 2>&1

echo
echo "  安装完成！以后可在“应用程序”或启动台里打开 LanKVM。"
echo "  卸载：双击安装包里的“卸载LanKVM.command”。"
echo
