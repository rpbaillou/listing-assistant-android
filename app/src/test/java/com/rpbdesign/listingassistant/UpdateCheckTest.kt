package com.rpbdesign.listingassistant

import org.junit.Assert.*
import org.junit.Test

class UpdateCheckTest {

    private val goodJson = """
        {"versionCode": 3, "versionName": "1.2",
         "apkUrl": "https://github.com/rpbaillou/listing-assistant-android/releases/download/apk-v3/app.apk",
         "notes": "Hello"}
    """.trimIndent()

    @Test fun parseValid() {
        val info = parseVersionJson(goodJson)
        assertNotNull(info)
        assertEquals(3, info!!.versionCode)
        assertEquals("1.2", info.versionName)
        assertTrue(info.apkUrl.startsWith("https://"))
        assertEquals("Hello", info.notes)
    }

    @Test fun parseMalformedReturnsNull() {
        assertNull(parseVersionJson("not json at all {{{"))
        assertNull(parseVersionJson(""))
    }

    @Test fun parseMissingVersionCodeReturnsNull() {
        assertNull(parseVersionJson("""{"versionName":"1.2","apkUrl":"https://x/y.apk"}"""))
    }

    @Test fun parseMissingUrlReturnsNull() {
        assertNull(parseVersionJson("""{"versionCode":3}"""))
    }

    @Test fun parseNonHttpsUrlRejected() {
        assertNull(parseVersionJson(
            """{"versionCode":3,"apkUrl":"http://evil.example/app.apk"}"""))
    }

    @Test fun parseOptionalFieldsDefault() {
        val info = parseVersionJson("""{"versionCode":5,"apkUrl":"https://x/y.apk"}""")
        assertNotNull(info)
        assertEquals("", info!!.versionName)
        assertEquals("", info.notes)
    }

    @Test fun newerIsAvailable() {
        val info = parseVersionJson(goodJson)!!
        assertTrue(isUpdateAvailable(2, info))
    }

    @Test fun equalIsNotAvailable() {
        val info = parseVersionJson(goodJson)!!
        assertFalse(isUpdateAvailable(3, info))
    }

    @Test fun olderIsNotAvailable() {
        val info = parseVersionJson(goodJson)!!
        assertFalse(isUpdateAvailable(4, info))
    }
}
