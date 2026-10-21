param([string]$Action, [string]$Target = '', [string]$AppDir = '')
$ErrorActionPreference = 'SilentlyContinue'

$Name     = 'LanKVM 键鼠共享'
$UnName   = '卸载 LanKVM'
$SiteName = '老白玩AI 官网'
$SiteUrl  = 'https://laobaiai.com/'
$Key      = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\LanKVM'
$Desktop  = [Environment]::GetFolderPath('Desktop')
$Programs = [Environment]::GetFolderPath('Programs')
if (-not $AppDir) { $AppDir = Split-Path -Parent $PSCommandPath }

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return ([Security.Principal.WindowsPrincipal]$id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

switch ($Action) {
    'stop' {
        Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*lankvm.py*' } |
            ForEach-Object { Invoke-CimMethod -InputObject $_ -MethodName Terminate | Out-Null }
    }
    'shortcuts' {
        $ws = New-Object -ComObject WScript.Shell
        $ico = Join-Path $AppDir 'lankvm.ico'
        foreach ($dir in @($Desktop, $Programs)) {
            $l = $ws.CreateShortcut((Join-Path $dir "$Name.lnk"))
            $l.TargetPath = $Target
            $l.Arguments = '"' + (Join-Path $AppDir 'lankvm.py') + '"'
            $l.WorkingDirectory = $AppDir
            $l.IconLocation = "$ico,0"
            $l.Description = '局域网键鼠共享 / 穿屏 / 剪贴板 / 文件互传'
            $l.Save()
        }
        $un = Join-Path $AppDir 'uninstall.cmd'
        $u = $ws.CreateShortcut((Join-Path $Programs "$UnName.lnk"))
        $u.TargetPath = $un
        $u.WorkingDirectory = $AppDir
        $u.IconLocation = "$ico,0"
        $u.Save()
        $site = $ws.CreateShortcut((Join-Path $Programs "$SiteName.url"))
        $site.TargetPath = $SiteUrl
        $site.Save()
        New-Item -Path $Key -Force | Out-Null
        Set-ItemProperty -Path $Key -Name DisplayName     -Value 'LanKVM 局域网键鼠共享'
        Set-ItemProperty -Path $Key -Name DisplayVersion  -Value '1.2.0'
        Set-ItemProperty -Path $Key -Name Publisher       -Value '老白玩AI'
        Set-ItemProperty -Path $Key -Name URLInfoAbout    -Value $SiteUrl
        Set-ItemProperty -Path $Key -Name HelpLink        -Value $SiteUrl
        Set-ItemProperty -Path $Key -Name InstallLocation -Value $AppDir
        Set-ItemProperty -Path $Key -Name DisplayIcon     -Value $ico
        Set-ItemProperty -Path $Key -Name UninstallString -Value ('cmd.exe /c "' + $un + '"')
        New-ItemProperty -Path $Key -Name NoModify -Value 1 -PropertyType DWord -Force | Out-Null
        New-ItemProperty -Path $Key -Name NoRepair -Value 1 -PropertyType DWord -Force | Out-Null
    }
    'unshortcuts' {
        Remove-Item -Force (Join-Path $Desktop "$Name.lnk"), (Join-Path $Programs "$Name.lnk"), (Join-Path $Programs "$UnName.lnk"), (Join-Path $Programs "$SiteName.url")
        Remove-Item -Path $Key -Recurse -Force
    }
    'uninstall' {
        $Host.UI.RawUI.WindowTitle = '卸载 LanKVM'
        Write-Host '正在卸载 LanKVM ...'
        & $PSCommandPath -Action stop
        & $PSCommandPath -Action unshortcuts
        Write-Host '正在移除防火墙规则（可能弹出管理员确认）...'
        & $PSCommandPath -Action unfirewall
        Write-Host ''
        Write-Host '已卸载。已接收的文件（下载\LanKVM）以及系统中原有的 Python 均保留。' -ForegroundColor Green
        Set-Location $env:TEMP
        Start-Process -FilePath cmd.exe -WindowStyle Hidden -WorkingDirectory $env:TEMP -ArgumentList (
            '/c timeout /t 2 /nobreak >nul & rmdir /s /q "' + $AppDir + '"')
        Start-Sleep -Seconds 3
    }
    { $_ -in 'firewall', 'unfirewall' } {
        if (-not (Test-Admin)) {
            try {
                Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden -ArgumentList @(
                    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $PSCommandPath + '"'), '-Action', $Action)
            } catch {
                Write-Host '      已跳过防火墙设置。'
            }
            return
        }
        Remove-NetFirewallRule -DisplayName 'LanKVM'
        if ($Action -eq 'firewall') {
            New-NetFirewallRule -DisplayName 'LanKVM' -Direction Inbound -Protocol TCP -LocalPort 24800, 24802 -Action Allow -Profile Any | Out-Null
            New-NetFirewallRule -DisplayName 'LanKVM' -Direction Inbound -Protocol UDP -LocalPort 24801 -Action Allow -Profile Any | Out-Null
        }
    }
}
