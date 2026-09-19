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
echo  C 盘安全清理工具
echo  删除前会展示完整清单并需要你输入 YES 确认。
echo  参数：可附加 -DryRun 仅预览；-SkipRecycleBin 跳过回收站
echo ============================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" %*

endlocal
echo.
pause
