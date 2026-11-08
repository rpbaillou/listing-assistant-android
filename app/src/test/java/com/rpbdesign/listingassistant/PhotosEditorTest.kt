package com.rpbdesign.listingassistant

import org.junit.Assert.*
import org.junit.Test

class PhotosEditorTest {

    @Test
    fun photosAvailableTargetsPhotos() {
        assertEquals(
            PhotosEditor.GOOGLE_PHOTOS_PACKAGE,
            PhotosEditor.targetPackage(true)
        )
    }

    @Test
    fun photosMissingFallsBackToSystemResolver() {
        assertNull(PhotosEditor.targetPackage(false))
    }

    @Test
    fun photosPackageIsTheRealGooglePhotosPackage() {
        assertEquals(
            "com.google.android.apps.photos",
            PhotosEditor.GOOGLE_PHOTOS_PACKAGE
        )
    }
}
