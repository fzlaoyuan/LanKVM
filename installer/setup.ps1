# LanKVM 安装程序（由 setup.cmd 调用）
$ErrorActionPreference = 'Continue'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}
$env:PYTHONIOENCODING = 'utf-8'
$Host.UI.RawUI.WindowTitle = 'LanKVM 安装程序'

$Src    = Split-Path -Parent $PSCommandPath
$AppDir = Join-Path $env:LOCALAPPDATA 'LanKVM'
$Helper = Join-Path $AppDir 'helper.ps1'
$Wheels = Join-Path $Src 'wheels'

function Step([string]$t) { Write-Host ''; Write-Host $t -ForegroundColor Cyan }
function Info([string]$t) { Write-Host ('      ' + $t) }
function Fail([string]$t) {
    Write-Host ''
    Write-Host ('  安装未完成：' + $t) -ForegroundColor Red
    Write-Host '  如需帮助，请截图本窗口。'
    Read-Host '  按回车键退出' | Out-Null
    exit 1
}

# 运行 findpy.py：解释器满足要求（3.8+ 且带 tkinter）时返回其真实路径
function Test-Python([string]$exe, [string[]]$pre) {
    try {
        $out = & $exe @pre (Join-Path $Src 'findpy.py') 2>$null | Select-Object -Last 1
        if ($LASTEXITCODE -eq 0 -and $out) {
            $out = "$out".Trim()
            if (Test-Path -LiteralPath $out) { return $out }
        }
    } catch {}
    return $null
}

function Find-SystemPython {
    $cands = New-Object System.Collections.ArrayList
    foreach ($root in 'HKCU:\Software\Python\PythonCore', 'HKLM:\Software\Python\PythonCore',
                      'HKLM:\Software\WOW6432Node\Python\PythonCore') {
        Get-ChildItem $root -ErrorAction SilentlyContinue | Sort-Object PSChildName -Descending | ForEach-Object {
            $ip = Get-ItemProperty -Path (Join-Path $_.PSPath 'InstallPath') -ErrorAction SilentlyContinue
            if ($ip) {
                $e = $ip.ExecutablePath
                if (-not $e -and $ip.'(default)') { $e = Join-Path $ip.'(default)' 'python.exe' }
                if ($e -and (Test-Path -LiteralPath $e)) { [void]$cands.Add(@($e)) }
            }
        }
    }
    foreach ($n in 'py', 'python', 'python3') {
        $c = Get-Command $n -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($c) {
            if ($n -eq 'py') { [void]$cands.Add(@($c.Source, '-3')) } else { [void]$cands.Add(@($c.Source)) }
        }
    }
    foreach ($c in $cands) {
        $pre = @($c | Select-Object -Skip 1)
        $r = Test-Python $c[0] $pre
        if ($r) { return $r }
    }
    return $null
}

try {
    Write-Host '============================================'
    Write-Host '   LanKVM 局域网键鼠共享  安装程序'
    Write-Host '   作者：老白玩AI   官网：https://laobaiai.com/'
    Write-Host '============================================'

    Step '[1/5] 检测本机 Python ...'
    $found = Find-SystemPython
    $PyExe = $null

    Step '[2/5] 准备 Python 运行环境 ...'
    if ($found) {
        Info ('检测到已安装的 Python：' + $found + '（跳过安装）')
        Info '安装运行依赖（离线）...'
        & $found -m pip --version *> $null
        if ($LASTEXITCODE -ne 0) { & $found -m ensurepip --upgrade *> $null }
        & $found -m pip install --no-index --find-links $Wheels pynput pyperclip six --disable-pip-version-check -q 2>&1 | Out-Host
        & $found -c 'import pynput, pyperclip' *> $null
        if ($LASTEXITCODE -eq 0) {
            $PyExe = $found
            Info '依赖安装完成。'
        } else {
            Info '该 Python 无法安装依赖，改用内置 Python。'
        }
    } else {
        Info '未检测到可用的 Python，将安装内置的 Python 3.12（免安装版，不修改系统设置）。'
    }

    Step '[3/5] 复制程序文件 ...'
    if (Test-Path -LiteralPath $Helper) { & $Helper -Action stop; Start-Sleep -Milliseconds 500 }
    New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
    Copy-Item -Path (Join-Path $Src 'app\*') -Destination $AppDir -Recurse -Force -ErrorAction Stop
    Info ('安装位置：' + $AppDir)

    if (-not $PyExe) {
        Info '正在安装内置 Python 3.12 ...'
        $rt = Join-Path $AppDir 'runtime'
        if (Test-Path -LiteralPath $rt) { Remove-Item -LiteralPath $rt -Recurse -Force -ErrorAction Stop }
        Copy-Item -Path (Join-Path $Src 'runtime') -Destination $rt -Recurse -Force -ErrorAction Stop
        $PyExe = Join-Path $rt 'python.exe'
        $env:PYTHONHOME = $null; $env:PYTHONPATH = $null
        $check = & $PyExe -c 'import tkinter, pynput, pyperclip; tkinter.Tcl(); print(6*7)' 2>&1
        if ("$check" -notmatch '42') { Fail ('内置 Python 自检失败：' + "$check") }
        Info '内置 Python 安装完成。'
    }
    $PyW = Join-Path (Split-Path -Parent $PyExe) 'pythonw.exe'
    if (-not (Test-Path -LiteralPath $PyW)) { $PyW = $PyExe }

    Step '[4/5] 创建桌面与开始菜单快捷方式 ...'
    & $Helper -Action shortcuts -Target $PyW -AppDir $AppDir

    Step '[5/5] 添加防火墙放行规则 ...'
    Info '（可能弹出管理员确认；选“否”也能用，首次运行时 Windows 会再询问）'
    & $Helper -Action firewall

    Write-Host ''
    Write-Host '  安装完成！桌面已创建“LanKVM 键鼠共享”，正在启动 ...' -ForegroundColor Green
    Start-Process -FilePath $PyW -ArgumentList ('"' + (Join-Path $AppDir 'lankvm.py') + '"') -WorkingDirectory $AppDir
    Start-Sleep -Seconds 4
    exit 0
} catch {
    Fail ($_.Exception.Message)
}
