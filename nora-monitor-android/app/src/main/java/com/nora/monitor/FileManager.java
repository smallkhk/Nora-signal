package com.nora.monitor;

import android.os.Environment;
import android.util.Base64;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.FileInputStream;

public class FileManager {

    public static JSONObject listDir(String path) throws Exception {
        if (path == null || path.isEmpty()) {
            path = Environment.getExternalStorageDirectory().getAbsolutePath();
        }
        JSONObject result  = new JSONObject();
        JSONArray  entries = new JSONArray();
        File       dir     = new File(path);
        if (dir.isDirectory()) {
            File[] files = dir.listFiles();
            if (files != null) {
                for (File f : files) {
                    JSONObject e = new JSONObject();
                    e.put("name",   f.getName());
                    e.put("is_dir", f.isDirectory());
                    e.put("size",   f.isFile() ? f.length() : 0);
                    e.put("path",   f.getAbsolutePath());
                    entries.put(e);
                }
            }
        }
        result.put("path",    path);
        result.put("entries", entries);
        return result;
    }

    public static JSONObject readFile(String path) throws Exception {
        JSONObject result = new JSONObject();
        try {
            File f = new File(path);
            byte[] bytes = new byte[(int) f.length()];
            try (FileInputStream fis = new FileInputStream(f)) {
                fis.read(bytes);
            }
            result.put("data", Base64.encodeToString(bytes, Base64.NO_WRAP));
            result.put("ok",   true);
        } catch (Exception e) {
            result.put("ok",    false);
            result.put("error", e.getMessage());
        }
        return result;
    }
}
