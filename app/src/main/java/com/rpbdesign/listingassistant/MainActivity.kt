package com.rpbdesign.listingassistant

import android.annotation.SuppressLint
import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.os.Message
import android.util.Base64
import android.view.View
import android.view.ViewGroup
import android.webkit.JavascriptInterface
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
import android.widget.Toolbar
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
    private var pendingCameraPhoto: File? = null
    private var pendingCameraPhotoUri: Uri? = null
    private var updateDialog: AlertDialog? = null
    private var soldPricesOverlay: View? = null
    private var soldPricesWebView: WebView? = null
    private var usePriceButton: Button? = null
    private var pendingPrice: Double? = null
    /** v19: true while the keys form is open for editing saved keys (as
     *  opposed to first run). Save then restarts the app so Python re-reads
     *  the .env; Cancel just closes the form back to the running app. */
    private var editingKeys = false

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

        // v20: native overflow menu (three dots, top right). Both items are
        // showAsAction="never", so the Toolbar renders the overflow button
        // itself — no wide buttons to get cut off. Each item calls the exact
        // same handler the old web header button used.
        findViewById<Toolbar>(R.id.toolbar).apply {
            inflateMenu(R.menu.main_menu)
            setOnMenuItemClickListener { item ->
                when (item.itemId) {
                    R.id.menu_check_updates -> {
                        runUpdateCheck()
                        true
                    }
                    R.id.menu_edit_keys -> {
                        showEditKeysForm()
                        true
                    }
                    else -> false
                }
            }
        }

        if (hasKeys()) {
            startServer()
        } else {
            setupView.visibility = View.VISIBLE
        }
        // Wired unconditionally: the form is first-run setup when no keys
        // exist, and the v19 Edit keys screen once the app is running.
        findViewById<Button>(R.id.btn_save).setOnClickListener { saveKeys() }
        findViewById<Button>(R.id.btn_cancel).setOnClickListener { cancelEditKeys() }
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
        if (editingKeys) {
            // v19: Python reads .env once at start, so edited keys only
            // take effect in a fresh process. Restart the app; on relaunch
            // hasKeys() is true and the server starts with the new keys.
            restartApp()
        } else {
            startServer()
        }
    }

    /** Reads the saved .env into a map (same quote-stripping as Python's
     *  _load_env, so pre-filled fields show exactly what will be used). */
    private fun readEnvValues(): Map<String, String> {
        val f = File(filesDir, ".env")
        if (!f.exists()) return emptyMap()
        val out = mutableMapOf<String, String>()
        for (raw in f.readText().lines()) {
            val line = raw.trim()
            if (line.isEmpty() || line.startsWith("#") || "=" !in line) continue
            val (k, v) = line.split("=", limit = 2)
            var value = v.trim()
            if (value.length >= 2 && value.first() == value.last() &&
                (value.first() == '"' || value.first() == '\'')
            ) value = value.substring(1, value.length - 1)
            if (k.trim().isNotEmpty()) out[k.trim()] = value
        }
        return out
    }

    /**
     * v19: opens the keys form pre-filled with the saved values, so he can
     * change just one key (e.g. the RuName) without retyping the rest or
     * clearing app data. v20: called from the native overflow menu item
     * (the old web header button is gone); the JS bridge below also calls
     * it. The bridge runs on a background thread, the menu on the UI thread.
     */
    private fun showEditKeysForm() {
        val values = readEnvValues()
        findViewById<EditText>(R.id.et_gemini).setText(values["GEMINI_API_KEY"] ?: "")
        findViewById<EditText>(R.id.et_client_id).setText(values["EBAY_CLIENT_ID"] ?: "")
        findViewById<EditText>(R.id.et_client_secret).setText(values["EBAY_CLIENT_SECRET"] ?: "")
        findViewById<EditText>(R.id.et_runame).setText(values["EBAY_RUNAME"] ?: "")
        findViewById<EditText>(R.id.et_fulfillment).setText(values["EBAY_FULFILLMENT_POLICY_ID"] ?: "")
        findViewById<EditText>(R.id.et_payment).setText(values["EBAY_PAYMENT_POLICY_ID"] ?: "")
        findViewById<EditText>(R.id.et_return).setText(values["EBAY_RETURN_POLICY_ID"] ?: "")
        findViewById<TextView>(R.id.setup_title).text = "Edit your keys"
        findViewById<TextView>(R.id.setup_subtitle).text =
            "Change only what you need. Keys stay on this phone."
        findViewById<Button>(R.id.btn_save).text = "Save keys"
        findViewById<Button>(R.id.btn_cancel).visibility = View.VISIBLE
        editingKeys = true
        webView.visibility = View.GONE
        setupView.visibility = View.VISIBLE
    }

    /** v19: closes the edit form without saving, back to the running app. */
    private fun cancelEditKeys() {
        editingKeys = false
        setupView.visibility = View.GONE
        webView.visibility = View.VISIBLE
    }

    /** Relaunches the launcher activity in a fresh task and exits this
     *  process, so the Python server restarts and re-reads the .env. */
    private fun restartApp() {
        val intent = packageManager.getLaunchIntentForPackage(packageName)
        if (intent != null) {
            startActivity(Intent.makeRestartActivityTask(intent.component))
        } else {
            finish()
        }
        Runtime.getRuntime().exit(0)
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
                        // Self-update check: offers when a newer build exists;
                        // a failed check shows a toast (never silent again).
                        Updater.check(
                            this@MainActivity,
                            { info -> showUpdateDialog(info) },
                            onError = { showUpdateCheckFailedToast() }
                        )
                        // If a previous update download already finished (e.g.
                        // the app was force-stopped), install it now.
                        Updater.resumePendingInstall(this@MainActivity)
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
        // v14: the Take photo button calls Android.openCamera() directly.
        // @JavascriptInterface methods run on a background thread, so the
        // bridge posts to the UI thread itself.
        webView.addJavascriptInterface(CameraJsBridge(), "Android")
        webView.webViewClient = object : WebViewClient() {
            // Safety net: if a sold-search URL ever tries to load in the main
            // WebView (instead of a new window), catch it into the pop-up so
            // the listing draft is never lost. Everything else loads normally.
            override fun shouldOverrideUrlLoading(
                view: WebView,
                request: WebResourceRequest
            ): Boolean {
                if (request.isForMainFrame) {
                    // v19: eBay signs him in, then sends the browser back to
                    // the RuName's callback URL — which eBay's portal forces
                    // to https. Flask is HTTP-only, so an https navigation
                    // would die in a TLS handshake before Flask ever sees
                    // the code. Rewrite it to http first (path and query,
                    // including code and state, are preserved) so the
                    // callback reaches the app. Only localhost:5050 is ever
                    // rewritten; eBay and every other host load untouched.
                    val rewritten = LocalhostRewrite.rewriteHttpsLocalhostToHttp(
                        request.url.toString()
                    )
                    if (rewritten != null) {
                        view.loadUrl(rewritten)
                        return true
                    }
                    if (SoldUrlMatcher.isSoldSearchUrl(request.url.toString())) {
                        openSoldPricesPopupWithUrl(request.url.toString())
                        return true
                    }
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
                // v12: the Take photo button's <input> sets capture="camera".
                // Go straight to the camera app — his phone opens the Photos
                // picker directly and never shows the system chooser, so the
                // v10 EXTRA_INITIAL_INTENTS camera option never appears for
                // him. Depending on the chooser is unreliable; the button is
                // the primary path now.
                val camIntent = buildCameraIntent()
                val cameraAvailable = camIntent != null
                if (CameraCapture.useCameraDirect(params.isCaptureEnabled, cameraAvailable)) {
                    @Suppress("DEPRECATION")
                    startActivityForResult(camIntent!!, FILE_CHOOSER_REQUEST)
                    return true
                }
                // Gallery/files picker (or capture with no camera app: fall
                // back to the gallery rather than stranding him). The camera
                // stays in EXTRA_INITIAL_INTENTS as a bonus on phones that
                // do show the chooser dialog.
                val galleryIntent = Intent(Intent.ACTION_GET_CONTENT).apply {
                    addCategory(Intent.CATEGORY_OPENABLE)
                    type = "image/*"
                    putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true)
                }
                val chooser = Intent.createChooser(galleryIntent, "Choose photos")
                if (camIntent != null) {
                    chooser.putExtra(Intent.EXTRA_INITIAL_INTENTS, arrayOf(camIntent))
                }
                @Suppress("DEPRECATION")
                startActivityForResult(chooser, FILE_CHOOSER_REQUEST)
                return true
            }
        }
    }

    /**
     * v14: JS bridge for the Take photo button. His phone's WebView ignores
     * the <input capture="camera"> attribute (isCaptureEnabled() is false),
     * so v12's button opened the gallery. Calling the camera directly
     * bypasses the WebView entirely — no attribute to ignore.
     */
    inner class CameraJsBridge {
        @JavascriptInterface
        fun openCamera() {
            runOnUiThread { launchCameraForJs() }
        }

        /** v15: JS bridge entry for the update check, kept so any page
         *  still calling it works. Delegates to the shared MainActivity
         *  handler that the v20 native menu item calls too. */
        @JavascriptInterface
        fun checkForUpdates() {
            runUpdateCheck()
        }

        /** v19: shows the native keys form pre-filled with the saved
         *  values. v20: kept for compatibility; the native overflow menu
         *  calls showEditKeysForm() directly. This runs on the JS-interface
         *  thread, so it posts to the UI thread itself. */
        @JavascriptInterface
        fun editKeys() {
            runOnUiThread { showEditKeysForm() }
        }
    }

    /**
     * v15: manual update check. Always answers: dialog when an update
     * exists, toast when current, toast when the check itself fails.
     * v20: the single handler shared by the native overflow menu item
     * and the JS bridge above — no duplicated logic. Updater.check moves
     * all callbacks to the UI thread, so it is safe from either thread.
     */
    private fun runUpdateCheck() {
        Updater.check(
            this@MainActivity,
            { info -> showUpdateDialog(info) },
            onUpToDate = {
                Toast.makeText(
                    this@MainActivity,
                    "You're on the latest version.",
                    Toast.LENGTH_LONG
                ).show()
            },
            onError = {
                Toast.makeText(
                    this@MainActivity,
                    "Could not check for updates. Check your connection and try again.",
                    Toast.LENGTH_LONG
                ).show()
            }
        )
    }

    /** Launches the camera app for the JS-interface path (no chooser). */
    private fun launchCameraForJs() {
        val camIntent = buildCameraIntent()
        if (camIntent == null) {
            Toast.makeText(this, "No camera app found.", Toast.LENGTH_LONG).show()
            return
        }
        @Suppress("DEPRECATION")
        startActivityForResult(camIntent, CAMERA_JS_REQUEST)
    }

    /**
     * Handles the JS-interface camera result: downscales natively to 1600px,
     * base64-encodes, and hands the data URL to JS. On cancel, just cleans up.
     * Runs on the UI thread (onActivityResult), so evaluateJavascript is safe.
     */
    private fun handleCameraJsResult(resultCode: Int, data: Intent?) {
        val photo = pendingCameraPhoto
        val photoUri = pendingCameraPhotoUri
        try {
            if (resultCode == Activity.RESULT_OK && photo != null) {
                // Same thumbnail fallback as the file-chooser path: some
                // camera apps ignore EXTRA_OUTPUT and return only a bitmap.
                if (photo.length() == 0L) {
                    val thumb = data?.extras?.get("data") as? android.graphics.Bitmap
                    if (thumb != null) {
                        try {
                            java.io.FileOutputStream(photo).use {
                                thumb.compress(android.graphics.Bitmap.CompressFormat.JPEG, 90, it)
                            }
                        } catch (_: Exception) { /* fall through: no usable photo */ }
                    }
                }
                if (photo.length() > 0L) {
                    val jpeg = CameraDownscale.downscale(photo)
                    if (jpeg != null) {
                        val b64 = Base64.encodeToString(jpeg, Base64.NO_WRAP)
                        // Base64.NO_WRAP alphabet is JS-string-safe (unit-tested);
                        // no escaping needed inside the single quotes.
                        webView.evaluateJavascript(
                            "onCameraPhoto('data:image/jpeg;base64,$b64')", null
                        )
                    } else {
                        Toast.makeText(this, "Couldn't read that photo.", Toast.LENGTH_LONG).show()
                    }
                }
            }
            // Cancel: do nothing (temp file is deleted in finally).
        } finally {
            photoUri?.let {
                revokeUriPermission(
                    it,
                    Intent.FLAG_GRANT_WRITE_URI_PERMISSION or Intent.FLAG_GRANT_READ_URI_PERMISSION
                )
            }
            // Unlike the file-chooser path (which hands the URI to the WebView
            // for async reading), here the bytes are already inlined above.
            photo?.delete()
            pendingCameraPhoto = null
            pendingCameraPhotoUri = null
        }
    }

    /**
     * Builds the camera capture intent writing to our app-private temp
     * photo (served through CameraPhotoProvider). No CAMERA permission
     * needed — the camera app does the capturing; we only hand it a place
     * to write. Returns null (after cleaning up) when no camera app exists.
     */
    private fun buildCameraIntent(): Intent? {
        val dir = CameraPhotoProvider.cameraDir(this)
        // Tidy up: temp photos older than a day are never coming back.
        val cutoff = System.currentTimeMillis() - 24L * 60 * 60 * 1000
        dir.listFiles()?.forEach { if (it.lastModified() < cutoff) it.delete() }
        pendingCameraPhoto =
            File(dir, CameraCapture.tempName(System.currentTimeMillis())).apply {
                createNewFile()
            }
        pendingCameraPhotoUri =
            CameraPhotoProvider.uriFor(this, pendingCameraPhoto!!)
        val camIntent = Intent(android.provider.MediaStore.ACTION_IMAGE_CAPTURE).apply {
            putExtra(android.provider.MediaStore.EXTRA_OUTPUT, pendingCameraPhotoUri)
            addFlags(Intent.FLAG_GRANT_WRITE_URI_PERMISSION or Intent.FLAG_GRANT_READ_URI_PERMISSION)
        }
        if (camIntent.resolveActivity(packageManager) == null) {
            pendingCameraPhoto?.delete()
            pendingCameraPhoto = null
            pendingCameraPhotoUri = null
            return null
        }
        // Explicit per-package grants: the reliable pre-Lollipop
        // pattern, still the safest on modern Android too.
        packageManager
            .queryIntentActivities(camIntent, android.content.pm.PackageManager.MATCH_DEFAULT_ONLY)
            .forEach {
                grantUriPermission(
                    it.activityInfo.packageName, pendingCameraPhotoUri,
                    Intent.FLAG_GRANT_WRITE_URI_PERMISSION or Intent.FLAG_GRANT_READ_URI_PERMISSION
                )
            }
        return camIntent
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
        // v14: Take photo button -> Android.openCamera() (JS interface).
        if (requestCode == CAMERA_JS_REQUEST) {
            handleCameraJsResult(resultCode, data)
            return
        }
        if (requestCode == FILE_CHOOSER_REQUEST) {
            val galleryUris = mutableListOf<Uri>()
            if (resultCode == Activity.RESULT_OK && data != null) {
                val clip = data.clipData
                if (clip != null) {
                    for (i in 0 until clip.itemCount) galleryUris += clip.getItemAt(i).uri
                } else {
                    data.data?.let { galleryUris += it }
                }
            }
            val photo = pendingCameraPhoto
            val photoUri = pendingCameraPhotoUri
            // Some camera apps ignore EXTRA_OUTPUT and return only a thumbnail
            // bitmap: save it into our temp file so the pipeline still works.
            if (galleryUris.isEmpty() && resultCode == Activity.RESULT_OK && photo != null &&
                photo.length() == 0L
            ) {
                val thumb = data?.extras?.get("data") as? android.graphics.Bitmap
                if (thumb != null) {
                    try {
                        java.io.FileOutputStream(photo).use {
                            thumb.compress(android.graphics.Bitmap.CompressFormat.JPEG, 90, it)
                        }
                    } catch (_: Exception) { /* fall through: no usable photo */ }
                }
            }
            val uris = CameraCapture.resolveUris(
                galleryUris.map { it.toString() },
                photoUri?.toString(),
                photo?.length() ?: 0L
            ).map { android.net.Uri.parse(it) }
            // Cleanup: revoke the camera app's grants; delete the temp photo
            // unless we just handed it to the WebView (it reads async).
            photoUri?.let {
                revokeUriPermission(
                    it,
                    Intent.FLAG_GRANT_WRITE_URI_PERMISSION or Intent.FLAG_GRANT_READ_URI_PERMISSION
                )
            }
            if (!CameraCapture.wasUsed(uris.map { it.toString() }, photoUri?.toString())) photo?.delete()
            pendingCameraPhoto = null
            pendingCameraPhotoUri = null
            fileCallback?.onReceiveValue(uris.toTypedArray())
            fileCallback = null
        }
    }

    override fun onBackPressed() {
        // v19: Back from the Edit keys form just closes it (no save), back
        // to the running app. First-run setup keeps the old behavior: Back
        // exits the app, since there is no app to go back to yet.
        if (setupView.visibility == View.VISIBLE && editingKeys) {
            cancelEditKeys()
            return
        }
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

    /** One-tap update offer. "Later" only dismisses this dialog — the next
     *  check offers again (v15: no per-version suppression, so tapping
     *  "Later" can never strand the user on an old build). The only guard
     *  is against stacking a second dialog while one is showing. */
    private fun showUpdateDialog(info: UpdateInfo) {
        if (!Updater.shouldShowUpdateDialog(updateDialog?.isShowing == true)) return
        val notes = if (info.notes.isNotEmpty()) "\n\n${info.notes}" else ""
        updateDialog = AlertDialog.Builder(this)
            .setTitle("A new version is ready")
            .setMessage("Version ${info.versionName.ifEmpty { "#${info.versionCode}" }} " +
                "is available.$notes")
            .setPositiveButton("Update") { _, _ -> Updater.downloadAndInstall(this, info) }
            .setNegativeButton("Later", null)
            .setOnDismissListener { updateDialog = null }
            .show()
    }

    override fun onResume() {
        super.onResume()
        // Re-check for updates every time the app comes to the foreground.
        // v15: "Later" no longer suppresses anything, so every check that
        // finds a newer build offers it again.
        Updater.check(
            this,
            { info -> showUpdateDialog(info) },
            onError = { showUpdateCheckFailedToast() }
        )
    }

    /** Toast only, never a dialog: a failed automatic check must not
     *  interrupt his work, but must not be silent either (v13 silence hid
     *  failed checks entirely). One toast per failed check. */
    private fun showUpdateCheckFailedToast() {
        Toast.makeText(
            this,
            "Update check failed — tap the menu (top right), then Check for updates to retry.",
            Toast.LENGTH_LONG
        ).show()
    }

    companion object {
        private const val FILE_CHOOSER_REQUEST = 1001
        /** v14: Take photo button -> Android.openCamera() (JS interface), bypassing
         *  the WebView file chooser entirely — his phone's WebView ignores the
         *  capture attribute, so isCaptureEnabled() is false and v12 fell back
         *  to the gallery. */
        private const val CAMERA_JS_REQUEST = 1002

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
