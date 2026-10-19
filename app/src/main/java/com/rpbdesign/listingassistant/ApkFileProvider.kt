package com.rpbdesign.listingassistant

import android.content.ContentProvider
import android.content.ContentValues
import android.database.Cursor
import android.database.MatrixCursor
import android.net.Uri
import android.os.Environment
import android.os.ParcelFileDescriptor
import android.provider.OpenableColumns
import java.io.File
import java.io.FileNotFoundException

/**
 * Serves the downloaded update APK to the package installer through a
 * content:// URI (Android 7+ refuses file:// URIs). No AndroidX needed.
 * Only files inside the app's own Downloads dir are reachable, and only
 * by name (no path tricks).
 */
class ApkFileProvider : ContentProvider() {

    override fun onCreate(): Boolean = true

    private fun apkFile(uri: Uri): File {
        val name = uri.lastPathSegment ?: throw FileNotFoundException()
        if (name.contains("/") || name.contains("..")) throw FileNotFoundException()
        val dir = context!!.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS)
            ?: throw FileNotFoundException()
        val f = File(dir, name)
        if (!f.isFile) throw FileNotFoundException()
        return f
    }

    override fun query(
        uri: Uri, projection: Array<String>?, selection: String?,
        selectionArgs: Array<String>?, sortOrder: String?
    ): Cursor? {
        val f = try { apkFile(uri) } catch (_: Exception) { return null }
        return MatrixCursor(arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE)).also {
            it.addRow(arrayOf(f.name, f.length()))
        }
    }

    override fun getType(uri: Uri): String = "application/vnd.android.package-archive"

    override fun openFile(uri: Uri, mode: String): ParcelFileDescriptor? {
        val f = try { apkFile(uri) } catch (_: Exception) { throw FileNotFoundException() }
        return ParcelFileDescriptor.open(f, ParcelFileDescriptor.MODE_READ_ONLY)
    }

    override fun insert(uri: Uri, values: ContentValues?): Uri? =
        throw UnsupportedOperationException()

    override fun delete(uri: Uri, selection: String?, selectionArgs: Array<String>?): Int = 0

    override fun update(
        uri: Uri, values: ContentValues?, selection: String?, selectionArgs: Array<String>?
    ): Int = 0
}
