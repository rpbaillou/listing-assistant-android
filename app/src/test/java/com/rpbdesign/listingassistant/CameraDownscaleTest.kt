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
