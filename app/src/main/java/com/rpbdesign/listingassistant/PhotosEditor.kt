package com.rpbdesign.listingassistant

/**
 * v44: listing-photo Edit targets Google Photos directly. The v36/v37
 * editor handoff left the package open, and the system resolver picks
 * the Pixel camera app editor on his phone — a limited editor. Google
 * Photos has the full editing tools (crop, rotate, filters, Magic
 * Eraser). When Photos is installed and can handle ACTION_EDIT for a
 * JPEG, the edit intent is pinned to its package; otherwise the system
 * resolver is used, exactly as before. The photo still goes in as a
 * MediaStore working copy (v37 lesson: a private provider URI resolved
 * to nothing on his Pixel).
 */
object PhotosEditor {

    /** Google Photos package, pinned so a typo can't silently fall back. */
    const val GOOGLE_PHOTOS_PACKAGE = "com.google.android.apps.photos"

    /**
     * Returns the package the edit intent should be pinned to, or null
     * to leave the intent open for the system resolver. Pure decision
     * logic so it can be unit-tested without a device.
     */
    fun targetPackage(photosCanEdit: Boolean): String? =
        if (photosCanEdit) GOOGLE_PHOTOS_PACKAGE else null
}
