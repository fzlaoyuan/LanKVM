#!/usr/bin/env bash
# 生成 macOS 安装包 LanKVM-Mac安装包.zip（可在 Linux/macOS 上构建）
# VENDOR 目录：
#   python-aarch64.tar.gz / python-x86_64.tar.gz
#       python-build-standalone 的 cpython-3.12.x-<arch>-apple-darwin-install_only 包（可重定位、含 Tk）
#   wheels/*.whl   pynput six pyperclip pyobjc-core pyobjc-framework-{Cocoa,Quartz,ApplicationServices,CoreText}
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
VENDOR="${VENDOR:?请设置 VENDOR 目录}"
OUT="${OUT:-$ROOT/dist/LanKVM-Mac安装包.zip}"
NAME="LanKVM-Mac安装包"
STAGE="$(mktemp -d)/$NAME"

lf() { sed -e 's/\r$//' "$1" > "$2"; }       # macOS 脚本必须是 LF 换行

mkdir -p "$STAGE/app" "$STAGE/runtime" "$STAGE/wheels" "$(dirname "$OUT")"
lf "$HERE/安装LanKVM.command" "$STAGE/安装LanKVM.command"
lf "$HERE/卸载LanKVM.command" "$STAGE/卸载LanKVM.command"
cp "$HERE/使用前必读.txt" "$STAGE/"
cp "$ROOT/lankvm.py" "$ROOT/README.md" "$STAGE/app/"
python3 - "$ROOT/installer/app/lankvm.ico" "$STAGE/app" <<'EOF'
import sys
from PIL import Image
ico, out = sys.argv[1], sys.argv[2]
im = Image.open(ico)
im.size = max(im.info.get("sizes", [im.size]))
im = im.convert("RGBA")
im.save(out + "/lankvm.icns")
im.resize((256, 256)).save(out + "/lankvm.png")
EOF
cp "$VENDOR/python-aarch64.tar.gz" "$VENDOR/python-x86_64.tar.gz" "$STAGE/runtime/"
cp "$VENDOR"/wheels/*.whl "$STAGE/wheels/"

# 打包：保留 .command 的可执行权限（访达解压后可直接运行）
rm -f "$OUT"
python3 - "$STAGE" "$OUT" <<'EOF'
import os, sys, zipfile
stage, out = sys.argv[1], sys.argv[2]
base = os.path.dirname(stage)
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
    for dp, dns, fs in os.walk(stage):
        dns.sort()
        for f in sorted(fs):
            p = os.path.join(dp, f)
            arc = os.path.relpath(p, base)
            zi = zipfile.ZipInfo.from_file(p, arc)
            zi.compress_type = zipfile.ZIP_DEFLATED
            mode = 0o755 if f.endswith(".command") else 0o644
            zi.external_attr = (0o100000 | mode) << 16
            with open(p, "rb") as fh:
                z.writestr(zi, fh.read())
EOF
rm -rf "$(dirname "$STAGE")"
echo "已生成：$OUT ($(du -h "$OUT" | cut -f1))"
