const { app, BrowserWindow, Tray, Menu, shell, session, Notification, nativeImage, ipcMain } = require("electron");
const path = require("path");
const Store = require("electron-store");
const vcam = require("./vcam");
const { autoUpdater } = require("electron-updater");
const fs = require("fs");

// One-time copy of the login session out of the old shared folder. Only our
// own partition folder ("Partitions/signal") is read; nothing in the Signal
// messenger's files is written, moved or deleted.
function migrateLoginFromOldFolder() {
  try {
    const oldPartition = path.join(app.getPath("appData"), "SIGNAL", "Partitions", "signal");
    const newPartition = path.join(app.getPath("userData"), "Partitions", PARTITION_NAME);
    if (fs.existsSync(newPartition) || !fs.existsSync(oldPartition)) return;
    fs.mkdirSync(path.dirname(newPartition), { recursive: true });
    fs.cpSync(oldPartition, newPartition, {
      recursive: true,
      // Lock files belong to the old running instance; never copy them.
      filter: (src) => !/(^|[\\/])(LOCK|lockfile|SingletonLock)$/i.test(src),
    });
  } catch {
    // If the copy fails the user simply logs in again.
  }
}

// Some Windows GPU/driver combos paint <video> and WebGL surfaces as solid
// blank/black inside Electron's hardware compositor (both the camera preview
// and the transformed output come up blank). Falling back to software
// compositing fixes that. Must run before the app is ready.
app.disableHardwareAcceleration();

const APP_NAME = "Nora AI Video Call";
const PARTITION_NAME = "nora";

// Our own data folder. Earlier versions were named "SIGNAL" and so used
// %APPDATA%\SIGNAL — the same folder (Windows ignores case) as the Signal
// messenger app, which made the two apps share Electron's single-instance lock
// and get in each other's way.
app.setPath("userData", path.join(app.getPath("appData"), APP_NAME));
migrateLoginFromOldFolder();

const APP_URL = "https://nora.eclipselivecam.online";
const APP_ORIGIN = new URL(APP_URL).origin;
const PARTITION = `persist:${PARTITION_NAME}`;
const IS_DEV = process.env.NODE_ENV === "development";
const ICON_PATH = path.join(__dirname, "build/icon.ico");

// Permissions auto-granted to the app's own origin only. 'media' is what
// getUserMedia needs for camera+mic; 'fullscreen' is what the site's video
// fullscreen button needs. Everything else — and every other origin — is
// denied. (Picture-in-Picture needs no permission entry; it works natively.)
const ALLOWED_PERMISSIONS = new Set(["media", "fullscreen"]);

const store = new Store();
let mainWindow = null;
let tray = null;
let isQuitting = false;
let updateReady = false; // a newer version has been downloaded, pending restart
let inputCameras = []; // real webcams reported by the page: [{ label }]
let streamStoppedForQuit = false;
let lastStreamFailure = ""; // why the last stream was auto-stopped, shown in the tray

/**
 * Strict same-origin test. Deliberately NOT a `startsWith` check: the string
 * "https://nora.eclipselivecam.online" is also a prefix of a hostile URL like
 * "https://nora.eclipselivecam.online.example.com", which must be treated as
 * external. Non-http schemes (mailto:, etc.) parse to a null origin and so are
 * correctly classified as external too.
 */
function isAppUrl(url) {
  try {
    return new URL(url).origin === APP_ORIGIN;
  } catch {
    return false;
  }
}

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.show();
      mainWindow.focus();
    }
  });

  // Required on Windows for native notifications to display reliably; must
  // match the electron-builder appId.
  app.setAppUserModelId("online.eclipselivecam.signal");

  // Quitting: stop any running stream first (so it is billed and closed
  // properly), then quit. If the page doesn't answer in time it is killed,
  // which drops the stream anyway.
  app.on("before-quit", (event) => {
    isQuitting = true;
    if (streamStoppedForQuit) {
      vcam.stop();
      return;
    }
    event.preventDefault();
    streamStoppedForQuit = true;
    stopStreamInPage().finally(() => app.quit());
  });

  app.whenReady().then(() => {
    // The window renders in the "persist:nora" partition, so the permission
    // handlers MUST be installed on that session — handlers on
    // session.defaultSession would never be consulted for this window.
    const appSession = session.fromPartition(PARTITION);

    // Async path: an actual getUserMedia() / fullscreen request.
    appSession.setPermissionRequestHandler((webContents, permission, callback, details) => {
      const requestingUrl = (details && details.requestingUrl) || (webContents ? webContents.getURL() : "");
      callback(ALLOWED_PERMISSIONS.has(permission) && isAppUrl(requestingUrl));
    });

    // Sync path: Chromium consults this for navigator.permissions.query() and
    // for exposing device labels to enumerateDevices(). Without it the site
    // can see permission as "denied" even though the request handler grants.
    appSession.setPermissionCheckHandler((webContents, permission, requestingOrigin) => {
      const origin = requestingOrigin || (webContents ? webContents.getURL() : "");
      return ALLOWED_PERMISSIONS.has(permission) && isAppUrl(origin);
    });

    // Virtual camera: start feeding the "Nora AI Video Call" camera (Windows only; a no-op
    // elsewhere). The renderer's injected grabber pushes frames over IPC.
    ipcMain.on("vcam:frame", (e, data) => vcam.frame(data));

    // Input camera: the page reports the real cameras it can see (our own
    // virtual camera already filtered out) and asks which one to use.
    // "" means automatic (first real webcam).
    ipcMain.on("camera:devices", (e, list) => {
      if (Array.isArray(list)) inputCameras = list.filter((d) => d && typeof d.label === "string");
      updateTrayMenu();
    });
    ipcMain.handle("camera:preference", () => store.get("inputCamera", ""));

    // The page stopped a stream that never went live (so fal stops billing).
    ipcMain.on("stream:auto-stopped", (e, reason) => {
      lastStreamFailure = String(reason || "unknown reason").slice(0, 200);
      updateTrayMenu();
      if (Notification.isSupported()) {
        new Notification({
          title: `${APP_NAME}: stream stopped`,
          body: `The AI output didn't start within 15 seconds, so the stream was stopped to save credits. Reason: ${lastStreamFailure}`,
        }).show();
      }
    });
    ipcMain.handle("vcam:status", () => vcam.status());
    ipcMain.handle("vcam:install", async () => {
      await vcam.install();
      await vcam.restart();
      return vcam.status();
    });
    vcam.onStatus(() => {
      updateTrayMenu();
      const wc = mainWindow && !mainWindow.isDestroyed() ? mainWindow.webContents : null;
      if (wc) wc.send("vcam:status", vcam.status());
    });
    vcam.refreshInstalled();
    vcam.setOrientation(store.get("cameraOrientation", "landscape")).then(() => vcam.start());

    createWindow();
    createTray();
    setupUpdater();

    app.on("activate", () => {
      if (BrowserWindow.getAllWindows().length === 0) {
        createWindow();
      } else if (mainWindow) {
        mainWindow.show();
        mainWindow.focus();
      }
    });
  });
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 900,
    minHeight: 600,
    title: APP_NAME,
    // NOTE: build/icon.ico is a placeholder. Drop in a real 256x256
    // multi-resolution .ico here before the final build.
    icon: ICON_PATH,
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      partition: PARTITION, // persistent session: cookies/localStorage survive restarts
      devTools: IS_DEV,
      contextIsolation: true,
      nodeIntegration: false,
      // Keep feeding the virtual camera while the window is minimised or
      // hidden behind Zoom — that is exactly when it is being used.
      backgroundThrottling: false,
    },
  });

  // Kiosk-style single-site wrapper: no File/Edit/View browser chrome.
  Menu.setApplicationMenu(null);

  mainWindow.loadURL(APP_URL);

  // The website sets its own <title>; keep the window named after the app.
  mainWindow.on("page-title-updated", (event) => {
    event.preventDefault();
    mainWindow.setTitle(APP_NAME);
  });

  // Block devtools shortcuts in production builds.
  if (!IS_DEV) {
    mainWindow.webContents.on("before-input-event", (event, input) => {
      const key = (input.key || "").toLowerCase();
      if (key === "f12") {
        event.preventDefault();
        return;
      }
      if (input.control && input.shift && (key === "i" || key === "j" || key === "c")) {
        event.preventDefault();
      }
    });
  }

  // Any navigation/window-open that would leave the app's origin opens in
  // the user's default system browser instead of inside the app window.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (!isAppUrl(url)) {
      shell.openExternal(url);
      return { action: "deny" };
    }
    return { action: "allow" };
  });

  mainWindow.webContents.on("will-navigate", (event, url) => {
    if (!isAppUrl(url)) {
      event.preventDefault();
      shell.openExternal(url);
    }
  });

  // A frozen page keeps streaming (the camera and the WebRTC link to Decart
  // run on their own threads) while it stops sending billing heartbeats, so
  // fal keeps charging. Kill a frozen or crashed page straight away.
  // Only act if it stays frozen for 5 more seconds, so a brief hiccup under
  // load never cuts a working stream.
  let frozenTimer = null;
  mainWindow.on("unresponsive", () => {
    clearTimeout(frozenTimer);
    frozenTimer = setTimeout(() => hardResetPage("The app stopped responding"), 5000);
  });
  mainWindow.on("responsive", () => clearTimeout(frozenTimer));
  mainWindow.webContents.on("render-process-gone", (event, details) => {
    if (details.reason === "clean-exit") return;
    hardResetPage("The app crashed");
  });

  mainWindow.on("close", (event) => {
    if (!isQuitting) {
      event.preventDefault();
      // Closing the window must not leave a stream running in the tray.
      stopStreamInPage();
      mainWindow.hide();
      if (!store.get("trayNoticeShown")) {
        if (Notification.isSupported()) {
          new Notification({
            title: APP_NAME,
            body: `${APP_NAME} is still running in the tray.`,
          }).show();
        }
        store.set("trayNoticeShown", true);
      }
    }
  });

  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

// ── Auto-update ────────────────────────────────────────────────────────────
// Checks GitHub Releases, downloads a newer version in the background, and
// installs it on quit (or immediately via the tray). No code signing needed:
// electron-updater verifies the download against the sha512 in latest.yml.
function setupUpdater() {
  if (!app.isPackaged) return; // the updater only works in a packaged build
  autoUpdater.autoDownload = true;
  autoUpdater.autoInstallOnAppQuit = true;

  autoUpdater.on("update-downloaded", (info) => {
    updateReady = true;
    updateTrayMenu();
    if (Notification.isSupported()) {
      const n = new Notification({
        title: `${APP_NAME} update ready`,
        body: `Version ${info.version} installs when you restart. Click to restart now.`,
      });
      n.on("click", installUpdateNow);
      n.show();
    }
  });
  // Never let a failed check crash or interrupt the app.
  autoUpdater.on("error", () => {});

  const check = () => autoUpdater.checkForUpdates().catch(() => {});
  check();
  setInterval(check, 6 * 60 * 60 * 1000); // re-check every 6 hours
}

function installUpdateNow() {
  isQuitting = true;
  stopStreamInPage().finally(() => {
    streamStoppedForQuit = true;
    vcam.stop();
    autoUpdater.quitAndInstall();
  });
}

// ── Stopping a stream from the app ─────────────────────────────────────────
function pageContents() {
  return mainWindow && !mainWindow.isDestroyed() ? mainWindow.webContents : null;
}

// Press the Studio's own Stop button (ends and bills the session properly).
// If the page doesn't answer within 3 seconds it is frozen: kill it.
function stopStreamInPage() {
  const wc = pageContents();
  if (!wc || wc.isCrashed()) return Promise.resolve(false);
  const press = wc
    .executeJavaScript("window.__noraStopStream ? window.__noraStopStream() : false", true)
    .catch(() => false);
  const timeout = new Promise((resolve) => setTimeout(() => resolve("timeout"), 3000));
  return Promise.race([press, timeout]).then((result) => {
    if (result === "timeout") {
      hardResetPage("The app did not respond to Stop");
      return true;
    }
    return result;
  });
}

// Kill the page's process (this drops the camera and the WebRTC link to
// Decart, so fal stops charging), then load it again.
let resetting = false;
function hardResetPage(reason) {
  const wc = pageContents();
  if (!wc || resetting) return;
  resetting = true;
  try {
    if (!wc.isCrashed()) wc.forcefullyCrashRenderer();
  } catch {}
  setTimeout(() => {
    resetting = false;
    if (!wc.isDestroyed()) wc.reload();
  }, 1000);
  if (Notification.isSupported()) {
    new Notification({
      title: APP_NAME,
      body: `${reason}, so the live stream was stopped to protect your credits. Reloading…`,
    }).show();
  }
}

function showMainWindow() {
  if (!mainWindow) {
    createWindow();
    return;
  }
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.show();
  mainWindow.focus();
}

// Human-readable one-line summary of the virtual camera for the tray.
function cameraStatusLabel() {
  const s = vcam.status();
  if (s.state === "unsupported") return "Camera: Windows only";
  if (s.state === "error") return `Camera: error (${s.error || "unknown"})`;
  if (s.state === "starting") return "Camera: starting…";
  if (s.state === "ready" && s.installed === false) return "Camera: not installed";
  if (s.state === "ready" && s.mode === "mf") return `Camera: ${s.name} — on (Windows 11 camera, all apps)`;
  if (s.state === "ready" && s.mode === "ds" && s.win11) return `Camera: ${s.name} — on (basic mode: WhatsApp/Store apps can't see it)`;
  if (s.state === "ready") return `Camera: ${s.name} — on`;
  return "Camera: off";
}

function updateTrayMenu() {
  if (!tray) return;
  const s = vcam.status();
  const launchAtStartup = app.getLoginItemSettings().openAtLogin;

  const template = [
    { label: `Open ${APP_NAME}`, click: showMainWindow },
    { label: "Stop stream now", click: () => stopStreamInPage() },
    ...(lastStreamFailure ? [{ label: `Last failed start: ${lastStreamFailure}`.slice(0, 120), enabled: false }] : []),
    { type: "separator" },
    { label: cameraStatusLabel(), enabled: false },
  ];
  if (s.state === "ready" && s.mode === "ds" && s.win11 && s.detail) {
    template.push({ label: `Why: ${s.detail}`.slice(0, 120), enabled: false });
  }

  if (s.state === "ready" && s.installed === false) {
    template.push({
      label: "Install the virtual camera…",
      click: async () => {
        const after = await vcam.install().then(() => vcam.status()).catch(() => null);
        if (after && after.installed && Notification.isSupported()) {
          new Notification({ title: APP_NAME, body: `${APP_NAME} camera installed. Restart Zoom/Meet/WhatsApp if it was open.` }).show();
        }
        updateTrayMenu();
      },
    });
  }

  // Which real webcam feeds the AI (never the app's own virtual camera).
  const chosen = store.get("inputCamera", "");
  const pickInput = (label) => {
    store.set("inputCamera", label);
    const wc = mainWindow && !mainWindow.isDestroyed() ? mainWindow.webContents : null;
    if (wc) wc.send("camera:preference", label);
    updateTrayMenu();
  };
  template.push({
    label: "Input webcam",
    submenu: [
      { label: "Automatic (first real webcam)", type: "radio", checked: !chosen, click: () => pickInput("") },
      ...inputCameras.map((d) => ({
        label: d.label,
        type: "radio",
        checked: chosen === d.label,
        click: () => pickInput(d.label),
      })),
      ...(inputCameras.length ? [] : [{ label: "Open the app once to list webcams", enabled: false }]),
    ],
  });

  if (vcam.isSupported()) {
    template.push({
      label: "Camera orientation",
      submenu: [
        {
          label: "Landscape (16:9)",
          type: "radio",
          checked: s.orientation === "landscape",
          click: () => {
            store.set("cameraOrientation", "landscape");
            vcam.setOrientation("landscape").then(updateTrayMenu);
          },
        },
        {
          label: "Portrait (9:16)",
          type: "radio",
          checked: s.orientation === "portrait",
          click: () => {
            store.set("cameraOrientation", "portrait");
            vcam.setOrientation("portrait").then(updateTrayMenu);
          },
        },
      ],
    });
  }

  if (app.isPackaged) {
    template.push({ type: "separator" });
    if (updateReady) {
      template.push({ label: `Restart to update ${APP_NAME}`, click: installUpdateNow });
    } else {
      template.push({
        label: "Check for updates…",
        click: () => {
          autoUpdater.checkForUpdates().catch(() => {});
          if (Notification.isSupported()) {
            new Notification({ title: APP_NAME, body: "Checking for updates…" }).show();
          }
        },
      });
    }
  }

  template.push(
    { type: "separator" },
    {
      label: "Launch on startup",
      type: "checkbox",
      checked: launchAtStartup,
      click: (item) => {
        app.setLoginItemSettings({ openAtLogin: item.checked });
        store.set("launchAtStartup", item.checked);
        updateTrayMenu();
      },
    },
    { type: "separator" },
    {
      label: "Quit",
      click: () => {
        isQuitting = true;
        app.quit();
      },
    }
  );

  tray.setContextMenu(Menu.buildFromTemplate(template));
}

function createTray() {
  const icon = nativeImage.createFromPath(ICON_PATH);
  tray = new Tray(icon);
  tray.setToolTip(APP_NAME);

  // Reconcile the persisted "launch on startup" preference (default OFF) with
  // the OS before the menu is first built, so the checkbox matches reality.
  const persistedLaunchAtStartup = store.get("launchAtStartup", false);
  if (persistedLaunchAtStartup !== app.getLoginItemSettings().openAtLogin) {
    app.setLoginItemSettings({ openAtLogin: persistedLaunchAtStartup });
  }

  updateTrayMenu();
  tray.on("click", showMainWindow);
}

// Stay alive in the tray when the window is hidden/closed. Simply having a
// listener that does not call app.quit() is what keeps the app running —
// window-all-closed is not a preventable event, so there is nothing to
// preventDefault here.
app.on("window-all-closed", () => {});
