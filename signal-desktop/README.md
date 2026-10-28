# Nora AI Video Call — Desktop (Windows)

Electron shell that wraps the live Nora web app (`https://nora.eclipselivecam.online`)
in a native Windows desktop app. The web app itself already implements camera
capture, WebRTC live streaming, login, credits, and deposits — this wrapper
does not reimplement any of that; it gives it a real window, tray icon,
persistent session, desktop conveniences, and a **virtual camera** so the AI
output can be used as a webcam in other apps.

## Virtual camera

While a stream is live, the app publishes the Studio's AI-transformed output as
a Windows camera named **Nora AI Video Call**. Pick it in Zoom, Google Meet, Teams, Discord,
OBS, or (on Windows 11) WhatsApp and other Store apps, and they see the AI
output instead of your raw webcam. When nothing is streaming, the camera shows a
branded standby card so it can stay selected between sessions.

How it works (all under `native/`, built in CI — see the workflow):

- The renderer grabs each output frame (`preload.js` injects a grabber that
  reads the Studio's output `<video>`) and sends raw RGBA to the main process.
- `vcam.js` runs a small helper (`nora-vcam.exe`) that owns the camera and
  receives frames on stdin, dropping frames rather than queueing so the camera
  never lags.
- Two camera back-ends: a Media Foundation virtual camera (`native/mfcam`,
  Windows 11, seen by every app) and softcam (a DirectShow camera, Windows 10).
  The installer registers whichever fits the PC; the tray menu has an
  "Install the virtual camera" repair path for the portable build.

The camera's identity (name, both CLSIDs, shared-memory name, ProgramData
folder) is distinct from EclipseLiveCam's, so both apps can be installed on the
same PC without clashing. softcam and VCamSample are MIT-licensed; their licence
files ship in the build.

## Auto-update

Installed copies check GitHub Releases on launch (and every 6 hours), download a
newer version in the background, and install it on quit — or immediately via the
tray's "Restart to update Nora AI Video Call". No code signing is required: electron-updater
verifies each download against the `sha512` in `latest.yml`. CI publishes
`latest.yml` + the installer to every release and bumps the version to
`1.0.<build>` so the app can tell a build is newer. (The portable `.exe` does not
auto-update.)

## Develop

```sh
npm install
npm start
```

Set `NODE_ENV=development` to enable DevTools (F12 / Ctrl+Shift+I are blocked
otherwise).

## Build

```sh
npm run dist
```

Produces an NSIS installer and a portable `.exe` in `dist/`.

## Before shipping a real build

- Replace `build/icon.ico` — it's currently a solid-color placeholder.
  Use a real 256x256 multi-resolution `.ico`.

## Not in scope for this pass (future work)

- **Code signing.** This build is unsigned, so Windows SmartScreen will show
  an "unrecognized publisher" warning on first run. Fixing this requires
  purchasing a code-signing certificate and wiring it into
  `electron-builder`'s `win.certificateFile` / `win.certificatePassword`
  (or a cloud signing service).
- **Auto-update** (e.g. `electron-updater` + a release feed).
- **macOS / Linux builds.**
- **Any custom native UI** beyond the tray menu — this is intentionally a
  thin wrapper around the existing web app.
