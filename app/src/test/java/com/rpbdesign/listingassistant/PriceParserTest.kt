package com.rpbdesign.listingassistant

import org.junit.Assert.*
import org.junit.Test

class PriceParserTest {

    @Test fun `plain dollar price`() {
        assertEquals(24.99, PriceParser.parsePrice("$24.99")!!, 0.001)
    }

    @Test fun `US prefix price`() {
        assertEquals(24.99, PriceParser.parsePrice("US $24.99")!!, 0.001)
    }

    @Test fun `thousands separator`() {
        assertEquals(1299.0, PriceParser.parsePrice("$1,299.00")!!, 0.001)
    }

    @Test fun `bare number`() {
        assertEquals(9.5, PriceParser.parsePrice("9.50")!!, 0.001)
    }

    @Test fun `price range takes first`() {
        assertEquals(24.99, PriceParser.parsePrice("$24.99 to $39.99")!!, 0.001)
    }

    @Test fun `json-encoded from evaluateJavascript`() {
        assertEquals(24.99, PriceParser.parsePrice("\"US \$24.99\"")!!, 0.001)
    }

    @Test fun `blank is null`() {
        assertNull(PriceParser.parsePrice(""))
        assertNull(PriceParser.parsePrice("   "))
        assertNull(PriceParser.parsePrice(null))
    }

    @Test fun `garbage is null`() {
        assertNull(PriceParser.parsePrice("Free shipping"))
        assertNull(PriceParser.parsePrice("Best offer accepted"))
        assertNull(PriceParser.parsePrice("---"))
    }

    @Test fun `zero or negative is null`() {
        assertNull(PriceParser.parsePrice("$0.00"))
        assertNull(PriceParser.parsePrice("-$5.00"))
    }

    @Test fun `absurd value is null`() {
        assertNull(PriceParser.parsePrice("$99,999,999.99"))
    }

    @Test fun `malformed decimals rejected`() {
        assertNull(PriceParser.parsePrice("12.34.56"))
    }

    @Test fun `formats two decimals`() {
        assertEquals("24.90", PriceParser.formatForDraft(24.9))
        assertEquals("1299.00", PriceParser.formatForDraft(1299.0))
    }
}
