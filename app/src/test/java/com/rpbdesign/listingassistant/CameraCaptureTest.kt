package com.rpbdesign.listingassistant

import org.junit.Assert.*
import org.junit.Test

class CameraCaptureTest {

    private val camUri = "content://com.rpbdesign.listingassistant.cameraprovider/la_123.jpg"
    private val galUri = "content://media/external/images/1"

    @Test
    fun galleryWinsOverCamera() {
        val out = CameraCapture.resolveUris(listOf(galUri), camUri, 5000)
        assertEquals(listOf(galUri), out)
    }

    @Test
    fun cameraUsedWhenGalleryEmpty() {
        val out = CameraCapture.resolveUris(emptyList(), camUri, 5000)
        assertEquals(listOf(camUri), out)
    }

    @Test
    fun emptyCameraFileYieldsNothing() {
        assertTrue(CameraCapture.resolveUris(emptyList(), camUri, 0).isEmpty())
    }

    @Test
    fun noCameraUriYieldsNothing() {
        assertTrue(CameraCapture.resolveUris(emptyList(), null, 5000).isEmpty())
    }

    @Test
    fun cancelledYieldsNothing() {
        assertTrue(CameraCapture.resolveUris(emptyList(), null, 0).isEmpty())
    }

    @Test
    fun tempNameIsProviderSafe() {
        val name = CameraCapture.tempName(1729660000000L)
        assertTrue(CameraCapture.isSafeName(name))
        assertTrue(name.endsWith(".jpg"))
    }

    @Test
    fun providerRejectsPathTricks() {
        assertFalse(CameraCapture.isSafeName("../evil.jpg"))
        assertFalse(CameraCapture.isSafeName("sub/dir.jpg"))
        assertFalse(CameraCapture.isSafeName(""))
    }

    @Test
    fun wasUsedDetectsHandoff() {
        assertTrue(CameraCapture.wasUsed(listOf(camUri), camUri))
        assertFalse(CameraCapture.wasUsed(listOf(galUri), camUri))
        assertFalse(CameraCapture.wasUsed(emptyList(), camUri))
    }
}
