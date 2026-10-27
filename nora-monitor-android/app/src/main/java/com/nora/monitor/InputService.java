package com.nora.monitor;

import android.accessibilityservice.AccessibilityService;
import android.accessibilityservice.GestureDescription;
import android.graphics.Path;
import android.os.Build;
import android.view.KeyEvent;
import android.view.accessibility.AccessibilityEvent;

import androidx.annotation.RequiresApi;

public class InputService extends AccessibilityService {

    private static InputService instance;

    public static InputService getInstance() { return instance; }

    @Override
    public void onServiceConnected() {
        super.onServiceConnected();
        instance = this;
    }

    @Override
    public void onDestroy() {
        instance = null;
        super.onDestroy();
    }

    @Override
    public void onAccessibilityEvent(AccessibilityEvent event) {}

    @Override
    public void onInterrupt() {}

    // --- tap ---
    @RequiresApi(api = Build.VERSION_CODES.N)
    public void tap(float x, float y) {
        Path path = new Path();
        path.moveTo(x, y);
        GestureDescription.StrokeDescription stroke =
            new GestureDescription.StrokeDescription(path, 0, 50);
        GestureDescription gesture = new GestureDescription.Builder()
            .addStroke(stroke)
            .build();
        dispatchGesture(gesture, null, null);
    }

    // --- swipe ---
    @RequiresApi(api = Build.VERSION_CODES.N)
    public void swipe(float x1, float y1, float x2, float y2, long durationMs) {
        Path path = new Path();
        path.moveTo(x1, y1);
        path.lineTo(x2, y2);
        GestureDescription.StrokeDescription stroke =
            new GestureDescription.StrokeDescription(path, 0, durationMs);
        GestureDescription gesture = new GestureDescription.Builder()
            .addStroke(stroke)
            .build();
        dispatchGesture(gesture, null, null);
    }

    // --- key ---
    public void pressKey(int keyCode) {
        performGlobalAction(keyCode);
    }

    // --- back / home / recents ---
    public void goBack()    { performGlobalAction(GLOBAL_ACTION_BACK); }
    public void goHome()    { performGlobalAction(GLOBAL_ACTION_HOME); }
    public void goRecents() { performGlobalAction(GLOBAL_ACTION_RECENTS); }
}
