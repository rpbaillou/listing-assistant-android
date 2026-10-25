package com.rpbdesign.listingassistant

import org.junit.Assert.*
import org.junit.Test
import java.io.IOException
import java.net.SocketTimeoutException
import java.net.UnknownHostException

/**
 * apk-v16: the updater no longer uses DownloadManager — it downloads with a
 * plain HttpURLConnection and shows a progress dialog. These tests cover
 * the pure helpers that drive that dialog and its error messages, so a
 * progress or wording regression is caught without a device.
 */
class UpdateDownloaderTest {

    @Test fun progress_zeroOfTotal_isZero() {
        assertEquals(0, Updater.progressPercent(0, 100))
    }

    @Test fun progress_half_isFifty() {
        assertEquals(50, Updater.progressPercent(50, 100))
    }

    @Test fun progress_roundsDownAndClamps() {
        assertEquals(33, Updater.progressPercent(1, 3))
        assertEquals(100, Updater.progressPercent(150, 100))
    }

    @Test fun progress_unknownTotal_isMinusOne() {
        assertEquals(-1, Updater.progressPercent(1234, -1))
        assertEquals(-1, Updater.progressPercent(1234, 0))
    }

    @Test fun formatMb_oneDecimal() {
        assertEquals("0.0 MB", Updater.formatMb(0))
        assertEquals("1.0 MB", Updater.formatMb(1048576))
        assertEquals("44.0 MB", Updater.formatMb(46100000))
    }

    @Test fun error_noHost_meansNoConnection() {
        val msg = Updater.plainDownloadError(UnknownHostException(), -1)
        assertTrue(msg.contains("no connection"))
    }

    @Test fun error_timeout_saysTimedOut() {
        val msg = Updater.plainDownloadError(SocketTimeoutException(), -1)
        assertTrue(msg.contains("timed out"))
    }

    @Test fun error_httpCode_reported() {
        assertEquals(
            "the server answered HTTP 404",
            Updater.plainDownloadError(null, 404)
        )
        // HTTP status wins over the exception when both are present.
        assertEquals(
            "the server answered HTTP 500",
            Updater.plainDownloadError(IOException("boom"), 500)
        )
    }

    @Test fun error_generic_io_mentionsNetwork() {
        val msg = Updater.plainDownloadError(IOException("reset"), -1)
        assertTrue(msg.contains("network error"))
    }
}
