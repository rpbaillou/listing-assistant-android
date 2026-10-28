"""eBay Listing Assistant v2.8 - personal listing draft helper with one-tap eBay listing.

Flow: pick photos -> Gemini identifies the item and drafts the listing ->
you check sold prices on eBay yourself -> review -> one tap lists it on eBay
(Sell Inventory + Sell Listing APIs, after you connect your eBay account).

v1 had no eBay connection (developer's account was suspended). v2 adds it.
Copy buttons and the photos zip remain as a manual fallback.
"""
import base64
import html
import io
import json
import os
import time
import uuid
import zipfile
from datetime import datetime
from urllib.parse import urlencode

import requests
from flask import Flask, request, jsonify, render_template, send_file, send_from_directory, redirect
from werkzeug.utils import secure_filename
from PIL import Image
try:
    import qrcode
    from qrcode.constants import ERROR_CORRECT_M
    QRCODE_AVAILABLE = True
except ImportError:
    # Phone installs skip qrcode (the second-phone QR is not used there).
    QRCODE_AVAILABLE = False

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()  # lets iPhone HEIC photos open like normal
except ImportError:
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# apk: when LA_DATA_DIR is set (Android APK), user data (drafts, photos,
# eBay tokens) lives in the app's private files dir. Chaquopy extracts code to
# a location that may be wiped on upgrade, so data must not live next to it.
DATA_DIR = os.environ.get("LA_DATA_DIR", "").strip() or os.path.join(BASE_DIR, "data")
PHOTOS_DIR = os.path.join(DATA_DIR, "photos")
DRAFTS_DIR = os.path.join(DATA_DIR, "drafts")
EBAY_TOKEN_FILE = os.path.join(DATA_DIR, "ebay_token.json")
for _d in (PHOTOS_DIR, DRAFTS_DIR):
    os.makedirs(_d, exist_ok=True)

# Port the app listens on. Default 5000 (PC Docker maps host 5050 to it).
# Phone installs (Pydroid) set PORT=5050 to match the eBay RuName callback URL.
PORT = int(os.environ.get("PORT", "5000").strip() or "5000")
APP_VERSION = os.environ.get("APP_VERSION", "v2.9").strip() or "v2.9"

GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "").strip()


def _gemini_model_list():
    """Priority list of Gemini models to try, in order.

    Google retires models without warning (e.g. gemini-2.0-flash returned a
    404 on Oct 19, 2026 with the suggestion to use gemini-3.8-flash), so we
    walk the list: on 404 (retired) or 503 (transient overload) we try the
    next model. Override with GEMINI_MODELS="model-a,model-b" in .env.
    The legacy single-model GEMINI_MODEL is still honored if set.
    """
    raw = os.environ.get("GEMINI_MODELS", "").strip()
    if raw:
        return [m.strip() for m in raw.split(",") if m.strip()]
    legacy = os.environ.get("GEMINI_MODEL", "").strip()
    if legacy and legacy != "gemini-2.0-flash":
        return [legacy]
    return ["gemini-3.8-flash", "gemini-3-flash-preview"]


GEMINI_MODELS = _gemini_model_list()
# Legacy name kept for anything referencing a single model.
GEMINI_MODEL = GEMINI_MODELS[0]

# --- eBay config (v2) ---
EBAY_CLIENT_ID = os.environ.get("EBAY_CLIENT_ID", "").strip()
EBAY_CLIENT_SECRET = os.environ.get("EBAY_CLIENT_SECRET", "").strip()
EBAY_REDIRECT_URI = os.environ.get(
    "EBAY_REDIRECT_URI", "http://localhost:5050/api/ebay/callback").strip()
EBAY_RUNAME = os.environ.get("EBAY_RUNAME", "").strip()
# v2.5: eBay's OAuth authorize endpoint requires the RuName (eBay Redirect URL
# name, e.g. Reynard_Baillou-ReynardB-Mr-PRD-...) as the redirect_uri
# value -- NOT the raw URL. The RuName itself must have the callback URL
# attached to it in the developer portal (User Tokens page). v19: eBay's
# portal forces https on that URL, so it is registered as https; the app's
# WebView rewrites the https callback to http so Flask receives it.
EBAY_FULFILLMENT_POLICY_ID = os.environ.get("EBAY_FULFILLMENT_POLICY_ID", "").strip()
EBAY_PAYMENT_POLICY_ID = os.environ.get("EBAY_PAYMENT_POLICY_ID", "").strip()
EBAY_RETURN_POLICY_ID = os.environ.get("EBAY_RETURN_POLICY_ID", "").strip()

EBAY_AUTH_URL = "https://auth.ebay.com/oauth2/authorize"
EBAY_TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
EBAY_API = "https://api.ebay.com"
# v2.1: the base scope covers the Trading API (picture hosting via IAF token);
# the two sell scopes cover the Sell Inventory / Listing APIs.
EBAY_SCOPES = ("https://api.ebay.com/oauth/api_scope "
               "https://api.ebay.com/oauth/api_scope/sell.inventory "
               "https://api.ebay.com/oauth/api_scope/sell.listing")

# eBay's own picture hosting lives in the classic Trading API (XML).
EBAY_TRADING_API = "https://api.ebay.com/ws/api.dll"
EBAY_TRADING_COMPAT_LEVEL = "1193"

MAX_PHOTOS = 24
MAX_FILE_MB = 15

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_PHOTOS * MAX_FILE_MB * 1024 * 1024

_oauth_states = {}      # state -> created epoch (single use, 10 min)
_app_token_cache = {}   # {"token":..., "expires_at":...} for taxonomy calls

CONDITIONS = [
    ("NEW", "New"),
    ("USED_EXCELLENT", "Used - excellent"),
    ("USED_VERY_GOOD", "Used - very good"),
    ("USED_GOOD", "Used - good"),
    ("USED_ACCEPTABLE", "Used - acceptable"),
    ("FOR_PARTS_OR_NOT_WORKING", "For parts or not working"),
]

IDENTIFY_PROMPT = """You are an expert eBay reseller assistant. The attached photos show ONE item a reseller wants to list on eBay. Identify it as specifically as you can (brand or maker, model or pattern name, era, materials, colors). Then write an eBay listing draft.

Rules:
- Title: 80 characters max. Buyer search keywords first. No ALL CAPS, no hype words like RARE!!! or L@@K.
- Description: 2 to 4 short factual sentences: what it is (maker, model or pattern name, era, materials, colors, visible markings, shape). Measurements only if visible. Never invent measurements, marks, or brands you cannot see. If you are unsure about the maker, say so.
- CONDITION RULE (strict, never break it): Do NOT describe, assess, or speculate about the item's condition in any way. Never write claims like "no chips", "no cracks", "good condition", "great shape", "mint", "appears to be in excellent condition", or anything similar. You cannot hold the item or see every angle, so any condition claim would be a guess, and a wrong guess misleads the buyer. The seller states the condition separately. Leave condition out of the description entirely.
- Category: the most specific eBay category path you can, for example "Collectibles > Decorative Collectibles > Plates".
- Attributes: key/value item specifics buyers filter by (Brand, Model, Era, Material, Color...). Use "Unknown" instead of guessing.

Return ONLY valid JSON, no markdown fences, exactly this shape:
{"title": "...", "description": "...", "category": "...", "attributes": {"Brand": "...", "Era": "..."}, "identification_notes": "..."}
"""


def _draft_path(draft_id):
    safe = secure_filename(draft_id)
    return os.path.join(DRAFTS_DIR, safe + ".json")


def _load_draft(draft_id):
    path = _draft_path(draft_id)
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_draft(draft):
    draft["updated"] = datetime.now().isoformat(timespec="seconds")
    with open(_draft_path(draft["id"]), "w", encoding="utf-8") as f:
        json.dump(draft, f, indent=2)
    return draft


def _gemini_identify(photo_relpaths):
    """Send saved photos to Gemini, return the parsed draft dict."""
    if not GEMINI_KEY or GEMINI_KEY == "PASTE_YOUR_KEY_HERE":
        raise RuntimeError(
            "No Gemini API key. Copy .env.example to .env and paste your free "
            "key from https://aistudio.google.com/apikey (setup guide: add your Gemini key)."
        )
    parts = [{"text": IDENTIFY_PROMPT}]
    for rel in photo_relpaths:
        full = os.path.join(PHOTOS_DIR, rel)
        with open(full, "rb") as f:
            raw = f.read()
        parts.append({"inlineData": {"mimeType": "image/jpeg",
                                     "data": base64.b64encode(raw).decode()}})
    url_tpl = ("https://generativelanguage.googleapis.com/v1beta/models/"
               "{model}:generateContent")
    payload = {"contents": [{"parts": parts}],
               "generationConfig": {"responseMimeType": "application/json",
                                    "temperature": 0.4,
                                    "maxOutputTokens": 2048}}
    tried = []
    resp = None
    for model in GEMINI_MODELS:
        tried.append(model)
        try:
            resp = requests.post(url_tpl.format(model=model),
                                 params={"key": GEMINI_KEY},
                                 json=payload, timeout=180)
        except requests.RequestException as exc:
            raise RuntimeError(f"Could not reach Gemini: {exc}")
        if resp.status_code == 200:
            break
        if resp.status_code in (404, 503):
            # Model retired (404) or overloaded (503): try the next one.
            continue
        detail = resp.text[:300]
        if resp.status_code in (400, 401, 403):
            raise RuntimeError(
                "Gemini rejected the request (bad or missing API key). "
                f"Check your key in .env. Details: {detail}")
        raise RuntimeError(f"Gemini error {resp.status_code}: {detail}")
    else:
        raise RuntimeError(
            "Gemini could not identify the item. The models tried "
            f"({', '.join(tried)}) are not answering right now. Try again "
            "in a few minutes, or set GEMINI_MODELS in .env to a newer model.")
    try:
        text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, ValueError):
        raise RuntimeError("Gemini returned an unexpected response. Try again.")
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        data = json.loads(text)
    except ValueError:
        raise RuntimeError("Gemini did not return a usable draft. Try again.")
    return {
        "title": str(data.get("title", ""))[:80],
        "description": str(data.get("description", "")),
        "category": str(data.get("category", "")),
        "attributes": {str(k): str(v) for k, v in (data.get("attributes") or {}).items()},
        "identification_notes": str(data.get("identification_notes", "")),
    }


def _store_uploads(files):
    """Normalize uploaded photos to JPEG, return list of relative paths."""
    rels = []
    draft_id = uuid.uuid4().hex[:12]
    folder = os.path.join(PHOTOS_DIR, draft_id)
    os.makedirs(folder, exist_ok=True)
    for i, fs in enumerate(files[:MAX_PHOTOS]):
        name = secure_filename(fs.filename or f"photo{i}.jpg")
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else "jpg"
        if ext not in {"png", "jpg", "jpeg", "webp", "gif", "heic", "heif"}:
            continue
        try:
            img = Image.open(fs.stream)
            img = img.convert("RGB")
            img.thumbnail((1600, 1600))
            out = os.path.join(folder, f"{i + 1}.jpg")
            img.save(out, "JPEG", quality=85)
            rels.append(f"{draft_id}/{i + 1}.jpg")
        except Exception:
            continue
    if not rels:
        raise RuntimeError("No usable photos found. Try JPG or PNG files.")
    return draft_id, rels


# ---------------------------------------------------------------- eBay v2

def _ebay_configured():
    return bool(EBAY_CLIENT_ID) and EBAY_CLIENT_ID != "PASTE_YOUR_APP_ID_HERE" \
        and bool(EBAY_CLIENT_SECRET) and EBAY_CLIENT_SECRET != "PASTE_YOUR_CERT_ID_HERE" \
        and bool(EBAY_RUNAME) and EBAY_RUNAME != "PASTE_YOUR_RUNAME_HERE"


def _load_ebay_tokens():
    if not os.path.isfile(EBAY_TOKEN_FILE):
        return None
    try:
        with open(EBAY_TOKEN_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _save_ebay_tokens(data):
    with open(EBAY_TOKEN_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _clear_ebay_tokens():
    if os.path.isfile(EBAY_TOKEN_FILE):
        os.remove(EBAY_TOKEN_FILE)


def _basic_auth_header():
    raw = f"{EBAY_CLIENT_ID}:{EBAY_CLIENT_SECRET}".encode()
    return "Basic " + base64.b64encode(raw).decode()


def _ebay_access_token():
    """Return a fresh user access token, refreshing automatically. Raises RuntimeError."""
    if not _ebay_configured():
        raise RuntimeError(
            "eBay is not set up yet. Add your eBay App ID and Cert ID to .env "
            "(setup guide: Connect eBay).")
    toks = _load_ebay_tokens()
    if not toks or not toks.get("refresh_token"):
        raise RuntimeError(
            "eBay is not connected. Open the app in this browser and tap Connect eBay.")
    if toks.get("expires_at", 0) > time.time() + 120:
        return toks["access_token"]
    # refresh (eBay rotates the refresh token; always save the new one)
    try:
        resp = requests.post(
            EBAY_TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "Authorization": _basic_auth_header()},
            data={"grant_type": "refresh_token",
                  "refresh_token": toks["refresh_token"],
                  "scope": EBAY_SCOPES},
            timeout=30)
    except requests.RequestException as exc:
        raise RuntimeError(f"Could not reach eBay: {exc}")
    if resp.status_code != 200:
        _clear_ebay_tokens()
        raise RuntimeError(
            "Your eBay sign-in expired and could not be renewed. "
            "Tap Connect eBay again in this browser.")
    body = resp.json()
    new_toks = {
        "access_token": body["access_token"],
        "refresh_token": body.get("refresh_token", toks["refresh_token"]),
        "expires_at": time.time() + int(body.get("expires_in", 7200)) - 60,
    }
    _save_ebay_tokens(new_toks)
    return new_toks["access_token"]


def _plain_ebay_error(resp):
    """Turn an eBay API error response into plain words."""
    try:
        body = resp.json()
        errs = body.get("errors") or []
        msg = errs[0].get("message", "") if errs else ""
        err_id = str(errs[0].get("errorId", "")) if errs else ""
    except ValueError:
        msg, err_id = resp.text[:300], ""
    low = (msg + " " + err_id).lower()
    if "business polic" in low or err_id == "25710":
        return ("eBay needs your shop's payment, shipping, and return policies on this "
                "listing. Check the three policy numbers in your .env (setup guide: Connect eBay), "
                "or pick different ones.")
    if "opt" in low and "program" in low:
        return ("Your eBay seller account needs business policies turned on before the "
                "app can list. In Seller Hub go to Account, then Business policies, and "
                "opt in. Then try again.")
    if "categor" in low:
        return ("eBay did not accept that category. Tap Suggest next to the category "
                "field and pick one from the list. Details: " + msg[:160])
    if "condition" in low:
        return ("eBay needs more condition detail for this category. Try a different "
                "condition choice. Details: " + msg[:160])
    if "image" in low:
        return ("eBay rejected a photo. Make sure every photo is a normal JPG under "
                "12 MB. Details: " + msg[:160])
    if "title" in low:
        return ("eBay did not accept the title. Keep it under 80 characters with no "
                "strange symbols. Details: " + msg[:160])
    if resp.status_code in (401, 403):
        return ("eBay refused the sign-in for this action. Tap Connect eBay again in "
                "this browser, then retry.")
    return ("eBay said: " + (msg[:220] if msg else f"error {resp.status_code}"))


def _ebay_call(method, path, token, payload=None):
    try:
        resp = requests.request(method, EBAY_API + path,
                                headers={"Authorization": "Bearer " + token,
                                         "Content-Type": "application/json"},
                                json=payload, timeout=60)
    except requests.RequestException as exc:
        raise RuntimeError(f"Could not reach eBay: {exc}")
    if resp.status_code in (401, 403):
        _clear_ebay_tokens()
        raise RuntimeError(
            "Your eBay sign-in stopped working. Tap Connect eBay again in this browser.")
    if resp.status_code >= 400:
        raise RuntimeError(_plain_ebay_error(resp))
    if resp.status_code == 204 or not resp.text.strip():
        return {}
    try:
        return resp.json()
    except ValueError:
        return {}


def _ebay_upload_picture(photo_path, token, picture_name):
    """Upload one photo to eBay's own picture hosting (Trading API
    UploadSiteHostedPictures, authorized with the OAuth user token in the
    X-EBAY-API-IAF-TOKEN header). Returns the public FullURL."""
    import xml.etree.ElementTree as ET
    with open(photo_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    name = "".join(c for c in picture_name if c.isalnum() or c in "-_")[:60] or "photo"
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<UploadSiteHostedPicturesRequest xmlns="urn:ebay:apis:eBLBaseComponents">'
        f"<PictureName>{name}</PictureName>"
        f"<PictureData>{b64}</PictureData>"
        "</UploadSiteHostedPicturesRequest>"
    )
    try:
        resp = requests.post(
            EBAY_TRADING_API,
            headers={"X-EBAY-API-CALL-NAME": "UploadSiteHostedPictures",
                     "X-EBAY-API-SITEID": "0",
                     "X-EBAY-API-COMPATIBILITY-LEVEL": EBAY_TRADING_COMPAT_LEVEL,
                     "X-EBAY-API-IAF-TOKEN": token,
                     "Content-Type": "text/xml"},
            data=xml.encode("utf-8"),
            timeout=120)
    except requests.RequestException as exc:
        raise RuntimeError(f"Could not reach eBay picture hosting: {exc}")
    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError:
        raise RuntimeError(
            "eBay picture hosting sent back something unreadable. Try again.")
    ns = {"e": "urn:ebay:apis:eBLBaseComponents"}
    ack = (root.findtext("e:Ack", default="", namespaces=ns) or "").lower()
    if ack not in ("success", "warning"):
        msg = (root.findtext("e:Errors/e:LongMessage", default="", namespaces=ns)
               or root.findtext("e:Errors/e:ShortMessage", default="", namespaces=ns))
        low = msg.lower()
        if resp.status_code in (401, 403) or "iaf" in low or "token" in low \
                or "auth" in low or "scope" in low:
            _clear_ebay_tokens()
            raise RuntimeError(
                "Your eBay sign-in stopped working (or is missing the new picture "
                "permission). Tap Connect eBay again in this browser.")
        raise RuntimeError(
            "eBay picture hosting said: " + (msg[:220] if msg else
                                             f"error {resp.status_code}"))
    url = root.findtext("e:SiteHostedPictureDetails/e:FullURL",
                        default="", namespaces=ns)
    if not url:
        raise RuntimeError(
            "eBay hosted the photo but did not return its address. Try again.")
    return url.strip()


def _app_token():
    """Client-credentials token for the taxonomy (category) API. Cached in memory."""
    if _app_token_cache.get("expires_at", 0) > time.time() + 60:
        return _app_token_cache["token"]
    if not _ebay_configured():
        raise RuntimeError("eBay is not set up yet (setup guide: Connect eBay).")
    try:
        resp = requests.post(
            EBAY_TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "Authorization": _basic_auth_header()},
            data={"grant_type": "client_credentials",
                  "scope": "https://api.ebay.com/oauth/api_scope"},
            timeout=30)
    except requests.RequestException as exc:
        raise RuntimeError(f"Could not reach eBay: {exc}")
    if resp.status_code != 200:
        raise RuntimeError("eBay rejected the app keys. Check EBAY_CLIENT_ID and "
                           "EBAY_CLIENT_SECRET in .env.")
    body = resp.json()
    _app_token_cache["token"] = body["access_token"]
    _app_token_cache["expires_at"] = time.time() + int(body.get("expires_in", 7200)) - 60
    return _app_token_cache["token"]


def _find_category_ids(node, out):
    """Walk a taxonomy response looking for category id/name pairs."""
    if isinstance(node, dict):
        cid = node.get("categoryId")
        cname = node.get("categoryName")
        if cid and cname:
            out.append((str(cid), str(cname)))
        for v in node.values():
            _find_category_ids(v, out)
    elif isinstance(node, list):
        for v in node:
            _find_category_ids(v, out)


# v8: cache of category_id -> (expires_at, (required, recommended)). Aspects
# change rarely; a day-long cache keeps publish fast and avoids hammering eBay.
_aspects_cache = {}


def _category_aspects(category_id):
    """Required/recommended item specifics for a category via the Taxonomy API.

    Returns (required, recommended) where each is a list of
    {"name": str, "values": [str]}. Returns (None, None) when eBay can't be
    reached or the response is unusable — callers must fall back to sending
    what they have instead of blocking the user.
    """
    cid = str(category_id).strip()
    if not cid:
        return None, None
    hit = _aspects_cache.get(cid)
    if hit and hit[0] > time.time():
        return hit[1]
    try:
        token = _app_token()
        resp = requests.get(
            EBAY_API + "/commerce/taxonomy/v1/category_tree/0/"
            "get_item_aspects_for_category",
            headers={"Authorization": "Bearer " + token},
            params={"category_id": cid},
            timeout=30)
    except (requests.RequestException, RuntimeError):
        return None, None
    if resp.status_code != 200:
        return None, None
    required, recommended = [], []
    try:
        for a in resp.json().get("aspects", []):
            if not isinstance(a, dict):
                continue
            name = str(a.get("localizedAspectName", "")).strip()
            if not name:
                continue
            con = a.get("aspectConstraint") or {}
            values = []
            for x in (a.get("aspectValues") or []):
                if isinstance(x, dict):
                    v = str(x.get("localizedValue", "")).strip()
                    if v:
                        values.append(v)
            item = {"name": name, "values": values[:50]}
            if con.get("aspectRequired"):
                required.append(item)
            else:
                recommended.append(item)
    except (AttributeError, TypeError, ValueError):
        return None, None
    _aspects_cache[cid] = (time.time() + 86400, (required, recommended))
    return required, recommended


def _clean_aspects(attributes):
    """Drop empty and 'Unknown' aspect values before sending to eBay.

    The AI is told to write "Unknown" instead of guessing; eBay gets no value
    at all in that case rather than the literal word "Unknown".
    """
    out = {}
    for k, v in (attributes or {}).items():
        k = str(k).strip()
        v = str(v).strip()
        if not k or not v or v.lower() == "unknown":
            continue
        out[k[:40]] = [v[:500]]
    return out


def _missing_required_aspects(attributes, required):
    """Names of required aspects with no real value. Pure function, tested."""
    missing = []
    for a in required or []:
        name = a.get("name", "") if isinstance(a, dict) else str(a)
        v = str((attributes or {}).get(name, "")).strip()
        if not v or v.lower() == "unknown":
            missing.append(name)
    return missing


def _parse_money(raw):
    """Parse a price string to a rounded float, or None if blank/invalid.

    Pure function, tested. Accepts "$25", "25.00", "1,299.99".
    """
    s = str(raw or "").strip().replace("$", "").replace(",", "")
    if not s:
        return None
    try:
        v = round(float(s), 2)
    except ValueError:
        return None
    return v if v > 0 else None


def _best_offer_terms(enabled, decline_raw, accept_raw, price_val):
    """Build listingPolicies.bestOfferTerms, or (None, error) on bad input.

    Pure function, tested. Field placement verified against eBay's Sell
    Inventory docs (via the Microsoft eBay connector reference, since
    developer.ebay.com is unreachable from here): bestOfferTerms lives
    INSIDE listingPolicies, with bestOfferEnabled / autoDeclinePrice /
    autoAcceptPrice. eBay requires auto-accept to be strictly greater than
    auto-decline, and both below the listing price. Toggle off (or all blank)
    returns (None, None): today's behavior, unchanged.
    """
    if not enabled:
        return None, None
    has_decline = bool(str(decline_raw or "").strip())
    has_accept = bool(str(accept_raw or "").strip())
    decline = _parse_money(decline_raw)
    accept = _parse_money(accept_raw)
    if has_decline and decline is None:
        return None, ("The auto-decline price isn't a valid amount. "
                      "Clear it or type a number like 25.00.")
    if has_accept and accept is None:
        return None, ("The auto-accept price isn't a valid amount. "
                      "Clear it or type a number like 40.00.")
    if decline is not None and decline >= price_val:
        return None, ("The auto-decline price has to be below your listing "
                      "price, or eBay will reject the listing.")
    if accept is not None and accept >= price_val:
        return None, ("The auto-accept price has to be below your listing "
                      "price, or eBay will reject the listing.")
    if decline is not None and accept is not None and accept <= decline:
        return None, ("The auto-accept price has to be higher than the "
                      "auto-decline price.")
    terms = {"bestOfferEnabled": True}
    if decline is not None:
        terms["autoDeclinePrice"] = {"currency": "USD",
                                     "value": f"{decline:.2f}"}
    if accept is not None:
        terms["autoAcceptPrice"] = {"currency": "USD",
                                    "value": f"{accept:.2f}"}
    return terms, None


@app.route("/api/ebay/status")
def api_ebay_status():
    toks = _load_ebay_tokens()
    return jsonify({
        "ebay_configured": _ebay_configured(),
        "connected": bool(toks and toks.get("refresh_token")),
        # v22: the sign-in name (RuName) currently in use, shown on the
        # connect area so it is never hidden again. A RuName is a public
        # identifier (it appears in every authorize URL), not a secret;
        # the Client Secret is never exposed here.
        "runame": EBAY_RUNAME,
        "policies_configured": bool(EBAY_FULFILLMENT_POLICY_ID and EBAY_PAYMENT_POLICY_ID
                                    and EBAY_RETURN_POLICY_ID),
        "policy_defaults": {
            "fulfillment": EBAY_FULFILLMENT_POLICY_ID,
            "payment": EBAY_PAYMENT_POLICY_ID,
            "return": EBAY_RETURN_POLICY_ID,
        },
        "conditions": [{"value": v, "label": l} for v, l in CONDITIONS],
    })


@app.route("/api/ebay/connect")
def api_ebay_connect():
    # The eBay sign-in must finish in the browser on the device running the app
    # (the RuName's auth accepted URL is the https localhost callback; the
    # app's WebView rewrites it to http so Flask receives it). Refuse
    # other hosts in plain words.
    host = (request.host or "").split(":")[0].strip().lower()
    if host not in ("localhost", "127.0.0.1", "::1"):
        return ("To connect eBay, open http://localhost:5050 in the browser on "
                "the device running the app. It will not work from another "
                "device."), 400
    if not _ebay_configured():
        return ("eBay is not set up yet. Add EBAY_CLIENT_ID, EBAY_CLIENT_SECRET, "
                "and EBAY_RUNAME to .env first (setup guide: Connect eBay)."), 400
    state = uuid.uuid4().hex
    _oauth_states[state] = time.time()
    params = urlencode({
        "client_id": EBAY_CLIENT_ID,
        "redirect_uri": EBAY_RUNAME,
        "response_type": "code",
        "scope": EBAY_SCOPES,
        "state": state,
    })
    return redirect(EBAY_AUTH_URL + "?" + params)


@app.route("/api/ebay/callback")
def api_ebay_callback():
    code = request.args.get("code", "")
    state = request.args.get("state", "")
    created = _oauth_states.pop(state, 0)
    if not code or not created or time.time() - created > 600:
        return redirect("/?ebay=error")
    try:
        resp = requests.post(
            EBAY_TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "Authorization": _basic_auth_header()},
            data={"grant_type": "authorization_code",
                  "code": code,
                  "redirect_uri": EBAY_RUNAME},
            timeout=30)
    except requests.RequestException:
        return redirect("/?ebay=error")
    if resp.status_code != 200:
        return redirect("/?ebay=error")
    body = resp.json()
    _save_ebay_tokens({
        "access_token": body["access_token"],
        "refresh_token": body.get("refresh_token", ""),
        "expires_at": time.time() + int(body.get("expires_in", 7200)) - 60,
    })
    return redirect("/?ebay=connected")


@app.route("/api/ebay/disconnect", methods=["POST"])
def api_ebay_disconnect():
    _clear_ebay_tokens()
    return jsonify({"ok": True})


@app.route("/api/ebay/category-suggest", methods=["POST"])
def api_ebay_category_suggest():
    body = request.get_json(force=True) or {}
    q = str(body.get("q", "")).strip()
    if not q:
        return jsonify({"error": "Type a few words of the title first."}), 400
    try:
        token = _app_token()
        resp = requests.post(
            EBAY_API + "/commerce/taxonomy/v1/category_tree/0/get_category_suggestions",
            headers={"Authorization": "Bearer " + token,
                     "Content-Type": "application/json"},
            json={"q": q[:100]},
            timeout=30)
    except requests.RequestException as exc:
        return jsonify({"error": f"Could not reach eBay: {exc}"}), 502
    if resp.status_code != 200:
        return jsonify({"error": _plain_ebay_error(resp)}), 502
    found, seen = [], set()
    _find_category_ids(resp.json(), found)
    suggestions = []
    for cid, cname in found:
        if cid not in seen:
            seen.add(cid)
            suggestions.append({"id": cid, "name": cname})
    return jsonify({"suggestions": suggestions[:12]})


@app.route("/api/ebay/category-aspects", methods=["POST"])
def api_ebay_category_aspects():
    """Item specifics eBay wants for a category (required + recommended).

    Used by the publish panel to render mandatory fields. Never errors hard:
    when eBay can't be reached the UI falls back to today's behavior.
    """
    body = request.get_json(force=True) or {}
    category_id = str(body.get("category_id", "")).strip()
    if not category_id:
        return jsonify({"error": "Pick an eBay category first."}), 400
    required, recommended = _category_aspects(category_id)
    if required is None:
        return jsonify({"required": [], "recommended": [],
                        "unavailable": True}), 200
    return jsonify({"required": required, "recommended": recommended,
                    "unavailable": False})


@app.route("/api/ebay/publish", methods=["POST"])
def api_ebay_publish():
    body = request.get_json(force=True) or {}
    draft_id = str(body.get("draft_id", ""))
    d = _load_draft(draft_id)
    if d is None:
        return jsonify({"error": "Draft not found."}), 404
    if d.get("ebay_listing_id"):
        return jsonify({
            "error": "This draft is already listed on eBay.",
            "listing_url": d.get("ebay_listing_url", ""),
            "listing_id": d.get("ebay_listing_id", ""),
        }), 409

    title = str(body.get("title", d.get("title", ""))).strip()[:80]
    description = str(body.get("description", d.get("description", ""))).strip()
    price_raw = str(body.get("price", d.get("price", ""))).strip()
    # v6: condition comes from the step-4 dropdown, falling back to the draft's
    # condition field (step 2). Empty means unset: we do NOT default to NEW,
    # because silently calling a used item "new" misleads the buyer. eBay gets
    # no condition value in that case; if the category requires one, eBay's own
    # error message says so in plain words.
    condition = str(body.get("condition", "") or d.get("condition", "")).strip()
    condition_details = str(body.get("condition_details", "")
                            or d.get("condition_details", "")).strip()[:1000]
    category_id = str(body.get("category_id", "")).strip()
    fulfillment_id = str(body.get("fulfillment_policy_id") or EBAY_FULFILLMENT_POLICY_ID).strip()
    payment_id = str(body.get("payment_policy_id") or EBAY_PAYMENT_POLICY_ID).strip()
    return_id = str(body.get("return_policy_id") or EBAY_RETURN_POLICY_ID).strip()
    # v8: step-4 aspect fields (required/recommended item specifics) arrive in
    # the body and overlay the draft's attributes.
    attributes = dict(d.get("attributes") or {})
    body_attrs = body.get("attributes")
    if isinstance(body_attrs, dict):
        for k, v in body_attrs.items():
            attributes[str(k)] = v

    if not title:
        return jsonify({"error": "The title is empty. Go back and write a title first."}), 400
    try:
        price_val = round(float(price_raw), 2)
        if price_val <= 0:
            raise ValueError()
    except (ValueError, TypeError):
        return jsonify({"error": "Set a price above $0 before listing."}), 400
    if not category_id:
        return jsonify({"error": "Pick an eBay category first (tap Suggest)."}), 400
    if not (fulfillment_id and payment_id and return_id):
        return jsonify({"error": "The three policy numbers are missing. Add them in .env "
                                 "or type them in the boxes (setup guide: Connect eBay)."}), 400
    # v9: best offer. The toggle and optional auto prices come from the
    # publish panel, falling back to the draft's saved values. Toggle off =
    # today's behavior, unchanged (no bestOfferTerms sent).
    best_offer = bool(body.get("best_offer", d.get("best_offer", False)))
    decline_raw = str(body.get("best_offer_auto_decline",
                               d.get("best_offer_auto_decline", "")) or "")
    accept_raw = str(body.get("best_offer_auto_accept",
                              d.get("best_offer_auto_accept", "")) or "")
    best_offer_terms, bo_error = _best_offer_terms(best_offer, decline_raw,
                                                   accept_raw, price_val)
    if bo_error:
        return jsonify({"error": bo_error}), 400
    if condition and condition not in {v for v, _ in CONDITIONS}:
        return jsonify({"error": "Pick a condition from the list."}), 400
    photos = d.get("photos") or []
    if not photos:
        return jsonify({"error": "This draft has no photos."}), 400

    try:
        token = _ebay_access_token()
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 400

    sku = "la" + d["id"]
    try:
        # 1. photos -> eBay-hosted URLs (Trading API picture hosting, no third party)
        image_urls = []
        for i, rel in enumerate(photos, 1):
            full = os.path.join(PHOTOS_DIR, rel)
            if os.path.isfile(full):
                image_urls.append(_ebay_upload_picture(full, token, f"{sku}-{i}"))
        if not image_urls:
            raise RuntimeError("No photos could be uploaded. Try again.")

        # 2. inventory item
        desc_html = html.escape(description).replace("\n", "<br>")
        # v8: normalize aspect names to eBay's exact spelling (case-insensitive
        # match) and enforce the category's required aspects. When eBay's
        # aspects lookup fails we skip validation and send what we have —
        # never block the user on our own API trouble.
        required, recommended = _category_aspects(category_id)
        if required is not None:
            exact = {a["name"].lower(): a["name"]
                     for a in (required + recommended)}
            normalized = {}
            for k, v in attributes.items():
                ks = str(k).strip()
                normalized[exact.get(ks.lower(), ks)] = v
            attributes = normalized
            missing = _missing_required_aspects(attributes, required)
            if missing:
                return jsonify({"error":
                                "eBay requires " + ", ".join(missing) +
                                " for this category. Fill them in under "
                                "'Details eBay requires' above, then try again."}), 400
        aspects = _clean_aspects(attributes)
        inventory_item = {
            "product": {
                "title": title,
                "description": desc_html or title,
                "imageUrls": image_urls,
                **({"aspects": aspects} if aspects else {}),
            },
            "availability": {"shipToLocationAvailability": {"quantity": 1}},
        }
        if condition:
            inventory_item["condition"] = condition
        if condition_details:
            inventory_item["conditionDescription"] = condition_details
        _ebay_call("PUT", f"/sell/inventory/v1/inventory_item/{sku}", token,
                   inventory_item)

        # 3. offer (reuse if one was already made for this draft)
        offer_id = d.get("ebay_offer_id")
        if not offer_id:
            offer = _ebay_call("POST", "/sell/inventory/v1/offer", token, {
                "sku": sku,
                "marketplaceId": "EBAY_US",
                "format": "FIXED_PRICE",
                "availableQuantity": 1,
                "categoryId": category_id,
                "listingDescription": desc_html or title,
                "listingPolicies": {
                    "fulfillmentPolicyId": fulfillment_id,
                    "paymentPolicyId": payment_id,
                    "returnPolicyId": return_id,
                    # v9: bestOfferTerms lives INSIDE listingPolicies per
                    # eBay's Sell Inventory docs. Omitted entirely when off.
                    **({"bestOfferTerms": best_offer_terms}
                       if best_offer_terms else {}),
                },
                "pricingSummary": {"price": {"currency": "USD",
                                             "value": f"{price_val:.2f}"}},
                "listing": {"listingDuration": "GTC"},
            })
            offer_id = offer.get("offerId")
            if not offer_id:
                raise RuntimeError("eBay made the offer but did not return an offer ID. "
                                   "Check Seller Hub; the listing may need finishing there.")

        # 4. publish
        published = _ebay_call(
            "POST", f"/sell/inventory/v1/offer/{offer_id}/publish", token)
        listing_id = str(published.get("listingId", ""))
        if not listing_id:
            raise RuntimeError("eBay accepted the listing but did not return a listing "
                               "number. Check Seller Hub.")
        listing_url = f"https://www.ebay.com/itm/{listing_id}"

        # 5. best-effort check that the listing is live (Sell Listing API)
        try:
            _ebay_call("GET", f"/sell/listing/v1/item/{listing_id}", token)
        except RuntimeError:
            pass  # publish already succeeded; the link below proves it
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 502

    d["ebay_offer_id"] = offer_id
    d["ebay_listing_id"] = listing_id
    d["ebay_listing_url"] = listing_url
    d["ebay_sku"] = sku
    _save_draft(d)
    return jsonify({"listing_id": listing_id, "listing_url": listing_url})


# ---------------------------------------------------------------- v1 routes

@app.route("/")
def index():
    return render_template("index.html",
                           app_version=APP_VERSION,
                           show_qr=bool(os.environ.get("PHONE_URL", "").strip()))


@app.route("/qr.png")
def qr_png():
    """QR code of the phone URL, for opening the app on another device."""
    url = os.environ.get("PHONE_URL", "").strip()
    if not url or not QRCODE_AVAILABLE:
        return ("", 404)
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_M)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png")


@app.route("/api/config")
def api_config():
    return jsonify({"gemini_configured": bool(GEMINI_KEY) and GEMINI_KEY != "PASTE_YOUR_KEY_HERE",
                    "max_photos": MAX_PHOTOS,
                    "ebay_configured": _ebay_configured(),
                    "ebay_connected": bool(_load_ebay_tokens())})


@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    files = request.files.getlist("photos")
    if not files:
        return jsonify({"error": "Pick at least one photo first."}), 400
    try:
        draft_id, rels = _store_uploads(files)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 400
    try:
        identified = _gemini_identify(rels)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 502
    now = datetime.now().isoformat(timespec="seconds")
    draft = {"id": draft_id, "created": now, "photos": rels, "price": "",
             "condition": "", "condition_details": "",
             # v9: best offer settings. Toggle is a bool; auto prices are kept
             # as raw strings in the draft and validated as money at publish.
             "best_offer": False, "best_offer_auto_decline": "",
             "best_offer_auto_accept": "",
             **identified}
    _save_draft(draft)
    return jsonify({"draft": draft})


@app.route("/api/drafts", methods=["GET"])
def api_draft_list():
    out = []
    for fn in sorted(os.listdir(DRAFTS_DIR), reverse=True):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(DRAFTS_DIR, fn), encoding="utf-8") as f:
                d = json.load(f)
            out.append({"id": d["id"], "title": d.get("title", "(untitled)"),
                        "updated": d.get("updated", ""),
                        "listed": bool(d.get("ebay_listing_id")),
                        "thumb": d["photos"][0] if d.get("photos") else None})
        except (OSError, KeyError, ValueError):
            continue
    return jsonify({"drafts": out})


@app.route("/api/drafts/<draft_id>", methods=["GET"])
def api_draft_get(draft_id):
    d = _load_draft(draft_id)
    if d is None:
        return jsonify({"error": "Draft not found."}), 404
    return jsonify({"draft": d})


@app.route("/api/drafts/<draft_id>", methods=["PUT"])
def api_draft_save(draft_id):
    d = _load_draft(draft_id)
    if d is None:
        return jsonify({"error": "Draft not found."}), 404
    body = request.get_json(force=True) or {}
    for key in ("title", "description", "category", "price", "identification_notes",
                "condition_details"):
        if key in body:
            d[key] = str(body[key])[:5000]
    # v6: condition must be one of eBay's condition values (or empty = unset).
    # Invalid values are ignored rather than saved, so a bad write can never
    # corrupt the draft; the publish step validates again.
    if "condition" in body:
        c = str(body["condition"]).strip()
        if not c or c in {v for v, _ in CONDITIONS}:
            d["condition"] = c
    # v9: best offer settings. The toggle is stored as a bool; the auto
    # prices as short raw strings (validated as money at publish time).
    if "best_offer" in body:
        d["best_offer"] = bool(body["best_offer"])
    for key in ("best_offer_auto_decline", "best_offer_auto_accept"):
        if key in body:
            d[key] = str(body[key])[:20]
    if "attributes" in body and isinstance(body["attributes"], dict):
        d["attributes"] = {str(k)[:80]: str(v)[:200] for k, v in body["attributes"].items()}
    _save_draft(d)
    return jsonify({"draft": d})


@app.route("/api/drafts/<draft_id>", methods=["DELETE"])
def api_draft_delete(draft_id):
    d = _load_draft(draft_id)
    if d is None:
        return jsonify({"error": "Draft not found."}), 404
    os.remove(_draft_path(draft_id))
    return jsonify({"ok": True})


@app.route("/api/drafts/<draft_id>/reanalyze", methods=["POST"])
def api_reanalyze(draft_id):
    d = _load_draft(draft_id)
    if d is None or not d.get("photos"):
        return jsonify({"error": "Draft not found or has no photos."}), 404
    try:
        identified = _gemini_identify(d["photos"])
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 502
    d.update(identified)  # keeps the price he already typed
    _save_draft(d)
    return jsonify({"draft": d})


@app.route("/api/drafts/<draft_id>/photos.zip", methods=["GET"])
def api_photos_zip(draft_id):
    d = _load_draft(draft_id)
    if d is None or not d.get("photos"):
        return jsonify({"error": "Draft not found or has no photos."}), 404
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, rel in enumerate(d["photos"], 1):
            full = os.path.join(PHOTOS_DIR, rel)
            if os.path.isfile(full):
                zf.write(full, f"photo-{i}.jpg")
    buf.seek(0)
    return send_file(buf, mimetype="application/zip",
                     as_attachment=True, download_name=f"listing-{draft_id}-photos.zip")


@app.route("/photos/<path:rel>")
def serve_photo(rel):
    return send_from_directory(PHOTOS_DIR, rel)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
