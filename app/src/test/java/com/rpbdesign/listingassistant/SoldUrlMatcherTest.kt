package com.rpbdesign.listingassistant

import org.junit.Assert.*
import org.junit.Test

class SoldUrlMatcherTest {

    private val sold =
        "https://www.ebay.com/sch/i.html?_nkw=vintage+doll&LH_Sold=1&LH_Complete=1"
    private val completedOnly =
        "https://www.ebay.com/sch/i.html?_nkw=vintage+doll&LH_Complete=1"
    private val mobileSubdomain =
        "https://m.ebay.com/sch/i.html?_nkw=x&LH_Sold=1"

    @Test fun matchesSoldSearch() {
        assertTrue(SoldUrlMatcher.isSoldSearchUrl(sold))
    }

    @Test fun matchesCompletedOnly() {
        assertTrue(SoldUrlMatcher.isSoldSearchUrl(completedOnly))
    }

    @Test fun matchesSubdomain() {
        assertTrue(SoldUrlMatcher.isSoldSearchUrl(mobileSubdomain))
    }

    @Test fun rejectsSearchWithoutSoldParams() {
        assertFalse(
            SoldUrlMatcher.isSoldSearchUrl(
                "https://www.ebay.com/sch/i.html?_nkw=vintage+doll"
            )
        )
    }

    @Test fun rejectsItemPage() {
        assertFalse(
            SoldUrlMatcher.isSoldSearchUrl("https://www.ebay.com/itm/123456789")
        )
    }

    @Test fun rejectsOAuth() {
        assertFalse(
            SoldUrlMatcher.isSoldSearchUrl(
                "https://auth.ebay.com/oauth2/authorize?client_id=x&redirect_uri=y"
            )
        )
    }

    @Test fun rejectsAppItself() {
        assertFalse(SoldUrlMatcher.isSoldSearchUrl("http://127.0.0.1:5050/"))
        assertFalse(
            SoldUrlMatcher.isSoldSearchUrl("http://127.0.0.1:5050/api/drafts")
        )
    }

    @Test fun rejectsNonEbay() {
        assertFalse(
            SoldUrlMatcher.isSoldSearchUrl("https://www.google.com/search?q=doll")
        )
    }

    @Test fun rejectsGarbage() {
        assertFalse(SoldUrlMatcher.isSoldSearchUrl("not a url"))
        assertFalse(SoldUrlMatcher.isSoldSearchUrl(""))
    }
}
