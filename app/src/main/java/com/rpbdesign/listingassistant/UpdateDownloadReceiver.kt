package com.rpbdesign.listingassistant

import android.app.DownloadManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.os.Build
import android.os.Environment
import android.widget.Toast
import java.io.File

/**
 * Manifest-registered: fires even when the app is closed or was swiped away.
 *
 * The old updater registered its ACTION_DOWNLOAD_COMPLETE receiver on the
 * Activity, so closing the app mid-download killed the receiver and the
 * install never happened (the "tap Update, nothing happens, offered again"
 * bug, Oct 23 2026). This receiver lives in the manifest, matches the
 * completed download against the ID saved when Update was tapped, and fires
 * the installer. Anything else is ignored.
 */
class UpdateDownloadReceiver : BroadcastReceiver() {

    override fun onReceive(ctx: Context, intent: Intent) {
        if (intent.action != DownloadManager.ACTION_DOWNLOAD_COMPLETE) return
        val doneId = intent.getLongExtra(DownloadManager.EXTRA_DOWNLOAD_ID, -1L)
        val prefs = ctx.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        val pendingId = prefs.getLong(KEY_DOWNLOAD_ID, -1L)
        val fileName = prefs.getString(KEY_FILE_NAME, null)
        val pendingCode = prefs.getInt(KEY_VERSION_CODE, -1)
        if (!isOurDownload(doneId, pendingId, fileName)) return

        clearPending(prefs)
        // Never reinstall something already installed (or older).
        if (pendingCode <= installedVersionCode(ctx)) return

        if (!downloadSucceeded(ctx, doneId)) {
            Toast.makeText(
                ctx, "The update download failed. Reopen the app to try again.",
                Toast.LENGTH_LONG
            ).show()
            return
        }
        if (!File(ctx.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS), fileName!!).isFile) {
            Toast.makeText(
                ctx, "The update file is missing. Reopen the app to try again.",
                Toast.LENGTH_LONG
            ).show()
            return
        }
        Updater.installApk(ctx, fileName)
    }

    companion object {
        const val PREFS_NAME = "update_prefs"
        const val KEY_DOWNLOAD_ID = "pending_download_id"
        const val KEY_FILE_NAME = "pending_file_name"
        const val KEY_VERSION_CODE = "pending_version_code"

        /** Pure logic, unit-tested: is this completed download ours? */
        fun isOurDownload(doneId: Long, pendingId: Long, fileName: String?): Boolean =
            doneId != -1L && pendingId != -1L && doneId == pendingId && !fileName.isNullOrEmpty()

        /**
         * Pure logic, unit-tested: what should happen with a pending download
         * in the given DownloadManager status.
         */
        fun pendingAction(status: Int): PendingAction = when (status) {
            DownloadManager.STATUS_SUCCESSFUL -> PendingAction.INSTALL
            DownloadManager.STATUS_FAILED -> PendingAction.DISCARD
            else -> PendingAction.WAIT
        }

        fun clearPending(prefs: SharedPreferences) {
            prefs.edit()
                .remove(KEY_DOWNLOAD_ID)
                .remove(KEY_FILE_NAME)
                .remove(KEY_VERSION_CODE)
                .apply()
        }

        @Suppress("DEPRECATION")
        fun installedVersionCode(ctx: Context): Int {
            val pi = ctx.packageManager.getPackageInfo(ctx.packageName, 0)
            return if (Build.VERSION.SDK_INT >= 28) pi.longVersionCode.toInt()
            else pi.versionCode
        }

        private fun downloadSucceeded(ctx: Context, id: Long): Boolean {
            val dm = ctx.getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager
            val cursor = dm.query(DownloadManager.Query().setFilterById(id))
            return cursor?.use {
                it.moveToFirst() &&
                    it.getInt(it.getColumnIndexOrThrow(DownloadManager.COLUMN_STATUS)) ==
                    DownloadManager.STATUS_SUCCESSFUL
            } == true
        }
    }
}

/** What to do with a pending update download, given its current status. */
enum class PendingAction { INSTALL, DISCARD, WAIT }
