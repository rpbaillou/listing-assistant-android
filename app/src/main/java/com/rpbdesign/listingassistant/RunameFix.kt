package com.rpbdesign.listingassistant

/**
 * v22: one-tap / zero-typing fix for the recurring eBay 400.
 *
 * HARDCODED-VALUE NOTE (read before worrying): the two RuName strings below
 * are PUBLIC identifiers, not secrets. A RuName appears in plain text in
 * every eBay OAuth authorize URL the app builds, and eBay itself displays
 * it on the developer portal. The Client Secret is the secret: it stays in
 * the app's private .env only and is NEVER baked into the app, logged, or
 * sent anywhere except eBay's token endpoint over HTTPS. Baking a public
 * redirect name into the app is safe in exactly the way baking the App ID
 * would be; baking the secret would not be, and nothing here does that.
 *
 * Background: the app shipped for weeks sending the RuName
 * ...-mwokavhzz, which was never registered in eBay's Production "Mr"
 * keyset, so eBay rejected every authorize attempt with invalid_request
 * (400). The working RuName ...-eayxussz was created Oct 27, 2026
 * (OAuth enabled, callback https://localhost:5050/api/ebay/callback),
 * and a clean-browser authorize test with it reached eBay's consent page
 * with no 400. The user then had to retype the name by hand in Edit keys
 * — and the 400 came back, because the old value was still in use
 * (the edit was skipped, or did not take). v22 removes typing from the
 * fix entirely: the known-bad value is auto-replaced on launch, and any
 * other wrong value gets a one-tap button instead of a text field.
 */
object RunameFix {

    /** The never-registered RuName that caused every 400. */
    const val KNOWN_BAD_RUNAME = "Reynard_Baillou-ReynardB-Mr-PRD-mwokavhzz"

    /** The OAuth-enabled RuName eBay accepts (proven by browser test). */
    const val WORKING_RUNAME = "Reynard_Baillou-ReynardB-Mr-PRD-eayxussz"

    /**
     * True only for the one known-bad value. The comparison trims first:
     * a stored value differing only by surrounding whitespace is the same
     * bad value, and must still auto-fix instead of silently missing.
     */
    fun needsAutoFix(stored: String?): Boolean =
        stored?.trim() == KNOWN_BAD_RUNAME

    /** True only for the working RuName (trimmed, same reason). */
    fun isWorkingRuname(stored: String?): Boolean =
        stored?.trim() == WORKING_RUNAME

    /**
     * Rewrites a .env file's text so EBAY_RUNAME becomes [newRuname].
     * Every other line is returned byte-for-byte: same order, same
     * comments, same other keys, same trailing-newline state. Every
     * EBAY_RUNAME line is replaced (there should only ever be one, but
     * replacing all of them guarantees the loaded value is unambiguous).
     * Lines without '=' or with a different key are never touched, so a
     * line like EBAY_RUNAME2=... can never be mistaken for the key.
     */
    fun rewriteEnvText(text: String, newRuname: String): String {
        val lines = text.split("\n")
        val out = lines.map { line ->
            val key = if ("=" in line) line.substringBefore("=").trim() else ""
            if (key == "EBAY_RUNAME") "EBAY_RUNAME=$newRuname" else line
        }
        return out.joinToString("\n")
    }
}
