/* v41: node tests for the frontend create-policy form in static/app.js.
   Same stub-DOM harness as tests/shipping-chooser.test.js: app.js is a
   browser script, so elements are stateful stubs (values, text, and
   hidden toggles persist across $() calls). The test drives the real
   functions: collectPolicyCreateForm (form JSON + errors, mirroring
   the server validation), refreshCreateFormVisibility (cost fields,
   international section, pickup-only, Other code inputs),
   createReviewLines (the exact policy shown before confirm),
   populateServiceSelect (verified codes + Other), cancelCreateForm
   (discards, clears, draft untouched), and confirmCreatePolicy
   (sends exactly what was reviewed; name-taken returns to the form
   with values intact; unconfirmed arms the retry flag).
   Run: node tests/policy-create.test.js (exit 0 = pass). */
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
    getBoundingClientRect() { return { left: 0, width: 100, height: 100 }; },
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
    const ok = next._ok !== false;
    const status = next._status || 200;
    const body = Object.assign({}, next);
    delete body._ok;
    delete body._status;
    return { ok, status, text: async () => JSON.stringify(body) };
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

function setForm(o) {
  $("pc-name").value = o.name !== undefined ? o.name : "Dummy Doll Policy";
  $("pc-description").value = o.description !== undefined ? o.description : "";
  $("pc-pickup-only").checked = !!o.pickupOnly;
  $("pc-local-pickup").checked = !!o.localPickup;
  $("pc-international").checked = !!o.international;
  $("pc-dom-cost-type").value = o.domCostType || "FLAT_RATE";
  $("pc-dom-service-1").value = o.domService1 !== undefined ? o.domService1 : "USPSPriority";
  $("pc-dom-service-1-other").value = o.domService1Other || "";
  $("pc-dom-service-2").value = o.domService2 || "";
  $("pc-dom-service-2-other").value = o.domService2Other || "";
  $("pc-dom-cost-1").value = o.domCost1 !== undefined ? o.domCost1 : "12.50";
  $("pc-dom-cost-2").value = o.domCost2 || "";
  $("pc-dom-free-1").checked = !!o.domFree;
  $("pc-intl-cost-type").value = o.intlCostType || "FLAT_RATE";
  $("pc-intl-service-1").value = o.intlService1 || "";
  $("pc-intl-service-1-other").value = o.intlService1Other || "";
  $("pc-intl-service-2").value = o.intlService2 || "";
  $("pc-intl-service-2-other").value = o.intlService2Other || "";
  $("pc-intl-cost-1").value = o.intlCost1 || "";
  $("pc-intl-cost-2").value = o.intlCost2 || "";
  $("pc-handling").value = o.handling !== undefined ? o.handling : "2";
}

(async () => {
  // Service select: placeholder + verified codes + Other, no IDs shown.
  sandbox.populateServiceSelect("pc-dom-service-1",
    [{ code: "USPSPriority", label: "USPS Priority Mail" }]);
  const texts = $("pc-dom-service-1").children.map((o) => o.textContent);
  const values = $("pc-dom-service-1").children.map((o) => o.value);
  check("service select renders placeholder, code, Other",
    texts[0].includes("Pick a service") && texts[1] === "USPS Priority Mail" &&
    texts[2] === "Other (type the code)" && values[1] === "USPSPriority" &&
    values[2] === "__OTHER__");

  // Collect: flat domestic, happy path.
  setForm({});
  let collected = sandbox.collectPolicyCreateForm();
  check("collect flat domestic has no errors", collected.errors.length === 0);
  check("collect keeps name, type, cost",
    collected.form.name === "Dummy Doll Policy" &&
    collected.form.dom_cost_type === "FLAT_RATE" &&
    collected.form.dom_cost_1 === "12.50" &&
    collected.form.handling_days === "2");

  // Collect: validation mirrors the server (no defaults, empty errors).
  setForm({ name: "" });
  check("collect missing name errors",
    sandbox.collectPolicyCreateForm().errors.some((e) => e.includes("name")));
  setForm({ domCost1: "abc" });
  check("collect bad cost errors",
    sandbox.collectPolicyCreateForm().errors.some((e) => e.includes("cost")));
  setForm({ domFree: true, domCost1: "" });
  check("collect free shipping needs no cost",
    sandbox.collectPolicyCreateForm().errors.length === 0);
  setForm({ handling: "31" });
  check("collect handling out of range errors",
    sandbox.collectPolicyCreateForm().errors.some((e) => e.includes("handling")));
  setForm({ international: true, intlService1: "" });
  check("collect international without service errors",
    sandbox.collectPolicyCreateForm().errors.some((e) => e.includes("international")));
  setForm({ domService1: "__OTHER__", domService1Other: "" });
  check("collect Other without typed code errors",
    sandbox.collectPolicyCreateForm().errors.length > 0);
  setForm({ domService1: "__OTHER__", domService1Other: "USPSParcel" });
  collected = sandbox.collectPolicyCreateForm();
  check("collect Other with typed code passes",
    collected.errors.length === 0 &&
    sandbox.createServiceCodeFor("dom", 1, collected.form) === "USPSParcel");

  // Visibility: cost wraps follow flat/calculated; pickup-only hides
  // shipping + handling; Other reveals its text input.
  setForm({ domCostType: "CALCULATED" });
  sandbox.refreshCreateFormVisibility();
  check("calculated hides flat cost wraps",
    hidden("pc-dom-cost-1-wrap") && hidden("pc-dom-cost-2-wrap"));
  setForm({ domCostType: "FLAT_RATE" });
  sandbox.refreshCreateFormVisibility();
  check("flat shows flat cost wraps",
    !hidden("pc-dom-cost-1-wrap") && !hidden("pc-dom-cost-2-wrap"));
  setForm({ pickupOnly: true });
  sandbox.refreshCreateFormVisibility();
  check("pickup-only hides domestic, international, handling",
    hidden("pc-domestic") && hidden("pc-intl") && hidden("pc-handling-wrap"));
  setForm({ international: true });
  sandbox.refreshCreateFormVisibility();
  check("international checkbox shows international section",
    !hidden("pc-intl"));
  setForm({ domService1: "__OTHER__" });
  sandbox.refreshCreateFormVisibility();
  check("Other service reveals its text input",
    !hidden("pc-dom-service-1-other"));

  // Review lines: exact policy, escaped seller text, warnings.
  vm.runInContext("createServices = { domestic: [{ code: 'USPSPriority', label: 'USPS Priority Mail' }], international: [] };", sandbox);
  setForm({});
  collected = sandbox.collectPolicyCreateForm();
  let lines = sandbox.createReviewLines(collected.form);
  check("review shows name, service label, cost, handling",
    lines.some((l) => l.includes("Dummy Doll Policy")) &&
    lines.some((l) => l.includes("USPS Priority Mail") && l.includes("$12.50")) &&
    lines.some((l) => l.includes("2 business days")));
  setForm({ name: "<b>Bold</b>" });
  collected = sandbox.collectPolicyCreateForm();
  lines = sandbox.createReviewLines(collected.form);
  check("review escapes seller-typed name",
    lines.some((l) => l.includes("&lt;b&gt;")) && !lines.some((l) => l.includes("<b>Bold</b>")));
  setForm({ pickupOnly: true });
  collected = sandbox.collectPolicyCreateForm();
  lines = sandbox.createReviewLines(collected.form);
  check("review pickup-only states no shipping",
    lines.some((l) => l.includes("Local pickup only")));
  setForm({ international: true, intlService1: "USPSPriority", intlCost1: "25.00" });
  collected = sandbox.collectPolicyCreateForm();
  lines = sandbox.createReviewLines(collected.form);
  check("review international states worldwide self-ship",
    lines.some((l) => l.includes("International shipping (worldwide, you ship it yourself)")));

  // Review screen: pickup-only warning shows; calculated with empty
  // package fields warns; calculated with a package does not.
  setForm({ pickupOnly: true });
  sandbox.showCreateReview();
  check("review pickup-only warning shown",
    !hidden("pc-review-warning") &&
    $("pc-review-warning").textContent.includes("cannot ship"));
  setForm({ domCostType: "CALCULATED" });
  $("p-weight").value = ""; $("p-length").value = "";
  $("p-width").value = ""; $("p-height").value = "";
  sandbox.showCreateReview();
  check("review calculated without package warns",
    !hidden("pc-review-warning") &&
    $("pc-review-warning").textContent.includes("package weight"));
  $("p-weight").value = "4"; $("p-length").value = "12";
  $("p-width").value = "9"; $("p-height").value = "1";
  sandbox.showCreateReview();
  check("review calculated with package shows no warning",
    hidden("pc-review-warning"));

  // Cancel discards: form cleared, review hidden, draft untouched
  // (no fetch calls for a policy create).
  fetchCalls.length = 0;
  setForm({});
  sandbox.showCreateReview();
  sandbox.cancelCreateForm();
  check("cancel hides form and review and clears name",
    hidden("policy-create-form") && hidden("policy-create-review") &&
    $("pc-name").value === "");
  check("cancel creates nothing",
    !fetchCalls.some((c) => String(c.url).includes("fulfillment-policies/create")));

  // Confirm sends exactly what was reviewed, then selects the new
  // policy (selectShippingPolicy stubbed to capture, like the
  // chooser test stubs postPublish).
  setForm({});
  sandbox.showCreateReview();
  let selected = null;
  sandbox.selectShippingPolicy = async (id) => { selected = id; };
  fetchQueue = [{ policy_id: "777",
                  policies: [{ id: "777", name: "Dummy Doll Policy", cost_type: "FLAT_RATE" }] }];
  fetchCalls.length = 0;
  await sandbox.confirmCreatePolicy();
  const createCall = fetchCalls.find((c) => String(c.url).includes("fulfillment-policies/create"));
  const sent = JSON.parse(createCall.opts.body);
  check("confirm posts reviewed name and flat cost, retry false",
    sent.name === "Dummy Doll Policy" && sent.dom_cost_1 === "12.50" && sent.retry === false);
  check("confirm selects the created policy", selected === "777");
  check("confirm shows created note",
    !hidden("policy-create-note") &&
    $("policy-create-note").textContent.includes("created and selected"));

  // Name taken: back to the form with values intact, no select.
  setForm({});
  sandbox.showCreateReview();
  selected = null;
  fetchQueue = [{ _ok: false, _status: 409, name_taken: true,
                  error: "That name is already used on your eBay account. Pick a different name." }];
  await sandbox.confirmCreatePolicy();
  check("name taken returns to form with values intact",
    !hidden("policy-create-form") && hidden("policy-create-review") &&
    $("pc-name").value === "Dummy Doll Policy" && selected === null);
  check("name taken message shown",
    !$("policy-create-error").classList.contains("hidden") &&
    $("policy-create-error").textContent.includes("already used"));

  // Unconfirmed: stays on review, arms retry; the retry re-sends
  // with retry true.
  setForm({});
  sandbox.showCreateReview();
  fetchQueue = [{ _ok: false, _status: 502, unconfirmed: true,
                  error: "Could not confirm whether the policy was created. Check Seller Hub before trying again, so you do not create a duplicate." }];
  await sandbox.confirmCreatePolicy();
  check("unconfirmed stays on review",
    !hidden("policy-create-review"));
  fetchQueue = [{ policy_id: "777", policies: [] }];
  fetchCalls.length = 0;
  await sandbox.confirmCreatePolicy();
  const retryCall = fetchCalls.find((c) => String(c.url).includes("fulfillment-policies/create"));
  check("retry re-sends with retry true",
    retryCall && JSON.parse(retryCall.opts.body).retry === true);

  if (failures) { console.log(failures + " FAILURES"); process.exit(1); }
  console.log("policy-create: all checks passed");
})();
