package com.rpbdesign.listingassistant

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** v22: the RuName auto-fix fires only for the one known-bad value, and
 *  the .env rewrite preserves every other line byte-for-byte. */
class RunameFixTest {

    private val bad = RunameFix.KNOWN_BAD_RUNAME
    private val good = RunameFix.WORKING_RUNAME

    @Test
    fun autoFixFiresOnlyForKnownBad() {
        assertTrue(RunameFix.needsAutoFix(bad))
        // Whitespace differences must not let the bad value slip through.
        assertTrue(RunameFix.needsAutoFix("  $bad  "))
        assertTrue(RunameFix.needsAutoFix("$bad\n"))
    }

    @Test
    fun autoFixLeavesEverythingElseAlone() {
        assertFalse(RunameFix.needsAutoFix(good))
        assertFalse(RunameFix.needsAutoFix("Reynard_Baillou-ReynardB-Mr-PRD-qqrocq"))
        assertFalse(RunameFix.needsAutoFix("Reynard_Baillou-ReynardB-Mr-PRD-eayxuss"))
        assertFalse(RunameFix.needsAutoFix(""))
        assertFalse(RunameFix.needsAutoFix(null))
        // A value that merely contains the bad name is not the bad name.
        assertFalse(RunameFix.needsAutoFix("xx$bad"))
        assertFalse(RunameFix.needsAutoFix("$bad-extra"))
    }

    @Test
    fun workingCheck() {
        assertTrue(RunameFix.isWorkingRuname(good))
        assertTrue(RunameFix.isWorkingRuname("  $good "))
        assertFalse(RunameFix.isWorkingRuname(bad))
        assertFalse(RunameFix.isWorkingRuname(null))
    }

    @Test
    fun rewritePreservesOtherLines() {
        val before = "# Listing Assistant keys. Saved on this phone only.\n" +
            "GEMINI_API_KEY=abc123\n" +
            "EBAY_CLIENT_ID=ReynardB-Mr-PRD-0cd475433-feb24602\n" +
            "EBAY_CLIENT_SECRET=s3cr3t-value\n" +
            "EBAY_RUNAME=$bad\n" +
            "EBAY_FULFILLMENT_POLICY_ID=111\n" +
            "EBAY_PAYMENT_POLICY_ID=222\n" +
            "EBAY_RETURN_POLICY_ID=333\n"
        val after = RunameFix.rewriteEnvText(before, good)
        val expected = before.replace("EBAY_RUNAME=$bad", "EBAY_RUNAME=$good")
        assertEquals(expected, after)
        // The secret and every other key survive untouched.
        assertTrue(after.contains("EBAY_CLIENT_SECRET=s3cr3t-value"))
        assertTrue(after.contains("GEMINI_API_KEY=abc123"))
    }

    @Test
    fun rewriteHandlesQuotedAndDuplicateLines() {
        val quoted = "EBAY_RUNAME=\"$bad\"\nEBAY_CLIENT_ID=x\n"
        assertEquals(
            "EBAY_RUNAME=$good\nEBAY_CLIENT_ID=x\n",
            RunameFix.rewriteEnvText(quoted, good)
        )
        val dup = "EBAY_RUNAME=$bad\nEBAY_RUNAME=$bad\n"
        assertEquals(
            "EBAY_RUNAME=$good\nEBAY_RUNAME=$good\n",
            RunameFix.rewriteEnvText(dup, good)
        )
    }

    @Test
    fun rewriteLeavesNonRunameLinesAlone() {
        // No EBAY_RUNAME line at all: text comes back identical.
        val text = "GEMINI_API_KEY=k\nEBAY_RUNAME2=not-the-key\n"
        assertEquals(text, RunameFix.rewriteEnvText(text, good))
        // No trailing newline stays no-trailing-newline.
        val noNl = "EBAY_RUNAME=$bad"
        assertEquals("EBAY_RUNAME=$good", RunameFix.rewriteEnvText(noNl, good))
    }
}
