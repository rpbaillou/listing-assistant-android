package com.rpbdesign.listingassistant

import org.junit.Assert.*
import org.junit.Test

/**
 * apk-v13: the update offer must not be suppressible forever.
 * The old `updateOffered` boolean meant tapping "Later" (or a Home-button
 * round-trip that didn't destroy the Activity) silenced the offer even when
 * a newer version went live. Now the offer is per-versionCode: "Later"
 * records the version, so the same version is never re-offered, but any
 * newer one always is.
 */
class UpdateOfferTest {

    @Test fun neverOfferedBefore_offers() {
        assertTrue(Updater.shouldOfferUpdate(13, 0))
    }

    @Test fun newerVersionAfterLater_reoffers() {
        // User tapped "Later" on v12 (recorded 12); v13 goes live.
        assertTrue(Updater.shouldOfferUpdate(13, 12))
    }

    @Test fun sameVersionAfterLater_doesNotReoffer() {
        assertFalse(Updater.shouldOfferUpdate(12, 12))
    }

    @Test fun olderVersion_neverOffers() {
        assertFalse(Updater.shouldOfferUpdate(11, 12))
    }
}
