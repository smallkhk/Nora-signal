package com.nora.monitor;

import android.service.notification.NotificationListenerService;
import android.service.notification.StatusBarNotification;
import android.util.Log;

import org.json.JSONObject;

public class NoraNotificationListener extends NotificationListenerService {

    private static final String TAG = "NoraNotifListener";
    private static NoraNotificationListener instance;

    public static NoraNotificationListener getInstance() { return instance; }

    @Override
    public void onCreate() {
        super.onCreate();
        instance = this;
        Log.d(TAG, "Notification listener connected");
    }

    @Override
    public void onDestroy() {
        instance = null;
        super.onDestroy();
    }

    @Override
    public void onNotificationPosted(StatusBarNotification sbn) {
        try {
            MonitorService svc = MonitorService.getInstance();
            if (svc == null) return;

            android.app.Notification notif = sbn.getNotification();
            if (notif == null) return;

            android.os.Bundle extras = notif.extras;
            String title = extras != null ? String.valueOf(extras.getCharSequence(android.app.Notification.EXTRA_TITLE)) : "";
            String text  = extras != null ? String.valueOf(extras.getCharSequence(android.app.Notification.EXTRA_TEXT))  : "";

            // Skip empty / system noise
            if ("null".equals(title) && "null".equals(text)) return;

            JSONObject o = new JSONObject();
            o.put("pkg",   sbn.getPackageName());
            o.put("title", title);
            o.put("text",  text);
            o.put("time",  sbn.getPostTime());

            RelayClient relay = svc.getRelay();
            if (relay != null) relay.emitNotification(o);

        } catch (Exception e) {
            Log.e(TAG, "notification event error", e);
        }
    }
}
