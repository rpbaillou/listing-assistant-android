package com.rpbdesign.listingassistant

import org.junit.Assert.*
import org.junit.Test

/**
 * apk-v15: the update offer must never be suppressible by "Later".
 * v13 recorded the dismissed versionCode, so tapping "Later" on v14 once
 * meant v14 was never offered again — and with no manual check, Reynard
 * got stuck on v13 with v14 live. The v15 policy: the ONLY thing that
 * suppresses an offer is a dialog already on screen (no stacking).
 * "Later" just dismisses this dialog; the next check offers again.
 */
class UpdateOfferTest {

    @Test fun noDialogShowing_offers() {
        assertTrue(Updater.shouldShowUpdateDialog(dialogShowing = false))
    }

    @Test fun dialogAlreadyShowing_doesNotStack() {
        assertFalse(Updater.shouldShowUpdateDialog(dialogShowing = true))
    }

    @Test fun laterThenRecheck_reoffers() {
        // "Later" dismisses the dialog (dialogShowing goes false again).
        // The next check must offer the same version again — no memory
        // of the dismissal survives.
        assertFalse(Updater.shouldShowUpdateDialog(dialogShowing = true))
        assertTrue(Updater.shouldShowUpdateDialog(dialogShowing = false))
    }
}
