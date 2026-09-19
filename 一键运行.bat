@echo off
rem ============================================================
rem  CleanCDrive - run the GUI directly from source code
rem  (Use this only if the .exe does not work on your PC.)
rem
rem  What it does:
rem    1. looks for Python on this computer
rem    2. if found, starts the cleaner right away
rem    3. if not found, tells you where to download Python
rem
rem  This file is intentionally all-ASCII so that cmd.exe can
rem  never mis-parse it. Chinese instructions live in README.md.
rem ============================================================
setlocal
cd /d "%~dp0"
title CleanCDrive

set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY (
    where py >nul 2>nul && set "PY=py"
)
if not defined PY (
    if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
)
if not defined PY (
    if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
)
if not defined PY (
    if exist "C:\Python313\python.exe" set "PY=C:\Python313\python.exe"
)
if not defined PY (
    if exist "C:\Python312\python.exe" set "PY=C:\Python312\python.exe"
)

if not defined PY goto :nopython

echo.
echo  Python found: %PY%
echo  Starting CleanCDrive ... please wait.
echo.
"%PY%" "%~dp0CleanCDrive.py"
if errorlevel 1 (
    echo.
    echo  [!] The program exited with an error.
    echo      Take a screenshot of this window and send it to the author.
    echo.
    pause
)
exit /b 0

:nopython
echo.
echo  ============================================================
echo   Python is NOT installed on this computer.
echo  ============================================================
echo.
echo   You have two options:
echo.
echo   OPTION 1  (easiest) - use the ready-made program instead:
echo             double-click  dist\CleanCDrive.exe
echo             It needs no Python at all.
echo.
echo   OPTION 2  - install Python, then run this file again:
echo             1. open  https://www.python.org/downloads/
echo             2. click the big yellow "Download Python" button
echo             3. run the installer
echo             4. IMPORTANT: tick "Add python.exe to PATH"
echo                at the bottom of the first installer screen
echo             5. click "Install Now" and wait
echo             6. come back and double-click this file again
echo.
echo   Full step-by-step guide (with pictures): README.md
echo.
pause
exit /b 1
