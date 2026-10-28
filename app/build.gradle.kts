plugins {
    // Versions come from the root buildscript classpath (see build.gradle.kts).
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.chaquo.python")
}

android {
    namespace = "com.rpbdesign.listingassistant"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.rpbdesign.listingassistant"
        minSdk = 24
        targetSdk = 34
        versionCode = 23
        versionName = "1.22"

        ndk {
            // Chaquopy Python 3.12 is 64-bit only.
            abiFilters += listOf("arm64-v8a", "x86_64")
        }
    }

    signingConfigs {
        create("listingAssistant") {
            // Local builds use the keystore copy in ../keystore/ (gitignored).
            // CI (GitHub Actions) provides it via KEYSTORE_PATH from secrets.
            // Same key, same signature, on any machine: in-app updates refuse
            // to install if this ever changes.
            val ksPath = System.getenv("KEYSTORE_PATH")
                ?: "../keystore/listing-assistant-debug.keystore"
            storeFile = file(ksPath)
            storePassword = System.getenv("KEYSTORE_PASSWORD") ?: "android"
            keyAlias = System.getenv("KEY_ALIAS") ?: "androiddebugkey"
            keyPassword = System.getenv("KEY_PASSWORD") ?: "android"
        }
    }

    buildTypes {
        debug {
            // Pinned to our own copy of the debug keystore (NOT the SDK default
            // location) so every build, on any machine, carries the same
            // signature. In-app updates refuse to install if this changes.
            // The keystore file itself is gitignored and never pushed.
            signingConfig = signingConfigs.getByName("listingAssistant")
        }
        release {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}

chaquopy {
    defaultConfig {
        // Must match the build machine's python3 (3.12).
        version = "3.12"
        pip {
            install("Flask==3.1.0")
            install("requests==2.32.3")
            install("Pillow==11.0.0")
        }
    }
}

dependencies {
    testImplementation("junit:junit:4.13.2")
}
