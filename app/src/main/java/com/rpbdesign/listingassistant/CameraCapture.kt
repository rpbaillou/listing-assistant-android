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

    /**
     * v12: routes the WebView file chooser. The Take photo button's input
     * sets capture="camera", which must go straight to the camera app —
     * his phone opens the Photos picker directly and never shows the
     * system chooser, so a chooser-dependent camera option never appears.
     * A capture request with no camera app installed falls back to the
     * gallery rather than stranding the user.
     */
    fun useCameraDirect(captureEnabled: Boolean, cameraAppAvailable: Boolean): Boolean =
        captureEnabled && cameraAppAvailable
}
