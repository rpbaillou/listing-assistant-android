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
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import android.widget.Toast
import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.net.HttpURLConnection
import java.net.SocketTimeoutException
import java.net.URL
import java.net.UnknownHostException
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference

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
 *
 * v16 rework (Oct 25, 2026): the DownloadManager path is GONE from the
 * main flow. Tapping UPDATE on v13/v15 produced no download, no
 * notification, no error — nothing — and the cause could not be diagnosed
 * remotely (permission confirmed ON, toasts missed or suppressed,
 * DownloadManager possibly failing silently on the GitHub redirect).
 * v16 downloads with a plain HttpURLConnection in a background thread and
 * shows a real progress dialog with MB/percentage, so there is never any
 * "did it start?" ambiguity. Any failure shows an AlertDialog with the
 * real error plus Retry/Cancel — never a toast, never silent. The old
 * manifest receiver + resumePendingInstall stay as a harmless backup only.
 */
object Updater {

    const val VERSION_URL =
        "https://raw.githubusercontent.com/rpbaillou/listing-assistant-android/main/version.json"

    /**
     * Check in the background. Calls back on the UI thread with exactly one
     * outcome: onUpdate when a newer build exists, onUpToDate when the
     * installed build is current, onError when the check itself could not
     * complete (offline, non-200, malformed file). onUpToDate/onError are
     * optional: automatic checks pass only onError (toast), manual checks
     * pass both so the user always gets an answer.
     */
    fun check(
        activity: Activity,
        onUpdate: (UpdateInfo) -> Unit,
        onUpToDate: (() -> Unit)? = null,
        onError: (() -> Unit)? = null
    ) {
        Thread {
            try {
                val installedCode = installedVersionCode(activity)
                val conn = URL(VERSION_URL).openConnection() as HttpURLConnection
                conn.connectTimeout = 15000
                conn.readTimeout = 15000
                try {
                    if (conn.responseCode != 200) {
                        onError?.let { activity.runOnUiThread { it() } }
                        return@Thread
                    }
                    val json = conn.inputStream.bufferedReader().readText()
                    val info = parseVersionJson(json)
                    if (info == null) {
                        onError?.let { activity.runOnUiThread { it() } }
                        return@Thread
                    }
                    if (isUpdateAvailable(installedCode, info)) {
                        activity.runOnUiThread { onUpdate(info) }
                    } else {
                        onUpToDate?.let { activity.runOnUiThread { it() } }
                    }
                } finally {
                    conn.disconnect()
                }
            } catch (_: Exception) {
                // Offline, DNS trouble: report it instead of staying silent
                // (the v13 check swallowed this, so a failed check looked
                // exactly like "no update available").
                onError?.let { activity.runOnUiThread { it() } }
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

    /** Pure offer decision, unit-tested: the ONLY thing that suppresses an
     *  update offer is a dialog already on screen (no stacking). There is
     *  deliberately no per-version "Later" memory any more: v13 recorded the
     *  dismissed versionCode, so tapping "Later" once meant that version was
     *  never offered again — and with no manual check, Reynard got stuck on
     *  v13 with v14 live. "Later" now just dismisses this dialog; the next
     *  check offers again. */
    fun shouldShowUpdateDialog(dialogShowing: Boolean): Boolean = !dialogShowing

    /**
     * Download the APK with a plain HttpURLConnection (v16: no
     * DownloadManager anywhere in this path) and install it when done.
     * Shows a non-cancelable progress dialog with real MB/percentage while
     * downloading, and an AlertDialog with the real error + Retry/Cancel
     * on any failure. Never silent, never toast-only.
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
        val fileName = "listing-assistant-v${info.versionName.ifEmpty { info.versionCode }}.apk"
        val target = apkFile(activity, fileName)
        val cancelled = AtomicBoolean(false)
        val connRef = AtomicReference<HttpURLConnection?>(null)

        // Progress UI: built in code (no XML layout) so this stays
        // self-contained. Non-cancelable via back press; the Cancel button
        // is the only way out mid-download, and it cleans up.
        val progressBar = ProgressBar(
            activity, null, android.R.attr.progressBarStyleHorizontal
        ).apply {
            max = 100
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            )
        }
        val statusText = TextView(activity).apply { text = "Starting download…" }
        val layout = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            val pad = (16 * resources.displayMetrics.density).toInt()
            setPadding(pad, pad, pad, pad)
            addView(statusText)
            addView(progressBar)
        }
        val progressDialog = AlertDialog.Builder(activity)
            .setTitle("Downloading update ${info.versionName.ifEmpty { "" }}")
            .setView(layout)
            .setCancelable(false)
            .setNegativeButton("Cancel") { _, _ ->
                // Flag + disconnect: the blocked read aborts promptly
                // instead of hanging until the read timeout.
                cancelled.set(true)
                try { connRef.get()?.disconnect() } catch (_: Exception) { }
            }
            .show()

        Thread {
            var failure: String? = null
            var httpCode = -1
            try {
                val dir = target.parentFile
                if (dir != null && !dir.exists()) dir.mkdirs()
                val conn = URL(info.apkUrl).openConnection() as HttpURLConnection
                connRef.set(conn)
                conn.instanceFollowRedirects = true // GitHub 302s to release-assets
                conn.connectTimeout = 30000
                conn.readTimeout = 30000
                conn.connect()
                httpCode = conn.responseCode
                if (httpCode != HttpURLConnection.HTTP_OK) {
                    failure = plainDownloadError(null, httpCode)
                } else {
                    val total = conn.contentLengthLong // -1 when unknown
                    var downloaded = 0L
                    var lastUiUpdate = 0L
                    conn.inputStream.use { input ->
                        FileOutputStream(target).use { output ->
                            val buf = ByteArray(64 * 1024)
                            while (true) {
                                if (cancelled.get()) break
                                val read = input.read(buf)
                                if (read == -1) break
                                output.write(buf, 0, read)
                                downloaded += read
                                val now = System.currentTimeMillis()
                                if (now - lastUiUpdate >= 200) {
                                    lastUiUpdate = now
                                    val d = downloaded
                                    val t = total
                                    activity.runOnUiThread {
                                        if (t > 0) {
                                            progressBar.isIndeterminate = false
                                            progressBar.progress = progressPercent(d, t)
                                            statusText.text =
                                                "${formatMb(d)} / ${formatMb(t)} " +
                                                "(${progressPercent(d, t)}%)"
                                        } else {
                                            progressBar.isIndeterminate = true
                                            statusText.text = "${formatMb(d)} downloaded"
                                        }
                                    }
                                }
                            }
                        }
                    }
                    if (!cancelled.get() && failure == null) {
                        // Final progress paint, then install on the UI thread.
                        val d = downloaded
                        val t = total
                        activity.runOnUiThread {
                            if (t > 0) {
                                progressBar.progress = 100
                                statusText.text =
                                    "${formatMb(d)} / ${formatMb(t)} (100%)"
                            }
                        }
                    }
                }
            } catch (e: Exception) {
                if (!cancelled.get()) failure = plainDownloadError(e, httpCode)
            } finally {
                try { connRef.get()?.disconnect() } catch (_: Exception) { }
            }
            val wasCancelled = cancelled.get()
            val failMessage = failure
            activity.runOnUiThread {
                if (activity.isFinishing || activity.isDestroyed) return@runOnUiThread
                try { progressDialog.dismiss() } catch (_: Exception) { }
                when {
                    wasCancelled -> {
                        // Clean cancel: drop the partial file, say nothing.
                        try { if (target.exists()) target.delete() } catch (_: Exception) { }
                    }
                    failMessage != null -> {
                        try { if (target.exists()) target.delete() } catch (_: Exception) { }
                        AlertDialog.Builder(activity)
                            .setTitle("Update download failed")
                            .setMessage(
                                "The update could not be downloaded: $failMessage.\n\n" +
                                "You can try again, or cancel and use the " +
                                "\"Check for updates\" button later."
                            )
                            .setPositiveButton("Retry") { _, _ ->
                                downloadAndInstall(activity, info)
                            }
                            .setNegativeButton("Cancel", null)
                            .show()
                    }
                    else -> installApk(activity, fileName)
                }
            }
        }.start()
    }

    /** Percent 0-100, or -1 when the total size is unknown. Pure, tested. */
    fun progressPercent(downloaded: Long, total: Long): Int {
        if (total <= 0) return -1
        val pct = (downloaded * 100 / total).toInt()
        return pct.coerceIn(0, 100)
    }

    /** "44.0 MB" style, one decimal. Pure, tested. */
    fun formatMb(bytes: Long): String {
        val mb = bytes / 1048576.0
        return "%.1f MB".format(mb)
    }

    /** Plain-words download failure, never a raw stack trace. Pure, tested. */
    fun plainDownloadError(e: Exception?, httpCode: Int): String {
        if (httpCode != -1 && httpCode != HttpURLConnection.HTTP_OK) {
            return "the server answered HTTP $httpCode"
        }
        return when (e) {
            is UnknownHostException -> "no connection (the phone could not reach the server)"
            is SocketTimeoutException -> "the connection timed out"
            is IOException -> "a network error (${e.message ?: "unknown"})"
            null -> "an unknown error"
            else -> e.message ?: "an unknown error"
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
