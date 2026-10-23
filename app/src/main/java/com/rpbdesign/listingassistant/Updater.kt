package com.rpbdesign.listingassistant

import android.app.Activity
import android.app.AlertDialog
import android.app.DownloadManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.provider.Settings
import android.widget.Toast
import java.io.File
import java.net.HttpURLConnection
import java.net.URL

/**
 * Self-updater. On launch (non-blocking) it asks GitHub what the newest
 * versionCode is. If newer, it offers a one-tap download + install.
 * No forced updates, no account, no Play Store.
 *
 * v11 rework (Oct 23, 2026): the download-complete receiver is now
 * manifest-registered (UpdateDownloadReceiver), so closing the app
 * mid-download no longer kills the install. The phone must also allow
 * THIS app to install packages — checked up front with plain-words
 * guidance to Settings when it isn't.
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
        return if (Build.VERSION.SDK_INT >= 28) pi.longVersionCode.toInt()
        else pi.versionCode
    }

    /** True when the phone lets this app install its own updates. */
    fun canInstall(activity: Activity): Boolean =
        Build.VERSION.SDK_INT < 26 || activity.packageManager.canRequestPackageInstalls()

    /** Pure offer decision, unit-tested: offer iff this versionCode was
     *  never offered before. Tapping "Later" records the versionCode, so the
     *  same version is never re-offered, but any newer one always is. */
    fun shouldOfferUpdate(infoVersionCode: Int, lastOfferedVersionCode: Int): Boolean =
        infoVersionCode > lastOfferedVersionCode

    /**
     * Download the APK, then install it — even if the app is closed meanwhile.
     * The manifest-registered UpdateDownloadReceiver fires the installer when
     * the download completes; nothing here depends on the Activity surviving.
     */
    fun downloadAndInstall(activity: Activity, info: UpdateInfo) {
        // The "install unknown apps" grant is per-app: Chrome having it does
        // not help us. Ask for it BEFORE downloading, in plain words.
        if (!canInstall(activity)) {
            AlertDialog.Builder(activity)
                .setTitle("One permission needed")
                .setMessage(
                    "Your phone needs to allow Listing Assistant to install its " +
                    "own updates. Tap Settings below, turn on \"Allow from this " +
                    "source\", then come back and tap Update again."
                )
                .setPositiveButton("Settings") { _, _ ->
                    val intent = Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES)
                        .apply { data = Uri.parse("package:${activity.packageName}") }
                    activity.startActivity(intent)
                }
                .setNegativeButton("Not now", null)
                .show()
            return
        }
        try {
            val dm = activity.getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager
            val prefs = activity.getSharedPreferences(
                UpdateDownloadReceiver.PREFS_NAME, Context.MODE_PRIVATE)
            // Drop any previous half-finished update before starting a new one.
            val oldId = prefs.getLong(UpdateDownloadReceiver.KEY_DOWNLOAD_ID, -1L)
            if (oldId != -1L) {
                try { dm.remove(oldId) } catch (_: Exception) { }
            }
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
            val downloadId = dm.enqueue(req)
            // The manifest receiver matches on this ID, so persist it.
            prefs.edit()
                .putLong(UpdateDownloadReceiver.KEY_DOWNLOAD_ID, downloadId)
                .putString(UpdateDownloadReceiver.KEY_FILE_NAME, fileName)
                .putInt(UpdateDownloadReceiver.KEY_VERSION_CODE, info.versionCode)
                .apply()
            Toast.makeText(
                activity,
                "Downloading the update — you can close the app; it will " +
                "install when the download finishes.",
                Toast.LENGTH_LONG
            ).show()
        } catch (e: Exception) {
            Toast.makeText(activity, "Could not start the download.", Toast.LENGTH_LONG).show()
        }
    }

    /**
     * Called on every app start. Safety net for the one case the manifest
     * receiver can't cover: the app was force-stopped (receivers don't fire
     * for force-stopped apps). If a previous download already finished,
     * install it now; clean up stale records so we never nag twice.
     */
    fun resumePendingInstall(activity: Activity) {
        val prefs = activity.getSharedPreferences(
            UpdateDownloadReceiver.PREFS_NAME, Context.MODE_PRIVATE)
        val id = prefs.getLong(UpdateDownloadReceiver.KEY_DOWNLOAD_ID, -1L)
        val fileName = prefs.getString(UpdateDownloadReceiver.KEY_FILE_NAME, null)
        val code = prefs.getInt(UpdateDownloadReceiver.KEY_VERSION_CODE, -1)
        if (id == -1L || fileName.isNullOrEmpty()) return
        try {
            val dm = activity.getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager
            val cursor = dm.query(DownloadManager.Query().setFilterById(id))
            val hasRecord = cursor?.use { it.moveToFirst() } == true
            if (!hasRecord) {
                // DownloadManager pruned it (or data was cleared): drop it.
                UpdateDownloadReceiver.clearPending(prefs)
                return
            }
            val status = cursor.use {
                it.getInt(it.getColumnIndexOrThrow(DownloadManager.COLUMN_STATUS))
            }
            when (UpdateDownloadReceiver.pendingAction(status)) {
                PendingAction.INSTALL -> {
                    val fresh = code > installedVersionCode(activity)
                    val present = apkFile(activity, fileName).isFile
                    if (fresh && present && canInstall(activity)) {
                        UpdateDownloadReceiver.clearPending(prefs)
                        installApk(activity, fileName)
                    } else {
                        // Older than installed, file gone, or permission
                        // revoked: drop it; the normal check re-offers if needed.
                        UpdateDownloadReceiver.clearPending(prefs)
                    }
                }
                PendingAction.DISCARD -> UpdateDownloadReceiver.clearPending(prefs)
                PendingAction.WAIT -> { /* still downloading; receiver will fire */ }
            }
        } catch (_: Exception) {
            // Never break app start over update bookkeeping.
        }
    }

    /** Fire the system installer for a downloaded APK. Works from any Context. */
    fun installApk(ctx: Context, fileName: String) {
        try {
            if (!apkFile(ctx, fileName).isFile) throw IllegalStateException("apk missing")
            val uri = Uri.parse("content://${ctx.packageName}.apkprovider/$fileName")
            val intent = Intent(Intent.ACTION_VIEW).apply {
                setDataAndType(uri, "application/vnd.android.package-archive")
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            }
            ctx.startActivity(intent)
        } catch (_: Exception) {
            Toast.makeText(
                ctx,
                "Could not install the update. Make sure \"install unknown " +
                "apps\" is allowed for Listing Assistant, then try again.",
                Toast.LENGTH_LONG
            ).show()
        }
    }

    private fun apkFile(ctx: Context, fileName: String): File =
        File(ctx.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS), fileName)
}
