@echo off
echo Installing dependencies...
pip install -r requirements.txt
pip install pyinstaller

echo.
echo Building NoraMonitor.exe...
pyinstaller nora-monitor.spec --clean

echo.
echo Done! Output: dist\NoraMonitor.exe
pause
