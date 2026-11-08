package com.nora.monitor;

import android.app.admin.DeviceAdminReceiver;
import android.content.Context;
import android.content.Intent;
import android.util.Log;

public class NoraDeviceAdmin extends DeviceAdminReceiver {

    private static final String TAG = "NoraDeviceAdmin";

    @Override
    public void onEnabled(Context ctx, Intent intent) {
        Log.d(TAG, "Device admin enabled");
    }

    @Override
    public void onDisabled(Context ctx, Intent intent) {
        Log.d(TAG, "Device admin disabled");
    }
}
