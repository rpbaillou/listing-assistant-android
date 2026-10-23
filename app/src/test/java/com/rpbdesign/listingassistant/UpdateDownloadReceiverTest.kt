package com.rpbdesign.listingassistant

import android.app.DownloadManager
import org.junit.Assert.*
import org.junit.Test

/**
 * apk-v11: the download-complete receiver must only install OUR download.
 * The manifest receiver fires for every completed download on the phone
 * (other apps included), so the ID match is the whole safety story.
 */
class UpdateDownloadReceiverTest {

    @Test fun matchingIdAndFileIsOurs() {
        assertTrue(UpdateDownloadReceiver.isOurDownload(42L, 42L, "listing-assistant-v1.10.apk"))
    }

    @Test fun mismatchedIdIsNotOurs() {
        assertFalse(UpdateDownloadReceiver.isOurDownload(43L, 42L, "listing-assistant-v1.10.apk"))
    }

    @Test fun missingIdsAreNotOurs() {
        assertFalse(UpdateDownloadReceiver.isOurDownload(-1L, 42L, "a.apk"))
        assertFalse(UpdateDownloadReceiver.isOurDownload(42L, -1L, "a.apk"))
        assertFalse(UpdateDownloadReceiver.isOurDownload(-1L, -1L, "a.apk"))
    }

    @Test fun missingFileNameIsNotOurs() {
        assertFalse(UpdateDownloadReceiver.isOurDownload(42L, 42L, null))
        assertFalse(UpdateDownloadReceiver.isOurDownload(42L, 42L, ""))
    }

    @Test fun successfulDownloadMeansInstall() {
        assertEquals(
            PendingAction.INSTALL,
            UpdateDownloadReceiver.pendingAction(DownloadManager.STATUS_SUCCESSFUL)
        )
    }

    @Test fun failedDownloadMeansDiscard() {
        assertEquals(
            PendingAction.DISCARD,
            UpdateDownloadReceiver.pendingAction(DownloadManager.STATUS_FAILED)
        )
    }

    @Test fun inProgressDownloadMeansWait() {
        assertEquals(
            PendingAction.WAIT,
            UpdateDownloadReceiver.pendingAction(DownloadManager.STATUS_RUNNING)
        )
        assertEquals(
            PendingAction.WAIT,
            UpdateDownloadReceiver.pendingAction(DownloadManager.STATUS_PENDING)
        )
        assertEquals(
            PendingAction.WAIT,
            UpdateDownloadReceiver.pendingAction(DownloadManager.STATUS_PAUSED)
        )
    }
}
