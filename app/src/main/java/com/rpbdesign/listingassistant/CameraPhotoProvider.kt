package com.rpbdesign.listingassistant

import android.content.ContentProvider
import android.content.ContentValues
import android.content.Context
import android.database.Cursor
import android.database.MatrixCursor
import android.net.Uri
import android.os.ParcelFileDescriptor
import android.provider.OpenableColumns
import java.io.File
import java.io.FileNotFoundException

/**
 * Serves the in-app camera's temp photo file: the camera app writes the
 * captured JPEG through this URI (EXTRA_OUTPUT), and our WebView file picker
 * reads it back. App-private cache dir only, exact file names only —
 * no path tricks. No AndroidX needed.
 */
class CameraPhotoProvider : ContentProvider() {

    override fun onCreate(): Boolean = true

    companion object {
        const val AUTHORITY_SUFFIX = ".cameraprovider"

        fun cameraDir(context: Context): File =
            File(context.cacheDir, "camera").apply { mkdirs() }

        fun uriFor(context: Context, file: File): Uri =
            Uri.parse("content://${context.packageName}$AUTHORITY_SUFFIX/${file.name}")
    }

    private fun photoFile(uri: Uri): File {
        val name = uri.lastPathSegment ?: throw FileNotFoundException()
        if (!CameraCapture.isSafeName(name)) throw FileNotFoundException()
        val f = File(cameraDir(context!!), name)
        if (!f.isFile) throw FileNotFoundException()
        return f
    }

    override fun query(
        uri: Uri, projection: Array<String>?, selection: String?,
        selectionArgs: Array<String>?, sortOrder: String?
    ): Cursor? {
        val f = try { photoFile(uri) } catch (_: Exception) { return null }
        return MatrixCursor(arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE)).also {
            it.addRow(arrayOf(f.name, f.length()))
        }
    }

    override fun getType(uri: Uri): String = "image/jpeg"

    override fun openFile(uri: Uri, mode: String): ParcelFileDescriptor? {
        val f = photoFile(uri)
        // parseMode turns the camera app's "w"/"rw" into the right flags.
        return ParcelFileDescriptor.open(f, ParcelFileDescriptor.parseMode(mode))
    }

    override fun insert(uri: Uri, values: ContentValues?): Uri? =
        throw UnsupportedOperationException()

    override fun delete(uri: Uri, selection: String?, selectionArgs: Array<String>?): Int = 0

    override fun update(
        uri: Uri, values: ContentValues?, selection: String?, selectionArgs: Array<String>?
    ): Int = 0
}
