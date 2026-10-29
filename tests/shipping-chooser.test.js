/* v35: node tests for the frontend Shipping dropdown in static/app.js.
   app.js is a browser script, so this stubs the DOM with STATEFUL
   elements (unlike scope-reconnect.test.js, whose proxy elements are
   no-ops): values, text, and hidden toggles must persist across $()
   calls, or the dropdown cannot be observed at all. The test drives
   the real functions: openShippingPick seeds the policy list + pick
   mode, hidePublishMsg resets pick mode (browse mode),
   selectShippingPolicy picks, refreshShippingChooser follows the
   number box. The v34 bare-text button list is gone: one compact
   dropdown shows policy NAMES ONLY (no ID numbers, no duplicated
   header line — the closed dropdown IS the current-selection
   display), one short line states flat rate vs charges-by-weight,
   and package fields show only for calculated policies, always
   empty unless the draft already has saved values (a policy named
   "Calculated 2lb" never sets 2lb).
   Run: node tests/shipping-chooser.test.js (exit 0 = pass). */
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
  /* Real DOM: setting innerHTML clears the child nodes. The stub must
     model that, or a re-render appends duplicates instead of replacing. */
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
const optionTexts = () => $("shipping-select").children.map((o) => o.textContent);
const optionValues = () => $("shipping-select").children.map((o) => o.value);

(async () => {
  // Plain type labels (shared with the untouched batch pick list).
  check("calculated label", sandbox.policyTypeLabel("CALCULATED") === "calculated (needs package weight)");
  check("flat label", sandbox.policyTypeLabel("FLAT_RATE") === "flat rate");
  check("pickup label", sandbox.policyTypeLabel("NOT_SPECIFIED") === "local pickup only");
  check("unknown label", sandbox.policyTypeLabel(null) === "unknown type");

  // Short type lines under the dropdown.
  check("calculated type line", sandbox.shippingTypeLine("CALCULATED") === "Charges by weight.");
  check("flat type line", sandbox.shippingTypeLine("FLAT_RATE") === "Flat rate.");
  check("pickup type line", sandbox.shippingTypeLine("NOT_SPECIFIED") === "Local pickup only.");
  check("unknown type line", sandbox.shippingTypeLine(null) === "Unknown type.");

  // Seed via the reactive-pick entry: dropdown renders, pick message shows.
  const POLICIES = [
    { id: "111", name: "Calculated 2lb", cost_type: "CALCULATED" },
    { id: "222", name: "Dummy Flat", cost_type: "FLAT_RATE" },
  ];
  sandbox.openShippingPick(POLICIES, "That shipping policy number no longer works. Pick one below.");
  check("dropdown renders placeholder + two policies",
    optionTexts().length === 3 && optionTexts()[0].includes("Pick a shipping policy"));
  check("dropdown options are names only, no IDs",
    optionTexts()[1] === "Calculated 2lb" && optionTexts()[2] === "Dummy Flat" &&
    !optionTexts().join(" ").includes("111") && !optionTexts().join(" ").includes("222"));
  check("pick message shown", !hidden("shipping-pick-msg") &&
    $("shipping-pick-msg").textContent.includes("no longer works"));

  // Dropdown value + type line + package visibility follow the number box.
  $("p-fulfillment").value = "111";
  sandbox.refreshShippingChooser();
  check("calculated selects in dropdown", $("shipping-select").value === "111");
  check("calculated type line states charges by weight",
    $("shipping-type-line").textContent === "Charges by weight.");
  check("calculated shows package fields", !hidden("shipping-package"));
  check("calculated shows EMPTY package fields (never from the policy name)",
    $("p-weight").value === "" && $("p-length").value === "" &&
    $("p-width").value === "" && $("p-height").value === "");
  check("type line carries no ID numbers",
    !$("shipping-type-line").textContent.includes("111"));
  $("p-fulfillment").value = "222";
  sandbox.refreshShippingChooser();
  check("flat selects in dropdown", $("shipping-select").value === "222");
  check("flat type line states flat rate",
    $("shipping-type-line").textContent === "Flat rate.");
  check("flat hides package fields", hidden("shipping-package"));
  $("p-fulfillment").value = "999";
  sandbox.refreshShippingChooser();
  check("unknown number leaves placeholder + details line + package fields",
    $("shipping-select").value === "" &&
    $("shipping-type-line").textContent === "Details not loaded yet." &&
    !hidden("shipping-package"));
  $("p-fulfillment").value = "";
  sandbox.refreshShippingChooser();
  check("empty number clears type line",
    $("shipping-type-line").textContent === "");

  // Browse mode: selecting saves the number + refreshes, never publishes.
  sandbox.hidePublishMsg();
  check("pick message hidden after fresh attempt", hidden("shipping-pick-msg"));
  fetchCalls.length = 0;
  /* app.js's `draft` is a lexical binding in the vm context, so a plain
     sandbox.draft assignment would not reach it; evaluate the
     assignment inside the context instead. */
  vm.runInContext("draft = { id: 'd1' };", sandbox);
  $("p-fulfillment").value = "111";
  sandbox.refreshShippingChooser();
  await sandbox.selectShippingPolicy("222");
  check("browse select sets the number box", $("p-fulfillment").value === "222");
  check("browse select refreshes dropdown to flat + hides package",
    $("shipping-select").value === "222" &&
    $("shipping-type-line").textContent === "Flat rate." &&
    hidden("shipping-package"));
  check("browse select never publishes",
    !fetchCalls.some((c) => String(c.url).includes("/api/ebay/publish")));
  check("browse select persists via draft autosave",
    fetchCalls.some((c) => String(c.url).includes("/api/drafts/d1") &&
      String(c.opts.body || "").includes('"fulfillment_policy_id":"222"')));

  // Pick mode: selecting re-posts the confirmed publish with the picked
  // number + the v34 discard flag (postPublish stubbed to capture).
  // Reactive pick also shows compliance problems found alongside.
  const PICK_BODY = { draft_id: "d1", title: "T", price: "10.00" };
  fetchQueue = [{ needs_policy_pick: true, policies: POLICIES, error: "Pick one below.",
                  problems: ["Pick a condition first."] }];
  await sandbox.postPublish(PICK_BODY);
  check("pick mode entered via failed publish", !hidden("shipping-pick-msg"));
  check("pick message carries compliance problems",
    $("shipping-pick-msg").textContent.includes("Pick a condition first."));
  let focused = false;
  $("shipping-select").focus = () => { focused = true; };
  sandbox.openShippingPick(POLICIES, "Pick one below.", ["Pick a condition first."]);
  check("reactive pick focuses the dropdown", focused);
  let repostBody = null;
  sandbox.postPublish = async (body) => { repostBody = body; return "listed"; };
  await sandbox.selectShippingPolicy("111");
  check("pick re-post carries picked number",
    repostBody && repostBody.fulfillment_policy_id === "111");
  check("pick re-post carries discard flag",
    repostBody && repostBody.policy_pick === true);
  check("pick re-post keeps confirmed body",
    repostBody && repostBody.draft_id === "d1" && repostBody.title === "T");
  check("listed pick exits pick mode + hides message", hidden("shipping-pick-msg"));

  // Package fields stay fully editable: a calculated pick never fills
  // them from the policy name, even when the draft has no values.
  $("p-weight").value = "";
  $("p-length").value = "";
  await sandbox.selectShippingPolicy("111");
  check("calculated pick leaves package inputs empty for the seller to fill",
    $("p-weight").value === "" && $("p-length").value === "");
  check("option values still carry the IDs internally (app use only)",
    optionValues().includes("111") && optionValues().includes("222"));

  if (failures) { console.log(failures + " failure(s)"); process.exit(1); }
  console.log("all shipping-chooser checks pass");
})().catch((e) => { console.log("ERROR: " + (e && e.stack || e)); process.exit(1); });
