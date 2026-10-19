# Listing Assistant — Android APK (apk-v1)

The eBay Listing Assistant as a real Android app. No Pydroid, no pip, no
.env editing, no PC. Install it and open it.

## What it is

The same Flask app (v2.8 + phone-v1) packaged with Chaquopy (free,
MIT-licensed Python for Android). When you open the app it starts the
server on the phone itself and shows it in a built-in browser view.

## Install (for Reynard)

1. Download `ebay-listing-assistant-apk-v1.apk` to the phone.
2. Tap it. The phone asks to allow installs from unknown sources once;
   allow it for the Files app (or Chrome, whichever you downloaded with).
3. Tap Install, then Open.
4. First run only: a setup screen asks for your 7 keys
   (Gemini key, eBay App ID, eBay Cert ID, RuName, shipping / payment /
   return policy IDs). They are saved on the phone and never sent anywhere
   except eBay and Google.
5. The app starts and you list. Photo picking, AI draft, price, review,
   one-tap eBay listing, all inside the app.

To update later: download the new APK and tap it. Your keys, drafts and
eBay sign-in carry over.

## Build (for the developer)

Needs JDK 17 and the Android SDK (platform 34, build-tools 34.0.0).

```sh
export JAVA_HOME=<jdk17> ANDROID_HOME=<sdk> ANDROID_SDK_ROOT=<sdk>
./gradlew assembleDebug   # or: <gradle-8.7>/bin/gradle assembleDebug
# APK: app/build/outputs/apk/debug/app-debug.apk
```

Python deps are declared in `app/build.gradle.kts` (Chaquopy `pip` block):
Flask, requests, Pillow. Chaquopy Python 3.12, 64-bit ABIs only
(arm64-v8a, x86_64).

## Layout

- `app/src/main/python/` — `apprunner.py` (entry point: loads .env from
  private storage, runs Flask on 127.0.0.1:5050), `app.py` (the assistant,
  with a 2-line `LA_DATA_DIR` patch so user data lives in private storage),
  `templates/`, `static/`.
- `app/src/main/java/.../MainActivity.kt` — first-run key form, Python
  startup on a background thread, WebView with a file picker
  (`onShowFileChooser`, multi-image select).
- Port 5050 is fixed: the eBay RuName's auth accepted URL is
  `http://localhost:5050/api/ebay/callback` and must match exactly.

## Known limits (apk-v1)

- Not tested on a real device in the lab; Reynard is the first tester.
- Highest-risk untested items: Chaquopy Python startup on device, the
  WebView file picker, and the eBay OAuth round-trip inside the WebView
  (eBay's sign-in page may behave differently there than in Chrome).
- Android only. iPhone cannot run Python apps this way.
