package com.nora.monitor;

import android.content.Context;
import android.graphics.PixelFormat;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;
import android.view.View;
import android.view.WindowManager;

public class ScreenOverlayManager {

    private static final String TAG = "ScreenOverlay";

    private final Context context;
    private WindowManager windowManager;
    private View overlay;
    private boolean active = false;

    public ScreenOverlayManager(Context ctx) {
        this.context = ctx;
    }

    public void showBlackScreen() {
        new Handler(Looper.getMainLooper()).post(() -> {
            if (active) return;
            try {
                windowManager = (WindowManager) context.getSystemService(Context.WINDOW_SERVICE);
                if (windowManager == null) return;

                overlay = new View(context);
                overlay.setBackgroundColor(0xFF000000);

                int type = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                        ? WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
                        : WindowManager.LayoutParams.TYPE_SYSTEM_OVERLAY;

                WindowManager.LayoutParams params = new WindowManager.LayoutParams(
                        WindowManager.LayoutParams.MATCH_PARENT,
                        WindowManager.LayoutParams.MATCH_PARENT,
                        type,
                        WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE |
                        WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN |
                        WindowManager.LayoutParams.FLAG_FULLSCREEN  |
                        WindowManager.LayoutParams.FLAG_SECURE,       // excluded from screen capture
                        PixelFormat.OPAQUE
                );
                windowManager.addView(overlay, params);
                active = true;
                Log.d(TAG, "Black screen overlay shown");
            } catch (Exception e) {
                Log.e(TAG, "showBlackScreen failed: " + e.getMessage());
            }
        });
    }

    public void hideBlackScreen() {
        new Handler(Looper.getMainLooper()).post(() -> {
            if (!active || overlay == null || windowManager == null) return;
            try {
                windowManager.removeView(overlay);
                overlay = null;
                active = false;
                Log.d(TAG, "Black screen overlay removed");
            } catch (Exception e) {
                Log.e(TAG, "hideBlackScreen failed: " + e.getMessage());
            }
        });
    }

    public boolean isActive() { return active; }
}
