package com.rpbdesign.listingassistant

/**
 * Pure decision logic for the photo-picker result, so it can be unit-tested
 * without an Android device. URIs travel as plain strings here; MainActivity
 * converts to/from android.net.Uri at the boundary.
 *
 *   - gallery URIs (clipData / data) always win when present;
 *   - otherwise the camera temp photo, but only if it actually has bytes;
 *   - otherwise nothing (user cancelled or capture produced nothing).
 */
object CameraCapture {

    fun resolveUris(
        galleryUris: List<String>,
        cameraUri: String?,
        cameraBytes: Long
    ): List<String> {
        if (galleryUris.isNotEmpty()) return galleryUris
        if (cameraUri != null && cameraBytes > 0) return listOf(cameraUri)
        return emptyList()
    }

    /** Temp photo names: plain, timestamped, no separators (the provider rejects anything else). */
    fun tempName(nowMs: Long): String = "la_$nowMs.jpg"

    /** Rejects anything that is not a plain file name — the provider enforces this. */
    fun isSafeName(name: String): Boolean =
        name.isNotEmpty() && !name.contains("/") && !name.contains("..")

    /** True when the temp photo was handed to the WebView and must be kept for its async read. */
    fun wasUsed(resolved: List<String>, cameraUri: String?): Boolean =
        cameraUri != null && resolved.contains(cameraUri)
}
