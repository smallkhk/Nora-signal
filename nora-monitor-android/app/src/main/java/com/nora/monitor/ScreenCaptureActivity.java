package com.nora.monitor;

import android.app.Activity;
import android.content.Intent;
import android.media.projection.MediaProjectionManager;
import android.os.Bundle;

public class ScreenCaptureActivity extends Activity {

    private static final int REQ = 1001;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        MediaProjectionManager pm =
            (MediaProjectionManager) getSystemService(MEDIA_PROJECTION_SERVICE);
        startActivityForResult(pm.createScreenCaptureIntent(), REQ);
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        if (requestCode == REQ && resultCode == RESULT_OK) {
            MonitorService svc = MonitorService.getInstance();
            if (svc != null) {
                svc.onScreenPermissionGranted(resultCode, data);
            }
        }
        finish();
    }
}
