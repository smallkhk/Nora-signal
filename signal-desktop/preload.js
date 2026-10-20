// Bridge + virtual-camera feed for the wrapped SIGNAL web app.
//
// The web app is loaded from the live site, so instead of shipping a
// frame-grabber inside the page we expose a small `signalDesktop` API to the
// page's main world and inject a grabber that finds the Studio's OUTPUT video
// (the AI-transformed stream) and pushes each frame to the "SIGNAL" virtual
// camera via the main process.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("signalDesktop", {
  vcamFrame: (rgba) => ipcRenderer.send("vcam:frame", rgba),
  vcamStatus: () => ipcRenderer.invoke("vcam:status"),
  vcamInstall: () => ipcRenderer.invoke("vcam:install"),
  onVcamStatus: (cb) => ipcRenderer.on("vcam:status", (e, s) => cb(s)),
});

// ── Frame grabber, injected into the page's main world ───────────────────────
// Runs in the page context so it can read the output <video>'s srcObject and
// use MediaStreamTrackProcessor (which keeps delivering frames while the
// window is minimised — exactly when the camera is in use).
function grabberSource() {
  const D = window.signalDesktop;
  if (!D) return;

  const FPS = 30;
  const FRAME_MS = 1000 / FPS;
  let W = 1280,
    H = 720; // replaced by the camera's real size from the app
  const canvas = document.createElement("canvas");
  canvas.width = W;
  canvas.height = H;
  const ctx = canvas.getContext("2d", { alpha: false, willReadFrequently: true });

  let camReady = false;
  let current = null; // the MediaStream being fed
  let feedToken = 0; // bumps whenever the stream changes; old loops exit
  let lastSent = 0;

  function send() {
    if (!camReady) return;
    D.vcamFrame(ctx.getImageData(0, 0, W, H).data);
  }

  // Fill the whole camera frame, cropping the edges that don't fit.
  function drawCover(src, sw, sh) {
    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, W, H);
    if (!sw || !sh) return;
    const scale = Math.max(W / sw, H / sh);
    const dw = Math.round(sw * scale),
      dh = Math.round(sh * scale);
    ctx.drawImage(src, (W - dw) >> 1, (H - dh) >> 1, dw, dh);
  }

  function resize(w, h) {
    if (!w || !h || (w === W && h === H)) return;
    W = w;
    H = h;
    canvas.width = W;
    canvas.height = H;
    if (!current) drawStandby();
  }

  function drawStandby() {
    const g = ctx.createLinearGradient(0, 0, W, H);
    g.addColorStop(0, "#0c0c12");
    g.addColorStop(1, "#1b1030");
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, W, H);
    ctx.beginPath();
    ctx.arc(W / 2, H / 2 - 40, 46, 0, Math.PI * 2);
    const lg = ctx.createLinearGradient(W / 2 - 46, H / 2 - 86, W / 2 + 46, H / 2 + 6);
    lg.addColorStop(0, "#7c3aed");
    lg.addColorStop(1, "#a855f7");
    ctx.fillStyle = lg;
    ctx.fill();
    ctx.beginPath();
    ctx.arc(W / 2 + 16, H / 2 - 52, 38, 0, Math.PI * 2);
    ctx.fillStyle = "#120c1f";
    ctx.fill();
    ctx.textAlign = "center";
    ctx.fillStyle = "#e8e8f0";
    ctx.font = '700 44px "Segoe UI", system-ui, sans-serif';
    ctx.fillText("SIGNAL", W / 2, H / 2 + 54);
    ctx.fillStyle = "rgba(232,232,240,.55)";
    ctx.font = '400 22px "Segoe UI", system-ui, sans-serif';
    ctx.fillText("Camera on standby — go live in the SIGNAL app", W / 2, H / 2 + 94);
  }

  // Preferred path: read frames straight off the WebRTC track. This keeps
  // working while the window is minimised, unlike drawing from <video>.
  async function feedFromTrack(track, token) {
    const clone = track.clone();
    const reader = new MediaStreamTrackProcessor({ track: clone }).readable.getReader();
    try {
      while (token === feedToken) {
        const { value: frame, done } = await reader.read();
        if (done) break;
        const now = performance.now();
        if (token === feedToken && now - lastSent >= FRAME_MS - 2) {
          drawCover(frame, frame.displayWidth, frame.displayHeight);
          send();
          lastSent = now;
        }
        frame.close();
      }
    } finally {
      try {
        reader.cancel();
      } catch {}
      clone.stop();
    }
  }

  // Fallback for engines without MediaStreamTrackProcessor.
  function feedFromVideo(stream, token) {
    const v = document.createElement("video");
    v.muted = true;
    v.playsInline = true;
    v.srcObject = stream;
    v.play().catch(() => {});
    const timer = setInterval(() => {
      if (token !== feedToken) {
        clearInterval(timer);
        v.srcObject = null;
        return;
      }
      if (v.readyState >= 2) {
        drawCover(v, v.videoWidth, v.videoHeight);
        send();
      }
    }, FRAME_MS);
  }

  function setStream(stream) {
    if (stream === current) return;
    current = stream;
    const token = ++feedToken;
    const track = stream && stream.getVideoTracks()[0];
    if (!track) {
      drawStandby();
      send();
      return;
    }
    if ("MediaStreamTrackProcessor" in window) {
      feedFromTrack(track, token).catch(() => {
        if (token === feedToken) feedFromVideo(stream, token);
      });
    } else {
      feedFromVideo(stream, token);
    }
  }

  // Find the Studio's OUTPUT video. The output panel is the only one with a
  // pop-out (PiP) button; failing that, take a playing, unmuted <video> that
  // carries a live stream (the camera preview is muted).
  function findOutputStream() {
    const videos = Array.from(document.querySelectorAll("video"));
    const withStream = videos.filter((v) => v.srcObject && v.srcObject.getVideoTracks && v.srcObject.getVideoTracks().length);
    if (!withStream.length) return null;
    const byPip = withStream.find((v) => {
      const frame = v.closest(".video-frame") || v.parentElement;
      return frame && frame.querySelector(".pip-btn");
    });
    if (byPip) return byPip.srcObject;
    const unmuted = withStream.find((v) => !v.muted);
    return (unmuted || withStream[withStream.length - 1]).srcObject;
  }

  setInterval(() => setStream(findOutputStream()), 300);
  // Re-send the standby card now and then, e.g. after the camera restarts.
  setInterval(() => {
    if (!current) {
      drawStandby();
      send();
    }
  }, 1000);

  function render(s) {
    resize(s.width, s.height);
    camReady = s.state === "ready";
    if (camReady && !current) {
      drawStandby();
      send();
    }
  }

  D.onVcamStatus(render);
  D.vcamStatus().then(render);
}

function injectInPage(fn) {
  const script = document.createElement("script");
  script.textContent = `(${fn.toString()})();`;
  (document.documentElement || document.head || document.body).appendChild(script);
  script.remove();
}

function injectAll() {
  injectInPage(grabberSource);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", injectAll, { once: true });
} else {
  injectAll();
}
