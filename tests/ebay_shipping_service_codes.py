"""eBay Trading API ShippingServiceCodeType enum, US codes only.

Pinned Nov 9, 2026 from eBay's official reference page:
https://developer.ebay.com/devzone/xml/docs/reference/ebay/types/ShippingServiceCodeType.html

Scope: the US service codes for the three carriers the app offers
(USPS, UPS, FedEx) plus the generic US codes (ShippingMethodStandard,
Other, Pickup, Freight, ...). Marketplace-prefixed codes (CA_, UK_,
AU_, ...) are intentionally NOT included: the app only sells on
EBAY_US, so a prefixed code can never be a valid pick here.

Two codes are deliberately ABSENT, and that absence is the point:
USPSGroundAdvantage, FedExSmartPost, and UPSGroundSaver are NOT in
eBay's enum at all. USPSGroundAdvantage was already proven live
(Nov 8, 20403 UNKNOWN_SHIPPING_SERVICE); the other two are unproven
on create and must be flagged, not silently trusted.

Refresh: re-read the page above and diff. eBay rarely changes this
enum; when it does, the picker catalog test below is what catches a
code the app offers that eBay no longer lists. Honest limit: an enum
allowlist catches codes eBay never issued. It does NOT catch an
account-specific LSAS rejection of a code that IS in the enum — only
a live create test catches that class (USPSGroundAdvantage was in
no enum either, but the class is real).
"""

EBAY_SHIPPING_SERVICE_CODES = frozenset({
    # FedEx (US).
    "FedEx2Day", "FedExExpressSaver", "FedExGround", "FedExHomeDelivery",
    "FedExInternationalEconomy", "FedExInternationalFirst",
    "FedExInternationalGround", "FedExInternationalPriority",
    "FedExPriorityOvernight", "FedExStandardOvernight",
    # UPS (US).
    "UPS2DayAirAM", "UPS2ndDay", "UPS3rdDay", "UPSGround", "UPSNextDay",
    "UPSNextDayAir", "UPSStandardToCanada", "UPSWorldWideExpedited",
    "UPSWorldWideExpress", "UPSWorldWideExpressBox10kg",
    "UPSWorldWideExpressBox25kg", "UPSWorldWideExpressPlus",
    "UPSWorldWideExpressPlusBox10kg", "UPSWorldWideExpressPlusBox25kg",
    "UPSWorldwideSaver",
    # USPS (US).
    "USPSAirmailLetter", "USPSAirmailParcel", "USPSEconomyLetter",
    "USPSEconomyParcel", "USPSExpressFlatRateEnvelope", "USPSExpressMail",
    "USPSExpressMailFlatRateBox", "USPSExpressMailFlatRateEnvelope",
    "USPSExpressMailInternational",
    "USPSExpressMailInternationalFlatRateBox",
    "USPSExpressMailInternationalFlatRateEnvelope",
    "USPSExpressMailInternationalLegalFlatRateEnvelope",
    "USPSExpressMailInternationalPaddedFlatRateEnvelope",
    "USPSExpressMailLegalFlatRateEnvelope",
    "USPSExpressMailPaddedFlatRateEnvelope", "USPSFirstClass",
    "USPSFirstClassMailInternational", "USPSGlobalExpress",
    "USPSGlobalExpressGuaranteed", "USPSGlobalPriority",
    "USPSGlobalPriorityLargeEnvelope", "USPSGlobalPrioritySmallEnvelope",
    "USPSGround", "USPSMedia", "USPSParcel", "USPSPriority",
    "USPSPriorityFlatRateBox", "USPSPriorityFlatRateEnvelope",
    "USPSPriorityMailFlatRateBox", "USPSPriorityMailFlatRateEnvelope",
    "USPSPriorityMailInternational",
    "USPSPriorityMailInternationalFlatRateBox",
    "USPSPriorityMailInternationalFlatRateEnvelope",
    "USPSPriorityMailInternationalLargeFlatRateBox",
    "USPSPriorityMailInternationalLegalFlatRateEnvelope",
    "USPSPriorityMailInternationalPaddedFlatRateEnvelope",
    "USPSPriorityMailInternationalSmallFlatRateBox",
    "USPSPriorityMailLargeFlatRateBox",
    "USPSPriorityMailLegalFlatRateEnvelope",
    "USPSPriorityMailPaddedFlatRateEnvelope",
    "USPSPriorityMailRegionalBoxA", "USPSPriorityMailRegionalBoxB",
    "USPSPriorityMailRegionalBoxC", "USPSPriorityMailSmallFlatRateBox",
    "USPSStandardPost",
    # Generic US codes.
    "ExpeditedInternational", "ExpeditedShippingFromOutsideUS", "Freight",
    "FreightShipping", "FreightShippingInternational",
    "InternationalNotSelected", "InternationalPriorityShipping",
    "LocalDelivery", "NotSelected", "Other", "OtherInternational",
    "Pickup", "ShippingMethodExpress", "ShippingMethodOvernight",
    "ShippingMethodStandard", "StandardInternational",
    "StandardShippingFromOutsideUS",
})
