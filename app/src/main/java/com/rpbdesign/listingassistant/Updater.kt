package com.rpbdesign.listingassistant

import android.app.Activity
import android.app.AlertDialog
import android.app.DownloadManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.widget.Toast
import java.net.HttpURLConnection
import java.net.URL

/**
 * Self-updater. On launch (non-blocking) it asks GitHub what the newest
 * versionCode is. If newer, it offers a one-tap download + install.
 * No forced updates, no account, no Play Store.
 */
object Updater {

    const val VERSION_URL =
        "https://raw.githubusercontent.com/rpbaillou/listing-assistant-android/main/version.json"

    /** Check in the background; calls back on the UI thread only when an update exists. */
    fun check(activity: Activity, onUpdate: (UpdateInfo) -> Unit) {
        Thread {
            try {
                val installedCode = installedVersionCode(activity)
                val conn = URL(VERSION_URL).openConnection() as HttpURLConnection
                conn.connectTimeout = 15000
                conn.readTimeout = 15000
                try {
                    if (conn.responseCode != 200) return@Thread
                    val json = conn.inputStream.bufferedReader().readText()
                    val info = parseVersionJson(json) ?: return@Thread
                    if (isUpdateAvailable(installedCode, info)) {
                        activity.runOnUiThread { onUpdate(info) }
                    }
                } finally {
                    conn.disconnect()
                }
            } catch (_: Exception) {
                // Offline, DNS trouble, malformed file: stay silent, try next launch.
            }
        }.start()
    }

    /** The versionCode of the APK actually installed on this phone. */
    @Suppress("DEPRECATION")
    private fun installedVersionCode(activity: Activity): Int {
        val pi = activity.packageManager.getPackageInfo(activity.packageName, 0)
        return if (android.os.Build.VERSION.SDK_INT >= 28) pi.longVersionCode.toInt()
        else pi.versionCode
    }

    /** Download the APK with a visible progress notification, then install it. */
    fun downloadAndInstall(activity: Activity, info: UpdateInfo) {
        try {
            val fileName = "listing-assistant-v${info.versionName.ifEmpty { info.versionCode }}.apk"
            val req = DownloadManager.Request(Uri.parse(info.apkUrl)).apply {
                setTitle("Listing Assistant update")
                setDescription("Downloading version ${info.versionName.ifEmpty { "new" }}")
                setNotificationVisibility(
                    DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                setDestinationInExternalFilesDir(
                    activity, Environment.DIRECTORY_DOWNLOADS, fileName)
                setMimeType("application/vnd.android.package-archive")
            }
            val dm = activity.getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager
            val downloadId = dm.enqueue(req)
            val filter = IntentFilter(DownloadManager.ACTION_DOWNLOAD_COMPLETE)
            val receiver = object : BroadcastReceiver() {
                override fun onReceive(ctx: Context, intent: Intent) {
                    if (intent.getLongExtra(DownloadManager.EXTRA_DOWNLOAD_ID, -1) != downloadId) return
                    try { ctx.unregisterReceiver(this) } catch (_: Exception) { }
                    installApk(activity, fileName)
                }
            }
            if (Build.VERSION.SDK_INT >= 33) {
                activity.registerReceiver(receiver, filter, Context.RECEIVER_NOT_EXPORTED)
            } else {
                @Suppress("DEPRECATION")
                activity.registerReceiver(receiver, filter)
            }
        } catch (e: Exception) {
            Toast.makeText(activity, "Could not start the download.", Toast.LENGTH_LONG).show()
        }
    }

    private fun installApk(activity: Activity, fileName: String) {
        try {
            val uri = Uri.parse("content://${activity.packageName}.apkprovider/$fileName")
            val intent = Intent(Intent.ACTION_VIEW).apply {
                setDataAndType(uri, "application/vnd.android.package-archive")
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            }
            activity.startActivity(intent)
        } catch (_: Exception) {
            // Usually means the "install unknown apps" permission was revoked.
            activity.runOnUiThread {
                AlertDialog.Builder(activity)
                    .setTitle("Could not install the update")
                    .setMessage(
                        "The phone blocked the install. Open Settings, search " +
                        "\"install unknown apps\", allow it for Listing Assistant, " +
                        "then reopen the app to try again.")
                    .setPositiveButton("OK", null)
                    .show()
            }
        }
    }
}
