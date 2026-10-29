/* v35: node tests for the frontend category-aware Condition dropdown in
   static/app.js. app.js is a browser script, so this stubs the DOM
   with STATEFUL elements: values, option children, and hidden toggles
   must persist across $() calls. The test drives the real
   fetchCategoryConditions: on a category change it fetches
   /api/ebay/category-conditions and rebuilds the f-condition options
   from the category's allowed conditions (plain eBay labels). The
   seller's current pick is never silently changed: if it is not in
   the allowed set it stays selected, marked "(not allowed for this
   category)", so the pre-publish check can flag it. On a failed or
   unavailable fetch the hardcoded fallback options stay and a plain
   hint shows.
   Run: node tests/condition-dropdown.test.js (exit 0 = pass). */
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
let fetchFails = false;
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
    if (fetchFails) throw new Error("network down");
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

/* Seed the template's hardcoded fallback options (v35 labels) and give
   the f-condition stub a working option lookup, so the "current label"
   read in fetchCategoryConditions behaves like a real select. */
function seedFallbackOptions() {
  const sel = $("f-condition");
  sel.innerHTML = "";
  const opts = [
    ["", "— Pick one —"],
    ["NEW", "New"],
    ["USED_EXCELLENT", "Used"],
    ["USED_VERY_GOOD", "Very Good"],
    ["USED_GOOD", "Good"],
    ["USED_ACCEPTABLE", "Acceptable"],
    ["FOR_PARTS_OR_NOT_WORKING", "For parts or not working"],
  ];
  opts.forEach(([v, t]) => {
    const o = sandbox.document.createElement("option");
    o.value = v; o.textContent = t;
    sel.appendChild(o);
  });
  sel.querySelector = (s) => {
    const m = /option\[value="([^"]+)"\]/.exec(s);
    if (!m) return makeEl();
    return sel.children.find((c) => c.value === m[1]) || makeEl();
  };
}
const optionPairs = () => $("f-condition").children.map((o) => [o.value, o.textContent]);

(async () => {
  seedFallbackOptions();
  $("f-condition").value = "USED_VERY_GOOD";
  $("p-category").value = "111";
  $("condition-fetch-hint").classList.add("hidden");
  fetchQueue = [{ conditions: [{ value: "NEW", label: "New" },
                               { value: "USED_EXCELLENT", label: "Used" }],
                  condition_required: true, available: true }];
  await sandbox.fetchCategoryConditions();
  check("fetch hits the category-conditions endpoint",
    fetchCalls.some((c) => String(c.url).includes("/api/ebay/category-conditions?category_id=111")));
  check("dropdown rebuilt to the allowed conditions only (+ marked current)",
    JSON.stringify(optionPairs()) === JSON.stringify([
      ["", "— Pick one —"], ["NEW", "New"], ["USED_EXCELLENT", "Used"],
      ["USED_VERY_GOOD", "Very Good (not allowed for this category)"]]));
  check("disallowed current value kept and marked, never silently changed",
    $("f-condition").value === "USED_VERY_GOOD" &&
    optionPairs().some(([v, t]) => v === "USED_VERY_GOOD" &&
      t === "Very Good (not allowed for this category)"));
  check("fetch hint hidden on success", hidden("condition-fetch-hint"));

  // Same category again: no refetch (guard on lastConditionCategory).
  const callsBefore = fetchCalls.length;
  await sandbox.fetchCategoryConditions();
  check("same category does not refetch", fetchCalls.length === callsBefore);

  // Allowed current value is preserved as-is.
  seedFallbackOptions();
  $("f-condition").value = "USED_EXCELLENT";
  $("p-category").value = "222";
  fetchQueue = [{ conditions: [{ value: "NEW", label: "New" },
                               { value: "USED_EXCELLENT", label: "Used" }],
                  condition_required: true, available: true }];
  await sandbox.fetchCategoryConditions();
  check("allowed current value preserved",
    $("f-condition").value === "USED_EXCELLENT" &&
    !optionPairs().some(([, t]) => t.includes("not allowed")));

  // Unavailable fetch: fallback options stay, hint shows.
  seedFallbackOptions();
  $("f-condition").value = "NEW";
  $("p-category").value = "333";
  fetchQueue = [{ conditions: [{ value: "NEW", label: "New" }],
                  condition_required: true, available: false }];
  await sandbox.fetchCategoryConditions();
  check("unavailable fetch keeps fallback options",
    optionPairs().some(([v]) => v === "USED_VERY_GOOD") &&
    optionPairs().some(([v]) => v === "FOR_PARTS_OR_NOT_WORKING"));
  check("unavailable fetch shows the plain hint", !hidden("condition-fetch-hint"));
  check("unavailable fetch keeps the current value", $("f-condition").value === "NEW");

  // Network failure: same fallback + hint, never a throw.
  seedFallbackOptions();
  $("f-condition").value = "GOOD".replace("GOOD", "USED_GOOD");
  $("p-category").value = "444";
  $("condition-fetch-hint").classList.add("hidden");
  fetchFails = true;
  await sandbox.fetchCategoryConditions();
  fetchFails = false;
  check("failed fetch keeps fallback options + shows hint",
    optionPairs().some(([v]) => v === "USED_VERY_GOOD") &&
    !hidden("condition-fetch-hint"));

  // Empty category: no fetch at all.
  $("p-category").value = "";
  const callsBeforeEmpty = fetchCalls.length;
  await sandbox.fetchCategoryConditions();
  check("empty category fetches nothing", fetchCalls.length === callsBeforeEmpty);

  if (failures) { console.log(failures + " failure(s)"); process.exit(1); }
  console.log("all condition-dropdown checks pass");
})().catch((e) => { console.log("ERROR: " + (e && e.stack || e)); process.exit(1); });
