@echo off
rem ============================================================
rem  CleanCDrive - build your own single-file .exe
rem
rem  Use this if you want to regenerate dist\CleanCDrive.exe
rem  from the source code (for example after editing it).
rem
rem  Requirements: Python 3.10+ installed with "Add to PATH".
rem  PyInstaller is installed automatically on the next step.
rem
rem  All-ASCII on purpose - see README.md for Chinese notes.
rem ============================================================
setlocal
cd /d "%~dp0"
title CleanCDrive Builder
chcp 65001 >nul

set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY (
    where py >nul 2>nul && set "PY=py"
)
if not defined PY goto :nopython

echo ============================================================
echo   Building CleanCDrive.exe  (single file, GUI version)
echo ============================================================
echo.

echo [1/3] Installing / updating PyInstaller ...
"%PY%" -m pip install --upgrade --quiet pyinstaller
if errorlevel 1 (
    echo.
    echo  [!] Could not install PyInstaller.
    echo      Check your internet connection, then run this file again.
    echo.
    pause
    exit /b 1
)

echo [2/3] Packaging ... this may take 1-3 minutes, please wait.
"%PY%" -m PyInstaller --noconfirm --clean --onefile --windowed ^
    --name CleanCDrive ^
    --add-data "cleanup_config.ini;." ^
    CleanCDrive.py
if errorlevel 1 (
    echo.
    echo  [!] Build failed. Take a screenshot of this window and send it to the author.
    echo.
    pause
    exit /b 1
)

echo.
echo [3/3] Finished.
echo.
echo   Your new program : dist\CleanCDrive.exe
echo   Settings file    : put cleanup_config.ini next to the exe
echo.
echo   You can now double-click dist\CleanCDrive.exe to start it.
echo.
pause
exit /b 0

:nopython
echo.
echo  ============================================================
echo   Python is NOT installed - cannot build the exe.
echo  ============================================================
echo.
echo   1. open  https://www.python.org/downloads/
echo   2. click "Download Python"
echo   3. run the installer and TICK "Add python.exe to PATH"
echo   4. click "Install Now"
echo   5. double-click this file again
echo.
echo   Or simply use the ready-made  dist\CleanCDrive.exe
echo.
pause
exit /b 1
