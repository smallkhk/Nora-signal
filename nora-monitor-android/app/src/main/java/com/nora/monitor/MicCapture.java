package com.nora.monitor;

import android.annotation.SuppressLint;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import android.util.Log;

public class MicCapture {

    private static final String TAG         = "MicCapture";
    private static final int    SAMPLE_RATE = 16000;

    public interface AudioCallback { void onChunk(byte[] pcm); }

    private final AudioCallback   cb;
    private AudioRecord           recorder;
    private Thread                thread;
    private volatile boolean      active = false;

    public MicCapture(AudioCallback cb) { this.cb = cb; }

    @SuppressLint("MissingPermission")
    public void start() {
        if (active) return;
        active = true;

        int bufSize = AudioRecord.getMinBufferSize(
            SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT) * 4;

        recorder = new AudioRecord(
            MediaRecorder.AudioSource.MIC,
            SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
            bufSize);
        recorder.startRecording();

        thread = new Thread(() -> {
            byte[] buf = new byte[bufSize];
            while (active) {
                int n = recorder.read(buf, 0, buf.length);
                if (n > 0) {
                    byte[] chunk = new byte[n];
                    System.arraycopy(buf, 0, chunk, 0, n);
                    cb.onChunk(chunk);
                }
            }
        }, "mic-capture");
        thread.setDaemon(true);
        thread.start();
    }

    public void stop() {
        active = false;
        if (recorder != null) {
            try { recorder.stop(); } catch (Exception ignored) {}
            recorder.release();
            recorder = null;
        }
    }
}
