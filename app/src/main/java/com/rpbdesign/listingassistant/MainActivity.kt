package com.rpbdesign.listingassistant

import android.annotation.SuppressLint
import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.os.Message
import android.view.View
import android.view.ViewGroup
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.EditText
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import java.io.File
import java.net.HttpURLConnection
import java.net.Socket
import java.net.URL

/**
 * Listing Assistant: the Flask app runs inside the APK (Chaquopy Python) and
 * the WebView shows it. First run asks for the 7 keys once; they are saved to
 * the app's private storage and never leave the phone except to eBay/Google.
 */
class MainActivity : Activity() {

    private lateinit var webView: WebView
    private lateinit var setupView: ScrollView
    private lateinit var startingView: TextView
    private var fileCallback: ValueCallback<Array<Uri>>? = null
    private var updateOffered = false
    private var soldPricesOverlay: View? = null
    private var soldPricesWebView: WebView? = null
    private var usePriceButton: Button? = null
    private var pendingPrice: Double? = null

    private val requiredKeys = listOf(
        "GEMINI_API_KEY",
        "EBAY_CLIENT_ID",
        "EBAY_CLIENT_SECRET",
        "EBAY_RUNAME",
        "EBAY_FULFILLMENT_POLICY_ID",
        "EBAY_PAYMENT_POLICY_ID",
        "EBAY_RETURN_POLICY_ID"
    )

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        webView = findViewById(R.id.webview)
        setupView = findViewById(R.id.setup_view)
        startingView = findViewById(R.id.starting_view)

        if (hasKeys()) {
            startServer()
        } else {
            setupView.visibility = View.VISIBLE
            findViewById<Button>(R.id.btn_save).setOnClickListener { saveKeys() }
        }
    }

    /** True when a .env with all 7 real keys is saved in private storage. */
    private fun hasKeys(): Boolean {
        val f = File(filesDir, ".env")
        if (!f.exists()) return false
        val text = f.readText()
        return requiredKeys.all { key ->
            val m = Regex("^$key=(.+)$", RegexOption.MULTILINE).find(text)
            val v = m?.groupValues?.get(1)?.trim()?.trim('"', '\'') ?: ""
            v.isNotEmpty() && !v.startsWith("PASTE_YOUR")
        }
    }

    private fun saveKeys() {
        val values = mapOf(
            "GEMINI_API_KEY" to findViewById<EditText>(R.id.et_gemini).text.toString().trim(),
            "EBAY_CLIENT_ID" to findViewById<EditText>(R.id.et_client_id).text.toString().trim(),
            "EBAY_CLIENT_SECRET" to findViewById<EditText>(R.id.et_client_secret).text.toString().trim(),
            "EBAY_RUNAME" to findViewById<EditText>(R.id.et_runame).text.toString().trim(),
            "EBAY_FULFILLMENT_POLICY_ID" to findViewById<EditText>(R.id.et_fulfillment).text.toString().trim(),
            "EBAY_PAYMENT_POLICY_ID" to findViewById<EditText>(R.id.et_payment).text.toString().trim(),
            "EBAY_RETURN_POLICY_ID" to findViewById<EditText>(R.id.et_return).text.toString().trim()
        )
        val missing = values.filter { it.value.isEmpty() }.keys
        if (missing.isNotEmpty()) {
            Toast.makeText(this, "Please fill in every field.", Toast.LENGTH_LONG).show()
            return
        }
        val env = buildString {
            appendLine("# Listing Assistant keys. Saved on this phone only.")
            for ((k, v) in values) appendLine("$k=$v")
        }
        File(filesDir, ".env").writeText(env)
        Toast.makeText(this, "Keys saved.", Toast.LENGTH_SHORT).show()
        startServer()
    }

    private fun startServer() {
        setupView.visibility = View.GONE
        startingView.visibility = View.VISIBLE
        setupWebView()

        Thread {
            try {
                if (!Python.isStarted()) {
                    Python.start(AndroidPlatform(this@MainActivity))
                }
                // Flask's app.run() blocks, so it gets its own daemon thread.
                val serverThread = Thread {
                    // v6: pass the real versionName so the header badge in the
                    // web UI is correct automatically on every release. (The
                    // old hardcoded "apk-v1" made his phone show v1 long after
                    // v5 was installed, so he thought the update didn't take.)
                    val versionName = try {
                        val pi = if (android.os.Build.VERSION.SDK_INT >= 33)
                            packageManager.getPackageInfo(
                                packageName,
                                android.content.pm.PackageManager.PackageInfoFlags.of(0))
                        else
                            @Suppress("DEPRECATION")
                            packageManager.getPackageInfo(packageName, 0)
                        pi.versionName ?: ""
                    } catch (_: Exception) { "" }
                    Python.getInstance().getModule("apprunner")
                        .callAttr("start", filesDir.absolutePath, versionName)
                }
                serverThread.isDaemon = true
                serverThread.start()
            } catch (e: Exception) {
                runOnUiThread {
                    startingView.text = "Could not start: ${e.message}"
                }
                return@Thread
            }

            // Wait until Flask is actually listening, then show the app.
            repeat(120) {
                try {
                    Socket("127.0.0.1", 5050).close()
                    runOnUiThread {
                        startingView.visibility = View.GONE
                        webView.visibility = View.VISIBLE
                        webView.loadUrl("http://127.0.0.1:5050/")
                        // Self-update check: silent unless a newer build exists.
                        Updater.check(this@MainActivity) { info -> showUpdateDialog(info) }
                    }
                    return@Thread
                } catch (_: Exception) {
                    Thread.sleep(500)
                }
            }
            runOnUiThread {
                startingView.text = "The app did not start. Close and reopen it."
            }
        }.start()
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun setupWebView() {
        webView.settings.javaScriptEnabled = true
        webView.settings.domStorageEnabled = true
        webView.settings.allowFileAccess = true
        webView.settings.mediaPlaybackRequiresUserGesture = false
        webView.webViewClient = object : WebViewClient() {
            // Safety net: if a sold-search URL ever tries to load in the main
            // WebView (instead of a new window), catch it into the pop-up so
            // the listing draft is never lost. Everything else loads normally.
            override fun shouldOverrideUrlLoading(
                view: WebView,
                request: WebResourceRequest
            ): Boolean {
                if (request.isForMainFrame &&
                    SoldUrlMatcher.isSoldSearchUrl(request.url.toString())
                ) {
                    openSoldPricesPopupWithUrl(request.url.toString())
                    return true
                }
                return false
            }
        }
        webView.webChromeClient = object : WebChromeClient() {
            // The web app's only window.open() is the "Check sold prices"
            // button (the "View your listing" links use target=_blank too).
            // Show those in an in-app pop-up with a Back button instead of
            // navigating the main WebView away from the draft.
            override fun onCreateWindow(
                view: WebView,
                isDialog: Boolean,
                isUserGesture: Boolean,
                resultMsg: Message
            ): Boolean {
                openSoldPricesPopup(resultMsg)
                return true
            }
            // Photo picking is the app's core input: without this, the
            // <input type="file"> button does nothing in a WebView.
            override fun onShowFileChooser(
                view: WebView,
                callback: ValueCallback<Array<Uri>>,
                params: FileChooserParams
            ): Boolean {
                fileCallback?.onReceiveValue(null)
                fileCallback = callback
                val intent = Intent(Intent.ACTION_GET_CONTENT).apply {
                    addCategory(Intent.CATEGORY_OPENABLE)
                    type = "image/*"
                    putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true)
                }
                @Suppress("DEPRECATION")
                startActivityForResult(
                    Intent.createChooser(intent, "Choose photos"),
                    FILE_CHOOSER_REQUEST
                )
                return true
            }
        }
    }

    /**
     * Shows the eBay page from a window.open() call in a pop-up overlay.
     * The main WebView (and the listing draft in it) is never touched.
     * CookieManager is app-wide, so his eBay website login carries over
     * after the first login.
     */
    private fun openSoldPricesPopup(resultMsg: Message) {
        val overlay = ensureSoldPricesOverlay() ?: return
        val popupWebView = soldPricesWebView ?: return
        resetUsePriceButton()
        val transport = resultMsg.obj as WebView.WebViewTransport
        transport.webView = popupWebView
        resultMsg.sendToTarget()
        overlay.visibility = View.VISIBLE
    }

    /** Same pop-up, but for a known URL (used by the safety net above). */
    private fun openSoldPricesPopupWithUrl(url: String) {
        val overlay = ensureSoldPricesOverlay() ?: return
        val popupWebView = soldPricesWebView ?: return
        resetUsePriceButton()
        overlay.visibility = View.VISIBLE
        popupWebView.loadUrl(url)
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun ensureSoldPricesOverlay(): View? {
        soldPricesOverlay?.let { return it }
        val root = findViewById<ViewGroup>(android.R.id.content)
        val overlay = layoutInflater.inflate(R.layout.popup_sold_prices, root, false)
        val popupWebView = overlay.findViewById<WebView>(R.id.popup_webview)
        popupWebView.settings.javaScriptEnabled = true
        popupWebView.settings.domStorageEnabled = true
        // v5: watch for eBay item pages so we can offer "Use this price".
        popupWebView.webViewClient = object : WebViewClient() {
            override fun onPageFinished(view: WebView, url: String) {
                maybeOfferPrice(url)
            }
        }
        overlay.findViewById<Button>(R.id.btn_back_to_listing).setOnClickListener {
            closeSoldPricesPopup()
        }
        val usePrice = overlay.findViewById<Button>(R.id.btn_use_price)
        usePrice.setOnClickListener { onUsePriceTapped() }
        usePriceButton = usePrice
        root.addView(overlay)
        overlay.visibility = View.GONE
        soldPricesOverlay = overlay
        soldPricesWebView = popupWebView
        return overlay
    }

    private fun resetUsePriceButton() {
        pendingPrice = null
        usePriceButton?.visibility = View.GONE
    }

    /**
     * v5: when an eBay item page finishes loading in the pop-up, try to read
     * its price off the page. The button appears only if a real price was
     * parsed — never a guess.
     */
    private fun maybeOfferPrice(url: String) {
        val btn = usePriceButton ?: return
        if (!SoldUrlMatcher.isEbayItemPage(url)) {
            runOnUiThread { resetUsePriceButton() }
            return
        }
        soldPricesWebView?.evaluateJavascript(PRICE_EXTRACT_JS) { result ->
            val price = PriceParser.parsePrice(result)
            runOnUiThread {
                if (price != null) {
                    pendingPrice = price
                    btn.text = "Use this price: $${PriceParser.formatForDraft(price)}"
                    btn.visibility = View.VISIBLE
                } else {
                    resetUsePriceButton()
                }
            }
        }
    }

    /** v5: push the pop-up's price into the current draft, then go back. */
    private fun onUsePriceTapped() {
        val price = pendingPrice ?: return
        // The draft ID lives in the web app's JS; the pop-up never touched
        // the main WebView, so it is still on the draft page.
        webView.evaluateJavascript("window.LA_getDraftId()") { result ->
            val draftId = result?.trim()?.removeSurrounding("\"") ?: ""
            if (draftId.isEmpty()) {
                Toast.makeText(
                    this,
                    "Open the draft first, then check sold prices.",
                    Toast.LENGTH_LONG
                ).show()
                return@evaluateJavascript
            }
            pushPriceToDraft(draftId, price)
        }
    }

    private fun pushPriceToDraft(draftId: String, price: Double) {
        val priceText = PriceParser.formatForDraft(price)
        Thread {
            val ok = try {
                val conn = URL("http://127.0.0.1:5050/api/drafts/$draftId")
                    .openConnection() as HttpURLConnection
                conn.requestMethod = "PUT"
                conn.doOutput = true
                conn.connectTimeout = 15000
                conn.readTimeout = 15000
                conn.setRequestProperty("Content-Type", "application/json")
                val body = "{\"price\":\"$priceText\"}"
                conn.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
                val code = conn.responseCode
                conn.disconnect()
                code in 200..299
            } catch (_: Exception) {
                false
            }
            runOnUiThread {
                if (ok) {
                    // Re-read the draft from the server so the price field
                    // shows the new value (server is the source of truth;
                    // autosave kept every other field current).
                    webView.evaluateJavascript(
                        "window.LA_refreshDraft && window.LA_refreshDraft()",
                        null
                    )
                    closeSoldPricesPopup()
                    Toast.makeText(
                        this,
                        "Price set to $$priceText",
                        Toast.LENGTH_LONG
                    ).show()
                } else {
                    Toast.makeText(
                        this,
                        "Couldn't save the price. Type it in the draft instead.",
                        Toast.LENGTH_LONG
                    ).show()
                }
            }
        }.start()
    }

    private fun closeSoldPricesPopup() {
        soldPricesWebView?.apply {
            stopLoading()
            loadUrl("about:blank")
            onPause()
            destroy()
        }
        soldPricesWebView = null
        (soldPricesOverlay?.parent as? ViewGroup)?.removeView(soldPricesOverlay)
        soldPricesOverlay = null
    }

    private fun isSoldPricesPopupOpen(): Boolean = soldPricesOverlay != null

    @Deprecated("Kept simple on purpose: no AndroidX needed.")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == FILE_CHOOSER_REQUEST) {
            val uris = mutableListOf<Uri>()
            if (resultCode == Activity.RESULT_OK && data != null) {
                val clip = data.clipData
                if (clip != null) {
                    for (i in 0 until clip.itemCount) uris += clip.getItemAt(i).uri
                } else {
                    data.data?.let { uris += it }
                }
            }
            fileCallback?.onReceiveValue(uris.toTypedArray())
            fileCallback = null
        }
    }

    override fun onBackPressed() {
        if (isSoldPricesPopupOpen()) {
            closeSoldPricesPopup()
            return
        }
        if (webView.visibility == View.VISIBLE && webView.canGoBack()) {
            webView.goBack()
        } else {
            super.onBackPressed()
        }
    }

    override fun onDestroy() {
        closeSoldPricesPopup()
        super.onDestroy()
    }

    /** One-tap update offer. "Later" dismisses it until the next app start. */
    private fun showUpdateDialog(info: UpdateInfo) {
        if (updateOffered) return
        updateOffered = true
        val notes = if (info.notes.isNotEmpty()) "\n\n${info.notes}" else ""
        AlertDialog.Builder(this)
            .setTitle("A new version is ready")
            .setMessage("Version ${info.versionName.ifEmpty { "#${info.versionCode}" }} " +
                "is available.$notes")
            .setPositiveButton("Update") { _, _ -> Updater.downloadAndInstall(this, info) }
            .setNegativeButton("Later", null)
            .show()
    }

    companion object {
        private const val FILE_CHOOSER_REQUEST = 1001

        /**
         * v5: reads the item price off an eBay item page. Multiple fallback
         * selectors because eBay changes its markup; returns "" when none
         * hit. Prefers the machine-readable itemprop="price" content
         * attribute, then the visible primary price text.
         */
        private const val PRICE_EXTRACT_JS = """
            (function(){
              function txt(sel){
                try {
                  var el = document.querySelector(sel);
                  if(!el) return '';
                  var c = el.getAttribute('content');
                  if(c) return c.trim();
                  return (el.innerText||'').trim();
                } catch(e){ return ''; }
              }
              var sels = [
                'span[itemprop="price"]',
                '.x-price-primary .ux-textspans',
                '.x-price-primary',
                '#prcIsum',
                '.display-price'
              ];
              for(var i=0;i<sels.length;i++){
                var v = txt(sels[i]);
                if(v) return v;
              }
              return '';
            })()
        """
    }
}
