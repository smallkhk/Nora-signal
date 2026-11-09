package com.nora.monitor;

import android.os.Environment;
import android.util.Base64;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.nio.file.Files;
import java.nio.file.StandardCopyOption;
import java.util.HashMap;
import java.util.Map;

public class FileManager {

    // Pending chunked uploads: upload_id -> FileOutputStream
    private static final Map<String, FileOutputStream> _uploads = new HashMap<>();

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
            if (!f.isFile()) throw new Exception("not a file");
            byte[] bytes = new byte[(int) f.length()];
            try (FileInputStream fis = new FileInputStream(f)) {
                int off = 0, rem = bytes.length, n;
                while (rem > 0 && (n = fis.read(bytes, off, rem)) != -1) { off += n; rem -= n; }
            }
            result.put("data", Base64.encodeToString(bytes, Base64.NO_WRAP));
            result.put("name", f.getName());
            result.put("ok",   true);
        } catch (Exception e) {
            result.put("ok",    false);
            result.put("error", e.getMessage());
        }
        return result;
    }

    public static JSONObject writeFile(String path, String base64Data) throws Exception {
        JSONObject result = new JSONObject();
        try {
            byte[] bytes = Base64.decode(base64Data, Base64.NO_WRAP);
            File f = new File(path);
            if (f.getParentFile() != null) f.getParentFile().mkdirs();
            try (FileOutputStream fos = new FileOutputStream(f)) { fos.write(bytes); }
            result.put("ok", true);
        } catch (Exception e) {
            result.put("ok",    false);
            result.put("error", e.getMessage());
        }
        return result;
    }

    // ── Chunked upload ────────────────────────────────────────────────────────

    public static JSONObject uploadStart(String uploadId, String path) {
        JSONObject result = new JSONObject();
        try {
            File f = new File(path);
            if (f.getParentFile() != null) f.getParentFile().mkdirs();
            FileOutputStream fos = new FileOutputStream(f);
            synchronized (_uploads) { _uploads.put(uploadId, fos); }
            result.put("ok", true);
        } catch (Exception e) {
            try { result.put("ok", false); result.put("error", e.getMessage()); } catch (Exception ignored) {}
        }
        return result;
    }

    public static JSONObject uploadChunk(String uploadId, String base64Chunk, boolean done) {
        JSONObject result = new JSONObject();
        try {
            FileOutputStream fos;
            synchronized (_uploads) { fos = _uploads.get(uploadId); }
            if (fos == null) throw new Exception("unknown upload_id");
            byte[] chunk = Base64.decode(base64Chunk, Base64.NO_WRAP);
            fos.write(chunk);
            if (done) {
                fos.close();
                synchronized (_uploads) { _uploads.remove(uploadId); }
            }
            result.put("ok",   true);
            result.put("done", done);
        } catch (Exception e) {
            try { result.put("ok", false); result.put("error", e.getMessage()); } catch (Exception ignored) {}
        }
        return result;
    }

    // ── CRUD operations ───────────────────────────────────────────────────────

    public static JSONObject deletePath(String path) throws Exception {
        JSONObject result = new JSONObject();
        try {
            File f = new File(path);
            if (!f.exists()) throw new Exception("not found");
            deleteRecursive(f);
            result.put("ok", true);
        } catch (Exception e) {
            result.put("ok",    false);
            result.put("error", e.getMessage());
        }
        return result;
    }

    public static JSONObject renamePath(String oldPath, String newName) throws Exception {
        JSONObject result = new JSONObject();
        try {
            File src = new File(oldPath);
            if (!src.exists()) throw new Exception("not found");
            File dst = new File(src.getParent(), newName);
            if (!src.renameTo(dst)) throw new Exception("rename failed");
            result.put("ok",      true);
            result.put("newPath", dst.getAbsolutePath());
        } catch (Exception e) {
            result.put("ok",    false);
            result.put("error", e.getMessage());
        }
        return result;
    }

    public static JSONObject movePath(String srcPath, String dstDir) throws Exception {
        JSONObject result = new JSONObject();
        try {
            File src = new File(srcPath);
            if (!src.exists()) throw new Exception("not found");
            File dst = new File(dstDir, src.getName());
            new File(dstDir).mkdirs();
            if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.O) {
                Files.move(src.toPath(), dst.toPath(), StandardCopyOption.REPLACE_EXISTING);
            } else {
                copyRecursive(src, dst);
                deleteRecursive(src);
            }
            result.put("ok",      true);
            result.put("newPath", dst.getAbsolutePath());
        } catch (Exception e) {
            result.put("ok",    false);
            result.put("error", e.getMessage());
        }
        return result;
    }

    public static JSONObject copyPath(String srcPath, String dstDir) throws Exception {
        JSONObject result = new JSONObject();
        try {
            File src = new File(srcPath);
            if (!src.exists()) throw new Exception("not found");
            File dst = new File(dstDir, src.getName());
            new File(dstDir).mkdirs();
            copyRecursive(src, dst);
            result.put("ok",      true);
            result.put("newPath", dst.getAbsolutePath());
        } catch (Exception e) {
            result.put("ok",    false);
            result.put("error", e.getMessage());
        }
        return result;
    }

    public static JSONObject createDir(String path) throws Exception {
        JSONObject result = new JSONObject();
        try {
            File f = new File(path);
            if (!f.mkdirs() && !f.isDirectory()) throw new Exception("failed to create");
            result.put("ok", true);
        } catch (Exception e) {
            result.put("ok",    false);
            result.put("error", e.getMessage());
        }
        return result;
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    private static void deleteRecursive(File f) {
        if (f.isDirectory()) {
            File[] children = f.listFiles();
            if (children != null) for (File c : children) deleteRecursive(c);
        }
        f.delete();
    }

    private static void copyRecursive(File src, File dst) throws Exception {
        if (src.isDirectory()) {
            dst.mkdirs();
            File[] children = src.listFiles();
            if (children != null) for (File c : children) copyRecursive(c, new File(dst, c.getName()));
        } else {
            try (FileInputStream in = new FileInputStream(src);
                 FileOutputStream out = new FileOutputStream(dst)) {
                byte[] buf = new byte[65536];
                int n;
                while ((n = in.read(buf)) != -1) out.write(buf, 0, n);
            }
        }
    }
}
