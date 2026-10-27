package com.rpbdesign.listingassistant

import org.junit.Assert.*
import org.junit.Test

class LocalhostRewriteTest {

    private val callback =
        "https://localhost:5050/api/ebay/callback?code=abc123&state=xyz789"

    @Test fun rewritesHttpsLocalhostCallbackToHttp() {
        assertEquals(
            "http://localhost:5050/api/ebay/callback?code=abc123&state=xyz789",
            LocalhostRewrite.rewriteHttpsLocalhostToHttp(callback)
        )
    }

    @Test fun rewritesHttpsLoopbackIp() {
        assertEquals(
            "http://127.0.0.1:5050/api/ebay/callback?code=x",
            LocalhostRewrite.rewriteHttpsLocalhostToHttp(
                "https://127.0.0.1:5050/api/ebay/callback?code=x"
            )
        )
    }

    @Test fun rewritesUppercaseHost() {
        assertEquals(
            "http://LOCALHOST:5050/api/ebay/callback",
            LocalhostRewrite.rewriteHttpsLocalhostToHttp(
                "https://LOCALHOST:5050/api/ebay/callback"
            )
        )
    }

    @Test fun leavesHttpLocalhostAlone() {
        assertNull(
            LocalhostRewrite.rewriteHttpsLocalhostToHttp(
                "http://localhost:5050/api/ebay/callback?code=x"
            )
        )
    }

    @Test fun leavesEbayAlone() {
        assertNull(
            LocalhostRewrite.rewriteHttpsLocalhostToHttp(
                "https://auth.ebay.com/oauth2/authorize?client_id=x"
            )
        )
    }

    @Test fun leavesWrongPortAlone() {
        assertNull(
            LocalhostRewrite.rewriteHttpsLocalhostToHttp(
                "https://localhost:9999/api/ebay/callback?code=x"
            )
        )
    }

    @Test fun leavesNoPortAlone() {
        assertNull(
            LocalhostRewrite.rewriteHttpsLocalhostToHttp(
                "https://localhost/api/ebay/callback?code=x"
            )
        )
    }

    @Test fun leavesOtherHostsAlone() {
        assertNull(
            LocalhostRewrite.rewriteHttpsLocalhostToHttp(
                "https://example.com:5050/api/ebay/callback?code=x"
            )
        )
    }

    @Test fun returnsNullForGarbage() {
        assertNull(LocalhostRewrite.rewriteHttpsLocalhostToHttp("not a url"))
        assertNull(LocalhostRewrite.rewriteHttpsLocalhostToHttp(""))
    }
}
