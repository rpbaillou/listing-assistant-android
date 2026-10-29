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
import re
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
# v24: one saved "usual settings" preset (settings, not secrets — never in
# .env, which holds keys). Lives with the drafts in private storage.
PRESET_FILE = os.path.join(DATA_DIR, "preset.json")
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
# v30 (audit F2): optional pin for the merchant location key eBay requires
# on the offer at publish time. When unset, the app uses the first
# location on the account (GET /sell/inventory/v1/location) instead.
# Only set this if the seller has several locations and wants a specific one.
EBAY_MERCHANT_LOCATION_KEY = os.environ.get("EBAY_MERCHANT_LOCATION_KEY", "").strip()

EBAY_AUTH_URL = "https://auth.ebay.com/oauth2/authorize"
EBAY_TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
EBAY_API = "https://api.ebay.com"
# v29: eBay's Trading API call UploadSiteHostedPictures was DECOMMISSIONED
# Sept 30, 2026 (deprecated Nov 6, 2025) — it now answers a plain gateway
# 404 for every format, so no Trading upload path remains. Photos go
# through eBay's Media API instead, authorized with the same OAuth user
# token; the Media methods require the sell.inventory scope, which the
# token below already holds (base api_scope alone is NOT enough).
# v32: sell.account lets the app READ the seller's fulfillment (shipping)
# policies itself (Account API getFulfillmentPolicy/list) so a bad or
# calculated shipping policy is caught before publish instead of failing
# with 25007. eBay's method pages list sell.account as a standard
# authorization-code scope (no Developer Central approval step found);
# a fresh consent is still required, because a refresh token can never
# gain a scope it was not granted with.
EBAY_SCOPES = ("https://api.ebay.com/oauth/api_scope "
               "https://api.ebay.com/oauth/api_scope/sell.inventory "
               "https://api.ebay.com/oauth/api_scope/sell.listing "
               "https://api.ebay.com/oauth/api_scope/sell.account")

# v32: plain words when the stored token was granted before sell.account
# was added (a refresh can never add a scope, and the policy read then
# answers 403). Shown instead of a bare "expired" or scope error, and
# never wipes anything but the unusable tokens (the reconnect replaces
# them). The button in the app is labeled "Connect eBay".
SCOPE_RECONNECT_MSG = (
    "The app needs one new permission to read your shipping policies. "
    "Tap Connect eBay and approve it.")

# eBay Media API (production only; not supported in Sandbox).
EBAY_MEDIA_API = "https://apim.ebay.com/commerce/media/v1_beta"

MAX_PHOTOS = 24
# v30 (audit F11): eBay's documented Media example caps photos at 12 MB.
MAX_FILE_MB = 12

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_PHOTOS * MAX_FILE_MB * 1024 * 1024

_oauth_states = {}      # state -> created epoch (single use, 10 min)
_app_token_cache = {}   # {"token":..., "expires_at":...} for taxonomy calls

CONDITIONS = [
    ("NEW", "New"),
    ("USED_EXCELLENT", "Used"),
    ("USED_VERY_GOOD", "Very Good"),
    ("USED_GOOD", "Good"),
    ("USED_ACCEPTABLE", "Acceptable"),
    ("FOR_PARTS_OR_NOT_WORKING", "For parts or not working"),
]

# v35: numeric condition ID -> Inventory API ConditionEnum, from eBay's
# official ID table (docs/ebay-listing-requirements.md). There is NO
# plain USED enum: a category that takes "Used" takes USED_EXCELLENT
# (3000). The old "Used - excellent" label for that value was
# misleading, so the static list above now labels it "Used".
CONDITION_ID_TO_ENUM = {
    1000: "NEW", 1500: "NEW_OTHER", 1750: "NEW_WITH_DEFECTS",
    2000: "CERTIFIED_REFURBISHED", 2010: "EXCELLENT_REFURBISHED",
    2020: "VERY_GOOD_REFURBISHED", 2030: "GOOD_REFURBISHED",
    2500: "SELLER_REFURBISHED", 2750: "LIKE_NEW",
    2990: "PRE_OWNED_EXCELLENT", 3000: "USED_EXCELLENT",
    3010: "PRE_OWNED_FAIR", 4000: "USED_VERY_GOOD",
    5000: "USED_GOOD", 6000: "USED_ACCEPTABLE",
    7000: "FOR_PARTS_OR_NOT_WORKING",
}

# Plain display names for every enum (official table's display names),
# used for the category-aware Condition dropdown and for naming the
# allowed choices in compliance messages.
CONDITION_DISPLAY = {
    "NEW": "New", "NEW_OTHER": "New other (see details)",
    "NEW_WITH_DEFECTS": "New with defects",
    "CERTIFIED_REFURBISHED": "Certified Refurbished",
    "EXCELLENT_REFURBISHED": "Excellent - Refurbished",
    "VERY_GOOD_REFURBISHED": "Very Good - Refurbished",
    "GOOD_REFURBISHED": "Good - Refurbished",
    "SELLER_REFURBISHED": "Seller refurbished",
    "LIKE_NEW": "Like New",
    "PRE_OWNED_EXCELLENT": "Pre-owned - Excellent",
    "USED_EXCELLENT": "Used", "PRE_OWNED_FAIR": "Pre-owned - Fair",
    "USED_VERY_GOOD": "Very Good", "USED_GOOD": "Good",
    "USED_ACCEPTABLE": "Acceptable",
    "FOR_PARTS_OR_NOT_WORKING": "For parts or not working",
}

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


# ---------------------------------------------------------------- v24: preset

PRESET_KEYS = ("condition", "category_id", "fulfillment_policy_id",
               "payment_policy_id", "return_policy_id", "best_offer",
               "best_offer_auto_decline", "best_offer_auto_accept")


def _load_preset():
    """One saved 'usual settings' preset, or None. Never crashes on a
    missing or corrupt file — no preset just means no prefill."""
    if not os.path.isfile(PRESET_FILE):
        return None
    try:
        with open(PRESET_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return {k: data.get(k) for k in PRESET_KEYS}


def _clean_preset(body):
    """Keep only known preset keys, with light validation. Invalid values
    are dropped, never saved, so a bad write can never corrupt it."""
    out = {}
    cond = str(body.get("condition", "") or "").strip()
    if cond in {v for v, _ in CONDITIONS}:
        out["condition"] = cond
    cat = str(body.get("category_id", "") or "").strip()
    if cat:
        out["category_id"] = cat[:20]
    for key in ("fulfillment_policy_id", "payment_policy_id", "return_policy_id"):
        v = str(body.get(key, "") or "").strip()
        if v:
            out[key] = v[:30]
    out["best_offer"] = bool(body.get("best_offer", False))
    for key in ("best_offer_auto_decline", "best_offer_auto_accept"):
        v = str(body.get(key, "") or "").strip()
        if v:
            out[key] = v[:20]
    return out


def _save_preset(data):
    with open(PRESET_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return data


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
        # v32: eBay's own Authorization doc says a refresh scope must be
        # equal to or a subset of the consent scopes — so a refresh that
        # now asks for sell.account (added after the old consent) fails
        # with a scope error, not an expiry. Name that plainly instead of
        # calling the sign-in "expired": the fix is one fresh consent.
        try:
            err_body = resp.json()
        except ValueError:
            err_body = {}
        err_text = ""
        if isinstance(err_body, dict):
            err_text = str(err_body.get("error_description", "")
                           or err_body.get("error", ""))
        if "scope" in err_text.lower():
            # v33: the scope failure must carry the typed signal, not a
            # bare RuntimeError — otherwise the endpoints return the
            # message without the flag and the app hides the Connect
            # button (the live v1.31 gap). The class is defined below
            # (line ~1109); Python resolves it at call time.
            raise _NeedsScopeReconnect(SCOPE_RECONNECT_MSG)
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
    """Turn an eBay API error response into plain words. v30 (audit F6):
    eBay's errorId is included in EVERY surfaced message, so the next
    failure names its code instead of arriving as a bare sentence. Keyword
    rewrites apply only on a tight match: aspect/specifics errors often
    mention "category", "condition", or "image" in passing, so those words
    alone must not trigger a rewrite — otherwise eBay's message + errorId
    is shown verbatim."""
    try:
        body = resp.json()
        errs = body.get("errors") or []
        msg = errs[0].get("message", "") if errs else ""
        err_id = str(errs[0].get("errorId", "")) if errs else ""
    except ValueError:
        msg, err_id = resp.text[:300], ""
    code = f" (error {err_id})" if err_id else ""
    low = (msg + " " + err_id).lower()
    if err_id == "190013":
        return ("Your eBay sign-in is missing a permission eBay needs for "
                "this action" + code + ". Details: " + msg[:160])
    if "business polic" in low or err_id == "25710":
        return ("eBay needs your shop's payment, shipping, and return policies on this "
                "listing. Check the three policy numbers in your .env (setup guide: Connect eBay), "
                "or pick different ones." + code)
    if "opt" in low and "program" in low:
        return ("Your eBay seller account needs business policies turned on before the "
                "app can list. In Seller Hub go to Account, then Business policies, and "
                "opt in. Then try again." + code)
    aspecty = ("aspect" in low or "specific" in low)
    # v35: condition is checked BEFORE category. Error 25021 ("provided
    # condition id is invalid for this category") contains BOTH words,
    # so the old category-first order mislabeled a condition problem as
    # "eBay did not accept that category. Tap Suggest" — wrong advice
    # that sent the seller to re-pick a fine category (live v1.33).
    if err_id == "25021":
        return ("That condition choice isn't allowed for this category. "
                "Pick a different condition." + code +
                ". Details: " + msg[:160])
    if "condition" in low and not aspecty:
        return ("eBay needs more condition detail for this category. Try a different "
                "condition choice. Details: " + msg[:160] + code)
    if "categor" in low and not aspecty:
        return ("eBay did not accept that category. Tap Suggest next to the category "
                "field and pick one from the list. Details: " + msg[:160] + code)
    if "image" in low and not aspecty:
        return ("eBay rejected a photo. Make sure every photo is a normal JPG under "
                "12 MB. Details: " + msg[:160] + code)
    if "title" in low and not aspecty:
        return ("eBay did not accept the title. Keep it under 80 characters with no "
                "strange symbols. Details: " + msg[:160] + code)
    if resp.status_code in (401, 403):
        return ("eBay refused the sign-in for this action. Tap Connect eBay again in "
                "this browser, then retry." + code)
    return ("eBay said" + (f" (error {err_id})" if err_id else "") + ": " +
            (msg[:220] if msg else f"error {resp.status_code}"))


class _EbayApiError(RuntimeError):
    """An eBay HTTP error with its status + errorId attached (v32).
    Still a RuntimeError, so every existing handler keeps catching it;
    new code (the v32 policy check) can tell a 403 (token lacks the
    sell.account scope) from a 404 (policy ID not found) without
    sniffing message text. Network failures stay plain RuntimeError
    (no status), so callers must use getattr(exc, "status", None)."""

    def __init__(self, message, status=None, error_id=""):
        super().__init__(message)
        self.status = status
        self.error_id = error_id


def _ebay_error_id(resp):
    """Best-effort errorId from an eBay error reply, "" when unparseable.
    Used only to attach to _EbayApiError; the surfaced message text
    still comes from _plain_ebay_error, unchanged."""
    try:
        errs = resp.json().get("errors") or []
        return str(errs[0].get("errorId", "")) if errs else ""
    except (ValueError, AttributeError):
        return ""


def _ebay_call(method, path, token, payload=None):
    try:
        resp = requests.request(method, EBAY_API + path,
                                headers={"Authorization": "Bearer " + token,
                                         "Content-Type": "application/json",
                                         # v30 (audit F1): eBay requires
                                         # Content-Language on Inventory calls.
                                         "Content-Language": "en-US"},
                                json=payload, timeout=60)
    except requests.RequestException as exc:
        raise RuntimeError(f"Could not reach eBay: {exc}")
    if resp.status_code == 401:
        _clear_ebay_tokens()
        raise _EbayApiError(
            "Your eBay sign-in stopped working. Tap Connect eBay again in this browser.",
            status=401, error_id=_ebay_error_id(resp))
    if resp.status_code == 403:
        # v30 (audit F7): do NOT clear on 403 — a scope error is not fixed
        # by reconnecting with the same scopes. Surface eBay's message +
        # errorId (including the 190013 permission naming in
        # _plain_ebay_error) instead.
        raise _EbayApiError(_plain_ebay_error(resp),
                            status=403, error_id=_ebay_error_id(resp))
    if resp.status_code >= 400:
        raise _EbayApiError(_plain_ebay_error(resp),
                            status=resp.status_code,
                            error_id=_ebay_error_id(resp))
    if resp.status_code == 204 or not resp.text.strip():
        return {}
    try:
        return resp.json()
    except ValueError:
        return {}


def _resp_bytes(resp):
    """Raw reply body as bytes. ElementTree parses bytes correctly even
    with a BOM or an encoding declaration; parsing str can fail on
    exactly those quirks, which is what hid eBay's real reply here."""
    raw = resp.content
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw)
    if isinstance(raw, str):
        return raw.encode("utf-8", "replace")
    return b""


def _media_api_error(resp, token):
    """Error text for the Media API. v30 (audit F8): extract eBay's message
    + errorId when parseable, so the failure names its code; fall back to
    the raw snippet only when unparseable. The reply never contains our
    token or photo data (those went in the request, not the reply), but
    the token is redacted from the fallback snippet anyway so it can never
    leak into a surfaced message."""
    try:
        body = resp.json()
        errs = body.get("errors") or []
        msg = errs[0].get("message", "") if errs else ""
        err_id = str(errs[0].get("errorId", "")) if errs else ""
    except ValueError:
        msg, err_id = "", ""
    if msg or err_id:
        code = f" (error {err_id})" if err_id else ""
        if msg:
            return f"eBay photo service said{code}: {msg[:220]}"
        return (f"eBay photo service answered with error {resp.status_code}"
                f"{code}.")
    text = _resp_bytes(resp).decode("utf-8", "replace").strip()
    if token:
        text = text.replace(token, "[removed]")
    snippet = (": " + text[:300]) if text else ""
    return (f"eBay photo service answered with error {resp.status_code}"
            f"{snippet}.")


def _picture_content_type(raw, photo_path):
    """Detect an image Content-Type from magic bytes, falling back to
    the file extension, then image/jpeg."""
    if raw[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    if raw[:2] == b"BM":
        return "image/bmp"
    if raw[:4] in (b"II*\x00", b"MM\x00*"):
        return "image/tiff"
    ext = os.path.splitext(photo_path)[1].lower()
    return {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
            ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
            ".tif": "image/tiff", ".tiff": "image/tiff"}.get(ext, "image/jpeg")


def _ebay_upload_picture(photo_path, token):
    """Upload one photo to eBay's Media API (create_image_from_file) and
    return the EPS imageUrl for the listing's imageUrls.

    This replaces the decommissioned Trading API call
    UploadSiteHostedPictures (dead Sept 30, 2026): that call answers a
    plain gateway 404 for every request format now, so no Trading upload
    path remains. Request: POST apim.ebay.com/commerce/media/v1_beta/
    image/create_image_from_file, Bearer user token (sell.inventory
    scope), multipart/form-data with form key "image" = the photo's raw
    bytes, no JSON payload. Response: 201 Created; the create body holds
    imageUrl + expirationDate, and imageUrl is the address that goes in
    the listing — NEVER the Location header URI or the bare image_id
    (those are not listing image URLs). If the create body has no
    imageUrl, fall back to GET image/{id} from the Location header and
    use that response's imageUrl. If both fail, raise naming what was
    missing — never proceed with a bad URL.

    Error handling (v25 pattern, adapted): one automatic retry on
    5xx/empty-body only, never on 4xx or a parsed error; 401/403 clears
    the tokens and tells him to reconnect (a 403 naming error 190013
    means the token lacks the sell.inventory photo permission, said
    plainly). Photo bytes and the token never appear in a message."""
    with open(photo_path, "rb") as f:
        raw = f.read()
    filename = os.path.basename(photo_path) or "photo.jpg"
    content_type = _picture_content_type(raw, photo_path)
    for attempt in range(2):
        try:
            resp = requests.post(
                EBAY_MEDIA_API + "/image/create_image_from_file",
                headers={"Authorization": "Bearer " + token},
                files={"image": (filename, raw, content_type)},
                timeout=120)
        except requests.RequestException as exc:
            raise RuntimeError(f"Could not reach eBay photo service: {exc}")
        if resp.status_code >= 500:
            if attempt == 0:
                continue
            raise RuntimeError(_media_api_error(resp, token))
        if resp.status_code in (401, 403):
            _clear_ebay_tokens()
            detail = _resp_bytes(resp).decode("utf-8", "replace")
            if resp.status_code == 403 and ("190013" in detail
                                            or "nauthorized" in detail.lower()):
                raise RuntimeError(
                    "Your eBay sign-in is missing the photo permission "
                    "(sell.inventory). Tap Connect eBay again in this "
                    "browser to grant it.")
            raise RuntimeError(
                "Your eBay sign-in stopped working. "
                "Tap Connect eBay again in this browser.")
        if resp.status_code == 201:
            try:
                body = resp.json()
            except ValueError:
                body = {}
            url = (body.get("imageUrl") or "").strip() if isinstance(body, dict) else ""
            if url:
                return url
            # Fallback: resolve the image id from the Location header.
            loc = (resp.headers or {}).get("Location", "") or ""
            image_id = loc.rstrip("/").rsplit("/", 1)[-1] if loc else ""
            if image_id:
                try:
                    got = requests.get(
                        EBAY_MEDIA_API + "/image/" + image_id,
                        headers={"Authorization": "Bearer " + token},
                        timeout=60)
                except requests.RequestException:
                    got = None
                if got is not None and got.status_code == 200:
                    try:
                        got_body = got.json()
                    except ValueError:
                        got_body = {}
                    url = (got_body.get("imageUrl") or "").strip() \
                        if isinstance(got_body, dict) else ""
                    if url:
                        return url
            raise RuntimeError(
                "eBay accepted the photo but did not return its address. "
                "Try again.")
        # Any other status: a 4xx is a real rejection (never retried); an
        # empty body is treated as transient and retried once.
        if not _resp_bytes(resp).strip():
            if attempt == 0:
                continue
        raise RuntimeError(_media_api_error(resp, token))
    raise RuntimeError("eBay photo service did not answer. Try again.")


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


_conditions_cache = {}


def _category_conditions(category_id):
    """v35: conditions a category allows, via the Metadata API's
    getItemConditionPolicies (official: supported condition values vary
    by category; the call returns numeric IDs that map to ConditionEnum
    values via CONDITION_ID_TO_ENUM).

    Returns (allowed_enums, condition_required) where allowed_enums is
    a list of ConditionEnum strings in official-table order. Returns
    (None, None) when eBay can't be reached, the token is rejected, or
    the response is unusable — callers must fall back to the static
    list + a plain hint instead of blocking the user. The app (client
    credentials) token is used, like the Taxonomy aspects fetch; the
    method's OpenAPI security block is the base scope, which that token
    carries. Cached 24 hours per category, like aspects.
    """
    cid = str(category_id).strip()
    if not cid:
        return None, None
    hit = _conditions_cache.get(cid)
    if hit and hit[0] > time.time():
        return hit[1]
    try:
        token = _app_token()
        resp = requests.get(
            EBAY_API + "/sell/metadata/v1/marketplace/EBAY_US/"
            "get_item_condition_policies",
            headers={"Authorization": "Bearer " + token},
            params={"filter": "categoryIds:" + cid},
            timeout=30)
    except (requests.RequestException, RuntimeError):
        return None, None
    if resp.status_code != 200:
        return None, None
    try:
        body = resp.json()
        policies = body.get("itemConditionPolicies") or []
        entry = None
        for p in policies:
            if isinstance(p, dict) and str(p.get("categoryId", "")).strip() == cid:
                entry = p
                break
        if entry is None:
            return None, None
        enums = []
        for c in (entry.get("itemConditions") or []):
            if not isinstance(c, dict):
                continue
            try:
                num = int(c.get("conditionId"))
            except (TypeError, ValueError):
                continue
            enum = CONDITION_ID_TO_ENUM.get(num)
            if enum and enum not in enums:
                enums.append(enum)
        if not enums:
            return None, None
        result = (enums, bool(entry.get("itemConditionRequired")))
    except (AttributeError, TypeError, ValueError):
        return None, None
    _conditions_cache[cid] = (time.time() + 86400, result)
    return result


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
        resp = requests.get(
            EBAY_API + "/commerce/taxonomy/v1/category_tree/0/get_category_suggestions",
            headers={"Authorization": "Bearer " + token},
            params={"q": q[:100]},
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

    Used by the listing page to render mandatory fields. Never errors hard:
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


class _PublishValidationError(RuntimeError):
    """A publish failure the user can fix (missing field, bad value).
    The single-publish wrapper maps these to 400; anything else is 502."""


class _NeedsShipFromAddress(RuntimeError):
    """The account has no inventory location and the caller supplied no
    ship-from address to create one with. The publish wrappers map this
    to a needs_ship_from signal so the app can show the address form
    instead of a dead-end error — never a 400/502 failure."""


# v31: the merchant location key used when the app creates the shop
# location from the address the seller types once in the app. eBay caps
# the key at 36 characters (per its own Inventory docs) and the key can
# never be changed once set, so a short, stable, generic key is used.
# The key is not personal data; the address itself is never stored here.
SHIP_FROM_LOCATION_KEY = "home"


class _EbayAuthError(RuntimeError):
    """The eBay sign-in itself failed (not connected, expired). The
    wrapper maps these to 400, exactly like the old publish code did
    for its token-fetch step."""


def _validate_ship_from(raw):
    """Clean a ship-from address the seller typed in the app into eBay's
    Address shape. Country is always US (the app only lists on EBAY_US),
    whatever the caller passed. Raises _PublishValidationError on any
    missing or malformed field, in plain words. The address is never
    logged, saved to a file, or put in a draft, preset, or .env — eBay
    holds the location after creation, and later publishes find it via
    GET, so the app needs the address for this one call only."""
    if not isinstance(raw, dict):
        raise _PublishValidationError(
            "Fill in the ship-from address first.")
    street = str(raw.get("street", "") or "").strip()
    city = str(raw.get("city", "") or "").strip()
    state = str(raw.get("state", "") or "").strip().upper()
    zip_code = re.sub(r"\s+", "", str(raw.get("zip", "") or ""))
    if not street or not city or not state or not zip_code:
        raise _PublishValidationError(
            "Fill in all four: street, city, state, ZIP code.")
    if not re.fullmatch(r"[A-Z]{2}", state):
        raise _PublishValidationError(
            "Use the 2-letter state code, e.g. TX.")
    if not re.fullmatch(r"\d{5}(-\d{4})?", zip_code):
        raise _PublishValidationError(
            "Enter a 5-digit ZIP code.")
    return {"addressLine1": street, "city": city,
            "stateOrProvince": state, "postalCode": zip_code,
            "country": "US"}


def _create_merchant_location(token, address):
    """Create the shop location (v31, eBay createInventoryLocation:
    POST /sell/inventory/v1/location/{merchantLocationKey}). Per eBay's
    own Inventory docs, only the seller-defined key and a physical
    location are required at first creation, and a WAREHOUSE location
    (traditional shipping, which is this seller's case) needs postalCode
    + country, or city + stateOrProvince + country — the form supplies
    all of them. Locations are enabled by default, so no status field is
    sent. Success is 204 No Content, which _ebay_call already maps to {}.
    If the create fails but a location now exists (the create landed
    while its reply was lost, or the key already exists), use that
    location instead of failing; otherwise re-raise eBay's own
    message + errorId."""
    body = {"name": "Home",
            "locationTypes": ["WAREHOUSE"],
            "location": {"address": address}}
    try:
        _ebay_call("POST",
                   f"/sell/inventory/v1/location/{SHIP_FROM_LOCATION_KEY}",
                   token, body)
    except RuntimeError as exc:
        try:
            existing = _ebay_call("GET", "/sell/inventory/v1/location",
                                  token)
        except RuntimeError:
            raise exc
        locations = existing.get("locations") \
            if isinstance(existing, dict) else None
        if locations:
            key = (locations[0].get("merchantLocationKey") or "").strip()
            if key:
                return key
        raise exc
    return SHIP_FROM_LOCATION_KEY


def _resolve_merchant_location_key(token, ship_from=None):
    """Merchant location key for the offer (v30, audit F2; v31 creation).
    eBay requires an inventory location to publish an offer. Pin:
    EBAY_MERCHANT_LOCATION_KEY in .env wins (for sellers with several
    locations); otherwise use the first location on the account. When
    the list is empty and the caller supplied a ship-from address typed
    once in the app, create the location and return its key; without an
    address, raise _NeedsShipFromAddress so the app shows the form.
    Never invent an address, never proceed to publish without one."""
    if EBAY_MERCHANT_LOCATION_KEY:
        return EBAY_MERCHANT_LOCATION_KEY
    body = _ebay_call("GET", "/sell/inventory/v1/location", token)
    locations = body.get("locations") if isinstance(body, dict) else None
    if locations:
        key = (locations[0].get("merchantLocationKey") or "").strip()
        if key:
            return key
    if ship_from is not None:
        address = _validate_ship_from(ship_from)
        return _create_merchant_location(token, address)
    raise _NeedsShipFromAddress(
        "No shop location found on your eBay account yet. Enter the "
        "address you ship from below and the app will create one, "
        "then list.")


class _NeedsScopeReconnect(RuntimeError):
    """The stored token was granted before sell.account was added, so
    the Account API policy read answers 403. The fix is one fresh
    consent (a refresh can never add a scope). The wrappers map this to
    a needs_scope_reconnect signal + the plain message — never a blind
    token wipe (v30 audit F7: a 403 surfaces, it does not clear)."""


class _NeedsFulfillmentPolicyChoice(RuntimeError):
    """The shipping policy ID the publish would send is unusable (not
    found, or for another marketplace, or offering no shippable domestic
    service). Carries the account's current EBAY_US fulfillment policies
    (plain dicts: id, name, cost_type, marketplace) so the app can offer
    a one-tap pick instead of a dead-end error. Policy IDs are seller
    config, not secrets — the status endpoint already exposes them.

    v35: also carries `problems` — compliance problems found alongside
    the unusable policy (e.g. a bad condition), so the pick screen can
    show them too instead of surfacing one wall at a time.
    """

    def __init__(self, message, policies, problems=None):
        super().__init__(message)
        self.policies = policies
        self.problems = list(problems or [])


def _policy_cost_type(policy):
    """'CALCULATED', 'FLAT_RATE', 'NOT_SPECIFIED', or None, from a
    FulfillmentPolicy dict (official Account API type: each
    ShippingOption's costType says whether flat-rate or calculated
    shipping is used). Only the DOMESTIC option decides: the app only
    lists in the US, so an international option never stands in for a
    missing domestic service. None means the policy has no domestic
    option at all."""
    if not isinstance(policy, dict):
        return None
    opts = policy.get("shippingOptions") or []
    for opt in opts:
        if isinstance(opt, dict) \
                and str(opt.get("optionType", "")).upper() == "DOMESTIC":
            ct = str(opt.get("costType", "") or "").strip().upper()
            if ct:
                return ct
    return None


def _policy_pick_entry(policy):
    """One pick-list row for a FulfillmentPolicy dict."""
    return {"id": str(policy.get("fulfillmentPolicyId", "") or "").strip(),
            "name": str(policy.get("name", "") or "").strip(),
            "cost_type": _policy_cost_type(policy),
            "marketplace": str(policy.get("marketplaceId", "") or "").strip()}


def _read_fulfillment_policy(token, policy_id):
    """Return (matched_policy_or_None, current_EBAY_US_policies).

    Single read first (GET /sell/account/v1/fulfillment_policy/{id});
    on any miss (404 or an empty body) fall back to the EBAY_US list
    (GET .../fulfillment_policy?marketplace_id=EBAY_US) and match by ID
    there. A 403 on either call means the token lacks sell.account and
    raises _NeedsScopeReconnect. Network failures propagate untouched
    (never misread as "policy not found")."""
    try:
        body = _ebay_call(
            "GET", f"/sell/account/v1/fulfillment_policy/{policy_id}", token)
        if isinstance(body, dict) and body.get("fulfillmentPolicyId"):
            return body, None
    except _EbayApiError as exc:
        if exc.status == 403:
            raise _NeedsScopeReconnect(SCOPE_RECONNECT_MSG)
        # Any other single-read failure (404 included) falls through
        # to the list, which is the authoritative "does it exist" check.
    listing = None
    try:
        listing = _ebay_call(
            "GET",
            "/sell/account/v1/fulfillment_policy?marketplace_id=EBAY_US",
            token)
    except _EbayApiError as exc:
        if exc.status == 403:
            raise _NeedsScopeReconnect(SCOPE_RECONNECT_MSG)
        raise
    policies = []
    if isinstance(listing, dict):
        for p in (listing.get("fulfillmentPolicies") or []):
            if isinstance(p, dict):
                policies.append(p)
    for p in policies:
        if str(p.get("fulfillmentPolicyId", "")).strip() == policy_id:
            return p, policies
    return None, policies


def _validate_package(raw):
    """Clean a package weight + size block into eBay's
    packageWeightAndSize shape (official Inventory API model:
    dimensions {height, length, width, unit INCH} + weight {value,
    unit POUND/OUNCE}). Every field is required when any is given — a
    half-filled block is worse than none for calculated shipping — and
    nothing is ever defaulted or invented: the weight must be real.
    Generous sanity caps (150 lbs, 108 in) catch unit typos (grams typed
    as pounds) without blocking any real print parcel."""
    if not isinstance(raw, dict):
        raise _PublishValidationError(
            "Enter the package weight and dimensions.")
    weight_raw = str(raw.get("weight_value", "") or "").strip()
    unit = str(raw.get("weight_unit", "") or "").strip().upper()
    dims = {}
    for key in ("length", "width", "height"):
        dims[key] = str(raw.get(key, "") or "").strip()
    if not weight_raw or unit not in ("POUND", "OUNCE") \
            or not all(dims.values()):
        raise _PublishValidationError(
            "Enter the package weight (with pounds or ounces) and all "
            "three dimensions in inches.")
    try:
        weight = float(weight_raw)
        length = float(dims["length"])
        width = float(dims["width"])
        height = float(dims["height"])
    except ValueError:
        raise _PublishValidationError(
            "Package weight and dimensions must be numbers.")
    if not (weight > 0 and length > 0 and width > 0 and height > 0):
        raise _PublishValidationError(
            "Package weight and dimensions must be above zero.")
    pounds = weight if unit == "POUND" else weight / 16.0
    if pounds > 150 or length > 108 or width > 108 or height > 108:
        raise _PublishValidationError(
            "That weight or size looks too large — check the numbers.")
    return {"dimensions": {"height": height, "length": length,
                           "width": width, "unit": "INCH"},
            "weight": {"value": weight, "unit": unit}}


def _check_fulfillment_policy(token, fulfillment_id, package):
    """Policy gate for a publish (v32, runs before offer creation).

    Returns (cost_type, package_block_or_None) where package_block is
    sent on the inventory item only for CALCULATED policies. Raises:
    _NeedsScopeReconnect (token lacks sell.account),
    _NeedsFulfillmentPolicyChoice (ID stale, wrong marketplace, or no
    shippable domestic service — with the current policies to pick
    from), or _PublishValidationError (CALCULATED policy with no package
    block, or a half-filled block). FLAT_RATE proceeds with no block
    (a flat price never uses weight, so an entered block is validated
    for typos but not sent)."""
    policy, policies = _read_fulfillment_policy(token, fulfillment_id)
    if policy is None:
        raise _NeedsFulfillmentPolicyChoice(
            "That shipping policy number is no longer on your eBay "
            "account. Pick one below.",
            [_policy_pick_entry(p) for p in (policies or [])])
    marketplace = str(policy.get("marketplaceId", "") or "").strip()
    if marketplace and marketplace != "EBAY_US":
        raise _NeedsFulfillmentPolicyChoice(
            f"That shipping policy is for {marketplace}, not EBAY_US. "
            "Pick one of your US shipping policies below.",
            [_policy_pick_entry(p) for p in (policies or [])])
    cost_type = _policy_cost_type(policy)
    # v35: a lone weight-unit default is not a package (see
    # _package_has_content) — treat it as no package, so the seller
    # hears "charges by weight" instead of a half-filled-fields error
    # for a section they never touched.
    if package is not None and not _package_has_content(package):
        package = None
    if cost_type == "CALCULATED":
        if package is None:
            raise _PublishValidationError(
                "Your shipping policy charges by weight, so eBay needs "
                "the package weight and dimensions. Enter them under "
                "Shipping, then list again.")
        return cost_type, _validate_package(package)
    if cost_type == "FLAT_RATE":
        if package is not None:
            _validate_package(package)  # flag typos, but do not send it
        return cost_type, None
    raise _NeedsFulfillmentPolicyChoice(
        "That shipping policy offers no shippable service (local pickup "
        "only, or no domestic option). Pick a shipping policy below.",
        [_policy_pick_entry(p) for p in (policies or [])])


def _package_has_content(raw):
    """v35: whether a package block carries anything the seller entered.
    The weight-unit dropdown always holds a value (OUNCE by default),
    so a lone unit must NOT count: a draft that only ever autosaved
    the unit is an empty package, not a half-filled one. Content means
    a weight value or any dimension is filled. Fixes the live v34
    trap where a flat-rate publish with nothing entered failed
    validation on the defaulted unit alone.
    """
    if not isinstance(raw, dict):
        return False
    for key in ("weight_value", "package_weight_value",
                "length", "package_length",
                "width", "package_width",
                "height", "package_height"):
        if str(raw.get(key, "") or "").strip():
            return True
    return False


def _draft_package(d):
    """A draft's saved package block (v32 listing-page fields), or None
    when nothing was entered. Mirrors the single-publish assembly: any
    field filled means the block is passed along for validation; a
    half-filled block is rejected there, never silently completed."""
    raw = {k: str(d.get(k, "") or "").strip() for k in (
        "package_weight_value", "package_weight_unit",
        "package_length", "package_width", "package_height")}
    if not _package_has_content(raw):
        return None
    return {"weight_value": raw["package_weight_value"],
            "weight_unit": raw["package_weight_unit"],
            "length": raw["package_length"],
            "width": raw["package_width"],
            "height": raw["package_height"]}


def _discard_offer(d, token):
    """v34: delete the draft's saved offer from eBay (best-effort) and
    forget its local ID, so the next publish creates a fresh offer
    instead of re-publishing a stale one. A saved offer is a snapshot
    of the data it was created with — eBay publishes the saved offer
    state, not the app's current data — so an offer created before a
    policy pick (or before a package block existed) can never be fixed
    by retrying it. Never touches a live listing: if the draft already
    has a listing ID, this is a no-op. Delete failures are ignored —
    the local ID is still cleared, and a later create either succeeds
    fresh or hits 25002 and reuses via the existing F3 path. DELETE
    /sell/inventory/v1/offer/{offerId} permanently deletes an
    unpublished offer (for a published offer it would end the live
    listing, which is why the listing guard above is absolute)."""
    offer_id = (d.get("ebay_offer_id") or "").strip()
    if not offer_id or d.get("ebay_listing_id"):
        return
    try:
        _ebay_call("DELETE", f"/sell/inventory/v1/offer/{offer_id}", token)
    except Exception:
        pass  # best-effort: never block the retry on the delete itself
    d.pop("ebay_offer_id", None)
    d.pop("ebay_offer_fulfillment_id", None)
    d.pop("ebay_offer_condition", None)
    _save_draft(d)


def _format_problems(problems, notes):
    """v35: render a compliance report as one error string. A single
    problem stands alone (so its wording matches the old single-error
    messages exactly); several problems list under one header, each
    naming what is wrong + how to fix it. Notes (checks that could not
    run) trail as a non-blocking line on a multi-problem report only.
    The frontend error box uses pre-wrap, so the line breaks render
    as a list.
    """
    if len(problems) == 1:
        body = problems[0]
    else:
        body = ("Fix these before listing:\n" +
                "\n".join("- " + p for p in problems))
    # v35: notes trail only on a multi-problem report. A single problem
    # keeps its exact legacy wording (existing tests assert it), so a
    # "could not check" note never rides along and changes the message.
    if notes and len(problems) > 1:
        body += "\nNote: " + " ".join(notes)
    return body


def _listing_readiness(d, f, token):
    """v35: pre-publish compliance check (docs/ebay-listing-requirements.md).
    Returns (problems, notes). problems are plain-word strings, each
    naming what is wrong + how to fix it; notes are checks that could
    not run (a fetch failed) and never block. Hard-blocks ONLY on
    verified rules. Runs after token acquisition and before location
    resolution, so a blocked publish never uploads photos or touches
    eBay state. A stale-policy pick signal or a scope-reconnect signal
    from the policy gate is re-raised, not converted: the pick UI (with
    any problems found so far attached) and the reconnect UI own those
    paths, and the next publish reports everything.
    """
    problems, notes = [], []
    title = (f.get("title") or "").strip()
    category_id = (f.get("category_id") or "").strip()
    condition = (f.get("condition") or "").strip()
    fulfillment_id = ((f.get("fulfillment_id") or "").strip()
                      or EBAY_FULFILLMENT_POLICY_ID)
    payment_id = ((f.get("payment_id") or "").strip()
                  or EBAY_PAYMENT_POLICY_ID)
    return_id = ((f.get("return_id") or "").strip()
                 or EBAY_RETURN_POLICY_ID)
    if not title:
        problems.append("The title is empty. Go back and write a title first.")
    elif len(title) > 80:
        problems.append("The title is over 80 characters. Shorten it to 80 or fewer.")
    price_error = f.get("price_error")
    if price_error:
        problems.append(str(price_error))
    else:
        price_val = f.get("price_val")
        if price_val is None or price_val <= 0:
            problems.append("Set a price above $0 before listing.")
    if not category_id:
        problems.append("Pick an eBay category first (tap Suggest).")
    if not (fulfillment_id and payment_id and return_id):
        problems.append("The three policy numbers are missing. Add them in .env "
                        "or type them in the boxes (setup guide: Connect eBay).")
    if not condition:
        problems.append("Pick a condition first.")
    elif condition not in {v for v, _ in CONDITIONS}:
        problems.append("Pick a condition from the list.")
    bo_error = f.get("bo_error")
    if bo_error:
        problems.append(str(bo_error))
    if not (d.get("photos") or []):
        problems.append("This draft has no photos.")
    # Condition vs category (Metadata API; never blocks on a failed fetch).
    if category_id and condition and condition in {v for v, _ in CONDITIONS}:
        allowed, _required = _category_conditions(category_id)
        if allowed is None:
            notes.append("Could not check which conditions this category "
                         "takes, so eBay will decide at publish.")
        elif condition not in allowed:
            labels = ", ".join(CONDITION_DISPLAY.get(e, e) for e in allowed)
            problems.append(
                "That condition choice isn't allowed for this category. "
                f"This category takes: {labels}. "
                "Pick one from the Condition list.")
    # Shipping policy gate (user token): a validation failure becomes
    # one more problem; a pick/scope signal propagates with the
    # problems found so far attached to the pick signal.
    if fulfillment_id:
        try:
            _check_fulfillment_policy(token, fulfillment_id, f.get("package"))
        except _NeedsFulfillmentPolicyChoice as exc:
            exc.problems = list(problems)
            raise
        except _NeedsScopeReconnect:
            raise
        except _PublishValidationError as exc:
            problems.append(str(exc))
    # Required specifics (Taxonomy API; never blocks on a failed fetch).
    if category_id:
        required, recommended = _category_aspects(category_id)
        if required is None:
            notes.append("Could not check this category's required details, "
                         "so eBay will decide at publish.")
        else:
            attributes = dict(f.get("attributes") or {})
            exact = {a["name"].lower(): a["name"]
                     for a in (required + recommended)}
            normalized = {}
            for k, v in attributes.items():
                ks = str(k).strip()
                normalized[exact.get(ks.lower(), ks)] = v
            missing = _missing_required_aspects(normalized, required)
            if missing:
                problems.append(
                    "eBay requires these details for this category: " +
                    ", ".join(missing) + ". Fill them in under "
                    "'Details eBay requires', then try again.")
    return problems, notes


def _do_publish(d, f, ship_from=None):
    """Create the live eBay listing for draft d. Shared by single publish
    and batch publish so both paths behave identically.

    f holds already-assembled fields: title (pre-truncated to 80),
    description, price_val (float), condition, condition_details,
    category_id, fulfillment_id, payment_id, return_id (any of the three
    may be empty — they fall back to .env here), attributes (dict),
    best_offer_terms (dict or None), package (dict or None: the
    listing-page weight + size block, validated by the v32 policy gate
    and sent on the inventory item only for calculated policies).
    best_offer_terms (dict or None). v35: the pre-publish compliance
    check (_listing_readiness) reports ALL problems at once (missing
    title, category, policy ids, condition value, photos, price, bad
    condition for the category, calculated policy without a package,
    missing required specifics) before any eBay call; the old
    one-at-a-time raises are gone. ship_from, when given, is the
    address the seller typed once in the app: if the account has no
    inventory location, one is created from it (v31) and the publish
    proceeds; without it, an empty location list raises
    _NeedsShipFromAddress instead of a dead-end error. Returns
    (listing_id, listing_url) and saves the draft's eBay fields. Raises
    RuntimeError for eBay-side failures.
    """
    fulfillment_id = (f.get("fulfillment_id") or "").strip() or EBAY_FULFILLMENT_POLICY_ID
    payment_id = (f.get("payment_id") or "").strip() or EBAY_PAYMENT_POLICY_ID
    return_id = (f.get("return_id") or "").strip() or EBAY_RETURN_POLICY_ID
    title = (f.get("title") or "").strip()
    category_id = (f.get("category_id") or "").strip()
    condition = (f.get("condition") or "").strip()
    condition_details = (f.get("condition_details") or "").strip()
    photos = d.get("photos") or []
    try:
        token = _ebay_access_token()
    except _NeedsScopeReconnect:
        # v33: the scope signal subclasses RuntimeError, so it needs
        # its own clause before the broad catch below — converting it
        # to _EbayAuthError would strip the flag and hide the Connect
        # button again (the live v1.31 gap).
        raise
    except RuntimeError as exc:
        raise _EbayAuthError(str(exc))

    # v35: pre-publish compliance check — ALL problems at once, before
    # location resolution, photo uploads, or any eBay state change.
    # This replaces the old one-at-a-time validations (empty title,
    # then category, then condition, then eBay's own verdict): a draft
    # with three things wrong now hears all three in one message, each
    # naming the fix. A stale-policy pick signal or a scope signal
    # propagates (the pick/reconnect UI owns those paths). Condition
    # is still required, never silently defaulted to NEW (v30 audit F4
    # + the v6 honesty rule).
    problems, _notes = _listing_readiness(d, f, token)
    if problems:
        raise _PublishValidationError(_format_problems(problems, _notes))

    # v30 (audit F2) + v31: eBay requires an inventory location to
    # publish. Resolve before any photo work so an empty location list
    # fails fast instead of wasting uploads on a listing that cannot
    # publish — with a ship-from address supplied, the location is
    # created here and the publish proceeds; without one, the caller
    # gets _NeedsShipFromAddress and shows the address form.
    merchant_location_key = _resolve_merchant_location_key(token, ship_from)

    # v32: shipping-policy gate, before any photo work for the same
    # fail-fast reason. Reads the policy the offer would send: flat
    # proceeds, calculated requires a real package block (no defaults),
    # a stale/wrong-marketplace/unshippable policy raises a pick signal,
    # and a token without sell.account raises the reconnect signal.
    # The returned block goes on the inventory item below, and only for
    # calculated policies (a flat price never uses weight).
    policy_cost_type, package_block = _check_fulfillment_policy(
        token, fulfillment_id, f.get("package"))

    sku = "la" + d["id"]
    # 1. photos -> eBay-hosted URLs (Media API photo service).
    image_urls = []
    for i, rel in enumerate(photos, 1):
        full = os.path.join(PHOTOS_DIR, rel)
        if os.path.isfile(full):
            image_urls.append(_ebay_upload_picture(full, token))
    if not image_urls:
        raise RuntimeError("No photos could be uploaded. Try again.")

    # 2. inventory item
    desc_html = html.escape(f.get("description") or "").replace("\n", "<br>")
    # v8: normalize aspect names to eBay's exact spelling (case-insensitive
    # match) and enforce the category's required aspects. When eBay's
    # aspects lookup fails we skip validation and send what we have —
    # never block the user on our own API trouble.
    required, recommended = _category_aspects(category_id)
    attributes = dict(f.get("attributes") or {})
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
            raise _PublishValidationError(
                "eBay requires " + ", ".join(missing) +
                " for this category. Fill them in under "
                "'Details eBay requires' above, then try again.")
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
    # v32: calculated shipping prices from the package, so the block
    # goes on the inventory item (official Inventory API model); flat
    # policies never receive one.
    if package_block:
        inventory_item["packageWeightAndSize"] = package_block
    _ebay_call("PUT", f"/sell/inventory/v1/inventory_item/{sku}", token,
               inventory_item)

    # 3. offer (reuse if one was already made for this draft)
    offer_id = d.get("ebay_offer_id")
    # v34: a policy-pick re-post carries a freshly picked policy, so
    # the saved offer (if any) predates it — discard it here so the
    # re-post creates a fresh offer carrying the picked policy.
    # No-op when no offer is saved.
    if f.get("fresh_offer") and offer_id:
        _discard_offer(d, token)
        offer_id = None
    # v34: a saved offer is a snapshot of the data it was created
    # with. If the fulfillment policy has changed since that offer was
    # made (a pick, or a hand-edited policy number), reusing it would
    # publish the OLD policy, not the current one — so discard it and
    # create fresh below. Drafts saved before v34 carry no recorded
    # policy, so the comparison is skipped and reuse is preserved.
    recorded_policy = (d.get("ebay_offer_fulfillment_id") or "").strip()
    if offer_id and recorded_policy and recorded_policy != fulfillment_id:
        _discard_offer(d, token)
        offer_id = None
    # v35: same snapshot rule for condition. A saved offer predates a
    # condition change, and publish validates the offer's snapshot
    # (error 25021 names "the inventory item, or the unpublished
    # offer"), so reusing it would publish the OLD condition, not the
    # current one — discard it and create fresh below. Drafts saved
    # before v35 carry no recorded condition, so the comparison is
    # skipped and reuse is preserved.
    recorded_condition = (d.get("ebay_offer_condition") or "").strip()
    if offer_id and recorded_condition and recorded_condition != condition:
        _discard_offer(d, token)
        offer_id = None
    if not offer_id:
        try:
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
                    **({"bestOfferTerms": f.get("best_offer_terms")}
                       if f.get("best_offer_terms") else {}),
                },
                "pricingSummary": {"price": {"currency": "USD",
                                             "value": f"{f['price_val']:.2f}"}},
                # v30 (audit F9): listingDuration is a TOP-LEVEL offer
                # field, not nested under a "listing" object.
                "listingDuration": "GTC",
                # v30 (audit F2): eBay requires an inventory location to
                # publish; offer creation succeeds without it, publish fails.
                "merchantLocationKey": merchant_location_key,
            })
            offer_id = offer.get("offerId")
            if not offer_id:
                raise RuntimeError("eBay made the offer but did not return an offer ID. "
                                   "Check Seller Hub; the listing may need finishing there.")
            # v34: record which policy this offer was created with, so
            # a later policy change discards it instead of reusing it.
            d["ebay_offer_fulfillment_id"] = fulfillment_id
            # v35: same for the condition, so a later condition change
            # discards it instead of publishing the old condition.
            d["ebay_offer_condition"] = condition
        except RuntimeError as exc:
            # v30 (audit F3): if eBay says an offer already exists for this
            # SKU (25002-style), reuse it instead of failing the retry.
            low_exc = str(exc).lower()
            if "25002" not in low_exc and not ("already" in low_exc
                                               and "offer" in low_exc):
                # v32: a 25007 here means the fulfillment policy the
                # offer sent was rejected after the gate passed (policy
                # changed mid-flight, or a subtler policy issue). Name
                # the exact policy ID + type as read, so diagnosis is
                # instant instead of another guessing round.
                if "25007" in low_exc:
                    raise RuntimeError(
                        f"{exc} [Shipping policy sent: {fulfillment_id} "
                        f"({policy_cost_type or 'unknown type'})]")
                raise
            existing = _ebay_call("GET", f"/sell/inventory/v1/offer?sku={sku}", token)
            offers = existing.get("offers") if isinstance(existing, dict) else None
            offer_id = (offers[0].get("offerId") or "").strip() if offers else ""
            if not offer_id:
                raise
            # v34: record the reused offer's policy when eBay reports
            # it, so a later policy change still discards correctly.
            # When eBay does not report it, leave any recorded value
            # untouched rather than guessing.
            if offers and isinstance(offers[0], dict):
                _lp = offers[0].get("listingPolicies") or {}
                if isinstance(_lp, dict):
                    _rp = str(_lp.get("fulfillmentPolicyId") or "").strip()
                    if _rp:
                        d["ebay_offer_fulfillment_id"] = _rp
        # v30 (audit F3): persist the offer ID IMMEDIATELY, before publish,
        # so a publish failure cannot orphan the offer and block the retry.
        d["ebay_offer_id"] = offer_id
        _save_draft(d)

    # 4. publish
    try:
        published = _ebay_call(
            "POST", f"/sell/inventory/v1/offer/{offer_id}/publish", token)
    except RuntimeError as exc:
        # v34: a 25007/25604 publish failure means the offer itself is
        # suspect (a stale snapshot — the publish used saved offer
        # state, not the current data). v35 adds 25021 (bad condition
        # for the category) to the same set: a reused offer can carry
        # the old condition snapshot. Discard it so the NEXT attempt
        # creates a fresh offer instead of re-publishing the same stale
        # one forever. Other failures keep the F3 reuse protection
        # intact, and a live listing is never touched (_discard_offer
        # guards it; listing_id is unset on this path in any case).
        low_publish = str(exc).lower()
        # v35: match the structured errorId too, not just message text
        # (a _plain_ebay_error message carries "(error 25021)", but a
        # direct _EbayApiError may not — the discard must fire either
        # way; plain RuntimeErrors have no error_id and fall back to
        # the message sniff below).
        exc_id = str(getattr(exc, "error_id", "") or "")
        if (exc_id in ("25007", "25604", "25021")
                or "25007" in low_publish or "25604" in low_publish
                or "25021" in low_publish):
            _discard_offer(d, token)
        # v32: same 25007 naming as the offer step above.
        if "25007" in str(exc).lower():
            raise RuntimeError(
                f"{exc} [Shipping policy sent: {fulfillment_id} "
                f"({policy_cost_type or 'unknown type'})]")
        raise
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

    d["ebay_offer_id"] = offer_id
    d["ebay_listing_id"] = listing_id
    d["ebay_listing_url"] = listing_url
    d["ebay_sku"] = sku
    _save_draft(d)
    return listing_id, listing_url


@app.route("/api/ebay/fulfillment-policies", methods=["GET"])
def api_ebay_fulfillment_policies():
    """v34: the Shipping chooser's policy list. Returns the account's
    current EBAY_US fulfillment policies as pick entries (id, name,
    cost_type, marketplace) — the same shape the publish gate returns
    for a reactive pick, so the chooser renders both identically. A 403
    means the token lacks sell.account and maps to the reconnect flag
    (the publish path's message + Connect button), never to a bare
    error."""
    try:
        token = _ebay_access_token()
        listing = _ebay_call(
            "GET",
            "/sell/account/v1/fulfillment_policy?marketplace_id=EBAY_US",
            token)
    except _NeedsScopeReconnect as exc:
        return jsonify({"needs_scope_reconnect": True,
                        "error": str(exc)}), 200
    except _EbayApiError as exc:
        if exc.status == 403:
            return jsonify({"needs_scope_reconnect": True,
                            "error": SCOPE_RECONNECT_MSG}), 200
        # _ebay_call already built this message with the errorId (v30
        # F6), so str(exc) is the surfaced text — no re-parse here.
        return jsonify({"error": str(exc)}), 502
    except _EbayAuthError as exc:
        if str(exc) == SCOPE_RECONNECT_MSG:
            return jsonify({"needs_scope_reconnect": True,
                            "error": str(exc)}), 200
        return jsonify({"error": str(exc)}), 400
    except RuntimeError as exc:
        if str(exc) == SCOPE_RECONNECT_MSG:
            return jsonify({"needs_scope_reconnect": True,
                            "error": str(exc)}), 200
        return jsonify({"error": str(exc)}), 502
    policies = []
    if isinstance(listing, dict):
        for p in (listing.get("fulfillmentPolicies") or []):
            if isinstance(p, dict):
                policies.append(_policy_pick_entry(p))
    return jsonify({"policies": policies})


@app.route("/api/ebay/category-conditions", methods=["GET"])
def api_ebay_category_conditions():
    """v35: the Condition dropdown's per-category list. Returns the
    category's allowed conditions (value + plain label) from the
    Metadata API, plus whether the category requires a condition at
    all. When the fetch fails, `available` is False and the static
    list is returned instead, so the page keeps the full dropdown plus
    a plain hint that eBay will decide — never a block, never an
    empty dropdown.
    """
    category_id = str(request.args.get("category_id", "") or "").strip()
    allowed, required = _category_conditions(category_id)
    if allowed is None:
        return jsonify({
            "conditions": [{"value": v, "label": l} for v, l in CONDITIONS],
            "condition_required": True,
            "available": False,
        })
    return jsonify({
        "conditions": [{"value": e, "label": CONDITION_DISPLAY.get(e, e)}
                       for e in allowed],
        "condition_required": bool(required),
        "available": True,
    })


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
    # v6: condition comes from the listing page, falling back to the draft's
    # condition field. Empty means unset: we do NOT default to NEW,
    # because silently calling a used item "new" misleads the buyer.
    # v35: the compliance check inside _do_publish reports a missing
    # condition together with every other problem, instead of eBay's
    # own error arriving later.
    condition = str(body.get("condition", "") or d.get("condition", "")).strip()
    condition_details = str(body.get("condition_details", "")
                            or d.get("condition_details", "")).strip()[:1000]
    category_id = str(body.get("category_id", "") or d.get("category_id", "")).strip()
    fulfillment_id = str(body.get("fulfillment_policy_id")
                         or d.get("fulfillment_policy_id")
                         or EBAY_FULFILLMENT_POLICY_ID).strip()
    payment_id = str(body.get("payment_policy_id")
                     or d.get("payment_policy_id")
                     or EBAY_PAYMENT_POLICY_ID).strip()
    return_id = str(body.get("return_policy_id")
                    or d.get("return_policy_id")
                    or EBAY_RETURN_POLICY_ID).strip()
    # v8: listing-page aspect fields (required/recommended item specifics) arrive in
    # the body and overlay the draft's attributes.
    attributes = dict(d.get("attributes") or {})
    body_attrs = body.get("attributes")
    if isinstance(body_attrs, dict):
        for k, v in body_attrs.items():
            attributes[str(k)] = v

    # v35: no early single-error returns here anymore. The endpoint
    # assembles f and lets _do_publish's compliance check report ALL
    # problems at once — a draft with three things wrong used to hear
    # only the first. Price and Best Offer parse results ride along as
    # error strings so compliance can include them in the same report.
    try:
        price_val = round(float(price_raw), 2)
        if price_val <= 0:
            raise ValueError()
        price_error = None
    except (ValueError, TypeError):
        price_val = None
        price_error = "Set a price above $0 before listing."
    # v9: best offer. The toggle and optional auto prices come from the
    # listing page, falling back to the draft's saved values. Toggle off =
    # today's behavior, unchanged (no bestOfferTerms sent).
    best_offer = bool(body.get("best_offer", d.get("best_offer", False)))
    decline_raw = str(body.get("best_offer_auto_decline",
                               d.get("best_offer_auto_decline", "")) or "")
    accept_raw = str(body.get("best_offer_auto_accept",
                              d.get("best_offer_auto_accept", "")) or "")
    best_offer_terms, bo_error = _best_offer_terms(best_offer, decline_raw,
                                                   accept_raw, price_val)
    # v35: bo_error rides into f for the compliance report (see above);
    # it is not returned early anymore.

    # v35: a lone weight-unit default (OUNCE) is not a package — only
    # a weight value or a dimension counts (see _package_has_content).
    # The assembled dict still carries the unit for validation when a
    # real block is present.
    package = None
    _pkg_raw = {k: str(body.get(k, "") or "").strip() for k in (
        "package_weight_value", "package_weight_unit",
        "package_length", "package_width", "package_height")}
    if _package_has_content(_pkg_raw):
        package = {"weight_value": _pkg_raw["package_weight_value"],
                   "weight_unit": _pkg_raw["package_weight_unit"],
                   "length": _pkg_raw["package_length"],
                   "width": _pkg_raw["package_width"],
                   "height": _pkg_raw["package_height"]}

    try:
        listing_id, listing_url = _do_publish(d, {
            "title": title,
            "description": description,
            "price_val": price_val,
            "price_error": price_error,
            "bo_error": bo_error,
            "condition": condition,
            "condition_details": condition_details,
            "category_id": category_id,
            "fulfillment_id": fulfillment_id,
            "payment_id": payment_id,
            "return_id": return_id,
            "attributes": attributes,
            "best_offer_terms": best_offer_terms,
            "package": package,
            # v34: a policy-pick re-post must discard the pre-pick
            # offer (see _do_publish); normal List taps never set it.
            "fresh_offer": bool(body.get("policy_pick")),
        }, ship_from=body.get("ship_from")
          if isinstance(body.get("ship_from"), dict) else None)
    except _PublishValidationError as exc:
        return jsonify({"error": str(exc)}), 400
    except _EbayAuthError as exc:
        # v33: defense in depth — a scope message converted to an auth
        # error anywhere upstream must still carry the flag, or the
        # Connect button stays hidden (the live v1.31 gap).
        if str(exc) == SCOPE_RECONNECT_MSG:
            return jsonify({"needs_scope_reconnect": True,
                            "error": str(exc)}), 200
        return jsonify({"error": str(exc)}), 400
    except _NeedsShipFromAddress:
        # v31: not a failure — the app shows the one-time ship-from
        # address form and re-posts with it; the publish then proceeds.
        return jsonify({"needs_ship_from": True}), 200
    except _NeedsScopeReconnect as exc:
        # v32: not a failure — the app shows the plain message + the
        # Connect eBay button; one fresh consent grants sell.account
        # and the publish can be retried.
        return jsonify({"needs_scope_reconnect": True,
                        "error": str(exc)}), 200
    except _NeedsFulfillmentPolicyChoice as exc:
        # v32: not a failure — the app offers the current policies as
        # a one-tap pick and re-posts with the picked ID.
        # v35: compliance problems found alongside the unusable policy
        # ride along, so the pick screen shows them too.
        return jsonify({"needs_policy_pick": True,
                        "policies": exc.policies,
                        "problems": getattr(exc, "problems", []),
                        "error": str(exc)}), 200
    except RuntimeError as exc:
        # v33: same defense — a bare scope raise must never surface
        # without the flag.
        if str(exc) == SCOPE_RECONNECT_MSG:
            return jsonify({"needs_scope_reconnect": True,
                            "error": str(exc)}), 200
        return jsonify({"error": str(exc)}), 502
    return jsonify({"listing_id": listing_id, "listing_url": listing_url})


@app.route("/api/ebay/batch-publish", methods=["POST"])
def api_ebay_batch_publish():
    """Post several drafts in one go, sequentially, one result per draft.

    The confirmation screen in the app is the review: this endpoint only
    ever publishes drafts the user confirmed there. One draft failing
    never stops the rest. The category comes from the batch screen
    (v29: drafts carry a saved category number from the listing page, but batch still
    uses the one number entered for the whole batch); policies come from
    the saved preset when one exists, otherwise .env (via the helper's
    fallback).
    """
    body = request.get_json(force=True) or {}
    ids = body.get("draft_ids") or []
    category_id = str(body.get("category_id", "") or "").strip()
    if not category_id:
        return jsonify({"error": "Pick a category for the batch first."}), 400
    if not isinstance(ids, list) or not ids:
        return jsonify({"error": "Pick at least one draft first."}), 400
    preset = _load_preset() or {}
    ship_from = body.get("ship_from") \
        if isinstance(body.get("ship_from"), dict) else None
    # v32: a policy pick from the one-tap list overrides preset/.env
    # for the whole re-run (same pattern as the ship-from re-run).
    fulfillment_override = str(body.get("fulfillment_policy_id", "") or "").strip()
    results = []
    for raw_id in ids:
        draft_id = str(raw_id or "")
        d = _load_draft(draft_id) if draft_id else None
        title = (d.get("title", "") if d else "") or "(untitled)"
        if d is None:
            results.append({"draft_id": draft_id, "title": title, "ok": False,
                            "error": "Draft not found."})
            continue
        if d.get("ebay_listing_id"):
            results.append({"draft_id": d["id"], "title": title, "ok": False,
                            "error": "Already live on eBay."})
            continue
        price_val = _parse_money(d.get("price", ""))
        if price_val is None:
            results.append({"draft_id": d["id"], "title": title, "ok": False,
                            "error": "No price set."})
            continue
        title_v = str(d.get("title", "")).strip()[:80]
        if not title_v:
            results.append({"draft_id": d["id"], "title": title, "ok": False,
                            "error": "The title is empty."})
            continue
        terms, bo_error = _best_offer_terms(
            bool(d.get("best_offer", False)),
            str(d.get("best_offer_auto_decline", "") or ""),
            str(d.get("best_offer_auto_accept", "") or ""), price_val)
        if bo_error:
            results.append({"draft_id": d["id"], "title": title, "ok": False,
                            "error": bo_error})
            continue
        try:
            listing_id, listing_url = _do_publish(d, {
                "title": title_v,
                "description": str(d.get("description", "")).strip(),
                "price_val": price_val,
                "condition": str(d.get("condition", "") or "").strip(),
                "condition_details": str(d.get("condition_details", "") or "").strip()[:1000],
                "category_id": category_id,
                "fulfillment_id": fulfillment_override or str(preset.get("fulfillment_policy_id", "") or "").strip(),
                "payment_id": str(preset.get("payment_policy_id", "") or "").strip(),
                "return_id": str(preset.get("return_policy_id", "") or "").strip(),
                "attributes": dict(d.get("attributes") or {}),
                "best_offer_terms": terms,
                # v32: batch drafts carry their saved package block
                # (listing-page fields, per draft — framed and unframed
                # parcels differ, so this is never a preset).
                "package": _draft_package(d),
                # v34: fulfillment_override is only ever set by a pick
                # re-run (confirm and ship-from re-runs pass none), so
                # it doubles as the pick flag: a pick re-run discards
                # each draft's pre-pick offer before creating fresh.
                "fresh_offer": bool(fulfillment_override),
            }, ship_from=ship_from)
        except _NeedsShipFromAddress as exc:
            # v31: one draft hitting the empty-location wall tells the
            # whole batch — the rest would hit the same wall. Stop here;
            # the app shows the address form once and re-runs the batch
            # with it (already-listed drafts are skipped above).
            results.append({"draft_id": d["id"], "title": title, "ok": False,
                            "needs_ship_from": True, "error": str(exc)})
            break
        except _NeedsScopeReconnect as exc:
            # v32: the token lacks sell.account, so every draft would
            # hit the same wall. Stop here; the app shows the plain
            # message and the user reconnects once, then re-runs.
            results.append({"draft_id": d["id"], "title": title, "ok": False,
                            "needs_scope_reconnect": True,
                            "error": str(exc)})
            break
        except _NeedsFulfillmentPolicyChoice as exc:
            # v32: the stored shipping policy is unusable for every
            # draft in the run. Stop here; the app offers the current
            # policies as a one-tap pick and re-runs with the pick.
            # v35: compliance problems found alongside ride along.
            results.append({"draft_id": d["id"], "title": title, "ok": False,
                            "needs_policy_pick": True,
                            "policies": exc.policies,
                            "problems": getattr(exc, "problems", []),
                            "error": str(exc)})
            break
        except RuntimeError as exc:
            # v33: a bare scope raise must carry the flag and stop the
            # run (every draft would hit the same wall), like the
            # _NeedsScopeReconnect branch above — never a bare error.
            if str(exc) == SCOPE_RECONNECT_MSG:
                results.append({"draft_id": d["id"], "title": title, "ok": False,
                                "needs_scope_reconnect": True,
                                "error": str(exc)})
                break
            # Covers validation, auth, and eBay-side failures alike: each
            # is reported per draft and never stops the rest.
            results.append({"draft_id": d["id"], "title": title, "ok": False,
                            "error": str(exc)})
            continue
        results.append({"draft_id": d["id"], "title": title, "ok": True,
                        "listing_id": listing_id, "listing_url": listing_url})
    return jsonify({"results": results})


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


@app.route("/api/quick-post", methods=["POST"])
def api_quick_post():
    """Start a listing from photos WITHOUT the AI Identify step.

    Same photo intake as /api/analyze, but the draft starts blank — he
    types the title himself. Never calls Gemini, so it needs no AI key
    and has no AI wait.
    """
    files = request.files.getlist("photos")
    if not files:
        return jsonify({"error": "Pick at least one photo first."}), 400
    try:
        draft_id, rels = _store_uploads(files)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 400
    now = datetime.now().isoformat(timespec="seconds")
    draft = {"id": draft_id, "created": now, "photos": rels, "price": "",
             "condition": "", "condition_details": "",
             "best_offer": False, "best_offer_auto_decline": "",
             "best_offer_auto_accept": "",
             "title": "", "description": "", "category": "",
             "attributes": {}, "identification_notes": ""}
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
    # v29: the listing page's eBay fields live in the draft itself (this
    # replaced the v27 separate publish-fields endpoint): the category
    # number + name and the three policy numbers save here with the same
    # draft autosave as everything else, so no separate panel-save chain
    # can lose them. Unknown keys are ignored, so secrets can never be
    # written through this endpoint.
    if "category_id" in body:
        d["category_id"] = str(body["category_id"])[:20]
    if "category_name" in body:
        d["category_name"] = str(body["category_name"])[:200]
    for key in ("fulfillment_policy_id", "payment_policy_id", "return_policy_id"):
        if key in body:
            d[key] = str(body[key])[:30]
    # v32: package weight + size save with the same draft autosave.
    # Deliberately NOT part of the preset (framed and unframed parcels
    # differ), so these keys live only on the draft.
    for key in ("package_weight_value", "package_length",
                "package_width", "package_height"):
        if key in body:
            d[key] = str(body[key])[:20]
    if "package_weight_unit" in body:
        u = str(body["package_weight_unit"]).strip().upper()
        if u in ("POUND", "OUNCE"):
            d["package_weight_unit"] = u
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


@app.route("/api/preset", methods=["GET"])
def api_preset_get():
    return jsonify({"preset": _load_preset()})


@app.route("/api/preset", methods=["POST"])
def api_preset_save():
    """Save one 'usual settings' preset. Re-saving overwrites it.
    Holds settings only (condition, category, policies, Best Offer) —
    never keys, which stay in .env."""
    body = request.get_json(force=True) or {}
    return jsonify({"preset": _save_preset(_clean_preset(body))})


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
