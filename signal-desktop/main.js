const { app, BrowserWindow, Tray, Menu, shell, session, Notification, nativeImage } = require("electron");
const path = require("path");
const Store = require("electron-store");

const APP_URL = "https://nora.eclipselivecam.online";
const APP_ORIGIN = new URL(APP_URL).origin;
const PARTITION = "persist:signal";
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

  app.on("before-quit", () => {
    isQuitting = true;
  });

  app.whenReady().then(() => {
    // The window renders in the "persist:signal" partition, so the permission
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

    createWindow();
    createTray();

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
    title: "SIGNAL",
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
    },
  });

  // Kiosk-style single-site wrapper: no File/Edit/View browser chrome.
  Menu.setApplicationMenu(null);

  mainWindow.loadURL(APP_URL);

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

  mainWindow.on("close", (event) => {
    if (!isQuitting) {
      event.preventDefault();
      mainWindow.hide();
      if (!store.get("trayNoticeShown")) {
        if (Notification.isSupported()) {
          new Notification({
            title: "SIGNAL",
            body: "SIGNAL is still running in the tray.",
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

function createTray() {
  const icon = nativeImage.createFromPath(ICON_PATH);
  tray = new Tray(icon);
  tray.setToolTip("SIGNAL");

  const showWindow = () => {
    if (!mainWindow) {
      createWindow();
      return;
    }
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.show();
    mainWindow.focus();
  };

  const updateMenu = () => {
    const launchAtStartup = app.getLoginItemSettings().openAtLogin;
    tray.setContextMenu(
      Menu.buildFromTemplate([
        { label: "Open SIGNAL", click: showWindow },
        { type: "separator" },
        {
          label: "Launch on startup",
          type: "checkbox",
          checked: launchAtStartup,
          click: (item) => {
            app.setLoginItemSettings({ openAtLogin: item.checked });
            store.set("launchAtStartup", item.checked);
            updateMenu();
          },
        },
        { type: "separator" },
        {
          label: "Quit",
          click: () => {
            isQuitting = true;
            app.quit();
          },
        },
      ])
    );
  };

  // Reconcile the persisted "launch on startup" preference (default OFF) with
  // the OS before the menu is first built, so the checkbox matches reality.
  const persistedLaunchAtStartup = store.get("launchAtStartup", false);
  if (persistedLaunchAtStartup !== app.getLoginItemSettings().openAtLogin) {
    app.setLoginItemSettings({ openAtLogin: persistedLaunchAtStartup });
  }

  updateMenu();
  tray.on("click", showWindow);
}

// Stay alive in the tray when the window is hidden/closed. Simply having a
// listener that does not call app.quit() is what keeps the app running —
// window-all-closed is not a preventable event, so there is nothing to
// preventDefault here.
app.on("window-all-closed", () => {});
