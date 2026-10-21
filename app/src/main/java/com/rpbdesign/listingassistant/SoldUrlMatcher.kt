package com.rpbdesign.listingassistant

import java.net.URI

/**
 * Matches eBay sold/completed search URLs so the app can show them in an
 * in-app pop-up instead of navigating the main WebView away (which loses
 * the user's place in their listing draft).
 *
 * Deliberately narrow: host must be ebay.com (any subdomain), the path must
 * be a search page (/sch/), and the query must ask for sold or completed
 * items. Everything else (OAuth, item pages, the app itself) is untouched.
 */
object SoldUrlMatcher {

    fun isSoldSearchUrl(url: String): Boolean {
        val uri = try {
            URI(url)
        } catch (_: Exception) {
            return false
        }
        val host = uri.host ?: return false
        val isEbay = host.equals("ebay.com", ignoreCase = true) ||
            host.endsWith(".ebay.com", ignoreCase = true)
        if (!isEbay) return false
        val path = uri.path ?: ""
        if (!path.contains("/sch/")) return false
        val query = uri.query ?: ""
        return query.contains("LH_Sold") || query.contains("LH_Complete")
    }

    /**
     * Matches an eBay item page (tapping a listing in the sold search lands
     * here). Deliberately narrow: ebay.com host, /itm/ path. Used to offer
     * "Use this price" in the sold-prices pop-up.
     */
    fun isEbayItemPage(url: String): Boolean {
        val uri = try {
            URI(url)
        } catch (_: Exception) {
            return false
        }
        val host = uri.host ?: return false
        val isEbay = host.equals("ebay.com", ignoreCase = true) ||
            host.endsWith(".ebay.com", ignoreCase = true)
        if (!isEbay) return false
        val path = uri.path ?: ""
        return path.contains("/itm/")
    }
}
