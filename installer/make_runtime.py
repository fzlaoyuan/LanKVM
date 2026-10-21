#!/usr/bin/env python3
"""
从 Python 官方 Windows 安装程序（python-3.x.y-amd64.exe）生成免安装运行环境，
并把 wheels 目录里的依赖直接装进 site-packages。可在 Linux/macOS 上运行。

需要：pip install python-msi ；7zz（7-Zip 命令行）
用法：python make_runtime.py python-3.12.10-amd64.exe wheels/ runtime/ [--7zz 路径]
"""
import argparse
import os
import shutil
import struct
import subprocess
import tempfile
import zipfile
from pathlib import Path

PARTS = ["core", "exe", "lib", "tcltk"]          # 需要的组件（单用户版本 JustForMe）
DROP = ["ProgramMenuFolder", "libs", "NEWS.txt", "Lib/idlelib", "Lib/turtledemo"]


def find_cabs(data):
    out, i = [], 0
    while True:
        i = data.find(b"MSCF\0\0\0\0", i)
        if i < 0:
            return out
        size = struct.unpack_from("<I", data, i + 8)[0]
        if 0 < size <= len(data) - i:
            out.append((i, size))
        i += 4


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("installer")
    ap.add_argument("wheels")
    ap.add_argument("out")
    ap.add_argument("--7zz", dest="sz", default="7zz")
    a = ap.parse_args()
    tmp = Path(tempfile.mkdtemp())
    data = Path(a.installer).read_bytes()
    cabs = find_cabs(data)
    ux, payload = tmp / "ux.cab", tmp / "payload.cab"
    ux.write_bytes(data[cabs[0][0]:sum(cabs[0])])
    payload.write_bytes(data[cabs[1][0]:sum(cabs[1])])
    subprocess.run([a.sz, "x", "-y", f"-o{tmp/'ux'}", str(ux), "0"], check=True, stdout=subprocess.DEVNULL)
    manifest = (tmp / "ux" / "0").read_text("utf-8", "ignore")

    # 在 Burn 清单里找到各组件 JustForMe 版本对应的内部文件名（a1、a3 ...）
    import re
    ids = {}
    for m in re.finditer(r'<Payload Id="([^"]+)"[^>]*SourcePath="([^"]+)"', manifest):
        pid, src = m.group(1), m.group(2)
        for p in PARTS:
            if pid == f"{p}_JustForMe":
                ids[p] = src
    assert len(ids) == len(PARTS), ids
    subprocess.run([a.sz, "x", "-y", f"-o{tmp/'msi'}", str(payload), *ids.values()],
                   check=True, stdout=subprocess.DEVNULL)

    out = Path(a.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for p, src in ids.items():
        msi = tmp / "msi" / f"{p}.msi"
        (tmp / "msi" / src).rename(msi)
        dst = tmp / f"rt_{p}"
        subprocess.run(["pymsi", "extract", "-o", str(dst), str(msi)], check=True, stdout=subprocess.DEVNULL)
        shutil.copytree(dst, out, dirs_exist_ok=True)
    for d in DROP:
        t = out / d
        if t.is_dir():
            shutil.rmtree(t)
        elif t.exists():
            t.unlink()
    sp = out / "Lib" / "site-packages"
    sp.mkdir(parents=True, exist_ok=True)
    for w in sorted(Path(a.wheels).glob("*.whl")):
        zipfile.ZipFile(w).extractall(sp)
    shutil.rmtree(tmp)
    n = sum(1 for _ in out.rglob("*") if _.is_file())
    print(f"已生成运行环境 {out}（{n} 个文件）")


if __name__ == "__main__":
    main()
