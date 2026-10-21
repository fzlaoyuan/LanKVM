@echo off
rem ASCII only: all messages are printed by helper.ps1
cd /d "%TEMP%"
set "PS=%SystemRoot%\Sysnative\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%PS%" set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0helper.ps1" -Action uninstall
