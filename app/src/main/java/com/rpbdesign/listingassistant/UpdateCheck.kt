package com.rpbdesign.listingassistant

/**
 * What the update server (version.json on GitHub) says about the newest build.
 */
data class UpdateInfo(
    val versionCode: Int,
    val versionName: String,
    val apkUrl: String,
    val notes: String
)

/**
 * Parse the tiny version.json by hand (no JSON library) so this stays unit
 * testable on plain JVM. Returns null when anything required is missing or
 * the download URL is not a plain https link.
 */
fun parseVersionJson(json: String): UpdateInfo? {
    fun strField(name: String): String? =
        Regex("\"$name\"\\s*:\\s*\"([^\"]*)\"").find(json)?.groupValues?.get(1)
    val code = Regex("\"versionCode\"\\s*:\\s*(\\d+)").find(json)
        ?.groupValues?.get(1)?.toIntOrNull() ?: return null
    val url = strField("apkUrl") ?: return null
    if (!url.startsWith("https://")) return null
    return UpdateInfo(
        versionCode = code,
        versionName = strField("versionName") ?: "",
        apkUrl = url,
        notes = strField("notes") ?: ""
    )
}

/** True when the server's build is newer than the installed one. */
fun isUpdateAvailable(installedCode: Int, info: UpdateInfo): Boolean =
    info.versionCode > installedCode
