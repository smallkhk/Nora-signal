; Nora AI Video Call installer additions: register / remove the virtual camera.
;
; Windows 11 (build 22000+): registers the Media Foundation camera source
;   (signal-mfcam.dll). Every app sees it, including Store apps like WhatsApp.
; Windows 10: registers softcam, a DirectShow camera (64- and 32-bit), seen
;   by Chrome, Zoom, OBS, Discord …
; Only one is registered so apps never list "Nora AI Video Call" twice.
; Registering a camera needs admin rights, which is why the installer is
; per-machine.
;
; CLSIDs must match native/patch-softcam.mjs, native/mfcam/dllmain.cpp and
; src/main/vcam.js.
!include LogicLib.nsh
!include x64.nsh

!define VCAM_CLSID "{B7123D88-046A-402A-81EC-B8B252CA709A}"
!define MFCAM_CLSID "{E74F3E0A-EFD3-4242-ABD8-1D8D8AD6403C}"
; DirectShow "Video Capture Sources" category. softcam registers the camera
; there under its friendly name (Instance\Nora AI Video Call).
!define VCAM_CATEGORY "{860BB310-5D01-11d0-BD3B-00A0C911CE86}"

; Windows' Frame Server service loads the Windows 11 camera DLL and keeps it
; locked; stop it so the DLL can be replaced or removed. Windows restarts it on
; demand the next time any app opens a camera.
!macro stopFrameServer
  nsExec::Exec '"$SYSDIR\net.exe" stop FrameServerMonitor /y'
  nsExec::Exec '"$SYSDIR\net.exe" stop FrameServer /y'
!macroend

!macro customInit
  !insertmacro stopFrameServer
!macroend

!macro customUnInit
  !insertmacro stopFrameServer
!macroend

!macro customInstall
  ; Camera settings (orientation) written by the app, read by the Windows 11
  ; camera inside the Frame Server service. Any signed-in user may change it.
  ExpandEnvStrings $1 "%ProgramData%\SIGNAL"
  CreateDirectory "$1"
  nsExec::Exec 'icacls "$1" /grant *S-1-5-11:(OI)(CI)M'

  ; The camera was called "SIGNAL" before the rename; remove its old DirectShow
  ; entry so apps don't list a stale second camera after an upgrade.
  SetRegView 64
  DeleteRegKey HKCR "CLSID\${VCAM_CATEGORY}\Instance\SIGNAL"
  SetRegView 32
  DeleteRegKey HKCR "CLSID\${VCAM_CATEGORY}\Instance\SIGNAL"
  SetRegView lastused

  ReadRegStr $0 HKLM "SOFTWARE\Microsoft\Windows NT\CurrentVersion" "CurrentBuildNumber"
  ${DisableX64FSRedirection}
  ${If} $0 >= 22000
    ; Windows 11: Media Foundation camera only; clear any DirectShow one left
    ; by an older version.
    nsExec::ExecToLog '"$WINDIR\System32\regsvr32.exe" /s "$INSTDIR\resources\vcam\x64\signal-mfcam.dll"'
    nsExec::ExecToLog '"$WINDIR\System32\regsvr32.exe" /s /u "$INSTDIR\resources\vcam\x64\softcam.dll"'
    ${EnableX64FSRedirection}
    nsExec::ExecToLog '"$WINDIR\SysWOW64\regsvr32.exe" /s /u "$INSTDIR\resources\vcam\x86\softcam.dll"'
  ${Else}
    nsExec::ExecToLog '"$WINDIR\System32\regsvr32.exe" /s "$INSTDIR\resources\vcam\x64\softcam.dll"'
    ${EnableX64FSRedirection}
    nsExec::ExecToLog '"$WINDIR\SysWOW64\regsvr32.exe" /s "$INSTDIR\resources\vcam\x86\softcam.dll"'
  ${EndIf}
!macroend

!macro customUnInstall
  ; Try a clean unregister first, then remove the keys directly in case the
  ; DLLs are already gone, so no dead camera is left behind.
  ${DisableX64FSRedirection}
  nsExec::ExecToLog '"$WINDIR\System32\regsvr32.exe" /s /u "$INSTDIR\resources\vcam\x64\signal-mfcam.dll"'
  nsExec::ExecToLog '"$WINDIR\System32\regsvr32.exe" /s /u "$INSTDIR\resources\vcam\x64\softcam.dll"'
  ${EnableX64FSRedirection}
  nsExec::ExecToLog '"$WINDIR\SysWOW64\regsvr32.exe" /s /u "$INSTDIR\resources\vcam\x86\softcam.dll"'
  SetRegView 64
  DeleteRegKey HKLM "SOFTWARE\Classes\CLSID\${MFCAM_CLSID}"
  DeleteRegKey HKCR "CLSID\${VCAM_CLSID}"
  DeleteRegKey HKCR "CLSID\${VCAM_CATEGORY}\Instance\Nora AI Video Call"
  SetRegView 32
  DeleteRegKey HKCR "CLSID\${VCAM_CLSID}"
  DeleteRegKey HKCR "CLSID\${VCAM_CATEGORY}\Instance\Nora AI Video Call"
  SetRegView lastused
!macroend
