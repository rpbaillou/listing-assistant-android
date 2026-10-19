// Top-level build file.
//
// Uses the classic buildscript classpath (not the plugins DSL with versions)
// because the Gradle Plugin Portal's redirect for the Android plugin marker
// is broken (it 303s to Maven Central, which 404s). All three artifacts are
// resolved directly: AGP from Google Maven, Kotlin and Chaquopy from Central.

buildscript {
    repositories {
        google()
        mavenCentral()
    }
    dependencies {
        classpath("com.android.tools.build:gradle:8.5.2")
        classpath("org.jetbrains.kotlin:kotlin-gradle-plugin:1.9.24")
        // Chaquopy 17.1.0: free and open-source (MIT), per chaquo.com news.
        classpath("com.chaquo.python:gradle:17.1.0")
    }
}
