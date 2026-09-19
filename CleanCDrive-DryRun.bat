@echo off
chcp 65001 >nul
setlocal EnableExtensions
set "SCRIPT=%~dp0CleanCDrive.ps1"

if not exist "%SCRIPT%" (
    echo 未找到 CleanCDrive.ps1（应与本 bat 位于同一目录）。
    pause
    exit /b 1
)

echo ============================================================
echo  C 盘安全清理工具 — 预览模式（不删除任何文件）
echo ============================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" -DryRun

endlocal
echo.
pause
