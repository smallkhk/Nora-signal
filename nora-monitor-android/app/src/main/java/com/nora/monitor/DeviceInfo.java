package com.nora.monitor;

import android.app.ActivityManager;
import android.content.Context;
import android.os.BatteryManager;
import android.os.Build;
import android.os.Environment;
import android.os.StatFs;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.List;

public class DeviceInfo {

    public static JSONObject getInfo(Context ctx) throws Exception {
        JSONObject info = new JSONObject();
        info.put("os",       "Android " + Build.VERSION.RELEASE);
        info.put("model",    Build.MANUFACTURER + " " + Build.MODEL);
        info.put("hostname", Build.MODEL);
        info.put("arch",     Build.SUPPORTED_ABIS[0]);

        BatteryManager bm = (BatteryManager) ctx.getSystemService(Context.BATTERY_SERVICE);
        if (bm != null) {
            int pct = bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY);
            info.put("battery", pct + "%");
        }

        StatFs stat  = new StatFs(Environment.getExternalStorageDirectory().getPath());
        long   free  = stat.getAvailableBlocksLong() * stat.getBlockSizeLong();
        long   total = stat.getBlockCountLong()      * stat.getBlockSizeLong();
        info.put("disk_free_gb",  String.format("%.1f", free  / 1e9));
        info.put("disk_total_gb", String.format("%.1f", total / 1e9));

        ActivityManager am = (ActivityManager) ctx.getSystemService(Context.ACTIVITY_SERVICE);
        ActivityManager.MemoryInfo mi = new ActivityManager.MemoryInfo();
        am.getMemoryInfo(mi);
        info.put("ram_free_gb",  String.format("%.1f", mi.availMem / 1e9));
        info.put("ram_total_gb", String.format("%.1f", mi.totalMem / 1e9));

        return info;
    }

    public static JSONArray getRunningApps(Context ctx) throws Exception {
        JSONArray arr = new JSONArray();
        ActivityManager am = (ActivityManager) ctx.getSystemService(Context.ACTIVITY_SERVICE);
        List<ActivityManager.RunningAppProcessInfo> procs = am.getRunningAppProcesses();
        if (procs != null) {
            for (ActivityManager.RunningAppProcessInfo p : procs) {
                JSONObject obj = new JSONObject();
                obj.put("pid",  p.pid);
                obj.put("name", p.processName);
                arr.put(obj);
            }
        }
        return arr;
    }
}
