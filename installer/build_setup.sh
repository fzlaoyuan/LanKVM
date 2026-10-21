#!/usr/bin/env bash
# 生成单文件安装包 LanKVM_Setup.exe（可在 Linux/macOS 上构建）
# VENDOR 目录：
#   7zz                7-Zip 命令行
#   7zSD.sfx           LZMA SDK bin/7zSD.sfx（安装型自解压模块）
#   wheels/*.whl       pynput、six、pyperclip
#   runtime/           免安装 Python（由 make_runtime.py 从官方安装程序生成）
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
VENDOR="${VENDOR:-$HERE/vendor}"
OUT="${OUT:-$ROOT/dist/LanKVM_Setup.exe}"
STAGE="$(mktemp -d)"

crlf()   { sed -e 's/\r$//' -e 's/$/\r/' "$1" > "$2"; }                               # Windows 换行
crlfbom(){ printf '\xEF\xBB\xBF' > "$2"; sed -e 's/\r$//' -e 's/$/\r/' "$1" >> "$2"; } # PowerShell 5.1 需 UTF-8 BOM

mkdir -p "$STAGE/app" "$STAGE/wheels" "$(dirname "$OUT")"
crlf    "$HERE/setup.cmd"            "$STAGE/setup.cmd"
crlfbom "$HERE/setup.ps1"            "$STAGE/setup.ps1"
cp      "$HERE/findpy.py"            "$STAGE/findpy.py"
cp      "$VENDOR"/wheels/*.whl       "$STAGE/wheels/"
cp -r   "$VENDOR/runtime"            "$STAGE/runtime"
cp      "$ROOT/lankvm.py"            "$STAGE/app/"
cp      "$ROOT/README.md"            "$STAGE/app/"
cp      "$HERE/app/lankvm.ico"       "$STAGE/app/"
crlf    "$HERE/app/uninstall.cmd"    "$STAGE/app/uninstall.cmd"
crlfbom "$HERE/app/helper.ps1"       "$STAGE/app/helper.ps1"
chmod -R u+w "$STAGE"
crlf    "$HERE/config.txt"           "$STAGE.cfg"

rm -f "$STAGE.7z"
(cd "$STAGE" && "$VENDOR/7zz" a -t7z -mx=7 -m0=LZMA2 -mf=BCJ "$STAGE.7z" . >/dev/null)
cat "$VENDOR/7zSD.sfx" "$STAGE.cfg" "$STAGE.7z" > "$OUT"
rm -rf "$STAGE" "$STAGE.7z" "$STAGE.cfg"
echo "已生成：$OUT ($(du -h "$OUT" | cut -f1))"
