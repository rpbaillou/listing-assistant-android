package com.rpbdesign.listingassistant

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Matrix
import android.media.ExifInterface
import java.io.ByteArrayOutputStream
import java.io.File

/**
 * v14: native downscale for the JS-interface camera path ("Take photo" ->
 * Android.openCamera()). The captured JPEG is shrunk to max 1600px on the
 * long edge (matching the JS downscaleFile), EXIF-rotated so it is never
 * sideways, and JPEG-compressed — keeping the base64 handed to
 * evaluateJavascript small enough to be fast.
 *
 * v12's Take photo relied on the WebView honoring <input capture="camera">;
 * his phone's WebView ignores the attribute, so both buttons opened the
 * gallery. The JS interface bypasses the WebView entirely.
 */
object CameraDownscale {

    /** Power-of-2 sample size for decoding. Keeps the decoded bitmap within
     *  2x of maxEdge per side (targetSize() then does the exact shrink) —
     *  using maxEdge directly would over-shrink tall/wide photos because
     *  power-of-2 steps are coarse. Pure math. */
    fun computeSampleSize(w: Int, h: Int, maxEdge: Int): Int {
        var s = 1
        while (w / s > maxEdge * 2 || h / s > maxEdge * 2) s *= 2
        return s
    }

    /** Final pixel size fitting within maxEdge, aspect ratio kept. Pure math. */
    fun targetSize(w: Int, h: Int, maxEdge: Int): Pair<Int, Int> {
        val scale = minOf(1f, maxEdge.toFloat() / maxOf(w, h))
        return Pair(maxOf(1, (w * scale).toInt()), maxOf(1, (h * scale).toInt()))
    }

    /** EXIF orientation tag -> clockwise degrees. Pure math. */
    fun exifDegrees(orientation: Int): Int = when (orientation) {
        ExifInterface.ORIENTATION_ROTATE_90 -> 90
        ExifInterface.ORIENTATION_ROTATE_180 -> 180
        ExifInterface.ORIENTATION_ROTATE_270 -> 270
        else -> 0
    }

    /**
     * Android's Base64.NO_WRAP alphabet is A-Za-z0-9+/= only — safe inside a
     * single-quoted JS string with no escaping. The "data:image/jpeg;base64,"
     * prefix is equally safe. Unit-tested so a future encoder change can't
     * silently break the evaluateJavascript injection.
     */
    fun isJsSafeBase64(s: String): Boolean =
        s.all { it in 'A'..'Z' || it in 'a'..'z' || it in '0'..'9' ||
                it == '+' || it == '/' || it == '=' }

    /**
     * Reads src, applies EXIF rotation, fits within maxEdge, returns JPEG
     * bytes at the given quality. Null when the file can't be decoded.
     */
    fun downscale(src: File, maxEdge: Int = 1600, quality: Int = 85): ByteArray? {
        return try {
            val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            BitmapFactory.decodeFile(src.absolutePath, bounds)
            val w = bounds.outWidth
            val h = bounds.outHeight
            if (w <= 0 || h <= 0) return null
            val opts = BitmapFactory.Options().apply {
                inSampleSize = computeSampleSize(w, h, maxEdge)
            }
            var bmp = BitmapFactory.decodeFile(src.absolutePath, opts) ?: return null
            // Camera photos are often stored rotated with an EXIF tag.
            val deg = try {
                exifDegrees(
                    ExifInterface(src.absolutePath).getAttributeInt(
                        ExifInterface.TAG_ORIENTATION,
                        ExifInterface.ORIENTATION_NORMAL
                    )
                )
            } catch (_: Exception) {
                0
            }
            if (deg != 0) {
                val m = Matrix().apply { postRotate(deg.toFloat()) }
                val rotated = Bitmap.createBitmap(bmp, 0, 0, bmp.width, bmp.height, m, true)
                if (rotated !== bmp) bmp.recycle()
                bmp = rotated
            }
            val (tw, th) = targetSize(bmp.width, bmp.height, maxEdge)
            if (tw != bmp.width || th != bmp.height) {
                val scaled = Bitmap.createScaledBitmap(bmp, tw, th, true)
                if (scaled !== bmp) bmp.recycle()
                bmp = scaled
            }
            val out = ByteArrayOutputStream()
            val ok = bmp.compress(Bitmap.CompressFormat.JPEG, quality, out)
            bmp.recycle()
            if (!ok) null else out.toByteArray()
        } catch (_: Exception) {
            null
        }
    }
}
