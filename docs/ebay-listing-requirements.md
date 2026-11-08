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
  error 20418), `marketplaceId` (`EBAY_US`), `handlingTime`
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
