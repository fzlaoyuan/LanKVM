#!/bin/bash
# LanKVM 局域网键鼠共享 —— macOS 卸载
echo "正在卸载 LanKVM …"
pkill -f "LanKVM.app/Contents/Resources/lankvm.py" >/dev/null 2>&1
sleep 0.5
for d in /Applications "$HOME/Applications"; do
    if [ -d "$d/LanKVM.app" ]; then
        rm -rf "$d/LanKVM.app" && echo "  已删除 $d/LanKVM.app"
    fi
done
echo
echo "已卸载。已接收的文件（下载/LanKVM）和设置文件（~/.lankvm.json）均保留。"
echo "如需撤销授权：系统设置 → 隐私与安全性 → 辅助功能 / 输入监控，删除 LanKVM。"
read -n 1 -s -r -p "按任意键关闭…"
echo
