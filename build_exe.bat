@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ============================================================
echo   Build CleanCDrive.exe  (GUI version, single-file)
echo ============================================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found in PATH.
    echo         Please install Python 3.10+ (with tkinter) first.
    pause
    exit /b 1
)

echo [1/3] Installing PyInstaller ...
python -m pip install --upgrade pyinstaller
if errorlevel 1 (
    echo [ERROR] Failed to install PyInstaller.
    pause
    exit /b 1
)

echo.
echo [2/3] Building single-file executable ...
python -m PyInstaller --noconfirm --onefile --windowed ^
    --name CleanCDrive ^
    --add-data "cleanup_config.ini;." ^
    CleanCDrive.py
if errorlevel 1 (
    echo [ERROR] Build failed.
    pause
    exit /b 1
)

echo.
echo [3/3] Done.
echo   Executable : dist\CleanCDrive.exe
echo   Config     : put cleanup_config.ini next to the exe to customize.
echo.
pause
