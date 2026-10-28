// Virtual camera bridge (Windows only).
//
// Two camera back-ends, both named "Nora AI Video Call" (see native/):
//   Windows 11: a Media Foundation virtual camera (native/mfcam). Every app
//     sees it, including Microsoft Store apps like WhatsApp.
//   Windows 10: softcam, a DirectShow camera (Chrome, Zoom, OBS, Discord …).
// The installer registers the one that fits the PC. While the app runs, a
// small helper process (nora-vcam.exe) owns the camera and we stream raw
// RGBA frames to its stdin.
const { app } = require("electron");
const { spawn, execFile } = require("child_process");
const path = require("path");
const fs = require("fs");
const os = require("os");

// Must match native/patch-softcam.mjs and build/installer.nsh.
const CAMERA_CLSID = "{B7123D88-046A-402A-81EC-B8B252CA709A}";
const CAMERA_NAME = "Nora AI Video Call";
// DirectShow "Video Capture Sources" category; softcam registers itself here
// under its friendly name.
const VIDEO_CAPTURE_CATEGORY = "{860BB310-5D01-11d0-BD3B-00A0C911CE86}";
// Must match CLSID_VCam in native/mfcam/dllmain.cpp and build/installer.nsh.
const MF_SOURCE_CLSID = "{E74F3E0A-EFD3-4242-ABD8-1D8D8AD6403C}";

// Windows 11 is build 22000 and later (os.release() is "10.0.<build>").
const isWindows11 = () => process.platform === "win32" && Number(os.release().split(".")[2]) >= 22000;

// Landscape (1280x720, like the AI output) or portrait (720x1280).
const SIZES = { landscape: [1280, 720], portrait: [720, 1280] };
const FPS = 15;
let orientation = "landscape";
const size = () => SIZES[orientation];

// The Windows 11 camera runs inside a Windows service, so it reads the
// orientation from this file (see native/mfcam/SharedFrame.h).
const configFile = () => path.join(process.env.ProgramData || "C:\\ProgramData", "NoraAIVideoCall", "camera.cfg");
function writeConfig() {
  if (process.platform !== "win32") return;
  try {
    fs.mkdirSync(path.dirname(configFile()), { recursive: true });
    fs.writeFileSync(configFile(), orientation);
  } catch (err) {
    // The Windows 11 camera only accepts frames of the size this file names;
    // surface a failure instead of silently showing a blank camera.
    detail = `could not write ${configFile()} (${err.code || err.message})`;
  }
}

function vcamDir() {
  return app.isPackaged
    ? path.join(process.resourcesPath, "vcam")
    : path.join(__dirname, "native", "out");
}
const helperPath = () => process.env.NORA_VCAM_HELPER || path.join(vcamDir(), "nora-vcam.exe");
const dllPath = (arch = "x64") => path.join(vcamDir(), arch, "softcam.dll");
const mfDllPath = () => path.join(vcamDir(), "x64", "nora-mfcam.dll");

let proc = null;
let state = "off"; // off | starting | ready | error | unsupported
let errorReason = "";
let detail = ""; // last diagnostic line from the helper (why the Windows 11 camera fell back)
let mode = null; // 'mf' (Windows 11 camera) | 'ds' (DirectShow) once running
let installed = null; // null = unknown
let dropped = 0;
let listeners = [];

function emit() {
  const s = status();
  listeners.forEach((fn) => fn(s));
}

function status() {
  const [width, height] = size();
  return { state, error: errorReason, detail, installed, mode, win11: isWindows11(), orientation, width, height, fps: FPS, name: CAMERA_NAME };
}

function onStatus(fn) {
  listeners.push(fn);
}

function isSupported() {
  return process.platform === "win32" || !!process.env.NORA_VCAM_HELPER;
}

const regKeyExists = (key) =>
  new Promise((resolve) => {
    execFile("reg", ["query", key], { windowsHide: true }, (err) => resolve(!err));
  });

// Is the camera registered, i.e. can other apps see it?
// Windows 11: the Media Foundation source must be registered in HKLM.
// Windows 10: the DirectShow filter needs two entries: the COM class (so apps
// can load the DLL) and the "Video Capture Sources" entry (so apps like OBS
// list it as a camera).
async function checkInstalled() {
  if (process.env.NORA_VCAM_HELPER) return true;
  if (process.platform !== "win32") return false;
  if (isWindows11()) {
    return regKeyExists(`HKLM\\SOFTWARE\\Classes\\CLSID\\${MF_SOURCE_CLSID}\\InprocServer32`);
  }
  const [comClass, deviceEntry] = await Promise.all([
    regKeyExists(`HKCR\\CLSID\\${CAMERA_CLSID}\\InprocServer32`),
    regKeyExists(`HKCR\\CLSID\\${VIDEO_CAPTURE_CATEGORY}\\Instance\\${CAMERA_NAME}`),
  ]);
  return comClass && deviceEntry;
}

async function refreshInstalled() {
  installed = await checkInstalled();
  emit();
  return installed;
}

// Register the camera (UAC prompt). Normally the installer does this; this is
// the repair path if it was skipped or another tool removed it.
function install() {
  if (process.platform !== "win32") return Promise.resolve(false);
  const regsvr = path.join(process.env.WINDIR || "C:\\Windows", "System32", "regsvr32.exe");
  const regsvr32bit = path.join(process.env.WINDIR || "C:\\Windows", "SysWOW64", "regsvr32.exe");
  const q = (s) => `'${s.replace(/'/g, "''")}'`;
  const run = (exe, args) => `Start-Process -FilePath ${q(exe)} -ArgumentList ${args.map(q).join(",")} -Verb RunAs -Wait`;
  const cmds = [];
  if (isWindows11()) {
    // One camera that every app sees; remove the DirectShow one so apps
    // don't list "Nora AI Video Call" twice.
    cmds.push(run(regsvr, ["/s", `"${mfDllPath()}"`]));
    cmds.push(run(regsvr, ["/s", "/u", `"${dllPath("x64")}"`]));
    if (fs.existsSync(regsvr32bit)) cmds.push(run(regsvr32bit, ["/s", "/u", `"${dllPath("x86")}"`]));
  } else {
    cmds.push(run(regsvr, ["/s", `"${dllPath("x64")}"`]));
    if (fs.existsSync(dllPath("x86")) && fs.existsSync(regsvr32bit)) {
      cmds.push(run(regsvr32bit, ["/s", `"${dllPath("x86")}"`]));
    }
  }
  return new Promise((resolve) => {
    execFile(
      "powershell.exe",
      ["-NoProfile", "-NonInteractive", "-Command", cmds.join("; ")],
      { windowsHide: true },
      async () => resolve(await refreshInstalled())
    );
  });
}

function start() {
  if (proc) return;
  if (!isSupported()) {
    state = "unsupported";
    emit();
    return;
  }
  if (!fs.existsSync(helperPath())) {
    state = "error";
    errorReason = "helper-missing";
    emit();
    return;
  }

  state = "starting";
  errorReason = "";
  detail = "";
  emit();
  writeConfig();
  const [width, height] = size();
  const args = [dllPath("x64"), String(width), String(height), String(FPS)];
  const child = spawn(helperPath(), args, { windowsHide: true, stdio: ["pipe", "pipe", "pipe"] });
  proc = child;

  // The helper explains on stderr why the Windows 11 camera could not start
  // before it falls back to DirectShow; keep the last line for the tray.
  let errOut = "";
  child.stderr.on("data", (chunk) => {
    errOut += chunk.toString();
    const lines = errOut.split(/\r?\n/).filter(Boolean);
    if (lines.length) detail = lines[lines.length - 1].trim();
    errOut = errOut.slice(-2048);
    emit();
  });

  let out = "";
  child.stdout.on("data", (chunk) => {
    out += chunk.toString();
    let nl;
    while ((nl = out.indexOf("\n")) >= 0) {
      const line = out.slice(0, nl).trim();
      out = out.slice(nl + 1);
      if (line.startsWith("READY")) {
        state = "ready";
        mode = line.split(" ")[1] || null;
        emit();
      } else if (line.startsWith("ERROR")) {
        state = "error";
        errorReason = line.slice(6);
        emit();
      }
    }
  });
  // The helper exiting closes the pipe; swallow the resulting EPIPE.
  child.stdin.on("error", () => {});
  child.on("error", (err) => {
    if (proc === child) proc = null;
    state = "error";
    errorReason = err.code || "spawn-failed";
    emit();
  });
  child.on("exit", () => {
    if (proc === child) proc = null;
    if (state !== "error") {
      state = "off";
      emit();
    }
  });
}

// Called for every frame the renderer captures. Frames are dropped rather than
// queued when the helper falls behind, so the camera never lags behind live.
function frame(data) {
  if (!proc || state !== "ready") return;
  const [width, height] = size();
  if (data.byteLength !== width * height * 4) return;
  if (proc.stdin.writableNeedDrain) {
    dropped++;
    return;
  }
  proc.stdin.write(Buffer.from(data.buffer, data.byteOffset, data.byteLength));
}

// Resolves once the helper has exited, so a restart never overlaps the old
// camera (two cameras with one name would clash).
function stop() {
  const child = proc;
  proc = null;
  if (!child) return Promise.resolve();
  const exited = new Promise((resolve) => {
    if (child.exitCode !== null) resolve();
    else child.once("exit", resolve);
  });
  try {
    child.stdin.end();
  } catch {}
  // The helper exits on stdin EOF; make sure it does.
  const timer = setTimeout(() => {
    try {
      child.kill();
    } catch {}
  }, 2000);
  exited.then(() => clearTimeout(timer));
  state = "off";
  emit();
  return exited;
}

async function restart() {
  await stop();
  start();
}

// Apps pick up the new shape the next time they open the camera.
async function setOrientation(value) {
  if (!SIZES[value] || value === orientation) return status();
  orientation = value;
  if (proc) await restart();
  else emit();
  return status();
}

module.exports = { start, stop, restart, frame, status, onStatus, install, refreshInstalled, isSupported, setOrientation, FPS };
