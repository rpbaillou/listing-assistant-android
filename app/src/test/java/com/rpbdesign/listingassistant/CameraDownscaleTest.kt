package com.rpbdesign.listingassistant

import android.media.ExifInterface
import org.junit.Assert.*
import org.junit.Test

/**
 * v14: the Take photo button calls Android.openCamera() (JS interface) and
 * the native side hands the downscaled JPEG back as a base64 data URL via
 * evaluateJavascript("onCameraPhoto('data:image/jpeg;base64,...')").
 * These tests pin the pure math and the JS-injection safety contract.
 */
class CameraDownscaleTest {

    @Test
    fun sampleSizeIsPowerOfTwoAndNotOverAggressive() {
        assertEquals(1, CameraDownscale.computeSampleSize(1600, 1200, 1600))
        assertEquals(1, CameraDownscale.computeSampleSize(3200, 2400, 1600))
        assertEquals(4, CameraDownscale.computeSampleSize(8000, 6000, 1600))
        // Tall photo: decoded at 375x1750, then exact-scaled to 343x1600.
        assertEquals(4, CameraDownscale.computeSampleSize(1500, 7000, 1600))
    }

    @Test
    fun targetSizeKeepsAspectWithinMaxEdge() {
        assertEquals(Pair(1600, 1200), CameraDownscale.targetSize(3200, 2400, 1600))
        assertEquals(Pair(1200, 1600), CameraDownscale.targetSize(1800, 2400, 1600))
        // Already small: untouched.
        assertEquals(Pair(800, 600), CameraDownscale.targetSize(800, 600, 1600))
        // Square.
        assertEquals(Pair(1600, 1600), CameraDownscale.targetSize(4000, 4000, 1600))
    }

    @Test
    fun maxEdgeConstantIs2048() {
        // v52: the photo cap is 2K on the long edge. The default
        // downscale() cap and the JS/server caps must match it, so a
        // photo is never shrunk twice.
        assertEquals(2048, CameraDownscale.MAX_EDGE)
    }

    @Test
    fun targetSizeAt2048Cap() {
        // Larger than 2K: shrinks to 2048 on the long edge, aspect kept.
        assertEquals(Pair(2048, 1536), CameraDownscale.targetSize(4096, 3072, 2048))
        assertEquals(Pair(1536, 2048), CameraDownscale.targetSize(1800, 2400, 2048))
        // Square.
        assertEquals(Pair(2048, 2048), CameraDownscale.targetSize(4000, 4000, 2048))
        // At the cap or smaller: untouched, never upscaled.
        assertEquals(Pair(2048, 1536), CameraDownscale.targetSize(2048, 1536, 2048))
        assertEquals(Pair(1600, 1200), CameraDownscale.targetSize(1600, 1200, 2048))
        assertEquals(Pair(800, 600), CameraDownscale.targetSize(800, 600, 2048))
    }

    @Test
    fun sampleSizeAt2048Cap() {
        assertEquals(1, CameraDownscale.computeSampleSize(4096, 3072, 2048))
        assertEquals(2, CameraDownscale.computeSampleSize(8000, 6000, 2048))
        assertEquals(4, CameraDownscale.computeSampleSize(16000, 12000, 2048))
    }

    @Test
    fun exifDegreesMapsOrientations() {
        assertEquals(90, CameraDownscale.exifDegrees(ExifInterface.ORIENTATION_ROTATE_90))
        assertEquals(180, CameraDownscale.exifDegrees(ExifInterface.ORIENTATION_ROTATE_180))
        assertEquals(270, CameraDownscale.exifDegrees(ExifInterface.ORIENTATION_ROTATE_270))
        assertEquals(0, CameraDownscale.exifDegrees(ExifInterface.ORIENTATION_NORMAL))
        assertEquals(0, CameraDownscale.exifDegrees(ExifInterface.ORIENTATION_UNDEFINED))
        assertEquals(0, CameraDownscale.exifDegrees(9999))
    }

    @Test
    fun base64AlphabetIsJsSafe() {
        // Every char Android's NO_WRAP encoder can emit.
        val alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
        assertTrue(CameraDownscale.isJsSafeBase64(alphabet))
        assertTrue(CameraDownscale.isJsSafeBase64(""))
        // Chars that WOULD need escaping must be rejected.
        assertFalse(CameraDownscale.isJsSafeBase64("ab'cd"))
        assertFalse(CameraDownscale.isJsSafeBase64("ab\\cd"))
        assertFalse(CameraDownscale.isJsSafeBase64("ab\ncd"))
        assertFalse(CameraDownscale.isJsSafeBase64("ab\"cd"))
    }

    @Test
    fun fullDataUrlCharsAreSafeInSingleQuotedJsString() {
        // The injected call is onCameraPhoto('data:image/jpeg;base64,<b64>').
        // The prefix is a hardcoded literal; these are its chars. None needs
        // escaping inside a single-quoted JS string.
        val prefix = "data:image/jpeg;base64,"
        assertTrue(prefix.none { it == '\'' || it == '\\' || it == '\n' || it == '\r' })
    }
}
