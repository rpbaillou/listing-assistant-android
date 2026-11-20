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

## v54: round X buttons, breathing room, edited thumbnails, the save line drains, one listing never splits

- Live v1.52 bugs (Nov 11, Barbie listing): he took 8 photos, the
  camera count said 8, only 2 were saved when he tapped Done; the
  rest trickled in later, and a restart lost the ones still waiting.
  The set then split into two drafts (a draft of 3 and a new
  listing of the rest). Editing the 2 saved photos did not change
  their thumbnails. The X buttons on the photo grid rendered as
  tall ovals, and the buttons crowded the neighboring photos.
- X buttons oval: the global `button` rule in style.css forces
  min-height 48px on every button. `.thumb-x` sets 26x26px but no
  min-height, so it rendered 26 wide by 48 tall — an oval with a
  50% radius. Fix: `min-height: 0` on `.thumb-x`, the same escape
  `#btn-resume-dismiss` already uses. Covers the step-1 strip and
  the draft strip (same class).
- Grid crowding: `.thumbs.photo-grid` inherited gap 0.5rem (8px)
  from `.thumbs`, while the X sticks out 8px (top/right -8px) and
  the Edit pill sticks out 8px (bottom/left -8px) — the buttons ate
  the whole gap and touched the next photo. Fix: gap 1.25rem
  (20px) on `.thumbs.photo-grid`, which clears both 8px overhangs
  with room to spare. The 2-column square grid and the
  drag-to-reorder indicators are unchanged.
- Edited thumbnails stale: a photo edit replaces the same file path
  (api_draft_replace_photo overwrites folder/N.jpg),
  renderDraftPhotos sets img.src="/photos/"+p with no cache
  buster, and serve_photo answered with Flask's default 12h cache
  — so the draft page and the restart/restore fetch kept showing
  the pre-edit picture even though the edited bytes had landed.
  Fix: serve_photo sends `Cache-Control: no-cache, max-age=0`, so
  every load revalidates and the current file is always what shows
  (an unchanged photo still answers 304). The step-1 strip is
  untouched: it uses blob object URLs, already fresh per edit.
- Save line drains: the auto-draft upload queue lives only in the
  page, so photos still waiting in it die when the page goes away
  (Done, restart). Each upload was slow twice over: the JS
  downscaleFile re-decoded and re-encoded camera photos the native
  camera had already shrunk to 2048px/q85, and the server
  _save_image_file PIL-decoded and re-saved every upload. Fix:
  the camera path skips the JS downscale (onCameraPhoto passes
  preShrunk through handlePickedFiles and uploadDraftPhotos;
  gallery picks still shrink), and _save_image_file keeps an RGB
  JPEG already within the 2048px cap byte for byte (anything
  bigger, non-JPEG, or non-RGB still takes the full normalize
  path). A visible "Saving N photos…" note counts the photos
  picked but not yet confirmed on the server, and hides when the
  line is empty — so he can see when it is safe to leave. The
  24-photo cap, the 2048px cap, and first-photo-is-main order are
  unchanged.
- Never split: two proven mechanisms made a second auto draft.
  (a) Any auto-draft sync failure nulled autoDraftId, so the next
  photo created a brand-new auto draft. Fix: the append retries
  the same draft (apiWithRetries, 3 attempts), and a failure that
  keeps failing keeps the id and marks the draft stale — the next
  photo retries it. Only a deleted draft ("Draft not found")
  clears the id, because a fresh draft is the right answer then.
  (b) After a restart, photo intake was not gated on the resume
  restore finishing (checkResumeDraft runs async on load), so a
  photo taken before the restore completed also started a new
  draft. Fix: the load-time restore promise is kept as resumeCheck
  and handlePickedFiles, analyzePhotos, and quickPostPhotos await
  it first, so intake reuses the remembered draft. Known edge,
  unchanged from v50: if the draft is deleted in My drafts
  mid-session, the photos taken before the deletion are on screen
  but not in the fresh draft — Analyze's stale fallback is the way
  back for those.
- Tests: tests/test_photo_cache_bust.py (no-cache header with ETag;
  a replaced photo serves its new bytes, not the cached old ones),
  tests/test_save_image_fastpath.py (small RGB JPEG kept byte
  identical; big JPEG still resized; PNG still converts; CMYK and
  grayscale JPEGs still fall through to RGB), tests/v54-photos.test.js
  (8 rapid camera photos all land with the Saving note tracking
  them, camera path uses 0 canvases while a gallery pick uses 1,
  restart after the drain restores everything with no second draft,
  failed appends retry the same draft, a deleted draft starts
  exactly one fresh draft, a photo during a slow restore lands in
  the resume draft), fit_check.py check 19 (X round, grid gap
  clears both overhangs, drag indicators survive, Saving note
  present and hidden, no-cache on /photos/, resume gate and retry
  wiring). The v50 stale-save test was updated to the v54 behavior
  (a failed save keeps the draft id).

## v55: Hide button on the price-check results card

- Live v1.53 report (Nov 11): after viewing the price check, the
  results card stays open as a big wall over the category fields,
  with no way to hide it.
- Cause: the v51 `#sold-results` card had no close control at all.
  "Check sold prices on eBay" removes `hidden` and runs the check,
  but nothing ever added `hidden` back — the sold half and the
  similar-items half both stayed on screen until the listing page
  was left.
- Fix: a ghost "Hide" button in the card header next to the
  "Price check" heading (`.card-head`, a wrapping flex row, so
  heading + button fit at 320dp and wrap rather than clip).
  Tapping it adds `hidden` to `#sold-results` and nothing else.
  Check sold prices re-opens the card with a fresh check, exactly
  as before (same handler: save, unhide, clear the similar list,
  re-fetch). No auto-hide: the card only closes on a Hide tap.
  "Use this price" is untouched on both paths; the confirmation
  note lives inside the card, so hiding hides it too, and the
  price field itself still shows the picked price.
- Tests: tests/sold-image.test.js section 7 (Hide folds the card
  away, Hide twice is harmless, Check re-opens it with fresh
  rows), fit_check.py check 20 (Hide button present in the card,
  ghost labeled Hide, Hide wiring, wrapping card header, 40px tap
  target, heading + Hide fit at 320dp).
- Release catch: the first v55 build went out with
  build.gradle.kts still at 53/1.52 (last bumped in the v53
  commit; v54 missed it too, so the 1.53 APK also installs as
  1.52). The updater compares version.json against the installed
  manifest version, and the header badge shows the manifest
  versionName, so an unbumped build would be offered forever and
  still show the old badge. Fix: bump build.gradle.kts with every
  release, and fit_check.py check 21 now fails the build when the
  gradle version and version.json disagree. The apk-v55 tag was
  re-created at the fixed commit and the release rebuilt.

## v56: full in-app photo editor on both screens, Pixel handoff gone

- Live ask (Nov 18): he needs more editing ability — enhance, brightness,
  contrast, and things of that nature — because the eBay app itself will
  not let him edit photos on these listings (it says something about an
  inventory process), so our editor is the only editor he has. Both
  screens, and keep future iPhone use in mind.
- History: Nov 3 he picked the Pixel/Google Photos editor because the
  in-app editor only did rotate. Nov 8 proved the handoff fragile:
  Android can kill the app while the outside editor is open, and on
  relaunch the app opens step 1 fresh with no record of the draft he
  was in (only way back is the My drafts link). The handoff is deleted,
  not fixed: no ACTION_EDIT, no MediaStore working copy, no
  Android.editDraftPhoto bridge, no PhotosEditor.kt, no ACTION_EDIT or
  Google Photos entry in the manifest <queries> (camera IMAGE_CAPTURE
  stays).
- Fix: one shared editor in pure HTML/JS/canvas web code, used by step 1
  (openEditor) and the draft page (openDraftEditor, which fetches the
  photo's bytes first). No native Android editor code, no new Android
  permissions, no new native dependency. Portability constraint: the
  same editor code can later run in an iOS WebView or a browser version
  without being rewritten — that is why it is web code, not Kotlin.
- Abilities: Auto enhance (one tap, histogram-based: dark photos get
  brightness, flat photos get contrast, capped so a moody photo is never
  blasted); sliders for Brightness, Contrast, Saturation, Warmth,
  Sharpness, all live; one-tap filters Original, Vivid, Warm, Cool,
  Mono (presets are slider values, so a manual slider move clears the
  active chip); crop freeform plus presets Original, Square, 4:3, 16:9;
  rotate 90, straighten (-15..15 degrees, crop auto-insets out of the
  empty corners), flip horizontal; Reset restores the photo as opened;
  Apply saves back into the same slot, Cancel discards everything.
  Adjustments are never baked into the source image until Apply, and
  the preview and the save run the same render path, so what he sees is
  what saves. Apply writes at the 2048px long-edge cap, JPEG q85,
  through the existing replace endpoints (step 1: replaceAutoDraftPhoto;
  draft: /api/drafts/<id>/photos/<i>/replace plus a thumbnail refresh,
  which shows at once under the v54 no-cache rule).
- Tests: tests/editor-math.test.js (filter strings, auto-enhance values,
  crop math, straightened-frame geometry, sharpen kernel),
  tests/photo-editor.test.js (29 checks: every adjustment reaches the
  render, crop/rotate/straighten/flip, Reset/Cancel/Apply on step 1,
  draft Edit opens the in-app editor and its Apply saves + refreshes),
  tests/multi-photo.test.js (draft Edit section rewritten to the in-app
  editor), tests/test_mediastore_edit.py and
  tests/test_manifest_queries_edit.py rewritten as no-handoff contracts,
  fit_check.py checks 22 (controls present and wired on both screens)
  and 23 (editor box scrolls, 44px sliders, active-chip contrast).
  fit_check.py main() now returns 1 when any check fails (it previously
  fell off the end and always exited 0, so a red gate could not stop a
  release).
- Release note: build.gradle.kts must be bumped to 56/1.55 in the code
  commit (check 21 guards it; v55 caught the unbumped-gradle defect).

## v57: photos-only update for a listing that is already live

- Live ask (Nov 18): the v56 editor only works on drafts. Once a
  listing is live, the app had no way to send a photo change to it:
  List only ever creates a brand-new listing, never updates one, and
  List on an app-published draft is blocked with 409. There was no
  revise path in the app at all. The eBay app will not edit photos on
  these listings either (it says something about an inventory
  process), so the only path besides eBay's website is this one.
- Fix: a photos-only revise, entered from the "Listed on eBay"
  section of My drafts. Each listed draft gets an Update photos
  button; it opens the draft on the listing page, where the photo
  grid, the v56 editor, add, delete and drag reorder all work as
  before. For listed drafts the listing page shows an Update live
  listing photos button instead of List. Only drafts this app
  published are eligible (ebay_listing_id + ebay_offer_id); anything
  else gets a plain refusal and no eBay call.
- Mechanics (reuses the publish pieces, nothing new invented):
  preview GETs the inventory item (sku "la" + draft id, or the stored
  ebay_sku) and uses its current imageUrls as the base. Publish and
  each successful update record ebay_image_urls + ebay_photo_hashes
  (sha256 of each local file, in order) on the draft; the plan matches
  hashes to stored URLs, so unchanged photos keep their existing
  eBay-hosted URLs and only changed/new photos are uploaded, via the
  existing Media API helper. A reorder moves the URLs with the photos
  and uploads nothing; a delete drops its URL. When the stored record
  does not line up with what eBay shows (photos changed somewhere
  other than this app, or a draft published before v57 kept no
  record), every photo is uploaded once and the confirm screen says
  so plainly instead of guessing.
- Review before submit: the Update button only fetches the preview.
  The confirm card lists exactly what will change (which photos
  upload, how many are removed, whether the order changed) and says
  only the photos change. Nothing goes to eBay until he taps Update
  live listing on that card. The execute endpoint recomputes the plan
  fresh — the client sends only confirmed:true, never the plan — so a
  stale preview cannot send a stale update. An unchanged plan sends
  nothing and says the live listing already shows these photos.
- Execute: uploads the changed/new photos, PUTs the inventory item
  back exactly as eBay returned it with only imageUrls swapped (no
  new offer, no re-publish, title/price/policies/quantity untouched),
  then GETs the item back and compares imageUrls in order. Only a
  verified match updates the draft's stored photo record. A listing
  with bids, a gone item, or any eBay error surfaces eBay's message
  plainly and stops; failures are logged with status + errorId +
  message (never a token or photo bytes). No silent retries beyond
  the Media helper's own one 5xx retry.
- Tests: tests/test_revise_photos.py (20 checks: eligibility 404/409/
  400, unchanged/edited/added/removed/reordered/legacy/external-change
  plans, missing local file named, confirmed required with zero eBay
  calls, execute uploads only the changed photo and PUTs the full item
  with only imageUrls swapped, unchanged sends nothing, PUT error and
  verify mismatch surface plainly and keep the old draft record,
  scope-reconnect flag), tests/revise-photos.test.js (22 checks:
  row buttons, status-card button visibility, confirm card, Cancel
  sends nothing, Confirm posts confirmed:true only, unchanged and
  failure paths), fit_check.py check 24 (entry point, confirm card,
  photos-only server shape, publish records the photo state).
  The Media API is production-only (not supported in Sandbox), so the
  revise flow cannot be proven end to end in CI: the eBay calls in
  tests are mocked, and live proof needs his one-listing trial after
  install. Do not claim live verification that was not done.
- Release note: build.gradle.kts must be bumped to 57/1.56 in the code
  commit (check 21 guards it; v55 caught the unbumped-gradle defect).

## v58: editor tilt, crop box, and Auto enhance

- Live ask (Nov 18): two editor problems on the v56 editor, plus a
  third from the same day. (1) A photo showed up tilted about 14
  degrees in Edit, though the original was straight, and Edit had
  worked fine before v56. (2) The crop box would not grow back once
  shrunk. (3) Auto enhance blew out the brightness on white-box
  photos.
- Cause, proven by his live test: tapping Reset in the editor
  straightened the photo. Reset zeroes rotation, flip, straighten,
  adjustments, filter, and crop, and only straighten can remove a
  14-degree tilt (rotate is 90-degree steps, flip mirrors, the
  sliders do not rotate), so the tilt at open was a nonzero
  Straighten value stuck on from a previous edit session. The small
  crop box was the same cause: straightening shrinks the safe frame
  and the straighten handler only clamped the crop into it, never
  growing it back. The leak, reproduced in a node harness before
  the fix: closeEditor reset neither the state nor the sliders, so
  after Cancel both editStraighten and the straighten slider still
  read 14; openEditorFor reset the state but the slider DOM still
  read 14 until the image loaded, and any input event in that
  window (a WebView form-state restore, a stray touch) read the
  stale 14 back into state — after which the image-load sync
  faithfully preserved the corrupted value. A second in-session
  cause was also possible: the editor panel scrolls, and a scroll
  gesture starting on the straighten slider dragged the slider
  instead of scrolling.
- Fix 1, tilt: the editor always opens with straighten (and every
  adjustment) at zero, on both screens. openEditorFor resets the
  state and calls syncEditorControls() synchronously, before the
  Image is created, so the sliders read zero the moment the editor
  opens. closeEditor now resets the state and syncs the controls
  too, so nothing survives Cancel. The image-load sync before
  drawEditor is kept. The render reads the edit state only, never
  the slider elements. The sliders also get touch-action: pan-y, so
  a vertical scroll gesture passes through to the panel instead of
  dragging a slider.
- Fix 2, crop box: the corner squares are 44px (were 28px) and the
  hit tolerance is 44px, so a fingertip landing near a corner grabs
  it instead of moving the whole box (inside the box) or doing
  nothing (outside it). Straightening now keeps the crop's relative
  place in the visible frame (new pure helper remapCropToFrame), so
  straightening to 14 and back to 0 grows the crop back to the full
  frame, and a deliberately small crop keeps its relative size and
  place. The Crop: Original preset still restores the full-frame
  crop in one tap without wiping the sliders, and a corner drag at
  straighten 0 can grow the crop back to the full frame.
- Fix 3, Auto enhance: autoEnhanceValues now caps brightness at 0
  when the mean is at or above the 118 target, so it never
  brightens an already-bright photo — lifting the whites on white
  packaging erases the faint print. When the histogram probe fails,
  the Auto button leaves the photo alone instead of applying the
  old blind +8 brightness / +12 contrast fallback.
- Tests: tests/photo-editor.test.js (13 new checks: straighten 14
  tilts the render, Cancel leaves no Straighten behind, a fresh
  open zeroes the sliders before the photo loads, a stray input
  while opening cannot re-tilt the photo, a fresh open renders
  untilted with a full-frame crop, straighten 10 insets the crop
  and back to 0 grows it back, corner drags shrink and regrow the
  crop, a near-corner tap grabs the corner, Auto enhance never
  brightens an already-bright photo, the bright white-box filter
  keeps the whites and the faint print, a failed probe applies no
  boost), tests/editor-math.test.js (5 new checks: auto-enhance
  cap at and above the target mean, dark photos still brighten,
  remapCropToFrame full-frame both ways and a small centered crop),
  fit_check.py check 25 (open sync before the Image, close clears
  the leftovers, render reads state only, 44px handles and
  tolerance, remap on the straighten handler, auto-enhance cap,
  no blind fallback, slider pan-y).
- Release note: build.gradle.kts must be bumped to 58/1.57 in the code
  commit (check 21 guards it; v55 caught the unbumped-gradle defect).

## v59: editor preview, feedback, Undo, and the stale app.js

- Live reports (Nov 18, after v58 shipped): (1) the tilt was STILL
  happening on another photo; (2) Auto enhance changes were invisible
  until after Apply, and then baked in with no undo; (3) the buttons
  gave no feedback, so he did not know if they did anything. The
  badge version was never confirmed (he tapped yes on v59 without
  answering whether the app showed 1.57 or 1.56), so the tilt work
  below covers both possibilities.
- Cause of the invisible preview: the editor panel scrolls internally
  and the photo sits at the top while the buttons and sliders sit
  below the fold, so when he scrolled down to tap Auto enhance or
  drag a slider, the photo scrolled off the top and he could not see
  the change. The code did schedule a live redraw, and the render
  did apply the adjustments, but nothing on screen showed it.
- Cause of the tilt coming back, found in the server code: Flask's
  static route answers with a 12h max-age, and the WebView does not
  clear its HTTP cache when the app updates. version.json is fetched
  with a cache buster (v38), so the badge said 1.57, but the editor
  still ran the cached v56 app.js, which has the closeEditor leak
  v58 fixed. The v58 fix itself was verified correct in the
  released code (openEditorFor resets state and syncs controls
  synchronously before the Image, closeEditor resets both, the
  render reads state only), and the tilt sequences below re-prove it
  through every open path. Same class of bug as the /photos/ no-cache
  fix in v54.
- Fix 1, preview and feedback: the preview (canvas plus status line)
  sticks to the top of the editor panel while the controls scroll
  beneath it, so the photo stays visible while he adjusts it. A
  status line in plain words says what each action just did: Auto
  enhance names the exact values it set, or that the photo was
  already bright enough to need no lift; a failed probe says it
  could not read the photo and changed nothing; filters, crop
  presets, rotate, flip, sliders, and Reset each announce
  themselves. Auto enhance and filter taps draw synchronously, not
  only on the next animation frame, so the preview visibly changes
  in the same moment as the tap; sliders keep the rAF path so
  dragging stays smooth. Apply shows Saving while it works and
  restores Apply after. Every redraw re-asserts the controls from
  the state, so a stale slider value can never sit on screen
  disagreeing with the render.
- Fix 2, Undo after Apply: Apply used to replace the photo in its
  slot with no way back. The pre-edit photo is now stashed at Apply
  time (step 1 keeps it on the photo entry, so it moves with the
  entry on reorder and leaves with it on delete; the draft page
  keeps it by photo path), and an Undo edit button appears on that
  photo on both screens until the next edit of the same photo. Undo
  restores the exact pre-edit bytes, not a re-render, and re-saves
  them the same way Apply does (the auto draft on step 1, the
  replace endpoint on the draft page). A draft Apply also confirms
  itself in the photo note ("Edit saved. Tap Undo edit on the photo
  to put the original back."). Session only: a page reload drops
  the stash and the button with it.
- Fix 3, stale app.js: the page ("/") and /static/* now send
  no-cache with max-age 0 (an after_request hook in app.py), so an
  update always reaches the WebView. An unchanged file still
  answers 304, so this costs nothing on a local server. API
  responses are untouched by the hook.
- Tests: tests/photo-editor.test.js (24 new checks: the preview
  pixels change right away after Auto enhance, a slider move, and a
  filter tap, and the status line names the values; Reset redraws
  the plain photo; Apply says Saving while it works; Undo appears
  on the edited photo, posts the original back, and restores the
  exact pre-edit bytes on step 1 and on the draft page, and is gone
  once used; a photo that was never edited has no Undo button; the
  tilt sequences — edit photo 1, Apply or Cancel, open photo 2 —
  on both screens, plus the stale-slider draft open),
  tests/editor-math.test.js (2 new checks: the Auto enhance status
  names the exact values, and says the photo needed no lift when it
  did not), tests/test_static_cache_bust.py (4 new checks: the page
  and both static files send no-cache, API responses are untouched),
  fit_check.py check 26 (sticky preview, status line present and
  dark on white, every action reports itself, taps draw
  synchronously, Apply Saving, Undo stash and buttons, shell
  revalidation). Also corrected here: the v58 section said
  photo-editor.test.js gained 12 new checks; the actual count is 13
  (42 total, up from 29).
- Release note: build.gradle.kts must be bumped to 59/1.58 in the code
  commit (check 21 guards it; v55 caught the unbumped-gradle defect).
- Lesson, Nov 18, the version.json-before-asset race: version.json
  went live at 59/1.58 while the apk-v59 release did not exist yet
  (the tag had just been pushed and CI was still building), and the
  in-app updater answered his update check with HTTP 404, because
  version.json already pointed at an asset that was not there. The
  asset landed minutes later (CI success, release published, asset
  verified downloadable), and a retry works, but the window must
  never happen again. Adopted for the next release: tag first, wait
  for CI to publish the asset, and only then commit version.json
  (tag, CI asset, version.json). The workflow builds the APK from
  the tagged commit's build.gradle.kts, not from version.json, so
  tagging the code commit works; the version.json commit lands
  after, and the final fit_check run (check 21) happens after it,
  before he is told to update.
- Live report, Nov 18, badge confirmed v1.57: the tilt STILL happens
  on 1.57, so the v58 fix missed a path or never reached him. The
  v58 fix itself is verified correct in the released code and
  re-proven in v59 through every open path in the harness (edit
  photo 1, Apply or Cancel, open photo 2, both screens, stale
  slider forced on open), and no missed path was found in the code.
  The remaining explanation that fits a badge-confirmed 1.57 with
  the tilt unchanged is the stale cached app.js: the badge reflects
  the APK version, not the JavaScript the WebView runs, and Flask's
  static route served app.js with a 12h max-age, so the editor could
  still be running the cached v56 code (the closeEditor leak) hours
  after the 1.57 update. The v59 no-cache fix is the remedy for
  that case: updating to 1.58 replaces the APK, and the new server
  forces the WebView to revalidate the script, so the v58 editor
  code finally runs. If the tilt still happens on 1.58, the
  stale-cache theory is dead and it is a missed path in the editor
  code, which needs a live repro on 1.58 (which photo, which screen,
  first open or after editing one).

## v60: editor redesign — one tool at a time, behind icons

- Live report (Nov 18, 22:23): he constantly hits a slider or
  another control by accident while scrolling all the way down to
  Apply. Everything was on one page, and the trip to the bottom
  kept catching controls. He wants it like the eBay photo tool:
  tap an icon for a setting and that brings up only that setting's
  options.
- Standing rule (Nov 18, saved in AGENTS.md): one tool at a time,
  behind icons. Never jam every control onto one long scrolling
  page. When a screen has several groups of controls, put each
  group behind its own icon in a toolbar; tapping an icon opens
  only that group's controls, and the main action (Apply/Save)
  stays fixed and reachable without scrolling. A long scrolling
  control panel is a defect, not a layout. Applies to everything
  built, not just the editor.
- Layout: a toolbar of four tools under the sticky preview —
  Adjust, Crop, Rotate, Filters — each with an icon and a label.
  Only the active tool's panel is visible; tapping another icon
  switches. Crop opens by default, since cropping is most of what
  he does (added Nov 18, 22:25), and the next open starts on Crop
  again whatever the last session left open. Apply, Cancel, and
  Reset sit in a bar stuck to the bottom of the panel, always in
  reach without scrolling.
- Panels: Adjust holds Auto enhance plus the five sliders
  (Brightness, Contrast, Saturation, Warmth, Sharpness). Crop
  holds the four crop presets plus the drag hint; the canvas crop
  handles only live in Crop mode, so outside it a stray touch on
  the photo cannot grab or move the crop (the box is still drawn).
  Rotate holds the Straighten slider plus Rotate left/right and
  Flip. Filters holds the five filter chips. Switching tools never
  touches the edit state: sliders, crop, rotation, and filters all
  survive a switch.
- Nothing is removed: the sticky preview and the status line (v59),
  Auto enhance with the v58 cap, Undo after Apply (v59), the v58
  tilt fixes, the 44px crop corners and remapCropToFrame, and the
  v59 no-cache static serving all stay exactly as they were.
- Tests: tests/photo-editor.test.js (12 new checks in section 12:
  the editor opens on Crop by default; each icon opens only its
  panel; switching keeps the adjustments; every ability still
  works through the new UI — crop preset, Reset, slider, filter,
  straighten; a corner touch outside Crop mode grabs nothing and
  leaves the crop alone; the same touch in Crop mode grabs the
  corner; reopening after Cancel starts on Crop again),
  fit_check.py check 27 (toolbar with four tools and Crop pressed,
  four panels with Crop open, controls inside their own panels,
  Reset/Cancel/Apply in a sticky bottom bar, finger-sized tool
  buttons, active tool contrast, Crop default on open and close,
  crop handles gated on Crop mode).
- Release note: build.gradle.kts must be bumped to 60/1.59 in the
  code commit (check 21 guards it; v55 caught the unbumped-gradle
  defect). Release order is the v59 order (tag first, wait for CI
  to publish the asset, only then commit version.json), so the
  updater never 404s on an asset that does not exist yet.

## v61: the photos clear after a listing goes live
- The report (Nov 18, twice): he took pictures, made a draft, and
  listed it on eBay, then went back to start and the old pictures
  were still there — he had to delete them to start a new set.
- Cause: postPublish on success only set the listing id and URL on
  the draft, showed the "It is live!" note, and re-prefilled the
  listing page. It never cleared selectedFiles, never reset the
  auto draft id, never cleared the resume record. Only the New
  button did that reset, so the step-1 grid kept the old set — and
  a photo taken next could append to the just-listed draft.
- Fix: after a CONFIRMED publish (postPublish returns "listed"),
  the step-1 working set is cleared the same way New clears it:
  previews cleared (preview URLs revoked, grid emptied, Analyze
  and Quick post disabled), auto draft id and step-1 draft id
  nulled, resume record cleared (and the resume card hidden),
  photo input cleared. New photos after that start a brand-new
  draft; they never append to the listed one.
- Guarded, not blind: the reset only runs when the photos on step
  1 are that draft's photos. Analyze / Quick post record the draft
  they built from the step-1 photos (step1DraftId, set on all four
  load paths); a photo-only draft restored into the grid is matched
  by its auto draft id instead. Listing a different draft from My
  drafts while step 1 holds another set leaves that set alone.
  New also clears step1DraftId, so starting fresh abandons the
  step-1 set too.
- Preserved: the "It is live!" note (link + item number) stays
  visible on the listing page; the listed draft stays in My drafts
  under "Listed on eBay" with its photos; draft stays set on the
  listing page, so prefill and the v57 revise flow keep working.
  No auto-navigation: he stays on the listing page with the success
  note, and step 1 is empty when he goes back to it himself.
- The intermediate publish returns never reset: needs-address,
  needs-pick, and needs-scope all return before the success tail,
  so the photos stay put while he finishes the publish.
- Batch: runBatch applies the same reset when one of its ok rows
  is the step-1 draft (the server already returns draft_id on
  every row, so no server change was needed). A draft that hit a
  wall never triggers it, and a batch of other drafts leaves the
  step-1 set alone. All three runBatch callers (confirm,
  policy-pick, ship-from) are covered because the check lives in
  runBatch itself. The v57 revise flow never resets.
- Tests: tests/publish-reset.test.js (25 checks: publish clears
  the step-1 set and the resume record while the note and the
  listed draft stay; new photos start a brand-new draft; no reset
  on needs-address / needs-pick / needs-scope; listing a different
  draft leaves the step-1 set alone; batch clears only when its
  draft went live, not on a wall and not for other drafts; no
  unhandled rejection), fit_check.py check 28 (step-1 draft
  tracked, reset in the listed tail, reset contents, listing page
  preserved, batch gating, revise untouched).
- Release note: build.gradle.kts must be bumped to 61/1.60 in the
  code commit (check 21 guards it). Release order is the v59 order
  (tag first, wait for CI to publish the asset, only then commit
  version.json).

## v62: one condition list everywhere
- The report (Nov 18, screenshots, v1.59): he picked "New other (see
  details)" for a Plastic Tablecloth listing, but the listing page
  showed "Pick a condition from the list." He had to scroll up and
  down between the dropdown and the error to figure out what
  condition to choose. They had talked about this before (the Nov 8
  "category takes: New, Used" note — same family).
- Cause: two different lists. The category allows NEW_OTHER (Metadata
  API getItemConditionPolicies, via /api/ebay/category-conditions),
  so the dropdown offered it and he picked it, and the publish path
  would have sent the enum fine. But the app's static CONDITIONS list
  had only six values and no NEW_OTHER, so the pre-publish check
  rejected his pick with "Pick a condition from the list." before
  the helpful category message ("This category takes: ...") ever ran.
  The static template dropdown had the same six values, and the
  category-conditions endpoint falls back to CONDITIONS when the
  fetch fails — so the short list was the fallback list too.
- Fix, root: CONDITIONS now carries the full ConditionEnum set in
  official-table order (CONDITION_ID_TO_ENUM), labels from
  CONDITION_DISPLAY — 16 values, NEW_OTHER included. The static
  template dropdown carries the same 16, so the check, the
  dropdown, and the fallback are one list. There is still NO bare
  USED enum: USED_EXCELLENT (3000) IS plain "Used". A condition the
  category truly does not allow is still blocked, with the message
  naming the allowed ones.
- Fix, junk values: "Pick a condition from the list." still fires
  for a value no enum table knows, but it now names what the
  category takes when that is known ("This category takes: New,
  Used."), so the seller is never left scrolling to guess.
- Fix, no more scrolling: the hint under the Condition dropdown now
  names what THIS category takes ("This category takes: New, New
  other (see details), Used.") as soon as the category's conditions
  load, so he knows before he picks. The fetch-failure hint is
  unchanged, and the takes line hides when the category is cleared
  or the fetch fails, so a stale line never lingers.
- Fix, live clear: changing the condition clears a single condition
  error on the publish line right away (clearConditionPublishError,
  wired to the dropdown's change event). A multi-problem list
  ("Fix these before listing: ...") stays until the next check,
  because it carries other problems too.
- Tests: tests/test_condition.py (full enum table in official order,
  labels match CONDITION_DISPLAY, no bare USED, template dropdown
  matches CONDITIONS), tests/test_compliance.py (NEW_OTHER allowed
  for its category passes — the exact reported case; NEW_OTHER not
  allowed is still blocked naming the allowed ones; a junk value
  names the allowed ones; endpoint fallback returns the full list),
  tests/condition-dropdown.test.js (fallback seed is the full 16;
  allowed hint shows/hides on success, failure, and empty category;
  single condition errors clear on change, non-condition errors and
  multi-problem lists stay), fit_check.py check 29 (full enum set,
  dropdown matches, takes hint, junk message names allowed,
  live clear wired).
- Release note: build.gradle.kts must be bumped to 62/1.61 in the
  code commit (check 21 guards it). Release order is the v59 order
  (tag first, wait for CI to publish the asset, only then commit
  version.json).

## v63: quantity — enter it before listing, change it live
- The report (Nov 19, v1.61): he could not enter a quantity anywhere
  and could not edit it on eBay either. The eBay app blocks editing
  his API-created listings with an inventory-process message (same
  as photos, Nov 18), so the app has to let him do both.
- Cause: there was NO quantity field anywhere in the app (no matches
  for "quantity" in the template or the listing-page JS), and the
  quantity was hardcoded to 1 in two places on the publish path
  (the inventory item availability and the offer availableQuantity).
  The v57 revise flow is photos-only by design, so a live listing's
  quantity could not be changed from the app either.
- eBay's quantity rule, verified from the Inventory API docs before
  building (not guessed): a listing has TWO quantities, and it shows
  the smaller of the two — the inventory item's
  shipToLocationAvailability.quantity and the offer's
  availableQuantity. Updating only one leaves the listing showing
  the other. And updateOffer replaces the WHOLE offer, so every
  field the offer was made with is required — a partial body with
  only availableQuantity is not enough, and listingDescription is
  required for a published offer.
- Fix, listing page: a Quantity field sits by Price — a number input,
  whole numbers only, minimum 1, default 1, with the plain hint
  "How many of this item you have to sell." It autosaves and
  prefills like Price (collectForm, listingPrefill, loadDraftIntoForm).
  The publish confirm dialog names the quantity, and the publish
  body carries it.
- Fix, publish: both hardcoded 1s now send the entered quantity
  (inventory item availability and offer availableQuantity). Invalid
  input (0, negative, fractional, non-number, blank) is blocked with
  the plain message "Enter a whole number of 1 or more for the
  quantity." — checked client-side before the confirm dialog and
  server-side in the compliance check (quantity_error rides with the
  other problems). A draft that never had a quantity (pre-v63, or
  the batch path reading an old draft) publishes as 1.
- Fix, snapshot: a saved offer is a snapshot, so changing the
  quantity after an offer was made discards it and creates a fresh
  offer carrying the new quantity — the v34/v35 rule, extended.
  The draft records ebay_offer_quantity at creation, at 25002 reuse
  (from eBay's own availableQuantity), and at publish. A reused
  offer (25002) publishes ITS saved quantity, not the requested one,
  so the draft records the reused number — otherwise a later change
  would be judged against a quantity eBay does not have.
- Fix, batch: each draft carries its own quantity through batch
  publish; a draft with a bad quantity is reported and skipped like
  a draft with no price, and the rest of the batch still runs.
- Fix, live update: a listed draft gets "Update live listing
  quantity" on the listing page (shown only for listed drafts, next
  to the photos button). It follows the v57 revise pattern: the
  preview reads the live offer (stored offer id first, sku lookup
  when it is missing or gone) and the confirm card names exactly
  what will change ("Quantity changes from 1 to 3.") — the LIVE
  quantity is the "from" value, so a stored number that drifted
  (a sale, for example) can never silently change the listing. On
  confirm, the server recomputes the plan, updates the inventory
  item quantity first, then the offer's availableQuantity as a full
  replacement built from the live offer (only the quantity swapped;
  read-only fields like status never go out; the offer body is
  built and validated BEFORE anything is sent, so a half-built
  offer fails with nothing changed), then reads both back and
  proves the new quantity took before updating the draft. An
  unchanged quantity sends nothing. Bids, sales, or any eBay error
  surfaces eBay's message plainly and stops; failures are logged
  with status + errorId + message (no tokens). Title, price,
  photos, policies, and everything else ride along untouched. No
  new offer, no re-publish.
- The v57 fit_check slice changed in this version: the "execute never
  creates an offer or re-publishes" check used to slice the photos
  execute up to the next @app.route, but the revise-quantity
  helpers now sit between the photos execute and the /photos/ route
  and legitimately touch the offer. The slice now ends at the next
  function definition, so the check still proves the PHOTOS execute
  itself never creates an offer or re-publishes.
- Tests: tests/test_quantity.py (27 tests: _parse_quantity accepts
  whole numbers of 1 or more and rejects blank/0/negative/fractional/
  non-number; publish with quantity 3 sends 3 on both levels and
  records it on the draft; untouched defaults to 1; invalid quantity
  blocked plainly with nothing sent; a quantity change discards the
  saved offer and creates a fresh one carrying the new quantity; the
  same quantity reuses the saved offer; a 25002 reuse records eBay's
  quantity, not the requested one; the publish endpoint passes the
  quantity through and defaults to 1; batch carries each draft's
  quantity and skips a bad one only; the live-update preview names
  the live and new quantities, the unchanged case, the invalid case,
  the gone-offer case, and the sku-lookup fallback; execute updates
  the item before the offer with a full replacement body, sends
  nothing when unchanged, fails plainly on a verify mismatch, on an
  unconfirmed post, and when the offer body is incomplete),
  tests/quantity.test.js (40 checks: parseQuantity, listingPrefill
  quantity, collectForm carries it, the Update quantity button shows
  only for listed drafts, preview + confirm card + Cancel sends
  nothing, confirm posts confirmed:true with the quantity and shows
  the success note with the listing link, unchanged says so plainly,
  a bad quantity shows the plain message and sends nothing, a
  preview failure shows eBay's message), fit_check.py check 30
  (no hardcoded quantity 1 left, field + hint, autosave + prefill,
  publish body + confirm, snapshot record + discard + clear, live
  update follows the v57 pattern, both levels updated and proved).
- Live proof still outstanding, same as v57: eBay's Inventory API
  calls are production-only in this flow, so the live update is only
  mock-tested — he was told to try it on ONE listing first and
  check the quantity changed on eBay.
- Release note: build.gradle.kts must be bumped to 63/1.62 in the
  code commit (check 21 guards it). Release order is the v59 order
  (tag first, wait for CI to publish the asset, only then commit
  version.json).

## v64: one update button for a listing that is already live
- The report (Nov 19, v1.62, screenshot): "What's up with all these
  update buttons? One update button and I click it updates." A listed
  draft showed two blue buttons ("Update live listing photos" from
  v57, "Update live listing quantity" from v63), plus each confirm
  card's own "Update live listing" button. He was mid-flow on the
  quantity update (the card showed "Quantity changes from 1 to 4."),
  so the quantity logic itself worked; the complaint is the two
  buttons, not the quantity flow.
- Fix: one button, one card, one confirm. The listing page has a
  single "Update live listing" button for listed drafts, and each
  "Listed on eBay" row has a single "Update listing" button. Tapping
  it plans both parts together (GET /api/drafts/<id>/revise, quantity
  as a query param) and shows one confirm card listing everything
  that will change. One confirm runs both parts. No old button label
  or id remains anywhere (checked by fit_check 31 and by the node
  tests).
- Order: photos first, then quantity. Each part re-reads live state
  before writing, so the photos PUT (which carries the whole item
  body) runs before the quantity change exists and can never
  overwrite it, and the quantity part then re-reads after the photos
  PUT. The v57 photos internals and v63 quantity internals are
  unchanged — the work moved into shared apply helpers
  (_apply_photo_revise, _apply_quantity_revise) and the single-part
  endpoints are thin wrappers over them with the same response
  shapes as before, so their tests still pass unchanged.
- Partial failure is reported honestly: a part that fails does not
  stop the other part, the answer names each part's result (the
  client shows the success note for the part that went through and
  the error note with eBay's message for the part that failed, both
  at once), and the draft record updates only for the part that
  actually succeeded — a failed part is never claimed. Both parts
  failing answers 502, as the single-part endpoints do. A scope
  shortfall is the one exception to running both parts: it means the
  sign-in itself is short a permission, so the flag answers at once.
- Preview is per-part too: a part that cannot be planned (a photo
  file missing from this phone, for example) is reported as that
  part's error with a plain note on the card ("Photos could not be
  checked: ... Only the quantity will change."), and the other part
  still plans. When both parts fail, the first error answers 502.
  Nothing changed sends nothing and says so plainly.
- Tests: tests/test_revise_combined.py (19 tests: combined preview
  with both parts changed, photos-only, quantity-only, nothing
  changed, a missing photo not blocking the quantity part, no photos
  at all not blocking it, both parts gone as 502, missing/unlisted/
  bad-quantity cases; execute with both changed, photos first then
  quantity, photos-only sending no offer PUT, quantity-only uploading
  nothing, nothing changed sending no PUTs, quantity failing with
  photos still completing, photos failing with quantity still
  completing, both failing as 502 claiming nothing, unconfirmed,
  scope flag), tests/revise-photos.test.js rewritten to the merged
  flow (one button, combined preview card, confirm posts
  confirmed:true with the quantity, both results shown, partial
  failure shows both notes, bad quantity sends nothing),
  tests/quantity.test.js revise sections updated to the merged flow,
  fit_check.py check 31 (old buttons gone, combined endpoints exist,
  preview merges both parts, a failed part does not block the other,
  execute runs photos then quantity, confirmed required, per-part
  reporting, single-part endpoints still underneath). Sections 24
  and 30 updated to the single button; the v61 slice now splits on
  the new button handler.
- Live proof still outstanding, same as v57/v63: eBay's Inventory API
  calls are production-only in this flow, so the merged update is
  only mock-tested — he was told to try it on ONE listing first and
  check eBay shows it.
- Release note: build.gradle.kts must be bumped to 64/1.63 in the
  code commit (check 21 guards it). Release order is the v59 order
  (tag first, wait for CI to publish the asset, only then commit
  version.json).

## v65: the Condition details ride in the Description
- The request (Nov 19, 21:01): "I would like the condition comments
  that I make in the listing show up in the description as well so
  Poshmark and Mercari have that information as well so easiest way
  to do it is just to take that and put it into the description. I'll
  try to keep it as short as possible so we're not going over any
  character limits." The batch (one item) was read back and confirmed
  Nov 19 22:58 ("That's everything, build it") per the
  app_quality_gate skill, then built as v65.
- Fix: the Condition details appear at the end of the Description
  field, live, as a trailing "Condition: <details>" note, so the note
  is visible in the field, copyable with Copy description, and
  published to eBay that way. Editing the details replaces the note in
  place (never two copies); clearing the details removes it; deleting
  the note by hand brings it back on the next details edit. The note
  is found by its "Condition: " marker, and the strip only ever
  removes a block that is the note we wrote (its words equal the
  details, or the exact note tracked this session) — a "Condition: "
  line he wrote in his own words is never eaten. The sync never runs
  on description input, so his own description edits are never
  fought, and text typed after the note stays put when the details
  change. The AI draft fills the Description through the draft record,
  and the note is re-applied on draft load, so the AI fill never wipes
  it. The AI rule (never guess at condition in the description) still
  stands — the note is his own words, never an AI guess.
- Publish: both publish paths send the Description field as-is, so
  they carry the note automatically (product description and offer
  listingDescription). A server backstop (_with_condition_note in
  _do_publish) appends the note when it is missing, which covers a
  draft published without ever opening the listing page (a batch
  publish of a pre-v65 draft); it never duplicates a note that is
  already there. The inventory item's conditionDescription still
  carries the details separately, exactly as before.
- Character count: a live count under the Description ("312
  characters"), updating as he types. Display only, never blocking.
  The hint names the limits he is watching: Mercari 1,000 characters,
  Poshmark 2,500.
- Copy everything no longer repeats the details: fullListingText used
  to add its own "Condition details: ..." line, which would now
  duplicate the note in the description. It skips that line when the
  description already carries the note, and keeps it for a description
  that never got one.
- Tests: tests/test_description_condition_note.py (10 tests: the
  backstop helper, publish carrying the note on both levels, no
  duplicate, no details means no note, conditionDescription still
  sent separately, batch passing description + details through),
  tests/description-condition-note.test.js (split/remove/sync pure
  behavior, draft load keeping the note, pre-v65 draft gaining it on
  open, character count, collectForm carrying the note, Copy
  everything not repeating the details line), fit_check.py check 32
  (count + hint, sync helpers and wiring, load re-applying the note,
  count following the field, server backstop, conditionDescription
  untouched, copy-everything dedup).
- Release note: build.gradle.kts must be bumped to 65/1.64 in the
  code commit (check 21 guards it). Release order is the v59 order
  (tag first, wait for CI to publish the asset, only then commit
  version.json).

## v66: the photo grid flows — layout must flow, nothing overlaps
- The report (Nov 20, 00:06, screenshot, 6 Barbie photos on step 1):
  the photo grid overlapped the elements below it — the photo-count
  line and the Take photo / Choose photos buttons were covered, and
  the bottom row of photos sat on top of the "Draft my listing with
  AI" button. His words: "Why are not the elements moving down and
  the images are overlapping them? We've had this issue before why is
  this not a standing rule that things adjust as things increase the
  things around it adjust. Or decrease. The things around it
  adjust." This is now a standing rule in AGENTS.md (layout must
  flow, nothing overlaps) and it applies to everything built, not
  just the editor. The Analyze button being covered makes this a
  broken core flow, so it got its own fast build, not a batch.
- Root cause, proven in a real Chromium layout (Playwright) against
  the shipped CSS: the grid item (.thumb-wrap) carried aspect-ratio
  1/1, so its height was locked to its width while its content
  (frame + an Undo edit button, ~31px) was taller. The grid's rows
  were sized from the locked height, so every row's content spilled
  ~31px past its row — accumulating to ~92px below the grid box for
  3 rows, which is why the photo-count, Take/Choose, and Draft
  button were all covered. And the locked height transferred back
  into width through the ratio, widening the columns ~31px (162px
  to 193px), so the grid ran off the right edge of the screen:
  sideways scroll (scrollWidth 448 against a 412px viewport) and the
  right-column X buttons half off-screen, both visible in his
  screenshot. The Undo button alone does not explain the size of the
  overlap — the accumulation across rows does. Drag transforms were
  ruled out: attachThumbDrag only toggles classes, never a
  transform.
- Fix: the square moved from the wrap to the frame. The wrap is
  height:auto now, so it grows with its content (frame + Undo
  button) and the grid — and everything below it — flows; the frame
  keeps aspect-ratio 1/1, so the photos stay square. The Undo
  button's top margin went from 0.3rem to 0.6rem (10px at the 17px
  root) so the hanging Edit pill (8px below the frame) clears it
  instead of overlapping its top by ~3px. Nothing else about the
  grid changed: 2 columns, 1.25rem gap, drag to reorder, Edit/X
  buttons, Undo button, square photos.
- Same pattern checked everywhere .thumbs is used: the listing-page
  draft strip (#draft-photos, flex .thumbs) never had aspect-ratio
  on the wrap, so it already grows with its content — verified in
  the same Chromium run (strip contains its wraps exactly, no X
  overlaps any Edit/Undo button, no sideways scroll), no change
  needed there. The drafts-list rows and batch rows are plain imgs
  with no wrap, buttons, or aspect-ratio, so the pattern cannot
  occur there.
- Tests: tests/test_photo_grid_flow.py (6 static CSS assertions
  pinning the sizing contract, plus 4 real-layout tests in Chromium:
  the reported state — 6 photos, Undo on all, 412px — 24 photos
  with Undo on all, no-Undo plus 360px and 320px widths, and the
  draft strip with Edit+Undo; each asserts the grid box grows to
  contain its content, the Analyze button sits below the grid, no
  photo or button overlaps the controls below, frames stay square,
  and the page never scrolls sideways),
  tests/photo-grid-flow.test.js (14 checks: the real renderThumbs
  with 6 photos, 3 edited — Undo button inside the wrap after the
  frame, frame with img + X + Edit pill — plus the CSS contract),
  fit_check.py check 33 (wrap/frame/grid have no locked height,
  frame is the square, base wrap has no aspect-ratio, Undo margin
  clears the pill, Undo button still in the wrap). The section 14
  and section 19 "square cells" checks were updated to the new
  structure (square on the frame, not the wrap).
- Release note: build.gradle.kts must be bumped to 66/1.65 in the
  code commit (check 21 guards it). Release order is the v59 order
  (tag first, wait for CI to publish the asset, only then commit
  version.json).

## v67: a photo added to a draft is offered to the AI
- The report (Nov 20): "If I take a photo after it moves to drafts,
  that information is not taken into consideration. For the listing
  and it should. Because I took a photo of the makeup of an item and
  that was not considered." He added a photo of an item's contents to
  an existing draft and the listing's title and description never
  reflected it.
- Cause: the capability already existed and already worked — "Ask AI
  again" POSTs /api/drafts/<id>/reanalyze, and the server loads the
  draft fresh and runs _gemini_identify over ALL its photos, so a
  newly added photo is included when reanalyze runs. But the button
  sat buried inside the collapsed "Or list manually" row at the
  bottom of the listing page, where he would never find it after
  adding a photo, and adding a photo only ever said "Added 1 photo."
  with nothing about the listing text being stale. The gap was
  discoverability at the moment it matters, not the AI.
- Fix: after a photo is successfully added to an existing draft, a
  prompt appears right there by the photos — "This listing does not
  know about the new photo yet." with a button "Update the listing
  with AI" that runs the same reanalyze flow as "Ask AI again". It
  appears only on a successful add (not on a failed upload, not when
  nothing was added, e.g. the 24-photo cap drop). After a successful
  reanalyze the prompt goes away (the listing is current again); if
  he adds another photo later it shows again. It hides when a draft
  is freshly loaded. The same code path covers a blank Quick-post
  draft too, which is the only way a quick-post draft gets AI text.
- It never runs on its own: reanalyze replaces the title,
  description, category, details (attributes), and AI notes, and his
  typed text must never be wiped silently, so it waits for his tap.
  Verified in code: _gemini_identify returns exactly those five
  keys and d.update(identified) overwrites only them; the price,
  condition, condition details, quantity, best offer settings, and
  shipping and policy picks are kept. The confirm now says so
  plainly: "Ask the AI to look at all the photos again? The title,
  description, category, details, and AI notes will be replaced with
  the AI's new version. Your typed price is kept." Both buttons share
  one reanalyzeDraft function, so the confirm is the same either way.
- The v65 condition note survives a reanalyze from the prompt:
  loadDraftIntoForm re-applies it after the AI fill, proved in the
  node tests (new AI description in the field, note still there
  exactly once).
- The prompt is a plain in-flow block reusing the .note look (blue
  tint, verified primary button), so it pushes the Title field and
  everything below it down and pulls them back up when it hides —
  the v66 flow rule, nothing overlaps.
- Tests: tests/test_reanalyze_new_photo.py (reanalyze receives a
  photo appended after the first analysis, in order; reanalyze
  replaces only the five AI fields and keeps the price, condition,
  condition details, quantity, and best offer settings; no photos
  is a 404), tests/reanalyze-prompt.test.js (35 checks: prompt
  hidden on load, appears after a successful add, button runs
  reanalyze with the honest confirm text, prompt clears after
  success and shows again on the next add, Ask AI again runs the
  same flow, cancel runs nothing and leaves the prompt up, no prompt
  on a failed upload or an empty add, condition note survives a
  reanalyze from the prompt), fit_check.py check 34 (prompt in the
  template by the photos, starts hidden, in-flow block, contrast of
  the text on the note tint, primary button pair, prompt gated on
  res.added, hidden on draft load, both buttons share one flow,
  confirm wording, prompt clears after reanalyze).
- Release note: build.gradle.kts must be bumped to 67/1.66 in the
  code commit (check 21 guards it). Release order is the v59 order
  (tag first, wait for CI to publish the asset, only then commit
  version.json).
