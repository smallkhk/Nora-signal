package com.nora.monitor;

import android.Manifest;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.media.projection.MediaProjectionManager;
import android.os.Build;
import android.os.Bundle;
import android.widget.Button;
import android.widget.EditText;
import android.widget.TextView;
import android.widget.Toast;

import androidx.annotation.NonNull;
import androidx.appcompat.app.AppCompatActivity;
import androidx.core.app.ActivityCompat;
import androidx.core.content.ContextCompat;

import java.util.ArrayList;
import java.util.List;

public class MainActivity extends AppCompatActivity {

    private static final int REQUEST_PROJECTION = 1001;
    private static final int REQUEST_PERMISSIONS = 1002;

    private MediaProjectionManager projManager;
    private EditText relayInput, secretInput;
    private TextView statusText;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        projManager = (MediaProjectionManager) getSystemService(MEDIA_PROJECTION_SERVICE);
        relayInput  = findViewById(R.id.relay_url);
        secretInput = findViewById(R.id.secret);
        statusText  = findViewById(R.id.status);
        Button startBtn = findViewById(R.id.start_btn);
        Button stopBtn  = findViewById(R.id.stop_btn);

        relayInput.setText(MonitorService.DEFAULT_RELAY);

        startBtn.setOnClickListener(v -> requestPermissionsThenStart());
        stopBtn.setOnClickListener(v -> {
            stopService(new Intent(this, MonitorService.class));
            statusText.setText("Stopped");
        });
    }

    private void requestPermissionsThenStart() {
        List<String> needed = new ArrayList<>();
        String[] perms = {
            Manifest.permission.CAMERA,
            Manifest.permission.RECORD_AUDIO,
        };
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            perms = new String[]{
                Manifest.permission.CAMERA,
                Manifest.permission.RECORD_AUDIO,
                Manifest.permission.READ_MEDIA_IMAGES,
                Manifest.permission.READ_MEDIA_VIDEO,
            };
        }
        for (String p : perms) {
            if (ContextCompat.checkSelfPermission(this, p) != PackageManager.PERMISSION_GRANTED) {
                needed.add(p);
            }
        }
        if (!needed.isEmpty()) {
            ActivityCompat.requestPermissions(this, needed.toArray(new String[0]), REQUEST_PERMISSIONS);
        } else {
            requestScreenCapture();
        }
    }

    @Override
    public void onRequestPermissionsResult(int code, @NonNull String[] perms, @NonNull int[] results) {
        super.onRequestPermissionsResult(code, perms, results);
        if (code == REQUEST_PERMISSIONS) requestScreenCapture();
    }

    private void requestScreenCapture() {
        // This shows Android's built-in "Start recording?" dialog — cannot be hidden
        startActivityForResult(projManager.createScreenCaptureIntent(), REQUEST_PROJECTION);
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == REQUEST_PROJECTION) {
            if (resultCode == RESULT_OK) {
                MonitorService.DEFAULT_RELAY  = relayInput.getText().toString().trim();
                MonitorService.AGENT_SECRET   = secretInput.getText().toString().trim();

                Intent svc = new Intent(this, MonitorService.class);
                svc.putExtra("resultCode", resultCode);
                svc.putExtra("data", data);
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    startForegroundService(svc);
                } else {
                    startService(svc);
                }
                statusText.setText("Running — check hub for this device");
            } else {
                Toast.makeText(this, "Screen capture permission denied", Toast.LENGTH_SHORT).show();
            }
        }
    }
}
