package com.nora.monitor;

import android.annotation.SuppressLint;
import android.content.Context;
import android.graphics.ImageFormat;
import android.hardware.camera2.CameraDevice;
import android.hardware.camera2.CameraManager;
import android.hardware.camera2.CameraCaptureSession;
import android.hardware.camera2.CaptureRequest;
import android.media.Image;
import android.media.ImageReader;
import android.os.Handler;
import android.os.HandlerThread;
import android.util.Log;

import java.nio.ByteBuffer;
import java.util.Arrays;

public class CameraCapture {

    private static final String TAG = "CameraCapture";

    public interface FrameCallback { void onFrame(byte[] jpeg); }

    private final Context       ctx;
    private final FrameCallback cb;
    private CameraDevice        camera;
    private CameraCaptureSession session;
    private ImageReader          reader;
    private HandlerThread        thread;
    private Handler              handler;
    private volatile boolean     active = false;

    public CameraCapture(Context ctx, FrameCallback cb) {
        this.ctx = ctx;
        this.cb  = cb;
    }

    @SuppressLint("MissingPermission")
    public void start() {
        if (active) return;
        active = true;

        thread  = new HandlerThread("camera");
        thread.start();
        handler = new Handler(thread.getLooper());

        CameraManager mgr = (CameraManager) ctx.getSystemService(Context.CAMERA_SERVICE);
        try {
            String id = mgr.getCameraIdList()[0];
            reader = ImageReader.newInstance(640, 480, ImageFormat.JPEG, 2);
            reader.setOnImageAvailableListener(r -> {
                Image img = r.acquireLatestImage();
                if (img != null) {
                    try {
                        ByteBuffer buf   = img.getPlanes()[0].getBuffer();
                        byte[]     bytes = new byte[buf.remaining()];
                        buf.get(bytes);
                        cb.onFrame(bytes);
                    } finally {
                        img.close();
                    }
                }
            }, handler);

            mgr.openCamera(id, new CameraDevice.StateCallback() {
                @Override public void onOpened(CameraDevice cam) {
                    camera = cam;
                    try {
                        CaptureRequest.Builder b = cam.createCaptureRequest(CameraDevice.TEMPLATE_PREVIEW);
                        b.addTarget(reader.getSurface());
                        cam.createCaptureSession(Arrays.asList(reader.getSurface()),
                            new CameraCaptureSession.StateCallback() {
                                @Override public void onConfigured(CameraCaptureSession s) {
                                    session = s;
                                    try { s.setRepeatingRequest(b.build(), null, handler); }
                                    catch (Exception e) { Log.e(TAG, "repeating request", e); }
                                }
                                @Override public void onConfigureFailed(CameraCaptureSession s) {}
                            }, handler);
                    } catch (Exception e) { Log.e(TAG, "camera setup", e); }
                }
                @Override public void onDisconnected(CameraDevice cam) { cam.close(); }
                @Override public void onError(CameraDevice cam, int err) { cam.close(); }
            }, handler);
        } catch (Exception e) {
            Log.e(TAG, "camera open", e);
        }
    }

    public void stop() {
        active = false;
        try { if (session != null) { session.close(); session = null; } } catch (Exception ignored) {}
        try { if (camera  != null) { camera.close();  camera  = null; } } catch (Exception ignored) {}
        try { if (reader  != null) { reader.close();  reader  = null; } } catch (Exception ignored) {}
        try { if (thread  != null) { thread.quitSafely(); thread = null; } } catch (Exception ignored) {}
    }
}
