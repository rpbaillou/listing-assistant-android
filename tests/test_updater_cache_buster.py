"""apk-v38 tests: cache-busting updater check.

v37 (1.36) went live and version.json verified at 37, yet his phone on
1.35 kept saying "You're on the latest version" across app restarts.
The menu check is a straight live fetch with no throttle, no saved
answer, and no app cache, and "latest" fires ONLY on a successful fetch
of a versionCode <= installed — so his network/CDN edge was serving a
STALE copy of version.json for 26+ minutes. A fixed URL lets any
CDN/proxy cache serve the old file; a unique URL per check cannot match
a cached key.

Kotlin cannot execute in this sandbox (no JVM/kotlinc, as with
v15-v37), so these are source-contract regression tests over the real
Updater.kt / UpdateCheck.kt / MainActivity.kt: they pin the exact
behaviors the live update test must prove, and they fail if anyone
reverts to a fixed fetch URL, drops the no-cache headers, re-enables
connection caching, or softens the strict > comparison. CI Gradle
compilation is the Kotlin execution gate; the live "new version shows
up right away" check on his phone is the proof. Dummy data only; eBay
is never touched.
"""

import os
import re
import unittest

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
KT_DIR = os.path.join(
    REPO, "app", "src", "main", "java",
    "com", "rpbdesign", "listingassistant")
UPDATER = os.path.join(KT_DIR, "Updater.kt")
CHECK = os.path.join(KT_DIR, "UpdateCheck.kt")
MAIN = os.path.join(KT_DIR, "MainActivity.kt")

with open(UPDATER, encoding="utf-8") as f:
    UPDATER_SRC = f.read()
with open(CHECK, encoding="utf-8") as f:
    CHECK_SRC = f.read()
with open(MAIN, encoding="utf-8") as f:
    MAIN_SRC = f.read()


def _check_body():
    start = UPDATER_SRC.index("fun check(")
    end = UPDATER_SRC.index("private fun installedVersionCode", start)
    return UPDATER_SRC[start:end]


class TestCacheBusterUrl(unittest.TestCase):
    def test_unique_query_param_appended_per_check(self):
        body = _check_body()
        # The fetch URL is built fresh on every check from the current
        # time, not a constant: two checks must produce different keys.
        self.assertIn("System.currentTimeMillis()", body)
        self.assertRegex(body, r'\$VERSION_URL\?t=')

    def test_no_hardcoded_buster_value(self):
        body = _check_body()
        # A fixed "?t=12345" literal would cache exactly like the old
        # fixed URL and defeat the whole fix.
        self.assertNotRegex(body, r'\?t=\d+')

    def test_version_url_constant_stays_plain(self):
        # The constant itself keeps the clean URL; the param is appended
        # per check so the constant stays readable and greppable.
        m = re.search(r'const val VERSION_URL =\s*\n?\s*"([^"]+)"',
                      UPDATER_SRC)
        self.assertIsNotNone(m)
        self.assertEqual(
            m.group(1),
            "https://raw.githubusercontent.com/rpbaillou/"
            "listing-assistant-android/main/version.json")
        self.assertNotIn("?", m.group(1))

    def test_buster_built_before_connection_opened(self):
        body = _check_body()
        self.assertLess(body.index("currentTimeMillis"),
                        body.index("openConnection"))


class TestNoCacheHeaders(unittest.TestCase):
    def test_cache_control_no_cache(self):
        body = _check_body()
        self.assertIn('"Cache-Control", "no-cache"', body)

    def test_pragma_no_cache(self):
        body = _check_body()
        self.assertIn('"Pragma", "no-cache"', body)

    def test_connection_caching_disabled(self):
        body = _check_body()
        self.assertIn("useCaches = false", body)

    def test_headers_set_before_response_read(self):
        body = _check_body()
        self.assertLess(body.index("no-cache"),
                        body.index("responseCode"))


class TestComparisonAndRoutingUnchanged(unittest.TestCase):
    def test_strict_greater_than_comparison(self):
        # A >= comparison would offer a reinstall of the installed
        # build; a == would never offer anything. Only strict > is
        # correct, and v38 must not soften it.
        self.assertIn("info.versionCode > installedCode", CHECK_SRC)

    def test_non_200_still_goes_to_on_error(self):
        body = _check_body()
        seg = body[body.index("responseCode"):body.index("responseCode") + 300]
        self.assertIn("onError", seg)

    def test_parse_failure_still_goes_to_on_error(self):
        body = _check_body()
        seg = body[body.index("parseVersionJson"):]
        self.assertIn("onError", seg[:300])

    def test_update_vs_uptodate_branches_intact(self):
        body = _check_body()
        self.assertIn("onUpdate(info)", body)
        self.assertIn("onUpToDate", body)

    def test_toasts_unchanged(self):
        # The user-facing words must not change: "latest" still means a
        # successful fetch of an older-or-equal build, and a failed
        # check still says it could not check (never "latest").
        self.assertIn("You're on the latest version.", MAIN_SRC)
        self.assertIn("Could not check for updates. Check your "
                      "connection and try again.", MAIN_SRC)


class TestNoSecretsInDiff(unittest.TestCase):
    def test_updater_has_no_secret_like_strings(self):
        for token in ("api_key", "secret", "password", "token="):
            self.assertNotIn(token, UPDATER_SRC.lower())


class TestKdocStructureValid(unittest.TestCase):
    def test_check_doc_comment_has_exactly_one_close(self):
        # v38 first CI run failed at "Build signed APK" because the
        # v38 note was inserted AFTER the original KDoc's closing */,
        # leaving bare "* ..." lines outside any comment — a Kotlin
        # syntax error no sandbox test caught (kotlinc unavailable).
        # The check() doc comment must open once and close exactly
        # once before "fun check(".
        idx = UPDATER_SRC.index("fun check(")
        doc_start = UPDATER_SRC.rindex("/**", 0, idx)
        segment = UPDATER_SRC[doc_start:idx]
        self.assertEqual(segment.count("*/"), 1)


if __name__ == "__main__":
    unittest.main()
