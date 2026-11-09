package com.rpbdesign.listingassistant

import android.annotation.SuppressLint
import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
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
import androidx.camera.view.PreviewView
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.LifecycleRegistry
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
class MainActivity : Activity(), LifecycleOwner {

    private lateinit var webView: WebView
    private lateinit var setupView: ScrollView
    private lateinit var startingView: TextView
    private var fileCallback: ValueCallback<Array<Uri>>? = null
    private var pendingCameraPhoto: File? = null
    private var pendingCameraPhotoUri: Uri? = null
    /** v44: the in-app camera is CameraX, which binds to a LifecycleOwner.
     *  This Activity is a plain Activity (no AppCompat), so it carries
     *  its own LifecycleRegistry and forwards the events itself. */
    private val lifecycleRegistry = LifecycleRegistry(this)
    override val lifecycle: Lifecycle
        get() = lifecycleRegistry
    /** v44: in-app camera state. Null when the camera is closed; the
     *  overlay (R.id.camera_view) mirrors cameraOpen. */
    private var inAppCamera: InAppCamera? = null
    private var cameraOpen = false
    private var cameraPhotosTaken = 0
    /** v36: Pixel-editor handoff state. Set when Edit on a listing photo
     *  launches the phone's editor; cleared when its result returns.
     *  v37: the editor opens a MediaStore working copy (pendingEditMediaUri),
     *  not the app's private provider file — v36's private URI resolved
     *  to nothing on his Pixel. */
    private var pendingEditDraftId: String? = null
    private var pendingEditIndex: Int = -1
    private var pendingEditMediaUri: Uri? = null
    private var pendingEditOut: File? = null
    private var pendingEditOutUri: Uri? = null
    private var pendingEditOrig: ByteArray? = null
    private var updateDialog: AlertDialog? = null
    private var soldPricesOverlay: View? = null
    private var soldPricesWebView: WebView? = null
    private var usePriceButton: Button? = null
    private var pendingPrice: Double? = null
    /** v19: true while the keys form is open for editing saved keys (as
     *  opposed to first run). Save then restarts the app so Python re-reads
     *  the .env; Cancel just closes the form back to the running app. */
    private var editingKeys = false

    /** v22: marker file requesting the eBay connect flow right after a
     *  restart (set by the "Use the working name" button, consumed on
     *  relaunch once the server is confirmed listening). */
    private fun connectAfterRestartFile(): File =
        File(filesDir, ".connect_after_restart")

    /**
     * v22: zero-typing fix, part 1. If the saved RuName is exactly the
     * known-bad, never-registered value, replace it with the working
     * RuName in the .env file (all other lines untouched) BEFORE Python
     * starts, so the server reads the good value on this very launch.
     * Returns true when a fix happened, so the main screen can show the
     * note. Any other stored value is left alone here — a wrong value
     * that is not the known-bad one gets the one-tap button instead
     * (see fixRunameAndConnect). A failed write returns false and never
     * crashes the launch; the 400 then remains visible, not hidden.
     */
    private fun maybeAutoFixRuname(): Boolean {
        val f = File(filesDir, ".env")
        if (!f.exists()) return false
        if (!RunameFix.needsAutoFix(readEnvValues()["EBAY_RUNAME"])) return false
        return try {
            f.writeText(RunameFix.rewriteEnvText(f.readText(), RunameFix.WORKING_RUNAME))
            true
        } catch (_: Exception) {
            false
        }
    }

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
        // v44: CameraX binds to this Activity's lifecycle.
        lifecycleRegistry.handleLifecycleEvent(Lifecycle.Event.ON_CREATE)
        setContentView(R.layout.activity_main)
        webView = findViewById(R.id.webview)
        setupView = findViewById(R.id.setup_view)
        startingView = findViewById(R.id.starting_view)
        // v44: the in-app camera's shutter and Done buttons live in the
        // overlay; wired once here, the overlay itself shows/hides.
        findViewById<Button>(R.id.camera_shutter).setOnClickListener { takeInAppPhoto() }
        findViewById<Button>(R.id.camera_done).setOnClickListener { closeInAppCamera() }

        // v20: native overflow menu (three dots, top right). Both items are
        // showAsAction="never", so the Toolbar renders the overflow button
        // itself — no wide buttons to get cut off. Each item calls the exact
        // same handler the old web header button used.
        // v21: the light activity theme tints the overflow icon dark, which
        // is invisible on the dark toolbar (he could not find the menu).
        // Force it white so the three dots always stand out. The popup menu
        // itself stays light-background with dark text (good contrast).
        findViewById<Toolbar>(R.id.toolbar).apply {
            inflateMenu(R.menu.main_menu)
            overflowIcon?.let { icon ->
                overflowIcon = icon.mutate().apply { setTint(Color.WHITE) }
            }
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

    /** v44: forward the Activity events to the LifecycleRegistry so
     *  CameraX can bind to this Activity. */
    override fun onStart() {
        super.onStart()
        lifecycleRegistry.handleLifecycleEvent(Lifecycle.Event.ON_START)
    }

    override fun onPause() {
        lifecycleRegistry.handleLifecycleEvent(Lifecycle.Event.ON_PAUSE)
        super.onPause()
    }

    override fun onStop() {
        lifecycleRegistry.handleLifecycleEvent(Lifecycle.Event.ON_STOP)
        super.onStop()
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
        // v22: auto-fix the known-bad RuName before Python reads .env,
        // so a launch that would have 400'd now starts with the working
        // name. The flag is captured for the first page load below.
        val runameFixed = maybeAutoFixRuname()
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
                        // v22: after the "Use the working name" button's
                        // restart, go straight into the eBay connect flow
                        // instead of the main screen. The marker is only
                        // consumed once the server is confirmed listening,
                        // so a failed start never eats it. After an
                        // auto-fix launch, the main screen gets the note
                        // flag instead (?runame_fixed=1).
                        val marker = connectAfterRestartFile()
                        val connectAfterRestart = if (marker.exists()) {
                            marker.delete()
                            true
                        } else {
                            false
                        }
                        val initialUrl = when {
                            connectAfterRestart ->
                                "http://127.0.0.1:5050/api/ebay/connect"
                            runameFixed ->
                                "http://127.0.0.1:5050/?runame_fixed=1"
                            else -> "http://127.0.0.1:5050/"
                        }
                        webView.loadUrl(initialUrl)
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
     * v44: the button opens the in-app camera (CameraX) for rapid
     * multi-shot; the camera-app intent below is the fallback when the
     * in-app camera cannot open or permission is denied.
     */
    inner class CameraJsBridge {
        @JavascriptInterface
        fun openCamera() {
            runOnUiThread { openInAppCamera() }
        }

        /** v36: listing-photo Edit hands the photo to the phone's own
         *  photo editor (Nov 3 decision: his Pixel camera app editor,
         *  NOT a limited in-app editor). v44: the editor is Google
         *  Photos, pinned by package when it can handle the edit, so
         *  he gets the full Photos editing tools; the system resolver
         *  is the fallback when Photos is missing. No package is
         *  hardcoded in the intent itself — PhotosEditor decides.
         *  v37: the photo goes in as a MediaStore working copy — v36's
         *  private provider URI resolved to nothing on his Pixel. Runs
         *  on the JS-interface thread; the launch itself does network
         *  on a worker thread and posts UI work back, so no thread
         *  assumption breaks. */
        @JavascriptInterface
        fun editDraftPhoto(draftId: String, rel: String, index: Int) {
            launchPixelEditor(draftId, rel, index)
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

        /**
         * v22: zero-typing fix, part 2. The web "Use the working name"
         * button calls this: rewrite .env so the RuName is the working
         * one (every other key untouched), leave a marker so the relaunch
         * goes straight into the eBay connect flow, then restart. This
         * runs on the JS-interface thread, so it posts to the UI thread
         * itself. If the rewrite fails, it says so and does NOT restart
         * — restarting with an unchanged .env would loop him back to
         * the same 400. If no .env exists at all, there is nothing to
         * fix; the menu's Edit keys form is the right path then.
         */
        @JavascriptInterface
        fun fixRunameAndConnect() {
            runOnUiThread {
                val f = File(filesDir, ".env")
                if (!f.exists()) {
                    Toast.makeText(
                        this@MainActivity,
                        "No saved keys found. Use Edit keys in the menu first.",
                        Toast.LENGTH_LONG
                    ).show()
                    return@runOnUiThread
                }
                try {
                    f.writeText(
                        RunameFix.rewriteEnvText(f.readText(), RunameFix.WORKING_RUNAME)
                    )
                    connectAfterRestartFile().createNewFile()
                } catch (_: Exception) {
                    Toast.makeText(
                        this@MainActivity,
                        "Couldn't update the sign-in name. Try Edit keys in the menu.",
                        Toast.LENGTH_LONG
                    ).show()
                    return@runOnUiThread
                }
                restartApp()
            }
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

    /**
     * v44: opens the in-app camera (CameraX). The shutter saves a photo
     * at once and the camera stays open, so he can snap several angles
     * back to back with no review screen and no check after each shot.
     * A missing or denied CAMERA permission falls back to the camera
     * app (the old path), never a dead button.
     */
    private fun openInAppCamera() {
        if (cameraOpen) return
        if (!CameraCapture.useInAppCamera(
                checkSelfPermission(android.Manifest.permission.CAMERA) ==
                    PackageManager.PERMISSION_GRANTED
            )
        ) {
            requestPermissions(
                arrayOf(android.Manifest.permission.CAMERA),
                CAMERA_PERMISSION_REQUEST
            )
            return
        }
        showInAppCamera()
    }

    /** v44: the CAMERA permission answer. Granted opens the in-app
     *  camera; denied falls back to the camera app and says so in
     *  plain words. */
    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == CAMERA_PERMISSION_REQUEST) {
            if (grantResults.isNotEmpty() &&
                grantResults[0] == PackageManager.PERMISSION_GRANTED
            ) {
                showInAppCamera()
            } else {
                Toast.makeText(
                    this,
                    "Camera permission is off, so photos open in the camera app instead.",
                    Toast.LENGTH_LONG
                ).show()
                launchCameraForJs()
            }
        }
    }

    /** v44: shows the camera overlay and binds CameraX. A bind failure
     *  falls back to the camera app, with the reason on screen. */
    private fun showInAppCamera() {
        val overlay = findViewById<View>(R.id.camera_view) ?: return
        val preview = findViewById<PreviewView>(R.id.camera_preview) ?: return
        cameraPhotosTaken = 0
        findViewById<TextView>(R.id.camera_count)?.text = "0 photos"
        overlay.visibility = View.VISIBLE
        cameraOpen = true
        val camera = InAppCamera(this)
        inAppCamera = camera
        camera.start(
            this,
            preview,
            onReady = { /* live; the shutter is already wired */ },
            onError = {
                closeInAppCamera()
                Toast.makeText(
                    this,
                    "Couldn't start the camera. Opening the camera app instead.",
                    Toast.LENGTH_LONG
                ).show()
                launchCameraForJs()
            }
        )
    }

    /**
     * v44: one shutter tap. Saves to a temp file, then runs the same
     * pipeline as the old camera-app result: native downscale to
     * 2048px, base64, hand to the WebView's onCameraPhoto — which
     * routes it by cameraTarget (step 1 or draft). The count on the
     * camera screen shows the running total. The shutter is ignored
     * while a capture is in flight.
     */
    private fun takeInAppPhoto() {
        val camera = inAppCamera ?: return
        if (camera.isCapturing()) return
        val dir = CameraPhotoProvider.cameraDir(this)
        val file = File(dir, CameraCapture.tempName(System.currentTimeMillis()))
        try {
            file.createNewFile()
        } catch (_: Exception) {
            Toast.makeText(this, "Couldn't save that photo. Try again.", Toast.LENGTH_LONG).show()
            return
        }
        camera.takePhoto(
            file,
            onPhoto = { saved ->
                val jpeg = CameraDownscale.downscale(saved)
                saved.delete()
                if (jpeg != null) {
                    val b64 = Base64.encodeToString(jpeg, Base64.NO_WRAP)
                    // Base64.NO_WRAP alphabet is JS-string-safe; no
                    // escaping needed inside the single quotes.
                    webView.evaluateJavascript(
                        "onCameraPhoto('data:image/jpeg;base64,$b64')", null
                    )
                    cameraPhotosTaken++
                    findViewById<TextView>(R.id.camera_count)?.text =
                        "$cameraPhotosTaken photo" +
                        if (cameraPhotosTaken == 1) "" else "s"
                } else {
                    Toast.makeText(this, "Couldn't read that photo.", Toast.LENGTH_LONG).show()
                }
            },
            onError = { msg ->
                file.delete()
                Toast.makeText(this, msg, Toast.LENGTH_LONG).show()
            }
        )
    }

    /** v44: closes the in-app camera back to the listing. Unbinds
     *  CameraX and hides the overlay; the photos already taken stay
     *  in the strip. */
    private fun closeInAppCamera() {
        inAppCamera?.stop()
        inAppCamera = null
        cameraOpen = false
        findViewById<View>(R.id.camera_view)?.visibility = View.GONE
    }

    /** Launches the camera app for the JS-interface path (no chooser). */
    private fun launchCameraForJs() {
        // v36: never launch a second camera while one is already pending.
        // A rapid double-tap used to overwrite pendingCameraPhoto, so the
        // first capture's result could land on the second (still empty)
        // temp file and deliver nothing. A fresh process always starts
        // with null pending state, and handleCameraJsResult always clears
        // it in finally, so this guard cannot strand the camera.
        if (pendingCameraPhoto != null) return
        val camIntent = buildCameraIntent()
        if (camIntent == null) {
            Toast.makeText(this, "No camera app found.", Toast.LENGTH_LONG).show()
            return
        }
        try {
            @Suppress("DEPRECATION")
            startActivityForResult(camIntent, CAMERA_JS_REQUEST)
        } catch (_: Exception) {
            pendingCameraPhoto?.delete()
            pendingCameraPhoto = null
            pendingCameraPhotoUri = null
            Toast.makeText(this, "Couldn't open the camera.", Toast.LENGTH_LONG).show()
        }
    }

    /**
     * Handles the JS-interface camera result: downscales natively to 2048px,
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
    /** v37: insert bytes into MediaStore as a WORKING COPY and return
     *  its content://media/... URI, or null on any failure. Gallery
     *  editors accept library links; v36's private provider URI
     *  resolved to nothing on his Pixel. The working copy is deleted
     *  by clearPendingEdit when the edit round ends, so his gallery
     *  is never polluted. On Android 10+ the row is IS_PENDING while
     *  bytes are written, then published, so the editor sees a whole
     *  file, never a half-written one. */
    private fun insertMediaStoreWorkingCopy(jpeg: ByteArray): Uri? {
        return try {
            val stamp = System.currentTimeMillis()
            val values = android.content.ContentValues().apply {
                put(android.provider.MediaStore.Images.Media.DISPLAY_NAME, "ListingAssistant-edit-$stamp.jpg")
                put(android.provider.MediaStore.Images.Media.MIME_TYPE, "image/jpeg")
                if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.Q) {
                    put(android.provider.MediaStore.Images.Media.RELATIVE_PATH, android.os.Environment.DIRECTORY_PICTURES + "/ListingAssistant")
                    put(android.provider.MediaStore.Images.Media.IS_PENDING, 1)
                }
            }
            val uri = contentResolver.insert(android.provider.MediaStore.Images.Media.EXTERNAL_CONTENT_URI, values) ?: return null
            try {
                contentResolver.openOutputStream(uri)?.use { it.write(jpeg) } ?: run {
                    contentResolver.delete(uri, null, null)
                    return null
                }
            } catch (_: Exception) {
                contentResolver.delete(uri, null, null)
                return null
            }
            if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.Q) {
                val done = android.content.ContentValues().apply {
                    put(android.provider.MediaStore.Images.Media.IS_PENDING, 0)
                }
                contentResolver.update(uri, done, null, null)
            }
            uri
        } catch (_: Exception) {
            null
        }
    }

    /** v37: MediaStore working-copy edit. Downloads the draft photo
     *  from the local Flask server, inserts it into MediaStore as a
     *  working copy, and hands that library URI to the system editor
     *  with a writable output file as a fallback shape. On return the
     *  edited bytes post back over the same draft slot (replace
     *  endpoint). The draft original is never touched until edited
     *  bytes come back. */
    private fun launchPixelEditor(draftId: String, rel: String, index: Int) {
        Thread {
            try {
                val orig = downloadLocalPhoto(rel)
                if (orig == null) {
                    runOnUiThread {
                        Toast.makeText(this, "Couldn't open that photo for editing.", Toast.LENGTH_LONG).show()
                    }
                    return@Thread
                }
                val mediaUri = insertMediaStoreWorkingCopy(orig)
                if (mediaUri == null) {
                    runOnUiThread {
                        Toast.makeText(this, "Couldn't open that photo for editing.", Toast.LENGTH_LONG).show()
                    }
                    return@Thread
                }
                pendingEditDraftId = draftId
                pendingEditIndex = index
                pendingEditMediaUri = mediaUri
                pendingEditOrig = orig
                val dir = CameraPhotoProvider.cameraDir(this)
                val stamp = System.currentTimeMillis()
                val outFile = File(dir, "edit_out_$stamp.jpg").apply { createNewFile() }
                val outUri = CameraPhotoProvider.uriFor(this, outFile)
                pendingEditOut = outFile
                pendingEditOutUri = outUri
                runOnUiThread {
                    val intent = Intent(Intent.ACTION_EDIT).apply {
                        setDataAndType(mediaUri, "image/jpeg")
                        putExtra(android.provider.MediaStore.EXTRA_OUTPUT, outUri)
                        addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION)
                    }
                    // v44: pin the edit to Google Photos when it can
                    // handle it (full editing tools); otherwise leave
                    // the intent open for the system resolver, exactly
                    // as v36/v37 did. The package query is explicit, so
                    // it resolves under Android 11+ package visibility.
                    val photosCanEdit = packageManager.queryIntentActivities(
                        Intent(intent).apply {
                            setPackage(PhotosEditor.GOOGLE_PHOTOS_PACKAGE)
                        },
                        android.content.pm.PackageManager.MATCH_DEFAULT_ONLY
                    ).isNotEmpty()
                    PhotosEditor.targetPackage(photosCanEdit)?.let {
                        intent.setPackage(it)
                    }
                    if (intent.resolveActivity(packageManager) == null) {
                        clearPendingEdit()
                        Toast.makeText(this, "No photo editor found on this phone.", Toast.LENGTH_LONG).show()
                        return@runOnUiThread
                    }
                    packageManager
                        .queryIntentActivities(intent, android.content.pm.PackageManager.MATCH_DEFAULT_ONLY)
                        .forEach {
                            grantUriPermission(it.activityInfo.packageName, mediaUri, Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION)
                            grantUriPermission(it.activityInfo.packageName, outUri, Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION)
                        }
                    @Suppress("DEPRECATION")
                    startActivityForResult(intent, EDIT_PHOTO_REQUEST)
                }
            } catch (_: Exception) {
                clearPendingEdit()
                runOnUiThread {
                    Toast.makeText(this, "Couldn't open that photo for editing.", Toast.LENGTH_LONG).show()
                }
            }
        }.start()
    }

    /** v37: clear editor-handoff state, revoke URI grants, delete the
     *  temp output file AND the MediaStore working copy, so no edited
     *  copy lingers in his gallery. Safe on any round end (success,
     *  cancel, failure, no return): the edited bytes are already in
     *  memory before this runs, and the draft original is untouched
     *  until the replace POST succeeds. */
    private fun clearPendingEdit() {
        pendingEditMediaUri?.let { mediaUri ->
            try { contentResolver.delete(mediaUri, null, null) } catch (_: Exception) { }
            revokeUriPermission(mediaUri, Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION)
        }
        pendingEditOutUri?.let {
            revokeUriPermission(it, Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION)
        }
        pendingEditOut?.delete()
        pendingEditDraftId = null
        pendingEditIndex = -1
        pendingEditMediaUri = null
        pendingEditOut = null
        pendingEditOutUri = null
        pendingEditOrig = null
    }

    /** v36: fetch one draft photo's bytes from the local Flask server
     *  (same origin the WebView uses). Null on any failure. */
    private fun downloadLocalPhoto(rel: String): ByteArray? {
        return try {
            val conn = URL("http://127.0.0.1:5050/photos/$rel").openConnection() as HttpURLConnection
            conn.connectTimeout = 10000
            conn.readTimeout = 30000
            try {
                if (conn.responseCode != HttpURLConnection.HTTP_OK) null
                else conn.inputStream.readBytes()
            } finally {
                conn.disconnect()
            }
        } catch (_: Exception) {
            null
        }
    }

    /** v36: POST edited bytes over one draft slot. True only on HTTP 200. */
    private fun postEditedPhoto(draftId: String, index: Int, jpeg: ByteArray): Boolean {
        return try {
            val boundary = "----ListingAssistantEdit" + System.currentTimeMillis()
            val conn = URL("http://127.0.0.1:5050/api/drafts/$draftId/photos/$index/replace").openConnection() as HttpURLConnection
            conn.requestMethod = "POST"
            conn.doOutput = true
            conn.connectTimeout = 10000
            conn.readTimeout = 60000
            conn.setRequestProperty("Content-Type", "multipart/form-data; boundary=$boundary")
            conn.outputStream.use { out ->
                out.write("--$boundary\r\n".toByteArray())
                out.write("Content-Disposition: form-data; name=\"photo\"; filename=\"edited.jpg\"\r\n".toByteArray())
                out.write("Content-Type: image/jpeg\r\n\r\n".toByteArray())
                out.write(jpeg)
                out.write("\r\n--$boundary--\r\n".toByteArray())
                out.flush()
            }
            val ok = conn.responseCode == HttpURLConnection.HTTP_OK
            conn.disconnect()
            ok
        } catch (_: Exception) {
            false
        }
    }

    /** v37: resolve the editor's return into edited bytes, then save
     *  them over the same draft slot. Resolution order covers how
     *  editors actually behave: (1) the MediaStore working copy
     *  changed in place (gallery editors typically edit library items
     *  in place), (2) the result carries a URI, (3) the EXTRA_OUTPUT
     *  file has bytes. Anything else keeps the original untouched and
     *  says so — never a silent loss. A cancelled edit is silent, like
     *  a cancelled camera snap. clearPendingEdit deletes the working
     *  copy in finally; that is safe because the edited bytes are
     *  already in memory and the draft original is untouched until the
     *  replace POST succeeds. */
    private fun handlePixelEditResult(resultCode: Int, data: Intent?) {
        val draftId = pendingEditDraftId
        val index = pendingEditIndex
        val mediaUri = pendingEditMediaUri
        val outFile = pendingEditOut
        val orig = pendingEditOrig
        val edited: ByteArray? = try {
            if (resultCode != Activity.RESULT_OK || draftId == null || index < 0) {
                null
            } else {
                val mediaBytes = mediaUri?.let { uri ->
                    try { contentResolver.openInputStream(uri)?.use { it.readBytes() } } catch (_: Exception) { null }
                }
                if (mediaBytes != null && mediaBytes.isNotEmpty() && orig != null && !mediaBytes.contentEquals(orig)) {
                    mediaBytes
                } else {
                    val returned = data?.data?.let { uri ->
                        try { contentResolver.openInputStream(uri)?.use { it.readBytes() } } catch (_: Exception) { null }
                    }
                    if (returned != null && returned.isNotEmpty()) {
                        returned
                    } else if (outFile != null && outFile.length() > 0L) {
                        outFile.readBytes()
                    } else {
                        null
                    }
                }
            }
        } catch (_: Exception) {
            null
        } finally {
            clearPendingEdit()
        }
        if (edited == null) {
            if (resultCode == Activity.RESULT_OK) {
                Toast.makeText(this, "The editor didn't return a photo. Your original is unchanged.", Toast.LENGTH_LONG).show()
            }
            return
        }
        val id = draftId ?: return
        Thread {
            val ok = postEditedPhoto(id, index, edited)
            runOnUiThread {
                if (ok) {
                    webView.evaluateJavascript("reloadDraftPhotosOnly()", null)
                } else {
                    Toast.makeText(this, "Couldn't save the edited photo. Your original is unchanged.", Toast.LENGTH_LONG).show()
                }
            }
        }.start()
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        // v14: Take photo button -> Android.openCamera() (JS interface).
        if (requestCode == CAMERA_JS_REQUEST) {
            handleCameraJsResult(resultCode, data)
            return
        }
        // v36: Pixel-editor return -> edited bytes into the same draft slot.
        if (requestCode == EDIT_PHOTO_REQUEST) {
            handlePixelEditResult(resultCode, data)
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
        // v44: Back from the in-app camera just closes it, back to the
        // listing — the photos already taken stay in the strip.
        if (cameraOpen) {
            closeInAppCamera()
            return
        }
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
        // v44: unbind the in-app camera before the Activity goes away.
        inAppCamera?.stop()
        inAppCamera = null
        lifecycleRegistry.handleLifecycleEvent(Lifecycle.Event.ON_DESTROY)
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
        // v44: CameraX binds to this Activity's lifecycle.
        lifecycleRegistry.handleLifecycleEvent(Lifecycle.Event.ON_RESUME)
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

        /** v36: listing-photo Edit -> phone's editor result. */
        private const val EDIT_PHOTO_REQUEST = 1003

        /** v44: in-app camera permission request. Granted opens the
         *  in-app camera; denied falls back to the camera app. */
        private const val CAMERA_PERMISSION_REQUEST = 1004

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
