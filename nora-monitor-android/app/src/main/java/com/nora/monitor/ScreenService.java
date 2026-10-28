package com.nora.monitor;

import org.json.JSONObject;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.graphics.Bitmap;
import android.graphics.PixelFormat;
import android.hardware.display.DisplayManager;
import android.hardware.display.VirtualDisplay;
import android.media.Image;
import android.media.ImageReader;
import android.media.projection.MediaProjection;
import android.media.projection.MediaProjectionManager;
import android.os.Build;
import android.os.IBinder;
import android.util.DisplayMetrics;
import android.util.Log;

import androidx.annotation.Nullable;
import androidx.core.app.NotificationCompat;

import java.io.ByteArrayOutputStream;
import java.nio.ByteBuffer;

public class ScreenService extends Service {

    private static final String TAG      = "ScreenService";
    private static final String CHANNEL  = "nora_monitor";
    private static final int    NOTIF_ID = 2;

    private static ScreenService instance;
    public static ScreenService getInstance() { return instance; }

    private MediaProjection  mediaProjection;
    private VirtualDisplay   virtualDisplay;
    private ImageReader      imageReader;
    private volatile boolean streaming = false;
    private Thread           streamThread;

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
        if (intent == null) { stopSelf(); return START_NOT_STICKY; }

        int    resultCode = intent.getIntExtra("resultCode", -1);
        Intent data       = intent.getParcelableExtra("data");

        Notification notif = new NotificationCompat.Builder(this, CHANNEL)
            .setContentTitle("Nora Monitor")
            .setContentText("Screen active")
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .build();

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(NOTIF_ID, notif,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION);
        } else {
            startForeground(NOTIF_ID, notif);
        }

        startStream(resultCode, data);
        return START_NOT_STICKY;
    }

    private void startStream(int resultCode, Intent data) {
        try {
            MediaProjectionManager pm =
                (MediaProjectionManager) getSystemService(MEDIA_PROJECTION_SERVICE);
            mediaProjection = pm.getMediaProjection(resultCode, data);

            DisplayMetrics m = getResources().getDisplayMetrics();
            int w = m.widthPixels  / 2;
            int h = m.heightPixels / 2;

            imageReader    = ImageReader.newInstance(w, h, PixelFormat.RGBA_8888, 2);
            virtualDisplay = mediaProjection.createVirtualDisplay(
                "NoraCap", w, h, m.densityDpi,
                DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
                imageReader.getSurface(), null, null);

            streaming    = true;
            streamThread = new Thread(() -> {
                while (streaming) {
                    try {
                        Image img = imageReader.acquireLatestImage();
                        if (img != null) {
                            try {
                                byte[] jpeg = toJpeg(img, w, h);
                                MonitorService svc = MonitorService.getInstance();
                                if (svc != null) svc.getRelay().emitFrame(jpeg);
                            } finally {
                                img.close();
                            }
                        }
                        Thread.sleep(100);
                    } catch (Exception e) {
                        if (streaming) Log.e(TAG, "stream error", e);
                    }
                }
            }, "screen-stream");
            streamThread.setDaemon(true);
            streamThread.start();
        } catch (Exception e) {
            Log.e(TAG, "startStream error", e);
            stopSelf();
        }
    }

    public void takeScreenshot(Object[] args, RelayClient relay) {
        if (imageReader == null) return;
        try {
            Image img = imageReader.acquireLatestImage();
            if (img == null) return;
            try {
                DisplayMetrics m = getResources().getDisplayMetrics();
                byte[] jpeg = toJpeg(img, m.widthPixels / 2, m.heightPixels / 2);
                String requester = null;
                if (args != null && args.length > 0)
                    requester = ((JSONObject) args[0]).optString("_requester");
                relay.emitScreenshot(jpeg, requester);
            } finally {
                img.close();
            }
        } catch (Exception e) {
            Log.e(TAG, "screenshot error", e);
        }
    }

    public void stopStream() {
        streaming = false;
        if (virtualDisplay  != null) { virtualDisplay.release();  virtualDisplay  = null; }
        if (mediaProjection != null) { mediaProjection.stop();    mediaProjection = null; }
        if (imageReader     != null) { imageReader.close();       imageReader     = null; }
        stopSelf();
    }

    static byte[] toJpeg(Image img, int w, int h) {
        Image.Plane[] planes    = img.getPlanes();
        ByteBuffer    buf       = planes[0].getBuffer();
        int           rowStride = planes[0].getRowStride();
        int           pxStride  = planes[0].getPixelStride();
        Bitmap bmp = Bitmap.createBitmap(rowStride / pxStride, h, Bitmap.Config.ARGB_8888);
        bmp.copyPixelsFromBuffer(buf);
        if (rowStride / pxStride != w) {
            Bitmap cropped = Bitmap.createBitmap(bmp, 0, 0, w, h);
            bmp.recycle();
            bmp = cropped;
        }
        ByteArrayOutputStream baos = new ByteArrayOutputStream();
        bmp.compress(Bitmap.CompressFormat.JPEG, 40, baos);
        bmp.recycle();
        return baos.toByteArray();
    }

    @Override
    public void onDestroy() {
        instance  = null;
        streaming = false;
        if (virtualDisplay  != null) virtualDisplay.release();
        if (mediaProjection != null) mediaProjection.stop();
        if (imageReader     != null) imageReader.close();
        super.onDestroy();
    }

    @Nullable @Override
    public IBinder onBind(Intent i) { return null; }
}
