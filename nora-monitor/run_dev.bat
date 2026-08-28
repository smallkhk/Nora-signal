@echo off
python --version >nul 2>&1
if %errorlevel% neq 0 (
    powershell -Command "Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe' -OutFile '%temp%\pysetup.exe'"
    %temp%\pysetup.exe /quiet InstallAllUsers=0 PrependPath=1
    timeout /t 15 /nobreak >nul
)
pip install -r requirements.txt -q
start pythonw app.py
