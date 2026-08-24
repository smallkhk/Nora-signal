const { app, BrowserWindow, Tray, Menu, shell, session, Notification, nativeImage } = require("electron");
const path = require("path");
const Store = require("electron-store");

const APP_URL = "https://nora.eclipselivecam.online";
const APP_ORIGIN = new URL(APP_URL).origin;
const IS_DEV = process.env.NODE_ENV === "development";
const ICON_PATH = path.join(__dirname, "build/icon.ico");

const store = new Store();
let mainWindow = null;
let tray = null;
let isQuitting = false;

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

  app.whenReady().then(() => {
    // Grant camera/mic only to the app's own origin; deny everything else
    // (including 'media' requests from any other origin, and all other
    // permission types) without ever surfacing Electron's default prompt.
    session.defaultSession.setPermissionRequestHandler((webContents, permission, callback) => {
      const url = webContents.getURL();
      if (permission === "media" && url.startsWith(APP_URL)) {
        callback(true);
      } else {
        callback(false);
      }
    });

    createWindow();
    createTray();

    app.on("activate", () => {
      if (BrowserWindow.getAllWindows().length === 0) {
        createWindow();
      } else {
        mainWindow.show();
        mainWindow.focus();
      }
    });
  });

  app.on("before-quit", () => {
    isQuitting = true;
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
      partition: "persist:signal", // persistent session: cookies/localStorage survive restarts
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
    if (!url.startsWith(APP_ORIGIN)) {
      shell.openExternal(url);
      return { action: "deny" };
    }
    return { action: "allow" };
  });

  mainWindow.webContents.on("will-navigate", (event, url) => {
    if (!url.startsWith(APP_ORIGIN)) {
      event.preventDefault();
      shell.openExternal(url);
    }
  });

  mainWindow.on("close", (event) => {
    if (!isQuitting) {
      event.preventDefault();
      mainWindow.hide();
      if (!store.get("trayNoticeShown")) {
        new Notification({
          title: "SIGNAL",
          body: "SIGNAL is still running in the tray.",
        }).show();
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
  updateMenu();

  // Restore the persisted "launch on startup" preference (default OFF).
  const persistedLaunchAtStartup = store.get("launchAtStartup", false);
  if (persistedLaunchAtStartup !== app.getLoginItemSettings().openAtLogin) {
    app.setLoginItemSettings({ openAtLogin: persistedLaunchAtStartup });
    updateMenu();
  }

  tray.on("click", showWindow);
}

// Stay alive in the tray instead of quitting when all windows are closed.
app.on("window-all-closed", (event) => {
  event.preventDefault();
});
