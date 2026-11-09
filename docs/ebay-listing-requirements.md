# eBay listing requirements (Sell APIs) — researched Oct 29, 2026

Why this doc exists: publish kept failing one check at a time
(25007 shipping policy, then 25604 availability, then 25021 condition).
eBay validates a listing at **publish** time and reports problems one at
a time, so the app must check the whole listing against eBay's own rules
**before** sending anything. Every rule below comes from eBay's official
developer docs. A rule with no official source is NOT enforced as a hard
block; eBay's error is surfaced plainly instead.

## The three calls (in order)

1. `PUT /sell/inventory/v1/inventory_item/{sku}` — the product record.
2. `POST /sell/inventory/v1/offer` — price, category, policies, location.
3. `POST /sell/inventory/v1/offer/{offerId}/publish` — activates it.
   Only the offerId goes in the publish URI; success returns a listingId.

Source: https://developer.ebay.com/api-docs/sell/static/inventory/inventory-item-to-offer.html

Prerequisites (same page): the seller must be opted in to business
policies, and an inventory location is required to publish an offer
(`merchantLocationKey` from `createInventoryLocation`).

## Inventory item — what publish requires

Source: https://developer.ebay.com/api-docs/sell/inventory/types/slr:InventoryItem
and https://developer.ebay.com/api-docs/sell/inventory/types/slr:Product

| Field | Rule | App enforcement |
|---|---|---|
| `product.title` | Required before publish. Max 80 characters. | Title required; truncated to 80. |
| `product.description` | Required before publish, unless the offer carries `listingDescription` (the app sends both; description falls back to the title, so it is never empty). Max 4000. Basic HTML only, no active content. | Description falls back to title. |
| `product.imageUrls` | Required before publish: at least one HTTPS URL. Up to 24 pictures per single-variation listing (12 per variation-group member). | At least one photo required; Media API uploads; 24-photo cap; 12 MB per-file app cap (see "Unsettled" below). |
| `product.aspects` | Item specifics. Required before publish **when the category requires them** — the authoritative set comes from Taxonomy `getItemAspectsForCategory` (aspect name max 40, value max 50). | Required aspects fetched per category; missing ones block with their names. Fetch failure never blocks. |
| Product identifiers | Many categories require at least one identifier (GTIN: UPC/EAN/ISBN, or Brand + MPN pair). They appear as required aspects in `getItemAspectsForCategory` (`localizedAspectName` + `aspectRequired` + `aspectUsage`). Brand max 65, MPN max 65. If a required identifier does not exist for the product, eBay wants a marketplace "identifier unavailable" string instead of a blank. | Covered by the required-aspects check (a missing required Brand/UPC/etc. blocks and names the field). The exact "unavailable" string is NOT hardcoded: its official source is Trading `GeteBayDetails` (`ProductDetails.ProductIdentifierUnavailableText`), which eBay deprecated 2026-09-21 and decommissions 2027-03-15, so the app tells the seller to enter the identifier and lets eBay's error name it if the value is wrong. |
| `availability.shipToLocationAvailability.quantity` | Required before publish; must be 1 or more. | Always 1 for this app (single-quantity personal listings). |
| `condition` | Required before publish for most categories. Value must be one of the category's supported conditions (see "Conditions" below). | Required, never silently defaulted to NEW. Category-aware check (see below). |
| `conditionDescription` | Optional; max 1000. Ignored (with a warning) for NEW, LIKE_NEW, NEW_OTHER, NEW_WITH_DEFECTS. Recommended for used items. | Truncated to 1000; sent only when entered. |
| `packageWeightAndSize` | Required when the listing uses calculated shipping, or flat-rate shipping with a weight surcharge. Supported marketplaces include US. Dimensions in inches, weight in pounds/ounces. | Sent only for calculated policies; validated whole (weight + all three dimensions, positive, within caps) and never defaulted or invented. |

## Offer — what publish requires

Source: the inventory guide above (offer fields) plus the Inventory API
Offer model (listingDuration top-level; location required before publish).

| Field | Rule | App enforcement |
|---|---|---|
| `sku`, `marketplaceId` | The item's SKU; `EBAY_US` for this app. | Fixed per draft; `EBAY_US` always. |
| `format` | `FIXED_PRICE` for Buy It Now. | Always `FIXED_PRICE` (auction is a separate, unbuilt feature). |
| `availableQuantity` | 1 or more (overrides the item quantity). | Always 1. |
| `categoryId` | A leaf category. | Required; picked via Taxonomy Suggest. |
| `listingPolicies` | All three policy IDs are required at offer creation: `fulfillmentPolicyId`, `paymentPolicyId`, `returnPolicyId`. `bestOfferTerms` lives INSIDE `listingPolicies` when used. | All three required (draft, preset, or .env fallback); Best Offer terms validated (auto-accept above auto-decline, both below price). |
| `pricingSummary.price` | Price + currency (`USD`). | Price above $0, two decimals. |
| `listingDuration` | Top-level offer field; `GTC` (Good Til Cancelled). | Top-level `GTC`. |
| `merchantLocationKey` | Not required at offer creation, but REQUIRED before publish. Max 36 characters, immutable once set. | Resolved before any photo work: .env pin, else first account location, else in-app creation from the seller's typed address (never invented). |

## Conditions — the per-category rule (this is what error 25021 means)

Sources:
- https://developer.ebay.com/api-docs/sell/inventory/types/slr:ConditionEnum
- https://developer.ebay.com/api-docs/sell/static/metadata/condition-id-values.html
- Metadata API `getItemConditionPolicies`:
  `GET /sell/metadata/v1/marketplace/{marketplace_id}/get_item_condition_policies?filter=categoryIds:{id}`
  (method list: https://developer.ebay.com/api-docs/sell/metadata/static/release-notes-archive.html ;
  OpenAPI: https://developer.ebay.com/api-docs/master/sell/metadata/openapi/3/sell_metadata_v1_oas3.yaml)

- Supported condition values **vary by category**. The authoritative set
  for a category comes from `getItemConditionPolicies`, which returns
  per category: `itemConditionRequired` (whether a condition is required
  at all) and `itemConditions` (the allowed condition IDs).
- Numeric condition IDs map to Inventory API enum values (official
  table, same for all marketplaces):

| ID | Enum | Plain display name |
|---|---|---|
| 1000 | `NEW` | New |
| 1500 | `NEW_OTHER` | New other (see details) |
| 1750 | `NEW_WITH_DEFECTS` | New with defects |
| 2000 | `CERTIFIED_REFURBISHED` | Certified Refurbished (application + qualification required) |
| 2010 | `EXCELLENT_REFURBISHED` | Excellent - Refurbished (application required) |
| 2020 | `VERY_GOOD_REFURBISHED` | Very Good - Refurbished (application required) |
| 2030 | `GOOD_REFURBISHED` | Good - Refurbished (application required) |
| 2500 | `SELLER_REFURBISHED` | Seller refurbished (replaced by the above in many categories) |
| 2750 | `LIKE_NEW` | Like New (trading cards/coins: Graded) |
| 2990 | `PRE_OWNED_EXCELLENT` | Pre-owned - Excellent (apparel only) |
| 3000 | `USED_EXCELLENT` | Used (most categories support this; this IS the plain "Used") |
| 3010 | `PRE_OWNED_FAIR` | Pre-owned - Fair (apparel only) |
| 4000 | `USED_VERY_GOOD` | Very Good (trading cards/coins: Ungraded) |
| 5000 | `USED_GOOD` | Good |
| 6000 | `USED_ACCEPTABLE` | Acceptable |
| 7000 | `FOR_PARTS_OR_NOT_WORKING` | For parts or not working |

- There is NO plain `USED` enum in the Inventory API. A category that
  takes "Used" takes `USED_EXCELLENT` (3000). The app's old label
  "Used - excellent" for that value was misleading; v35 labels it "Used".
- A condition ID that is not valid for the category fails the listing
  (error 25021, "provided condition id is invalid for the category").
  The app's old message headline mislabeled this as a category problem
  because the error text contains the word "category"; v35 checks
  "condition" before "category" when wording errors.
- Condition descriptors (structured name-value condition details) are
  mandated only for trading-card and coin categories using LIKE_NEW or
  USED_VERY_GOOD. The app has no descriptor inputs, so it does NOT
  block on descriptors; if eBay rejects a card/coin listing for missing
  descriptors, its error is surfaced plainly. This is a known gap, not
  a silent pass.

App enforcement (v35): when the listing page opens (and when the
category changes), the app fetches the category's allowed conditions
and offers ONLY those in the Condition dropdown, labeled with the plain
names above. If the fetch fails, the full static list is offered plus a
plain hint that eBay will decide. The pre-publish check blocks when the
picked condition is not in the fetched allowed set and names the
allowed choices. A picked condition is never silently changed: if the
current value is not in the fetched set, it stays selected and marked,
so the check can flag it instead of rewriting the seller's choice.

## Shipping policies — flat vs calculated

Sources:
- https://www.developer.ebay.com/api-docs/sell/account/types/api:FulfillmentPolicy
- https://developer.ebay.com/api-docs/sell/account/types/api:ShippingService

- A fulfillment policy carries `shippingOptions[]`; each option has
  `optionType` (`DOMESTIC` / `INTERNATIONAL`) and `costType`
  (`FLAT_RATE` / `CALCULATED` / `NOT_SPECIFIED` for local pickup).
- Only the DOMESTIC option decides whether a US listing ships: an
  international-only policy cannot stand in for a missing domestic
  service, and a policy with no shippable domestic service cannot be
  used for a shipped listing.
- `freeShipping` may only be set on the first domestic service option.
- Calculated shipping prices from the package: eBay needs the package
  weight AND dimensions. Flat-rate shipping never uses the package
  (unless the policy charges a weight surcharge, which the app does not
  model — a surcharge policy is treated as flat and the package is not
  sent; if eBay rejects it, its error is surfaced).

App enforcement (v32–v35): the policy gate reads the policy before any
photo work. Flat proceeds with no package. Calculated requires a
complete, real package block (no defaults, no parsing the policy's
name: a policy named "Calculated 2lb" does NOT set 2 lb). A stale,
wrong-marketplace, or unshippable policy raises the one-tap pick. A
token without `sell.account` raises the reconnect signal.

## Shipping policies — creating one in the app (v41)

Sources:
- https://www.developer.ebay.com/api-docs/sell/account/types/api:FulfillmentPolicyRequest
- https://www.developer.ebay.com/api-docs/sell/account/types/api:ShippingService
- eBay Knowledge Base: how to set up free shipping / worldwide
  shipping (createFulfillmentPolicy, ShippingOption elements).

- Create: `POST /sell/account/v1/fulfillment_policy` with a
  `FulfillmentPolicyRequest` body. Success returns the
  `getFulfillmentPolicy` URI in the `Location` header and the new
  policy's ID in the response payload. The app's HTTP helper returns
  the body, not headers, so the ID is read from the payload and
  falls back to a get-by-name read (names are unique).
- Request fields: `name` (required, unique — a reuse answers 409 /
  error 20418; max 64 characters, eBay's limit — v47 corrected the
  app's old 100 cap), `marketplaceId` (`EBAY_US`), `handlingTime`
  (conditionally required when shipping options are offered; NOT
  applicable for local-pickup-only or freight), `localPickup`,
  `description` (max 250, seller-only, never shown to buyers),
  `shippingOptions[]`.
- Each `ShippingOption` has `optionType` (`DOMESTIC` /
  `INTERNATIONAL`), `costType` (`FLAT_RATE` / `CALCULATED`), and
  `shippingServices[]`. Each `ShippingService` has
  `shippingServiceCode` (required, predefined enum value),
  `shippingCost` (required for flat-rate, omitted for calculated;
  auto `0.0` on the first domestic service when `freeShipping` is
  true), `freeShipping` (only the first domestic service may be
  true; international must be false), and `shipToLocations`
  (worldwide self-ship international: `regionIncluded` of
  `[{"regionName": "Worldwide", "regionType": "WORLDWIDE"}]` on the
  service, per eBay's own worldwide-shipping guide).
- `globalShipping` is EBAY_GB only and is NEVER sent for EBAY_US.
- eBay International Shipping is an ACCOUNT-level setting, not a
  policy field: if a US seller's account is opted in, eBay adds it
  automatically to listings where international shipping is
  available, and the seller may still specify individual
  international services in a policy. The app's form therefore
  offers self-ship international services plus a plain note about
  eIS, and never fakes an eIS enrollment.
- Create needs the full `sell.account` scope (added v32); a 403
  maps to the reconnect signal.
- App enforcement (v41): the create form is full options (flat and
  calculated, US plus worldwide international, local pickup), all
  editable, nothing defaulted from a name. Review-before-create is
  mandatory: the exact policy shows for confirm, and nothing is
  created without it. The server re-validates before any POST. A
  name-taken 409 gives a plain message and keeps the form values
  for renaming. A network failure with unknown outcome re-reads by
  name before any retry, so a lost success is reused instead of
  duplicated; if nothing is found it reports unconfirmed (check
  Seller Hub before retrying). After creation the dropdown
  refreshes from a fresh list and selects the new policy for the
  current draft; the List-time policy gate still runs at List
  (calculated still requires the package then). Editing existing
  policies is out of scope.

## Photos — Media API (replaces the decommissioned Trading call)

Source: Media API `createImageFromFile`
(`POST /commerce/media/v1_beta/image/create_image_from_file`,
multipart form key `image`, Bearer token with `sell.inventory` scope;
201 + Location header with the image id; use `imageUrl` from the
create response, or `GET image/{id}`, in the inventory item's
`imageUrls`). Supported formats: JPG, GIF, PNG, BMP, TIFF, AVIF, HEIC,
WEBP. Rate limit 50 POSTs per 5 seconds.
Deprecation: Trading `UploadSiteHostedPictures` was decommissioned
2026-09-30
(https://developer.ebay.com/develop/get-started/api-deprecation-status);
the app must never call it.

## Taxonomy — categories and specifics

Source: Commerce Taxonomy API
(OpenAPI: https://developer.ebay.com/api-docs/master/commerce/taxonomy/openapi/3/commerce_taxonomy_v1_oas3.yaml)

- `getCategorySuggestions` (tree `0` for EBAY_US): category search.
  The app's Suggest endpoint is GET-only with `q` as a URL parameter.
- `getItemAspectsForCategory` (tree `0`, `category_id` must be a leaf):
  returns aspects with `localizedAspectName`, `aspectRequired`,
  `aspectUsage`, and allowed values. Required aspects (including
  product identifiers: brand, mpn, upc, ean, isbn) must be present or
  publish fails naming them one at a time (e.g. 25002 for a missing
  required aspect). The app fetches this per category (cached 24 hours)
  and blocks on missing required aspects with all their names at once.
  Fetch failure never blocks.

## Headers and auth

- Inventory API calls require `Content-Language: en-US` for EBAY_US
  (missing it fails with error 25709; this was the v30 publish blocker).
  The app sends it on every `_ebay_call`.
- Tokens: authorization-code token with base scope + `sell.inventory`
  + `sell.listing` + `sell.account`. A refresh cannot add a scope, so a
  scope addition requires one fresh consent. Taxonomy and Metadata
  reads use the app (client-credentials) token, whose OpenAPI security
  block is the base scope; if eBay rejects it, the check degrades to
  the fallback instead of blocking.
- A saved offer is a snapshot: publish uses the offer's stored state,
  not the app's current data. So the app discards (best-effort DELETE
  + clear local ID) a saved offer whenever the fulfillment policy or
  the condition changes, on a policy pick, and on a 25007, 25604, or
  25021 publish failure — never when a listing already exists. Other
  publish failures keep the reuse protection (a second createOffer for
  the same SKU fails with 25002, so the app reuses via
  `GET /sell/inventory/v1/offer?sku=`).

## What the app CANNOT pre-check (eBay decides; errors surface plainly)

- Exact per-category aspect value lists (the app names missing required
  aspects but does not validate each value against the allowed list;
  a wrong value surfaces as eBay's error with its errorId).
- Condition descriptors for trading cards/coins (no descriptor inputs).
- The exact "identifier unavailable" string per marketplace (see above).
- The official per-image MB limit: eBay's docs point to the Picture
  policy without stating a single number in the API reference (a
  secondary source says 7 MB; the app's own cap is 12 MB). If eBay
  rejects an image for size, its error (with errorId) is surfaced.
- Whether an offer's stored state matches current data beyond policy
  and condition (the app records those two; any other drift surfaces
  as eBay's publish error, and the 25007/25604/25021 discard set covers
  the observed stale-offer failures).
- Category-specific rules that have no metadata endpoint (e.g. vehicle
  compatibility properties, regulatory documents). eBay's error is
  surfaced with its errorId and plain guidance.

## The pre-publish check (v35) — all problems at once

Before any eBay call, the publish path validates the draft against the
rules above and reports EVERY problem in one message, each naming what
is wrong and how to fix it. It hard-blocks ONLY on verified rules.
Anything it could not fetch (condition set, required aspects, policy
read) is a non-blocking note, never a block, and publish proceeds to
eBay's own verdict. The confirmation dialog (title + price + category)
still gates every live post; the check never posts anything itself.

## Shipping flexibility, v42 — carrier + service pick, lb/oz weight

SUPERSEDED, v50 (F5): two claims in this section are stale since
v49. The catalog is 10 codes, not 11 (see the v49 section), and the
attestation below — "live production fulfillment policies
(USPSGroundAdvantage)" — was disproven live on Nov 8, 2026: eBay's
LSAS checker rejects USPSGroundAdvantage on this account, and the
working code for the same service is USPSParcel. The catalog is
pinned against eBay's official Trading API ShippingServiceCodeType
enum in v50 (`tests/ebay_shipping_service_codes.py`), so a code
eBay never issued cannot be offered again.

The listing page's Shipping section works like eBay's own lister:
a carrier picker (USPS, UPS, FedEx) plus a service picker per
carrier, pounds + ounces weight boxes (the way a scale reads it),
and length/width/height in inches, always visible and saved per
draft. The saved-policy dropdown stays as the "use one of my usual
policies" path.

- Service codes are never invented. The app's catalog of 11 codes is
  attested against eBay's own ShippingService type page examples
  (USPSPriority, FedEx2Day, UPS3rdDay), the mirrors of eBay's
  ShippingServiceCode enum (UPSGround, UPS2DayAirAM, UPSNextDay,
  FedExGround, FedExExpressSaver, USPSParcel, FedExHomeDelivery),
  and live production fulfillment policies (USPSGroundAdvantage;
  USPSGround is retired). USPS Priority Express is excluded on
  purpose: the mirrors disagree on its code (USPSExpress vs
  USPSExpressMail). USPS First Class is excluded: retired in favor
  of Ground Advantage. Both stay reachable through "Other (type the
  code)" on every service select; eBay validates a typed code on
  create. The account-harvested list (from the seller's own
  policies) adds any service the account already uses, documented
  order first, extras after; unclassifiable codes are kept, never
  dropped.
- The carrier picker is the weight-driven path: picking carrier +
  service finds the matching existing policy, and only a policy
  whose domestic option charges by weight (CALCULATED), carries
  that service, and has NO international option matches (the eBay
  International Shipping ONLY decision: a policy with self-ship
  international must never be picked silently). A match selects it
  with no review screen, because the policy already exists. No
  match opens the v41 create form prefilled (calculated, that
  service, domestic only, no self-ship international) and the
  review-before-create screen still stands; nothing is created
  without it. Flat/free policies stay on the saved-policy dropdown.
- Package rule, relaxed for flat/free: a CALCULATED policy still
  requires weight + all three dims (eBay needs the full
  packageWeightAndSize block), and the app still blocks before any
  upload when it is missing. For flat/free, weight and dims are
  optional: whatever the seller enters is saved for their records
  and labels and is NOT sent to eBay (eBay ignores the package for
  flat), and the UI says so in plain words. Only garbage is flagged
  for flat: a non-number, a zero or negative number, or a value
  over the caps (150 lbs, 108 in).
- Pounds alone stay pounds; any ounces convert to the exact total
  in ounces (2 lb 8 oz is 40 oz, never a rounded decimal).
- A policy name never sets the weight. "Calculated 2lb" is only
  the label the seller gave that policy on eBay.
- Insurance and signature are NOT in v42; they are a later build,
  pending a check of what eBay's API actually allows.
- Display: proper controls only (dropdowns, fields, buttons), no
  bare-text lists, no ID numbers in display. Every native select's
  options are dark background with light text (global `select
  option` rule in the stylesheet), because Android draws the open
  option list on a white sheet.

## Shipping fixes, v43 — audit follow-ups

- The carrier/service pickers broke on the real phone because
  `select.children` is an HTMLCollection (no Array methods), while
  the node test mocks used arrays. Fixed with `Array.from(...)` at
  both throw sites; a static guard test now fails the suite if
  `.children.` or `.options.` is ever followed by an Array method,
  and a real-DOM regression suite
  (`tests/real-dom-shipping.test.js`) runs the shipping flows
  against a browser-faithful DOM in both fetch orders.
- Two harvested-only service codes are labeled (display only; the
  code is always sent verbatim): `FedExSmartPost` is the eBay API
  code for FedEx Ground Economy (isdntek eBay shipping-code table;
  the seller's orphan policy is named "Calculated: FedExSmartPost"),
  and `UPSGroundSaver` is the eBay code for UPS Ground Saver
  (SolidCommerce GetEbayShipService sample enum; eBay's own label
  screens offer "UPS Ground Saver"). Neither joins the 11-code
  picker catalog; they are harvested labels only.
- Policy-for-service: an INTERNATIONAL option with no services at
  all (an empty shell eBay may serialize on a domestic-only policy)
  no longer disqualifies the policy; only a real self-ship
  international service does. When a flat policy carries the
  picked service, the page says so honestly and offers to select
  it, instead of claiming no policy exists; the create form opens
  only when no policy at all carries the service.
- The "It is live!" note now survives the post-publish page
  refresh (previously hidden again in the same tick by
  `prefillListing()`), and a select failure after a successful
  policy create can no longer report the create itself as failed.

## Camera, edit, arrange, thumbnails — v44

- Camera is now in the app (CameraX 1.3.4, `InAppCamera.kt`):
  Take photo opens a full-screen camera with a big round shutter.
  Each tap saves a photo at once and the camera stays open, so
  several angles can be shot back to back with no review screen
  and no check after each shot. A count ("3 photos") shows on
  the camera screen; Done or Back closes back to the listing,
  and the photos already taken stay in the strip. Each shot runs
  the same pipeline as before: native downscale to 1600px,
  base64, hand to the WebView's `onCameraPhoto`, which routes by
  camera target (step 1 or draft). The CAMERA permission is now
  declared (camera feature not required, so the app still
  installs on a device with no camera) and is requested the
  first time the in-app camera opens. If permission is denied,
  or the in-app camera cannot start, the old camera-app intent
  (`ACTION_IMAGE_CAPTURE`) is the fallback, never a dead button.
  MainActivity is a plain Activity, so it carries its own
  `LifecycleRegistry` and forwards the events for CameraX.
- Edit now opens in Google Photos. The edit intent is pinned to
  package `com.google.android.apps.photos` when a query shows
  Photos can handle the edit, so he gets the full Photos editing
  tools instead of the Pixel camera app editor. If Photos is not
  installed the intent stays open for the system resolver, exactly
  as before. The v37 rule is kept: the photo goes in as a
  MediaStore working copy, because a private provider URI
  resolved to nothing on his Pixel. The manifest's `<queries>`
  names the Photos package so the query resolves under Android
  11+ package visibility.
- Photos now reorder by dragging, on the step-1 strip and the
  draft strip alike. The v40 left/right arrow buttons and their
  row are gone. Drag uses pointer events (pointerdown, move, up)
  with an 8px start threshold and `touch-action: none`, so it
  works by touch on his Pixel, not just mouse drag. A drag that
  starts on the X or Edit button still taps that button. The drop
  spot is the nearest photo by center distance; the left half of
  a photo means before it, the right half after it. The order
  still rules the publish: image URLs go in strip order and the
  first photo is the main one. The move endpoint now also takes
  `{"to": n}` (final index) alongside the old direction form.
- Thumbnails are bigger and the buttons smaller: step-1 thumbs
  96px to 128px, draft thumbs 72px to 96px, the X button 30px
  to 26px, Edit min-height 30px to 26px. The arrow row is gone
  with the arrows. Fit on a narrow phone width is unchanged
  (two 128px thumbs plus gap fit in 288px of content at 320px
  wide); the fit check still passes.

## Drafts list order — v45

- The drafts list ("My drafts") shows newest first, by the date
  shown on each row. The list endpoint (`GET /api/drafts`) used
  to iterate `sorted(os.listdir(DRAFTS_DIR), reverse=True)`, but
  draft files are named `<random-uuid>.json`, so that order was
  effectively random. It now sorts by the draft's `updated`
  timestamp descending (ISO strings sort correctly as strings),
  falling back to `created` for a draft that lacks `updated`, and
  a draft missing both dates sorts last. "Newest" means most
  recently updated, so a draft he just opened or edited jumps to
  the top. The listing page renders drafts in server order, so no
  client change was needed.

## Drafts list sections — v46

- "My drafts" now has two sections. Working (unlisted) drafts sit
  on top under a "Drafts (n)" header, newest first within the
  section (v45 sort). Drafts already live on eBay sit in a
  "Listed on eBay (n)" section at the bottom, collapsible, closed
  by default; its header is a real button with aria-expanded and a
  chevron. The section itself is the indication, no badge needed.
  Listed rows keep no Post button and batch still excludes them.
  If there are no listed drafts the section is hidden entirely;
  if there are no working drafts the Drafts section shows the
  "No drafts yet" hint. Grouping is client-side on the `listed`
  flag from `GET /api/drafts`; the row's small "Listed on eBay"
  text stays as-is inside the Listed section. Boundary: the
  Listed section only knows drafts the app published
  (`ebay_listing_id` saved on publish); a draft listed outside
  the app stays in Drafts.

## Policy create 20403 — v47

- Live failure on 1.45 (Nov 8): creating a shipping policy
  answered eBay error 20403 with the message "Invalid ." — the
  field name came back empty. eBay's official Account API spec
  lists createFulfillmentPolicy 400 errors as 20400 "Invalid
  request.", 20401 "Missing field {fieldName}.", 20402 "Invalid
  input.", 20403 "Invalid {fieldName}." — so the empty field name
  means eBay did not substitute it into the message. eBay's
  standard error format carries an optional `parameters` array of
  name/value pairs; the app's `_plain_ebay_error` only read
  message + errorId and dropped `parameters`, so the field never
  reached the screen. v47 surfaces the parameters in plain words
  on the generic fallback and on every Details-carrying rewrite
  branch, capped at 160 characters, output only (never fed into
  keyword matching). A missing `categoryTypes` is ruled out as the
  cause: that would be 20401 "Missing field", not 20403, so it is
  not sent. A retired FedExSmartPost code is weakened, not proven:
  isdntek's eBay shipping-code table still lists FedExSmartPost,
  and the app's codes come from live GETs of his own policies or
  the documented set; no service code changed.
- The long policy name is the leading suspect but NOT proven (the
  full name was not visible in the screenshot). eBay's official
  FulfillmentPolicyRequest caps `name` at 64 characters; the app
  allowed 100 and the name input had no maxlength at all. v47
  caps the name at 64 in all three places: server validation in
  `_build_fulfillment_policy_body`, `maxlength="64"` on `#pc-name`,
  and the client-side `collectPolicyCreateForm` check mirroring
  the server. The review screen already shows the full name with
  no truncation, and the prefilled "Calculated " + label name
  cannot exceed 64 (labels are short).

## v48: live character counters, one thumbnail size

- The two capped fields on the create-policy form now show a live
  character count in the label, same pattern as the listing title
  counter (`0/80`): policy name `#pc-name-count` (`0/64`) and policy
  description `#pc-description-count` (`0/250`), both in the existing
  `.count` style. `updatePolicyCreateCounts()` runs on input and on
  every path that sets the fields (form open, carrier prefill, Back
  from review, cancel), so the count is never stale. No maxlength,
  validation, or error surfacing changed. The description label
  shortened to "Description" (the "(optional)" moved to the
  placeholder) so label plus counter fits on one line at 320dp.
- All photo thumbnails are now one size: `--thumb-size: 128px`,
  read by the step-1 strip, the listing-page draft strip, and the
  drafts-list rows. v44 had grown only the step-1 strip (128px)
  while the draft strip stayed 96px and the drafts list 56px, so
  the smaller screens looked like the photos had gone back to
  small. The old `.thumbs.small` 96px overrides are gone and the
  stale `small` class is off the draft-photos container. The QR
  code image is not a photo thumbnail and is unchanged. A static
  guard test (`tests/test_thumbnail_size.py`) fails the suite if
  any slot gets its own size again.

## v49: Diffui design 4 on all four screens, plus the USPS Ground Advantage fix

- Look-and-layout only (no feature change), built from the four
  Diffui design-4 images he picked Nov 8. Light theme kept
  (#f4f4f5 page, white cards, dark text, blue #2a63d8 buttons).
  Pick photos: header with a back arrow and centered "Pick Photos"
  title; photos in a 2x2 grid of large uniform square thumbs, each
  with a round X top-right and a pencil Edit pill bottom-left;
  "N of 24 photos" count below the grid; Take photo (blue filled)
  and Choose photos (outlined) side by side, then the big blue
  "Draft my listing with AI" button full width. The v44 in-app
  CameraX camera, Google Photos edit, drag-to-reorder (pointer
  drag keeps working on the grid; taps on X/Edit still tap, not
  drag), 24 photo cap, and count text are unchanged. The grid is a
  deliberate exception to the v48 one-shared-size rule: grid cells
  fill their column and stay square via aspect-ratio, so the grid
  thumbs are uniform with each other; the listing-page draft strip,
  the drafts-list rows, and the batch rows still read the shared
  `--thumb-size: 128px` variable, and `tests/test_thumbnail_size.py`
  pins the exception explicitly. The "Take another photo" button is
  gone — Take photo is always visible, so it covers the same tap.
  The phone status bar and clock in the mockup are not app UI and
  are not built.
- Listing page, plus the streamline: header "Listing Assistant"
  with a back arrow (the "?" help icon in the mockup is dropped —
  no help screen exists behind it). Photos in a row, each with Edit
  underneath as a blue text-style button that is still a real
  button (min-height 26px, the v44 Edit sizing), not a tiny text
  link. Core fields stay open and in order: Title with 0/80
  counter, Description, Condition dropdown, Price with Check sold
  prices, category number with Suggest, then the big blue button
  reading exactly "List it on eBay now". Below it, collapsed rows
  with live summary lines and chevrons: Shipping (carrier,
  service, weight in lb and oz, dimensions, updating on every
  change), Item specifics (row names, falling back to "(n rows)"),
  Shop policy numbers ("Return, Payment, Handling" — labels only,
  never raw policy IDs), Or list manually ("Copy details and
  download photos"). Nothing vital is removed: the Shipping row
  opens to the full carrier/service pickers, package fields,
  usual-policy dropdown, and create-policy form; item specifics
  keeps dynamic rows plus Add a row; shop policy numbers keeps the
  policy fields; the manual block keeps Copy title, Copy
  description, Copy everything, Download photos (.zip), Ask AI
  again, Start a new item. The ship-from form, scope-reconnect
  box, eBay status card, "It is live!" note behavior (v43 F2
  keepPublishOk), Best Offer fields, and Save as my usual settings
  all stay working. The Shipping row opens itself whenever a
  shipping error, pick screen, or create form needs to be seen.
  Currency is $ everywhere (the mockups' £ and ₹ never reach the
  app). Package inputs now autosave on input (before, they only
  saved when another field or the publish step saved the draft).
- Drafts list: centered "My drafts" title; rows separated by
  divider lines, no card borders; checkbox, 128px thumb, bold
  title, date, Open (outlined), Post (blue), Delete (grey with a
  trash icon). Newest-first sort (v45) and the two sections (v46)
  are kept. The design's quirk is fixed: the bottom section reads
  exactly "Listed on eBay (n)", starts closed, and listed rows live
  only in that section with no Post button; the small "Listed on
  eBay" pill is gone from every row.
- Batch posting: big bold "Post several drafts" title; rows show a
  checkbox, thumb, title, and date (price is not shown on the row
  — it lives on the listing page); unticking a row drops it from
  the batch and updates the confirm card, re-ticking puts it back.
  "Category for the whole batch" is a category NUMBER input (the
  "123456" in the mockup is placeholder text only, never a real
  value; the field starts empty). The confirmation is a card with
  a centered "N drafts, 1 category" heading, a big blue "Post all
  N" button, and Cancel. The v43 batch rules stay: policy pick
  shows name and type only, never raw IDs; posting is sequential,
  one failure does not stop the rest; listed drafts are excluded.
- USPS Ground Advantage fix (folded into v49 so he installs once).
  The Nov 8 live policy create failed with eBay error 20403
  UNKNOWN_SHIPPING_SERVICE because the form sent
  shippingServiceCode USPSGroundAdvantage. eBay's LSAS checker
  rejects that code on this account. eBay's own code for the same
  Ground Advantage service is USPSParcel: eBay's reports use
  USPSParcel for Ground Advantage, and his old policy is literally
  named "Calculated: USPSParcel". The app now canonicalizes every
  service code through `_canonical_service_code` (server) and
  `canonicalServiceCode` (JS): USPSGroundAdvantage maps to
  USPSParcel, everything else passes through unchanged. Applied in
  the create body builder (`_create_service_code`, so it covers
  every path that builds a policy, including a code typed in
  Other), in the policy-for-service matcher (the requested code and
  the policy's codes are both canonicalized, so a policy carrying
  USPSParcel matches a Ground Advantage pick and vice versa), in
  the harvest (a harvested USPSGroundAdvantage merges into
  USPSParcel, so the picker never offers the rejected code or shows
  two Ground Advantage entries), in the pick entry, and in the JS
  create form (`createServiceCodeFor`), the prefilled create form,
  and the service restore (a draft saved with the old code restores
  onto the USPSParcel option). The picker catalog is 10 codes now,
  not 11: Ground Advantage is offered as USPSParcel only, labeled
  "USPS Ground Advantage", and the separate "USPS Parcel Post"
  entry is gone because it was the same code for the same service.
  USPSParcel is labeled "USPS Ground Advantage" in every label map;
  USPSGroundAdvantage is kept in the label maps for READING only
  (an old policy or saved draft may still carry it) and is never
  sent. FedExSmartPost (FedEx Ground Economy) is deliberately left
  as-is: whether create accepts it is UNPROVEN. The retry test
  settles it — the same combination (Ground Advantage plus Ground
  Economy) either creates, which proves FedExSmartPost is fine, or
  fails again with UNKNOWN_SHIPPING_SERVICE, which proves it is
  also bad and it gets dropped or mapped in the next fix. Do not
  guess it away.
- Tests: Python 392/392, node 12 suites, fit check passes at
  320/360/768dp. New `tests/design4.test.js` (grid, collapsed rows
  with live summaries, back arrows, drafts wording, batch panel,
  USPS mapping) and `tests/test_usps_ground_advantage.py` (18
  tests: canonicalizer, create body on all three service slots,
  matcher both directions, harvest merge, catalog and labels).
  `tests/test_thumbnail_size.py` pins the grid exception and the
  batch-row slot; `tests/fit_check.py` check 5 was rewritten for
  the 128px thumb with the wrapped-row model (the old 56px budget
  was stale since v48), check 13 adds the batch-row slot, and check
  14 pins the design-4 structure (headers, grid, collapsed rows,
  Open outlined, Delete grey, batch confirm card, exact button
  wordings, no £ or ₹).
## v50: shipping audit fixes + photos save as they are taken + resume your draft

Part A — the Nov 8 second shipping audit, fixes F1–F5, F7, F8 and
the never-again guard. The v49 USPS fix is real and complete, but
the same 20403 could come back through a second door.

- F1: UPS Ground Saver (UPSGroundSaver) is NOT in eBay's Trading
  API ShippingServiceCodeType enum and is unproven on create. The
  create form no longer offers it (`CREATE_EXCLUDED_SERVICE_CODES`
  on the server, `create_domestic` / `create_international` in the
  shipping-services response, and the same exclusion in JS, so a
  prefilled carrier pick leaves the service blank with the plain
  "cannot be used for a new policy yet" note). It stays in the
  harvest, the matcher, and the labels, so existing policies that
  carry it (his "Calculated 2lb - Framed Prints") still match and
  still read as "UPS Ground Saver". FedExSmartPost (FedEx Ground
  Economy) is deliberately NOT removed: it stays offered until his
  live retry on 1.48 settles whether create accepts it.
- F8: draft save canonicalizes `ship_service_code` now, so the
  rejected USPSGroundAdvantage can never sit in storage again.
  Every consumer already canonicalized on read; the storage side
  is closed too.
- F2: a code typed in Other is matched case-insensitively against
  the known-code list (picker catalog plus harvested labels) before
  anything is sent: "uspsparcel" and "USPS Ground Advantage" both
  become USPSParcel. A typed code that matches nothing known is
  refused in the form with plain words ("may not know the shipping
  code... double-check it against eBay's list"), instead of being
  sent verbatim for eBay to fail with 20403. The server enforces
  the same check in `_validate_create_service_code`, so the
  browser can never sneak one past.
- F3: the policy-for-service matcher reports a NOT_SPECIFIED
  domestic option by name, like the flat honest-note path, instead
  of saying "no policy" and opening the create form toward a
  duplicate. The note says its price type isn't set on eBay, and a
  "Use that policy" button picks it. Flat still wins over
  NOT_SPECIFIED, CALCULATED still wins over both. The publish gate
  still decides whether a NOT_SPECIFIED policy can actually list,
  so reporting it never silently lists on a policy that cannot
  ship.
- F4: the publish gate rejects a policy whose domestic option has
  no shipping services at all, with the pick signal, before any
  photo work or offer creation. A pickup-only policy (no domestic
  option) still gets the old "offers no shippable service" message.
  Note for future builders: real eBay policies always carry
  shippingServices, so the test fixtures had to be made realistic
  (19 fixtures gained one) rather than the gate being loosened.
- F7: eBay's parameters text is now appended to EVERY
  `_plain_ebay_error` branch, including 190013, business-policies,
  opt/program, and 401/403 — the four branches that used to return
  without it.
- F5: the v42 section above is annotated as superseded by v49
  (catalog 10 codes, not 11; the USPSGroundAdvantage attestation
  disproven live Nov 8).
- The never-again guard: eBay's ShippingServiceCodeType enum is
  pinned in the repo as `tests/ebay_shipping_service_codes.py`
  (87 US codes, read off eBay's official Trading API page Nov 9,
  2026). Two tests guard it: the picker catalog
  (DOCUMENTED_DOMESTIC_SERVICES) is a subset of the enum, and any
  harvested code not in the enum is flagged as unproven on create.
  Run today, the flag test names exactly FedExSmartPost and
  UPSGroundSaver. Harvested codes stay exempt from the subset rule
  (they come from his live policies) but are flagged, never
  silently trusted. Honest limit: an enum allowlist catches codes
  eBay never issued; it would NOT catch an account-specific LSAS
  rejection of a code that IS in the enum — only a live create
  test catches that class.
- NOT in v50: the create-review clarity line (audit F6) still
  needs his separate tap; sale type/auction, RuName help note,
  insurance/signature, PC/browser version stay parked.

Part B — photos save the moment they are taken, and the app
remembers the open draft.

- Auto draft: the first photo taken or picked on step 1 creates a
  blank draft on the server right away (POST /api/auto-draft, the
  quick-post shape, no Gemini call) and every photo is uploaded to
  it as it arrives through the existing draft-photo append path.
  The auto draft is an ordinary draft: it shows in My drafts
  newest first (v45 sort). A photo-only draft has no title yet, so
  it shows as "(untitled)" with its thumbnail as the identifier.
  The 24 cap, the downscale, the replace-vs-append semantics of
  Choose photos vs Take photo, and the v44 CameraX flow are all
  unchanged. Remove, drag-reorder, and edit-save on step 1 also
  land on the auto draft (delete, move, replace endpoints, queued
  in order so a fast tap targets the right photo). If a photo save
  fails, the app says so in plain words ("Your photos didn't save
  to the app yet — they are still on this screen and will go with
  your listing"), abandons the auto draft, and Analyze/Quick post
  fall back to uploading the photos on screen. An abandoned auto
  draft is never deleted for him: it stays in My drafts, deletable
  there like any draft.
- No duplicates: when he taps "Draft my listing with AI" or Quick
  post with photos already in the auto draft, the app reuses that
  draft's photos (reanalyze for Analyze, GET draft for Quick post)
  and never uploads them a second time. The auto draft id is
  cleared once the real draft exists, so the next item starts
  fresh.
- Resume: the app remembers the open draft across restarts in
  `resume_draft.json` on the server (GET/POST /api/resume-draft),
  set whenever a draft opens on the listing page and cleared on
  "Start a new item", on delete, and on publish. On launch, a
  titled draft shows a plain white "Resume your draft" card on
  step 1 with the draft's name and a Continue button (one tap
  opens it), or dismiss it; a photo-only draft restores its photos
  straight into the step-1 grid instead, so he is back where he
  was. If the process died while the photo editor was open, the
  relaunch still returns to the draft; a lost edit result needs no
  special handling — the original photo is unchanged on the server,
  the draft reopens with its saved fields, and no stale error
  shows. Typed fields survive the round trip the same way
  `reloadDraftPhotosOnly` already protects them.
- Bug found by the v50 tests and fixed in the same build:
  `_append_uploads` handed back a throwaway list when a blank
  draft's photos list was empty, so the file landed on disk while
  the draft stayed empty. The auto draft starts blank, so this
  fired on the very first photo. The list is now bound into the
  draft when missing, and a blank draft's photo folder is its own
  id (keeps the "photo folder == draft id" invariant).
- Tests: Python 436/436, node 13 suites, fit check passes at
  320/360/768dp. New `tests/test_shipping_audit_v50.py` (F1/F2/F3/
  F4/F7/F8 plus the enum guard and the harvested-flag test),
  `tests/test_auto_draft_resume.py` (auto draft, reanalyze reuse,
  24 cap, the `_append_uploads` regression, resume set/get/clear,
  resume forgets a listed or deleted draft, corrupt resume file
  never 500s), `tests/v50-photo-resume.test.js` (typed-code
  normalization and warnings, create-form exclusion, NOT_SPECIFIED
  note, auto-draft persistence, no duplicates on Analyze/Quick
  post, stale fallback, resume card/restore/dismiss), and fit
  check 15 pins the resume card (white card, text shrinks and
  wraps, buttons wrap, fits at 320dp).

## v51: combined price check — sold prices plus similar items by image

- The "Check sold prices" button now opens one results screen
  with two halves, instead of opening eBay's sold search straight
  away. Nothing is removed: the sold half is the same title-based
  search, and the image half is new.
- Sold half — "What they sold for": the title text opens eBay's
  sold search page
  (`https://www.ebay.com/sch/i.html?_nkw=<title>&LH_Sold=1&LH_Complete=1`)
  in the native pop-up, exactly as before, and "Use this price"
  on an eBay item page still sets the draft price. Sold data has
  NO public eBay API: the Finding API was decommissioned
  February 5, 2025, the Marketplace Insights API is restricted
  and approval-only, and the app does not scrape eBay. The title
  already comes from Gemini looking at the photos during Analyze,
  so the photo is already informing this half.
- Image half — "Similar items listed now": eBay Browse API
  `POST https://api.ebay.com/buy/browse/v1/item_summary/search_by_image`
  (official Buy Browse API spec:
  https://developer.ebay.com/api-docs/master/buy/browse/openapi/3/buy_browse_v1_oas3.yaml).
  The request sends the draft's first photo as a base64 string
  (`SearchByImageRequest.image`), with `limit` 10 and the draft's
  category id as `category_ids` when it is a number. Auth is an
  application token from the client-credentials grant (the Client
  ID and Client Secret already in the app's `.env`); the app
  reuses the cached `_app_token()` that the Taxonomy API already
  uses, so there is no new sign-in, no new keys, and no user
  consent. The response is a `SearchPagedCollection` with
  `itemSummaries` (title, price value + currency, imageUrl,
  itemWebUrl, itemId). The app keeps only rows with a title and
  a numeric USD price above zero, and shows each with thumbnail,
  title, and price; a "Use this price" button on the row sets
  the draft price the same way the sold pop-up does (PUT the
  draft, confirm only when the server saved it).
- Honest limit, stated in the app's own words too: the image half
  returns ACTIVE listings only. It cannot show sold prices.
  The sold half still comes from the title words, and the two
  halves answer different questions — what it looks like now,
  and what ones like it sold for.
- The image half can never block the sold half. The server
  endpoint `GET /api/drafts/<draft_id>/similar` returns 200 with
  an empty list and a plain-words note on any eBay failure, token
  failure, missing photo, or empty result (404 only for a missing
  draft). Results are cached in memory for 10 minutes, keyed by
  draft, photo, category, and the photo file's modification time.
- Match thumbnails are eBay's own listing pictures, not the
  user's photos, so they are small reference images (72px) and
  deliberately do NOT read the shared `--thumb-size` variable —
  that variable is only for the user's photo slots (see
  `tests/test_thumbnail_size.py`).
- Tests: Python 454/454, node 15 suites, fit check passes at
  320/360/768dp. New `tests/test_similar_by_image.py` (request
  shape with and without a category, parsing keeps only priced
  USD rows, empty results, eBay 500, token failure, no photos,
  missing photo file, missing draft, cache reuse) and
  `tests/sold-image.test.js` (results screen opens, rows render
  thumb/title/price/button, sold URL unchanged with the
  "vintage item" fallback, price pick saves and confirms, stale
  rows clear on a re-check, empty image search leaves the sold
  half working), and fit check 16 pins the results screen
  (rows wrap, title shrinks, price never wraps, 72px thumbs,
  floor fits at 320dp).

- Aspect values are capped at 65 characters by eBay (v51 fold-in).
  Live failure Nov 8 2026 on v1.49: tapping List failed with eBay
  error 25002, "Set Includes's value of 'Doll, Gown, Stole,
  Gloves, Jewelry, Shoes, Doll Stand, Certificate of
  Authenticity' is too long. Enter a value of no more than 65
  characters." The AI had written an 82-character value and
  `_clean_aspects` only caps values at 500 (`v[:500]`), so it
  sailed through to the inventory item PUT. `_listing_readiness`
  checked missing required specifics but never value lengths, so
  nothing caught it first. The v47 parameters surfacing is what
  named the field, which is why the fix was clear. The failure is
  at the inventory item PUT (aspects ride on that call), before
  any offer is created, so a retry after shortening the value is
  clean and no stale offer exists. The fix, three parts: (1)
  `_listing_readiness` checks every item-specific value against
  the 65 limit and reports ALL over-long values at once in plain
  words, each naming the field, its current length, and the 65
  limit, before any eBay call; values are NEVER silently
  truncated — truncation would mangle his words. (2) The Gemini
  analyze prompt now tells the AI each detail value has to be 65
  characters or less, so it stops writing over-long values in the
  first place. (3) The listing page shows a live "n/65" counter
  on each item-specifics value field (same `.count` style as the
  v48 policy counters), so he sees it as he types. Tests:
  `tests/test_aspect_length.py` (82-char live value flagged by
  name, several over-long at once, exactly 65 passes, 66 fails,
  prompt carries the limit, publish blocked before any eBay call)
  and `tests/attrs-counter.test.js` (counter span on the row,
  82/65 then 65/65 then 5/65 as the value shortens); fit check 16
  pins the counter (centered, never shrinks, row wraps).

- Drafts sort tie-break (v51, found by the release gate): "updated"
  is stamped to the second, so two drafts touched in the same
  second tie — and a stable sort over random UUID filenames made
  the tie order a coin flip. The v50 newest-first test failed 4
  times in 5 runs on the v51 gate run. `api_draft_list` now breaks
  the tie with the draft file's write time (the draft written
  later is the newer one); the write time never overrides the
  shown date, it only orders drafts whose dates are equal.
  Pinned by a new test in `tests/test_draft_sort.py`, and the
  flaky v50 test now passes 10 times in 10 runs.

- List button moved (v51, seller's pick Nov 9 2026): the whole
  publish block — `#publish-error`, `#publish-ok`, `#btn-publish`
  ("List it on eBay now"), `#publish-spinner` — moved from right
  after the core fields (v49 design 4 spot) to the bottom of the
  listing page, directly above the Or list manually collapsed row.
  The error/ok feedback moves with the button so the result shows
  where he tapped. There is exactly one List button; the top spot
  holds nothing now. All JS references are by element id, so the
  move needs no code change; the fit gate checks the big button in
  its new spot at 320dp.

## v52: photos are 2K (2048px) + optical stabilization in the in-app camera

- Photo cap raised from 1600px to 2048px on the long edge (2K),
  on all three paths that can shrink a photo, so no path shrinks
  one twice: native `CameraDownscale.downscale`
  (`CameraDownscale.kt`, `MAX_EDGE` constant, default), JS
  `downscaleFile` (`app.js`, `MAX_EDGE`), and server
  `_save_image_file` (`app.py`, `img.thumbnail((2048, 2048))`).
  JPEG quality stays 85 and EXIF rotation is untouched. The
  larger base64 handoff at 2048 is fine. Thumbnails stay 128px
  (the v48 shared `--thumb-size` variable) and the v49
  pick-photos grid is unchanged — this cap is only about the
  stored listing photo. Tests: `tests/test_photo_2048.py`
  (server save shrinks 4096x3072 to 2048x1536, tall photo to
  1536x2048, at-cap and smaller photos untouched; source
  contracts pin all three caps at 2048 and sweep the photo path
  for a stale 1600 cap), `tests/photo-2048.test.js` (the real
  `downscaleFile` against a stubbed canvas: 4096x3072 to
  2048x1536, 1800x2400 to 1536x2048, at-cap and smaller
  untouched), and Kotlin `CameraDownscaleTest` (MAX_EDGE is
  2048, targetSize/sampleSize math at the 2048 cap).

- Optical stabilization in the in-app camera (v44 CameraX),
  requested explicitly for the first time. `InAppCamera`
  asks the back camera, via the Camera2 interop extender,
  for `LENS_OPTICAL_STABILIZATION_MODE ON` on both the
  preview and the capture use cases — but only when the camera
  reports ON in `LENS_INFO_AVAILABLE_OPTICAL_STABILIZATION`.
  When it does not report OIS, or the bind rejects the request,
  the camera binds exactly as before: the bind is retried once
  without OIS, and only then does the existing "Couldn't start
  the camera" fallback to the camera app run. No crash, no
  blocked capture. Capture mode stays
  `CAPTURE_MODE_MINIMIZE_LATENCY` — the fast shutter for rapid
  multi-shot is unchanged. Honest limits, recorded so the next
  builder does not overclaim: this is the lens hardware
  steadying the shot, NOT video stabilization (EIS is a video
  feature and does not apply to a still capture), and on this
  Pixel the lens may already steady by default — the change
  makes the request explicit and visible in code, it does not
  guarantee a measured shake reduction, and no on-phone shake
  test has been run. The `camera-camera2` artifact that carries
  the interop classes was already a dependency; no new
  dependency was added. Tests: source contracts in
  `tests/test_photo_2048.py` (interop imports, OIS ON
  requested, availability gate, bind retry without OIS, fast
  mode kept, no video stabilization); CI Gradle compilation is
  the Kotlin execution gate.

## v53: the status card tells the truth after the sign-in dies

- Live v1.51 bug (Nov 9 02:08 CST): the listing page said BOTH
  "eBay connected. Ready to list." (status card, from
  GET /api/ebay/status, which only checks the token file for a
  refresh_token at page load) AND "eBay is not connected. Open the
  app in this browser and tap Connect eBay." (publish error, from
  _ebay_access_token() after a failed renewal or a 401 wiped the
  tokens mid-page — the shipping-policies fetch runs on every
  listing-page open and is one of the calls that can trigger the
  wipe). Two checks, two moments, one dead sign-in; the card never
  re-checked.
- Fix: the status-card rendering moved out of prefillListing into
  refreshEbayStatus(), and a new isTokenDeathText() +
  refreshStatusIfTokenDied() pair re-runs it after any token-death
  message ("not connected", "sign-in stopped working", "sign-in
  expired and could not be renewed", the photo-permission message,
  the scope-reconnect message) at every place that surfaces one:
  publish catch, postPublish scope paths, policies fetch, policy
  lookup, batch catches, pick/ship-from re-posts, policy create.
  The card flips to not-connected with the Connect eBay button
  right there, and the List button hides until he reconnects. A
  dropped connection ("Could not reach eBay") is deliberately not
  a token death — it wipes nothing, so the card stays. The token
  refresh/clear behavior itself is unchanged.
- Plain wording: the server token-death messages no longer say "in
  this browser" (he IS in the app): "eBay is not connected. Tap
  Connect eBay to sign in again.", "Your eBay sign-in expired and
  could not be renewed. Tap Connect eBay to sign in again.",
  "Your eBay sign-in stopped working. Tap Connect eBay to sign in
  again.", and the photo-permission "Tap Connect eBay to grant
  it." The not-connected card hint and the "connection did not
  finish" banner were fixed the same way. SCOPE_RECONNECT_MSG is
  byte-identical — the JS matches on its substring.
- Tests: tests/test_status_card_truth.py (all five messages in
  their new wording, scope message unchanged, never-again guard
  that "in this browser" appears nowhere in app.py or app.js),
  tests/status-card-refresh.test.js (the live v1.51 case replayed:
  connected card, token death, card flips with Connect button and
  List hidden; non-death messages do not re-fetch; failed status
  check never throws), fit_check.py check 18 (Connect button in a
  wrapping row, no "in this browser" left).
