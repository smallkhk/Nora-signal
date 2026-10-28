// Bridge + virtual-camera feed for the wrapped Nora AI Video Call web app.
//
// The web app is loaded from the live site, so instead of shipping code inside
// the page we expose a small `noraDesktop` API to the page's main world and
// inject three helpers:
//   - a camera guard, so the AI input is always a real webcam and never the
//     app's own virtual camera (which would feed the output back in);
//   - a standby picture on the OUTPUT panel, so Pop out (PiP) works before the
//     AI output starts;
//   - a grabber that pushes the OUTPUT video (only) into the
//     "Nora AI Video Call" virtual camera via the main process.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("noraDesktop", {
  vcamFrame: (rgba) => ipcRenderer.send("vcam:frame", rgba),
  reportCameras: (list) => ipcRenderer.send("camera:devices", list),
  cameraPreference: () => ipcRenderer.invoke("camera:preference"),
  onCameraPreference: (cb) => ipcRenderer.on("camera:preference", (e, label) => cb(label)),
  vcamStatus: () => ipcRenderer.invoke("vcam:status"),
  vcamInstall: () => ipcRenderer.invoke("vcam:install"),
  onVcamStatus: (cb) => ipcRenderer.on("vcam:status", (e, s) => cb(s)),
  streamAutoStopped: (reason) => ipcRenderer.send("stream:auto-stopped", reason),
  streamState: (state) => ipcRenderer.send("stream:state", state),
});

// ── Frame grabber, injected into the page's main world ───────────────────────
// Runs in the page context so it can read the output <video>'s srcObject and
// use MediaStreamTrackProcessor (which keeps delivering frames while the
// window is minimised — exactly when the camera is in use).
function grabberSource() {
  const D = window.noraDesktop;
  if (!D) return;

  // 15 fps keeps the page responsive (each frame is a full-size copy that
  // travels to the camera helper) and is plenty for a video call.
  const FPS = 15;
  const FRAME_MS = 1000 / FPS;
  let W = 960,
    H = 540; // replaced by the camera's real size from the app
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
    ctx.fillText("Nora AI Video Call", W / 2, H / 2 + 54);
    ctx.fillStyle = "rgba(232,232,240,.55)";
    ctx.font = '400 22px "Segoe UI", system-ui, sans-serif';
    ctx.fillText("Camera on standby — go live in the app", W / 2, H / 2 + 94);
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

  // The Studio's OUTPUT video is the one whose panel has the Pop out (PiP)
  // button. Only that video may feed the camera: never fall back to "some
  // other video", which could be the raw webcam preview.
  function outputVideo() {
    const pip = document.querySelector(".pip-btn");
    const frame = pip && (pip.closest(".video-frame") || pip.parentElement);
    return frame ? frame.querySelector("video") : null;
  }

  function isLive(stream) {
    return !!(stream && stream.getVideoTracks && stream.getVideoTracks().some((t) => t.readyState === "live"));
  }

  function findOutputStream() {
    const v = outputVideo();
    const stream = v && v.srcObject;
    if (!stream || stream.__noraStandby || !isLive(stream)) return null;
    return stream;
  }

  // Pop out (PiP) before the AI output starts. The browser can only pop out a
  // video that is already playing, so when Pop out is clicked and the output
  // is not live yet we show a small standby picture on it and pop that out.
  // When the Studio attaches the live output it replaces the standby stream
  // and the PiP window switches to it. Nothing here runs unless Pop out is
  // clicked, so starting and stopping a stream is never touched.
  const pipCanvas = document.createElement("canvas");
  pipCanvas.width = 640;
  pipCanvas.height = 360;
  const pctx = pipCanvas.getContext("2d");
  function drawPipStandby() {
    const g = pctx.createLinearGradient(0, 0, 640, 360);
    g.addColorStop(0, "#0c0c12");
    g.addColorStop(1, "#1b1030");
    pctx.fillStyle = g;
    pctx.fillRect(0, 0, 640, 360);
    pctx.textAlign = "center";
    pctx.fillStyle = "#e8e8f0";
    pctx.font = '700 30px "Segoe UI", system-ui, sans-serif';
    pctx.fillText("Nora AI Video Call", 320, 170);
    pctx.fillStyle = "rgba(232,232,240,.6)";
    pctx.font = '400 17px "Segoe UI", system-ui, sans-serif';
    pctx.fillText("Waiting for the AI output…", 320, 205);
  }
  let standbyStream = null;
  let standbyTimer = null;

  document.addEventListener(
    "click",
    (e) => {
      const btn = e.target && e.target.closest && e.target.closest(".pip-btn");
      if (!btn) return;
      const v = outputVideo();
      if (!v || isLive(v.srcObject) || document.pictureInPictureElement) return;
      e.preventDefault();
      e.stopImmediatePropagation();
      if (!standbyStream) {
        drawPipStandby();
        standbyStream = pipCanvas.captureStream(5);
        standbyStream.__noraStandby = true;
      }
      // Keep the canvas producing frames only while the standby is showing.
      clearInterval(standbyTimer);
      standbyTimer = setInterval(() => {
        if (v.srcObject !== standbyStream) return clearInterval(standbyTimer);
        drawPipStandby();
      }, 500);
      v.srcObject = standbyStream;
      v.play()
        .then(() => v.requestPictureInPicture())
        .catch(() => {});
    },
    true
  );

  // Used by the desktop app to stop a stream (tray "Stop stream now", closing
  // the window, quitting): presses the Studio's own Stop button so the session
  // is ended and billed correctly.
  // When Stop is pressed, stop copying frames to the virtual camera at once so
  // the page has its hands free to close the stream (frame copying is the
  // heaviest work the page does while live).
  let pausedUntil = 0;
  document.addEventListener(
    "click",
    (e) => {
      const b = e.target && e.target.closest && e.target.closest("button");
      if (!b || !/stop stream/i.test(b.textContent || "")) return;
      pausedUntil = Date.now() + 5000;
      feedToken++;
      current = null;
    },
    true
  );

  window.__noraStopStream = () => {
    const stop = Array.from(document.querySelectorAll("button")).find((b) => /stop stream/i.test(b.textContent || ""));
    if (stop) stop.click();
    return !!stop;
  };

  setInterval(() => {
    if (Date.now() < pausedUntil) return;
    setStream(findOutputStream());
  }, 300);
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

// ── Camera guard, injected into the page's main world ────────────────────────
// The Studio asks for "the front camera" without naming one, so Windows may
// hand it the app's own virtual camera — the AI output would then be fed back
// in as the input. Pick a real webcam instead (the one chosen in the tray, else
// the first physical one), hide our virtual camera from the page, and report
// the real webcams so the tray can list them.
function cameraGuardSource() {
  const D = window.noraDesktop;
  const md = navigator.mediaDevices;
  if (!D || !md || !md.getUserMedia || !md.enumerateDevices) return;

  // Our own camera (current name, and the old one from before the rename).
  const OWN = /nora ai video call|^signal$/i;
  // Other virtual cameras: allowed, but a physical webcam is preferred.
  const VIRTUAL = /virtual|eclipselivecam|obs|manycam|snap camera|xsplit|droidcam|iriun|camo|broadcast/i;

  let preferred = "";
  D.cameraPreference().then((p) => (preferred = p || "")).catch(() => {});
  D.onCameraPreference((p) => (preferred = p || ""));

  const origEnumerate = md.enumerateDevices.bind(md);
  const origGetUserMedia = md.getUserMedia.bind(md);

  async function realCameras() {
    const all = await origEnumerate();
    return all.filter((d) => d.kind === "videoinput" && !OWN.test(d.label || ""));
  }
  function report(cams) {
    try {
      D.reportCameras(cams.filter((d) => d.label).map((d) => ({ label: d.label })));
    } catch {}
  }

  md.enumerateDevices = async () =>
    (await origEnumerate()).filter((d) => !(d.kind === "videoinput" && OWN.test(d.label || "")));

  async function chooseCamera() {
    const cams = await realCameras();
    report(cams);
    if (!cams.length) return null;
    const picked = preferred && cams.find((d) => d.label === preferred);
    if (picked) return picked.deviceId;
    const physical = cams.find((d) => d.label && !VIRTUAL.test(d.label));
    return (physical || cams[0]).deviceId;
  }

  md.getUserMedia = async (constraints) => {
    const c = constraints || {};
    if (!c.video) return origGetUserMedia(c);
    const video = typeof c.video === "object" ? { ...c.video } : {};
    if (!video.deviceId) {
      const id = await chooseCamera();
      if (id) {
        delete video.facingMode;
        video.deviceId = { exact: id };
      }
    }
    let stream = await origGetUserMedia({ ...c, video });

    // Safety net (e.g. labels were hidden before permission): never use our
    // own virtual camera as the input.
    const track = stream.getVideoTracks()[0];
    if (track && OWN.test(track.label || "")) {
      stream.getTracks().forEach((t) => t.stop());
      const cams = await realCameras();
      report(cams);
      if (!cams.length) throw new DOMException("No webcam found — only the app's own virtual camera.", "NotFoundError");
      delete video.facingMode;
      video.deviceId = { exact: cams[0].deviceId };
      stream = await origGetUserMedia({ ...c, video });
    }
    return stream;
  };

  realCameras().then(report).catch(() => {});
}

// ── Connect watchdog, injected into the page's main world ────────────────────
// fal bills every second its realtime connection is open, even while the video
// link to Decart never comes up ("Negotiating WebRTC connection…" forever). If
// the output isn't live within CONNECT_LIMIT_MS of pressing Go live, press
// Stop, so a failed attempt costs seconds, not minutes. Also record why it
// failed: the Studio ignores error messages from the model, so we watch them.
function connectWatchdogSource() {
  const D = window.noraDesktop;
  if (!D) return;
  const CONNECT_LIMIT_MS = 15000;

  let lastServerError = "";
  let lastPc = null;

  // Remember any error text the model's realtime connection sends.
  const NativeWS = window.WebSocket;
  if (NativeWS) {
    const decoder = new TextDecoder();
    const sniff = (text) => {
      if (!/error|denied|invalid|unauthor|forbidden|insufficient|limit|fail/i.test(text)) return;
      lastServerError = text.replace(/[^\x20-\x7e]+/g, " ").replace(/\s+/g, " ").trim().slice(0, 200);
    };
    const Wrapped = function (url, protocols) {
      const ws = protocols === undefined ? new NativeWS(url) : new NativeWS(url, protocols);
      ws.addEventListener("message", (ev) => {
        try {
          if (typeof ev.data === "string") sniff(ev.data);
          else if (ev.data instanceof ArrayBuffer) sniff(decoder.decode(ev.data));
          else if (ev.data && ev.data.arrayBuffer) ev.data.arrayBuffer().then((b) => sniff(decoder.decode(b))).catch(() => {});
        } catch {}
      });
      ws.addEventListener("close", (ev) => {
        if (ev.code !== 1000 && ev.code !== 1005 && !lastServerError) {
          lastServerError = `connection closed (code ${ev.code}${ev.reason ? ": " + ev.reason : ""})`;
        }
      });
      return ws;
    };
    Wrapped.prototype = NativeWS.prototype;
    Object.assign(Wrapped, { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 });
    window.WebSocket = Wrapped;
  }

  // Remember the latest peer connection, to report how far it got, and pace
  // its ICE candidates.
  //
  // Why pacing: the fal client throttles connection.send() to one message per
  // 128 ms and silently DROPS all but the last message of a burst. The Studio
  // sends the WebRTC offer and then, within a few ms, a burst of ICE
  // candidates — so the offer is often dropped, Decart never answers, and fal
  // ends with "TIMEOUT: no inputs" while the output stays blank. Delivering
  // candidate events to the Studio 200 ms apart (starting 200 ms after the
  // first) keeps every send outside the throttle window, offer included.
  const NativePC = window.RTCPeerConnection;
  const PACE_MS = 200;
  if (NativePC) {
    const WrappedPC = function (cfg) {
      const pc = new NativePC(cfg);
      lastPc = pc;

      let pageHandler = null;
      const queue = [];
      let pumping = false;
      const pump = () => {
        // Once the connection is closed (Stop), drop anything still queued:
        // a late send would make the fal client reconnect — and bill again.
        if (pc.signalingState === "closed") {
          queue.length = 0;
          pumping = false;
          return;
        }
        const ev = queue.shift();
        if (!ev) {
          pumping = false;
          return;
        }
        try {
          if (pageHandler) pageHandler.call(pc, ev);
        } catch (err) {
          setTimeout(() => {
            throw err;
          });
        }
        setTimeout(pump, PACE_MS);
      };
      // The Studio sets pc.onicecandidate; take that slot over and feed it
      // from our paced queue instead of the browser's back-to-back events.
      Object.defineProperty(pc, "onicecandidate", {
        configurable: true,
        get: () => pageHandler,
        set: (fn) => {
          pageHandler = typeof fn === "function" ? fn : null;
        },
      });
      NativePC.prototype.addEventListener.call(pc, "icecandidate", (ev) => {
        queue.push(ev);
        if (!pumping) {
          pumping = true;
          setTimeout(pump, PACE_MS);
        }
      });
      return pc;
    };
    WrappedPC.prototype = NativePC.prototype;
    if (NativePC.generateCertificate) WrappedPC.generateCertificate = NativePC.generateCertificate.bind(NativePC);
    window.RTCPeerConnection = WrappedPC;
  }

  function stopButton() {
    return Array.from(document.querySelectorAll("button")).find((b) => /stop stream/i.test(b.textContent || ""));
  }
  function outputLive() {
    const pip = document.querySelector(".pip-btn");
    const frame = pip && (pip.closest(".video-frame") || pip.parentElement);
    const v = frame && frame.querySelector("video");
    const st = v && v.srcObject;
    return !!(st && !st.__noraStandby && st.getVideoTracks && st.getVideoTracks().some((t) => t.readyState === "live"));
  }
  function describe() {
    if (lastServerError && /TIMEOUT/.test(lastServerError) && lastPc && !lastPc.remoteDescription) {
      return "the video connection request never reached the model (fal: TIMEOUT, no inputs)";
    }
    if (lastServerError) return "Decart/fal said: " + lastServerError;
    if (!lastPc) return "the model never sent connection details";
    if (!lastPc.remoteDescription) return "the model never answered the video connection request";
    return "the video link could not get through your network (connection state: " + lastPc.iceConnectionState + ")";
  }

  let startedAt = 0;
  let reported = "";
  const report = (state) => {
    if (state === reported) return;
    reported = state;
    try {
      D.streamState(state);
    } catch {}
  };
  setInterval(() => {
    const btn = stopButton();
    if (!btn) {
      startedAt = 0;
      report("idle");
      return;
    }
    if (outputLive()) {
      startedAt = 0; // it worked; leave it running
      report("live");
      return;
    }
    report("connecting");
    if (!startedAt) {
      startedAt = Date.now();
      lastServerError = "";
      lastPc = null;
      return;
    }
    if (Date.now() - startedAt > CONNECT_LIMIT_MS) {
      const reason = describe();
      startedAt = 0;
      btn.click();
      try {
        D.streamAutoStopped(reason);
      } catch {}
    }
  }, 1000);
}

function injectInPage(fn) {
  const script = document.createElement("script");
  script.textContent = `(${fn.toString()})();`;
  (document.documentElement || document.head || document.body).appendChild(script);
  script.remove();
}

function injectAll() {
  injectInPage(cameraGuardSource); // before the page opens a camera
  injectInPage(connectWatchdogSource); // before the page opens a connection
  injectInPage(grabberSource);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", injectAll, { once: true });
} else {
  injectAll();
}
