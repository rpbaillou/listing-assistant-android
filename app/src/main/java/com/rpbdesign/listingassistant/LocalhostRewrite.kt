package com.rpbdesign.listingassistant

import java.net.URI

/**
 * v19: eBay's developer portal will not save a plain-http callback URL — its
 * form forces https. So the app's RuName callback is registered as
 * https://localhost:5050/api/ebay/callback, but the app's Flask server is
 * HTTP-only: an https navigation to localhost would die in a TLS handshake
 * before Flask ever sees the authorization code. Rewrite an https
 * localhost:5050 navigation to its http equivalent so the callback reaches
 * Flask. Deliberately narrow: only https, only localhost/127.0.0.1, only
 * port 5050. Everything else loads unchanged.
 *
 * URI is used only to validate the URL; the rewrite itself swaps just the
 * scheme prefix in the original string, so the path and query (including
 * the authorization code and state) survive byte-for-byte — no
 * decode/re-encode round trip that could mangle a code. Pure java.net.URI
 * logic so it stays unit-testable without Android.
 */
object LocalhostRewrite {

    fun rewriteHttpsLocalhostToHttp(url: String): String? {
        val uri = try {
            URI(url)
        } catch (_: Exception) {
            return null
        }
        if (!uri.scheme.equals("https", ignoreCase = true)) return null
        val host = uri.host ?: return null
        if (!host.equals("localhost", ignoreCase = true) &&
            !host.equals("127.0.0.1", ignoreCase = true)
        ) return null
        if (uri.port != 5050) return null
        // The scheme is the first component, ending at the first colon.
        val colon = url.indexOf(':')
        if (colon <= 0) return null
        return "http" + url.substring(colon)
    }
}
