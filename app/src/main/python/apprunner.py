"""APK entry point (Chaquopy).

Called from MainActivity on a background thread. Loads the .env saved in the
app's private files dir, points user data at private storage, then runs Flask.
Must run before `import app`, because app.py reads its config at import time.
"""
import os


def _load_env(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            os.environ[key] = value


def start(files_dir, app_version=""):
    # User data (drafts, photos, eBay tokens) belongs in private storage, not
    # next to the extracted code: Chaquopy may wipe the code dir on upgrade.
    data_dir = os.path.join(files_dir, "appdata")
    os.makedirs(data_dir, exist_ok=True)
    os.environ["LA_DATA_DIR"] = data_dir

    _load_env(os.path.join(files_dir, ".env"))

    # The eBay RuName's auth accepted URL is http://localhost:5050/... and must
    # match exactly, so the APK always listens on 5050.
    os.environ["PORT"] = "5050"
    # v6: the header version badge comes from the Android versionName, passed
    # in by MainActivity, so it is correct automatically on every release.
    # No hardcoded version string lives here anymore: the old literal kept
    # showing v1 on his phone long after v5 was installed, which made him
    # think the update didn't take.
    if app_version:
        v = app_version if app_version.startswith("v") else "v" + app_version
        os.environ["APP_VERSION"] = v

    import app
    app.app.run(host="127.0.0.1", port=5050, threaded=True)
