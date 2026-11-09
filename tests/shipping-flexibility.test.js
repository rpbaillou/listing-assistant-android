/* v42: node tests for the frontend shipping-flexibility UI in
   static/app.js: carrier picker (USPS/UPS/FedEx) + service picker
   per carrier, pounds + ounces weight boxes always visible, the
   carrier+service pick finding the matching domestic-only
   CALCULATED policy or opening the v41 create form prefilled
   (review-before-create still stands), and the dark select-option
   styling the contrast rule requires. Same stateful-stub harness as
   shipping-chooser.test.js: values, text, and hidden toggles persist
   across $() calls. Run: node tests/shipping-flexibility.test.js
   (exit 0 = pass). */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

function makeClassList() {
  const set = new Set();
  return {
    add(c) { set.add(c); },
    remove(c) { set.delete(c); },
    toggle(c, force) {
      if (force === undefined) { set.has(c) ? set.delete(c) : set.add(c); }
      else if (force) set.add(c);
      else set.delete(c);
    },
    contains(c) { return set.has(c); },
  };
}

function makeEl() {
  const el = {
    value: "",
    textContent: "",
    checked: false,
    disabled: false,
    children: [],
    style: {},
    dataset: {},
    options: [],
    classList: makeClassList(),
    addEventListener() {},
    removeEventListener() {},
    appendChild(child) { el.children.push(child); return child; },
    querySelector() { return makeEl(); },
    querySelectorAll() { return []; },
    getContext() { return null; },
    getBoundingClientRect() { return { left: 0, top: 0, width: 100, height: 100 }; },
    scrollIntoView() {},
    setPointerCapture() {},
    releasePointerCapture() {},
    focus() {},
    click() {},
  };
  let _html = "";
  Object.defineProperty(el, "innerHTML", {
    get() { return _html; },
    set(v) { _html = v; el.children = []; },
  });
  return new Proxy(el, {
    get(t, p) {
      if (p in t) return t[p];
      if (typeof p === "string") return undefined;
      return undefined;
    },
    set(t, p, v) { t[p] = v; return true; },
  });
}

const registry = {};
const fetchCalls = [];
let fetchQueue = [];
const sandbox = {
  document: {
    getElementById(id) {
      if (!registry[id]) registry[id] = makeEl();
      return registry[id];
    },
    createElement() { return makeEl(); },
    querySelectorAll() { return []; },
    addEventListener() {},
    body: makeEl(),
  },
  window: {
    addEventListener() {},
    location: { search: "" },
    history: {},
    innerWidth: 400,
  },
  fetch: async (url, opts = {}) => {
    fetchCalls.push({ url, opts });
    const next = fetchQueue.length ? fetchQueue.shift() : {};
    return { ok: true, status: 200, text: async () => JSON.stringify(next) };
  },
  URLSearchParams,
  console,
  setTimeout,
  clearTimeout,
};
sandbox.window.document = sandbox.document;
vm.createContext(sandbox);
const src = fs.readFileSync(
  path.join(__dirname, "..", "app", "src", "main", "python", "static", "app.js"), "utf8");
vm.runInContext(src, sandbox);

let failures = 0;
function check(name, cond) {
  if (!cond) { failures++; console.log("FAIL: " + name); }
  else { console.log("ok: " + name); }
}
const $ = (id) => sandbox.document.getElementById(id);
const hidden = (id) => $(id).classList.contains("hidden");
const serviceTexts = () => $("ship-service").children.map((o) => o.textContent);
const serviceValues = () => $("ship-service").children.map((o) => o.value);

(async () => {
  // The service catalog: all 10 attested picker codes, plain labels,
  // no IDs. v43: the label map also carries the 2 harvested-only
  // codes his policies use (UPSGroundSaver, FedExSmartPost), so the
  // map is 12 entries — the picker catalog itself stays exactly the
  // 10 documented codes (Python CatalogTest covers that side).
  // v49: USPSGroundAdvantage is gone from the catalog (eBay's LSAS
  // checker rejects it on this account — Nov 8 live create, 20403
  // UNKNOWN_SHIPPING_SERVICE); Ground Advantage is offered as
  // USPSParcel only, labeled "USPS Ground Advantage", and the
  // separate "USPS Parcel Post" entry is gone (same code, same
  // service). The map still labels the old code for reading.
  const labels = sandbox.carrierServiceLabels();
  const documented = ["USPSParcel", "USPSPriority",
    "UPSGround", "UPS3rdDay", "UPS2DayAirAM", "UPSNextDay",
    "FedExGround", "FedExHomeDelivery", "FedEx2Day", "FedExExpressSaver"];
  check("catalog has all 10 attested codes",
    documented.every((c) => c in labels));
  check("catalog labels are plain names, never codes or IDs",
    labels.USPSPriority === "USPS Priority Mail" &&
    labels.USPSParcel === "USPS Ground Advantage" &&
    labels.UPS3rdDay === "UPS 3 Day Select" &&
    labels.FedExHomeDelivery === "FedEx Home Delivery" &&
    labels.FedExExpressSaver === "FedEx Express Saver");
  check("the rejected Ground Advantage code is not offered",
    !("USPSGroundAdvantage" in labels));
  check("harvested-only codes have plain labels too",
    labels.UPSGroundSaver === "UPS Ground Saver" &&
    labels.FedExSmartPost === "FedEx Ground Economy");
  check("no invented codes in the catalog",
    !("USPSExpress" in labels) && !("USPSFirstClass" in labels) &&
    !("UPS3DaySelect" in labels));

  // Carrier services fetch groups by carrier; unclassified extras stay.
  fetchQueue = [{ carriers: {
    USPS: [{ code: "USPSParcel", label: "USPS Ground Advantage" },
           { code: "USPSPriority", label: "USPS Priority Mail" }],
    UPS: [{ code: "UPSGround", label: "UPS Ground" }],
    FedEx: [{ code: "FedEx2Day", label: "FedEx 2 Day" }],
  }, other: [{ code: "ZZZCustom", label: "ZZZCustom" }] }];
  await sandbox.fetchCarrierServices();
  check("fetch groups carriers from the account harvest",
    fetchCalls.some((c) => String(c.url).includes("/api/ebay/shipping-services")));

  // Carrier picker fills the service picker for that carrier only.
  $("ship-carrier").value = "USPS";
  sandbox.populateShipService();
  check("USPS services listed with plain labels",
    serviceValues().includes("USPSParcel") &&
    serviceValues().includes("USPSPriority") &&
    serviceTexts().includes("USPS Ground Advantage") &&
    serviceTexts().includes("USPS Priority Mail"));
  check("other carriers' services are not in the USPS list",
    !serviceValues().includes("UPSGround") &&
    !serviceValues().includes("FedEx2Day"));
  check("unclassified account service stays reachable",
    serviceValues().includes("ZZZCustom"));
  check("Other (type the code) is always last",
    serviceValues()[serviceValues().length - 1] === "__OTHER__" &&
    serviceTexts()[serviceTexts().length - 1] === "Other (type the code)");
  check("service options carry no ID numbers",
    !serviceTexts().join(" ").match(/\b\d{9,}\b/));

  $("ship-carrier").value = "FedEx";
  sandbox.populateShipService();
  check("FedEx services listed",
    serviceValues().includes("FedEx2Day") &&
    !serviceValues().includes("USPSPriority"));

  // No carrier picked: placeholder says so, no carrier services shown
  // (the unclassified account service and Other stay reachable).
  $("ship-carrier").value = "";
  sandbox.populateShipService();
  check("no carrier shows the pick-a-carrier placeholder",
    serviceTexts()[0] === "— Pick a carrier first —" &&
    !serviceValues().includes("USPSPriority") &&
    !serviceValues().includes("UPSGround"));

  // Effective code: dropdown value, or the typed code when Other.
  $("ship-carrier").value = "USPS";
  sandbox.populateShipService();
  $("ship-service").value = "USPSPriority";
  check("dropdown code is the effective code",
    sandbox.shipServiceCode() === "USPSPriority");
  $("ship-service").value = "__OTHER__";
  $("ship-service-other").value = "USPSExpress";
  check("Other returns the typed code",
    sandbox.shipServiceCode() === "USPSExpress");

  // Restore: listed code selects, unlisted code goes to the Other box.
  sandbox.restoreShipService("USPSPriority");
  check("restore listed code selects it",
    $("ship-service").value === "USPSPriority" && hidden("ship-service-other"));
  sandbox.restoreShipService("USPSExpress");
  check("restore unlisted code types it into the Other box",
    $("ship-service").value === "__OTHER__" &&
    $("ship-service-other").value === "USPSExpress" &&
    !hidden("ship-service-other"));

  // Sync from a picked policy: carrier + service shown together.
  sandbox.syncShipPickers("UPS", "UPSGround");
  check("sync shows the policy carrier and service",
    $("ship-carrier").value === "UPS" &&
    $("ship-service").value === "UPSGround");

  // Pounds + ounces conversion for the weight boxes.
  check("lb/oz boxes win over legacy",
    JSON.stringify(sandbox.draftPackageLbOz(
      { package_weight_lb: "2", package_weight_oz: "8",
        package_weight_value: "4", package_weight_unit: "OUNCE" })) ===
    JSON.stringify({ lb: "2", oz: "8" }));
  check("legacy POUND 2 converts to 2 lb",
    JSON.stringify(sandbox.draftPackageLbOz(
      { package_weight_value: "2", package_weight_unit: "POUND" })) ===
    JSON.stringify({ lb: "2", oz: "" }));
  check("legacy OUNCE 40 converts to 2 lb 8 oz",
    JSON.stringify(sandbox.draftPackageLbOz(
      { package_weight_value: "40", package_weight_unit: "OUNCE" })) ===
    JSON.stringify({ lb: "2", oz: "8" }));
  check("bad saved weight restores empty, never a wrong weight",
    JSON.stringify(sandbox.draftPackageLbOz(
      { package_weight_value: "abc", package_weight_unit: "OUNCE" })) ===
    JSON.stringify({ lb: "", oz: "" }));

  // Package completeness: weight (lb or oz) plus all three sizes.
  $("p-weight-lb").value = "2"; $("p-weight-oz").value = "8";
  $("p-length").value = "12"; $("p-width").value = "9"; $("p-height").value = "1";
  check("full package is complete", sandbox.shippingPackageComplete());
  $("p-height").value = "";
  check("missing a size is not complete", !sandbox.shippingPackageComplete());
  $("p-height").value = "1"; $("p-weight-lb").value = ""; $("p-weight-oz").value = "";
  check("no weight is not complete", !sandbox.shippingPackageComplete());

  // Autosave carries the lb/oz boxes and the carrier pick per draft.
  $("p-weight-lb").value = "2"; $("p-weight-oz").value = "8";
  $("ship-carrier").value = "USPS";
  $("ship-service").value = "USPSPriority";
  const form = sandbox.collectForm();
  check("autosave sends lb/oz, dims, carrier and service",
    form.package_weight_lb === "2" && form.package_weight_oz === "8" &&
    form.package_length === "12" && form.ship_carrier === "USPS" &&
    form.ship_service_code === "USPSPriority");
  check("autosave no longer sends the old single weight box",
    !("package_weight_value" in form) && !("package_weight_unit" in form));

  // Policy find: a match selects it with no review screen. The
  // create form starts hidden in the real page; model that here.
  vm.runInContext("draft = { id: 'd1' };", sandbox);
  $("policy-create-form").classList.add("hidden");
  sandbox.openShippingPick([
    { id: "111", name: "Calculated USPS Priority", cost_type: "CALCULATED",
      service_code: "USPSPriority", carrier: "USPS" },
    { id: "222", name: "Dummy Flat", cost_type: "FLAT_RATE" },
  ]);
  $("ship-carrier").value = "USPS";
  sandbox.populateShipService();
  $("ship-service").value = "USPSPriority";
  fetchCalls.length = 0;
  fetchQueue = [{ draft: { id: "d1" } },
                { policy_id: "111", name: "Calculated USPS Priority" },
                { draft: { id: "d1" } }];
  await sandbox.onShipServicePicked();
  check("match selects the existing policy",
    $("p-fulfillment").value === "111");
  check("match note names the policy, no review screen opened",
    $("ship-service-note").textContent.includes("Using your policy: Calculated USPS Priority") &&
    hidden("policy-create-form"));
  check("match asked the policy-for-service endpoint",
    fetchCalls.some((c) => String(c.url).includes("/api/ebay/policy-for-service") &&
      String(c.opts.body || "").includes("USPSPriority")));

  // Policy find: no match opens the create form prefilled, review stands.
  vm.runInContext("draft = { id: 'd1' };", sandbox);
  $("ship-carrier").value = "FedEx";
  sandbox.populateShipService();
  $("ship-service").value = "FedEx2Day";
  fetchQueue = [{ draft: { id: "d1" } },
                { policy_id: null },
                { domestic: [{ code: "FedEx2Day", label: "FedEx 2 Day" },
                             { code: "FedExGround", label: "FedEx Ground" }],
                  international: [] }];
  await sandbox.onShipServicePicked();
  // openCreateFormPrefilled runs un-awaited after a no-match (it
  // awaits the form's own fetch); give it one tick to land.
  await new Promise((r) => setTimeout(r, 0));
  check("no match opens the create form",
    !hidden("policy-create-form"));
  check("no match note says a policy must be created",
    $("ship-service-note").textContent.includes("No policy with that service yet"));
  check("prefill is calculated with that service, domestic only",
    $("pc-dom-cost-type").value === "CALCULATED" &&
    $("pc-dom-service-1").value === "FedEx2Day" &&
    $("pc-international").checked === false &&
    $("pc-local-pickup").checked === false &&
    $("pc-pickup-only").checked === false);
  check("prefill suggests a name the seller can change",
    $("pc-name").value.includes("Calculated") &&
    $("pc-name").value.includes("FedEx 2 Day"));

  // The contrast rule: every native select's options are dark bg +
  // light text, with the global safety net in the stylesheet.
  const css = fs.readFileSync(
    path.join(__dirname, "..", "app", "src", "main", "python", "static", "style.css"), "utf8");
  const rule = css.match(/select\s+option\s*\{[^}]*\}/);
  check("stylesheet carries the select option safety-net rule", !!rule);
  check("select options are dark background with light text",
    !!rule && /background\s*:\s*#1c1c1e/.test(rule[0]) &&
    /color\s*:\s*#fff/.test(rule[0]));
  const html = fs.readFileSync(
    path.join(__dirname, "..", "app", "src", "main", "python", "templates", "index.html"), "utf8");
  check("shipping section has carrier, service, lb, oz and dims controls",
    html.includes('id="ship-carrier"') && html.includes('id="ship-service"') &&
    html.includes('id="p-weight-lb"') && html.includes('id="p-weight-oz"') &&
    html.includes('id="p-length"') && html.includes('id="p-width"') &&
    html.includes('id="p-height"'));
  check("package section is always visible (never hidden for flat/free)",
    !html.match(/id="shipping-package"[^>]*class="[^"]*hidden/));
  check("shipping section shows no policy ID numbers in display text",
    !html.match(/ship-carrier[\s\S]{0,200}\d{9,}/));

  if (failures) { console.log(failures + " FAILURES"); process.exit(1); }
  console.log("ALL SHIPPING-FLEXIBILITY TESTS PASSED");
})();
