package com.rpbdesign.listingassistant

/**
 * Parses a price string read off an eBay item page into a number the draft
 * can store. Pure Kotlin, no Android dependency, fully unit-tested.
 *
 * Handles: "US $24.99", "$1,299.00", "24.99", "$24.99 to $39.99" (takes the
 * first). Returns null for anything that is not clearly a price — the
 * caller must hide "Use this price" rather than write a wrong number.
 */
object PriceParser {

    /** "US $24.99" -> 24.99; garbage -> null. */
    fun parsePrice(raw: String?): Double? {
        if (raw.isNullOrBlank()) return null
        // evaluateJavascript returns a JSON-encoded string: strip quotes.
        var s = raw.trim()
        if (s.length >= 2 && s.startsWith("\"") && s.endsWith("\"")) {
            s = s.substring(1, s.length - 1)
                .replace("\\\"", "\"")
                .replace("\\\\", "\\")
                .replace("\\n", " ")
        }
        // A range ("$24.99 to $39.99"): take the first price only.
        val rangeCut = Regex("\\s+to\\s+", RegexOption.IGNORE_CASE)
            .split(s, limit = 2)
        s = rangeCut[0]
        // Keep digits, dots, commas, minus. Drop currency words/symbols.
        val cleaned = s.replace(Regex("[^0-9.,-]"), "")
        if (cleaned.isEmpty() || !cleaned.any { it.isDigit() }) return null
        // "1,299.00" -> "1299.00". (European "24,99" would misparse, but
        // eBay US shows US formatting; a comma is a thousands separator.)
        val normalized = cleaned.replace(",", "")
        // Reject things like "12.34.56" or a lone "-" / ".".
        if (normalized.count { it == '.' } > 1) return null
        val value = normalized.toDoubleOrNull() ?: return null
        if (value <= 0 || value > 10_000_000) return null
        return value
    }

    /** 24.9 -> "24.90", the shape the draft's price field expects. */
    fun formatForDraft(value: Double): String = "%.2f".format(value)
}
