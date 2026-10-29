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
