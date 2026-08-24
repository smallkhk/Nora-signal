# SIGNAL Desktop (Windows)

Electron shell that wraps the live SIGNAL web app (`https://nora.eclipselivecam.online`)
in a native Windows desktop app. The web app itself already implements camera
capture, WebRTC live streaming, login, credits, and deposits — this wrapper
does not reimplement any of that; it just gives it a real window, tray icon,
persistent session, and desktop conveniences.

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
