/* apk-v65 node tests for the condition note in the description
 * (static/app.js). Loads the REAL app.js in a vm with a
 * REAL-browser-faithful DOM, same pattern as tests/quantity.test.js.
 * Covers: splitConditionNote (trailing block only, mid-body lines are
 * not the note); removeConditionNote (exact note only, text typed
 * after it stays put); syncConditionNote (details present appends
 * the labeled note, editing the details replaces it in place with no
 * duplicate, clearing the details removes it, empty details add
 * nothing, a hand-deleted note comes back on the next details edit,
 * manual description edits never duplicate it, text typed after the
 * note survives a details change); loadDraftIntoForm (the AI draft
 * filling the Description keeps the note); the live character count;
 * collectForm carries the note; fullListingText does not repeat the
 * details line once the description carries the note.
 * Run: node tests/description-condition-note.test.js (exit 0 = pass). */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const APP_JS = path.join(__dirname, "..",
  "app/src/main/python/static/app.js");
const SRC = fs.readFileSync(APP_JS, "utf8");

/* ---- faithful HTMLCollection stand-in (from draft-sections.test.js) ---- */
function makeCollection(backing) {
  const coll = {};
  for (let i = 0; i < 64; i++) {
    Object.defineProperty(coll, String(i), {
      get() { return backing[i]; }, enumerable: true, configurable: true,
    });
  }
  Object.defineProperty(coll, "length", {
    get() { return backing.length; }, enumerable: true, configurable: true,
  });
  coll.item = (i) => backing[i] || null;
  coll.namedItem = () => null;
  return coll;
}

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

function makeEl(id, tag) {
  const kids = [];
  const listeners = {};
  const attrs = {};
  const el = {
    _kids: kids,
    _listeners: listeners,
    _attrs: attrs,
    _value: "",
    textContent: "",
    checked: false,
    disabled: false,
    style: {},
    dataset: {},
    classList: makeClassList(),
    type: "",
    addEventListener(t, fn) { (listeners[t] = listeners[t] || []).push(fn); },
    removeEventListener() {},
    setAttribute(k, v) { attrs[k] = String(v); },
    getAttribute(k) { return attrs[k] === undefined ? null : attrs[k]; },
    appendChild(child) { kids.push(child); child._parent = el; return child; },
    querySelector() { return makeEl("", "div"); },
    querySelectorAll() { return []; },
    getContext() { return null; },
    getBoundingClientRect() { return { left: 0, top: 0, width: 100, height: 100 }; },
    scrollIntoView() {},
    setPointerCapture() {},
    releasePointerCapture() {},
    focus() {},
    click() { (listeners.click || []).forEach((f) => f({ preventDefault() {} })); },
  };
  Object.defineProperty(el, "children", {
    get() { return makeCollection(kids); }, enumerable: true, configurable: true,
  });
  Object.defineProperty(el, "value", {
    get() { return el._value; },
    set(v) { el._value = v == null ? "" : String(v); },
    enumerable: true, configurable: true,
  });
  let _html = "";
  Object.defineProperty(el, "innerHTML", {
    get() { return _html; },
    set(v) {
      _html = v; kids.length = 0;
      el.textContent = String(v).replace(/<[^>]*>/g, "");
    },
    enumerable: true, configurable: true,
  });
  return el;
}

function newContext(source) {
  const registry = {};
  const fetchCalls = [];
  const pending = [];
  const unhandled = [];
  const onUnhandled = (err) => unhandled.push(err);
  process.on("unhandledRejection", onUnhandled);
  const sandbox = {
    document: {
      getElementById(id) {
        if (!registry[id]) registry[id] = makeEl(id, "");
        return registry[id];
      },
      createElement(tag) { return makeEl("", tag); },
      querySelectorAll() { return []; },
      querySelector() { return makeEl("", "div"); },
      addEventListener() {},
      body: makeEl("body", "body"),
    },
    window: {
      addEventListener() {},
      location: { search: "" },
      history: {},
      innerWidth: 400,
      scrollTo() {},
      confirm: () => true,
    },
    fetch: (url, opts = {}) => {
      fetchCalls.push({ url, opts });
      return new Promise((resolve) => { pending.push({ url, opts, resolve }); });
    },
    URLSearchParams,
    console,
    setTimeout,
    clearTimeout,
  };
  sandbox.window.document = sandbox.document;
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox);
  const $ = (id) => sandbox.document.getElementById(id);
  const ctx = (code) => vm.runInContext(code, sandbox);
  const resolveFetch = (urlPart, json, ok = true) => {
    const i = pending.findIndex((p) => String(p.url).includes(urlPart));
    if (i < 0) throw new Error("no pending fetch for " + urlPart +
      " (pending: " + pending.map((p) => p.url).join(", ") + ")");
    const [p] = pending.splice(i, 1);
    p.resolve({ ok, status: ok ? 200 : 500,
      text: async () => JSON.stringify(json === null ? {} : json) });
  };
  // app.js fires GET /api/config on load; drain it.
  const ci = pending.findIndex((p) => String(p.url).includes("/api/config"));
  if (ci >= 0) {
    const [p] = pending.splice(ci, 1);
    p.resolve({ ok: true, status: 200, text: async () => "{}" });
  }
  const tick = () => new Promise((r) => setTimeout(r, 0));
  const fire = (el, type, event) =>
    (el._listeners[type] || []).forEach((f) => f(event || {}));
  const dispose = () => process.removeListener("unhandledRejection", onUnhandled);
  return { sandbox, $, ctx, resolveFetch, tick, fire,
    fetchCalls, pending, unhandled, dispose };
}

let failures = 0;
function check(name, cond, extra) {
  if (!cond) { failures++; console.log("FAIL: " + name + (extra ? " -- " + extra : "")); }
  else { console.log("ok:   " + name); }
}

function countNote(desc) {
  return (desc.match(/Condition: /g) || []).length;
}

async function main() {
  console.log("== splitConditionNote: the trailing block only ==");
  {
    const c = newContext(SRC);
    const split = (s) => c.ctx("splitConditionNote(" + JSON.stringify(s) + ")");
    check("body + note splits cleanly",
      JSON.stringify(split("A doll.\n\nCondition: chips")) ===
      JSON.stringify({ base: "A doll.", note: "chips" }));
    check("no note leaves the text alone",
      JSON.stringify(split("A doll.")) ===
      JSON.stringify({ base: "A doll.", note: "" }));
    check("note alone (empty body) splits to an empty base",
      JSON.stringify(split("Condition: chips")) ===
      JSON.stringify({ base: "", note: "chips" }));
    check("split takes the trailing block from the last marker",
      split("A doll.\n\nCondition: great shape\n\nShips fast.").note ===
      "great shape\n\nShips fast." &&
      split("A doll.\n\nCondition: great shape\n\nShips fast.").base === "A doll.");
    check("no unhandled rejection", c.unhandled.length === 0);
    c.dispose();
  }

  console.log("== removeConditionNote: the exact note only ==");
  {
    const c = newContext(SRC);
    const rm = (s, k) => c.ctx("removeConditionNote(" +
      JSON.stringify(s) + ", " + JSON.stringify(k) + ")");
    check("trailing note comes off, body stays",
      rm("A doll.\n\nCondition: chips", "chips") === "A doll.");
    check("note alone leaves an empty description",
      rm("Condition: chips", "chips") === "");
    check("text typed after the note stays put",
      rm("A doll.\n\nCondition: chips\n\nShips fast.", "chips") ===
      "A doll.\n\nShips fast.");
    check("a different note is not touched",
      rm("A doll.\n\nCondition: wear", "chips") ===
      "A doll.\n\nCondition: wear");
    check("empty known text touches nothing",
      rm("A doll.\n\nCondition: chips", "") === "A doll.\n\nCondition: chips");
    check("no unhandled rejection", c.unhandled.length === 0);
    c.dispose();
  }

  console.log("== syncConditionNote: the note follows the details ==");
  {
    const c = newContext(SRC);
    c.$("f-description").value = "A nice doll, 12 inches tall.";
    c.$("f-condition-details").value = "small chips on the base";
    c.fire(c.$("f-condition-details"), "input");
    check("details present appends the labeled note",
      c.$("f-description").value ===
      "A nice doll, 12 inches tall.\n\nCondition: small chips on the base");
    c.$("f-condition-details").value = "wear on the glaze";
    c.fire(c.$("f-condition-details"), "input");
    check("editing the details replaces the note in place, no duplicate",
      c.$("f-description").value ===
      "A nice doll, 12 inches tall.\n\nCondition: wear on the glaze" &&
      countNote(c.$("f-description").value) === 1);
    c.$("f-condition-details").value = "";
    c.fire(c.$("f-condition-details"), "input");
    check("clearing the details removes the note",
      c.$("f-description").value === "A nice doll, 12 inches tall.");
    check("empty details add nothing",
      c.$("f-condition-details").value === "" &&
      countNote(c.$("f-description").value) === 0);
    c.$("f-condition-details").value = "small chips on the base";
    c.fire(c.$("f-condition-details"), "input");
    check("a hand-deleted note comes back on the next details edit",
      c.$("f-description").value.includes("Condition: small chips on the base"));
    // Hand-delete the note, then edit the details: it re-appends.
    c.$("f-description").value = "A nice doll, 12 inches tall.";
    c.$("f-condition-details").value = "wear on the glaze";
    c.fire(c.$("f-condition-details"), "input");
    check("hand-deleted note re-appends",
      c.$("f-description").value ===
      "A nice doll, 12 inches tall.\n\nCondition: wear on the glaze");
    // Manual description edits never duplicate the note.
    c.fire(c.$("f-description"), "input");
    check("typing in the description does not duplicate the note",
      countNote(c.$("f-description").value) === 1);
    // Text typed after the note survives a details change.
    c.$("f-description").value += "\n\nShips fast.";
    c.$("f-condition-details").value = "small chips on the base";
    c.fire(c.$("f-condition-details"), "input");
    check("text typed after the note survives a details change",
      c.$("f-description").value ===
      "A nice doll, 12 inches tall.\n\nShips fast.\n\nCondition: small chips on the base");
    // A "Condition: " line in the middle of his own words is not the
    // note: it stays put and the fresh note is appended after it.
    c.$("f-description").value = "A doll.\n\nCondition: great shape\n\nShips fast.";
    c.$("f-condition-details").value = "chips";
    c.fire(c.$("f-condition-details"), "input");
    check("a Condition line in his own words stays put",
      c.$("f-description").value ===
      "A doll.\n\nCondition: great shape\n\nShips fast.\n\nCondition: chips");
    check("no unhandled rejection", c.unhandled.length === 0);
    c.dispose();
  }

  console.log("== loadDraftIntoForm: the AI draft keeps the note ==");
  {
    const c = newContext(SRC);
    c.ctx("loadDraftIntoForm({ id: 'd1', title: 'Doll', " +
      "description: 'A nice doll, 12 inches tall.', " +
      "condition: 'NEW_OTHER', condition_details: 'small chips on the base', " +
      "price: '10.00', quantity: '1', photos: [], attributes: {} });");
    check("AI description plus details shows the note",
      c.$("f-description").value ===
      "A nice doll, 12 inches tall.\n\nCondition: small chips on the base");
    check("a draft that already carries the note is not duplicated",
      countNote(c.$("f-description").value) === 1);
    // A pre-v65 draft (details but no note yet) gains the note on open.
    const c2 = newContext(SRC);
    c2.ctx("loadDraftIntoForm({ id: 'd2', title: 'Doll', " +
      "description: 'A nice doll, 12 inches tall.', " +
      "condition: 'NEW_OTHER', condition_details: 'wear on the glaze', " +
      "price: '10.00', quantity: '1', photos: [], attributes: {} });");
    check("pre-v65 draft gains the note on open",
      c2.$("f-description").value ===
      "A nice doll, 12 inches tall.\n\nCondition: wear on the glaze");
    check("no unhandled rejection",
      c.unhandled.length === 0 && c2.unhandled.length === 0);
    c.dispose(); c2.dispose();
  }

  console.log("== live character count under the Description ==");
  {
    const c = newContext(SRC);
    c.$("f-description").value = "A nice doll.";
    c.fire(c.$("f-description"), "input");
    check("count follows the field",
      c.$("description-count").textContent === "12 characters");
    c.$("f-description").value = "A nice doll, 12 inches tall.";
    c.fire(c.$("f-description"), "input");
    check("count updates as he types",
      c.$("description-count").textContent === "28 characters");
    check("no unhandled rejection", c.unhandled.length === 0);
    c.dispose();
  }

  console.log("== collectForm carries the note; Copy everything does not repeat it ==");
  {
    const c = newContext(SRC);
    c.ctx("draft = { id: 'd1', title: 'Doll', photos: [] };");
    c.$("f-title").value = "Doll";
    c.$("f-description").value = "A nice doll.\n\nCondition: chips";
    c.$("f-condition").value = "NEW_OTHER";
    c.$("f-condition-details").value = "chips";
    check("collectForm description carries the note",
      c.ctx("collectForm().description") === "A nice doll.\n\nCondition: chips");
    check("Copy everything does not repeat the details line",
      c.ctx("fullListingText()").indexOf("Condition details:") === -1 &&
      c.ctx("fullListingText()").indexOf("Condition: chips") !== -1);
    // A description that never got the note still gets the details line.
    c.$("f-description").value = "A nice doll.";
    check("without the note, Copy everything still lists the details",
      c.ctx("fullListingText()").indexOf("Condition details: chips") !== -1);
    check("no unhandled rejection", c.unhandled.length === 0);
    c.dispose();
  }

  console.log(failures === 0 ? "ALL PASS" : failures + " FAILURES");
  process.exit(failures === 0 ? 0 : 1);
}

main().catch((e) => { console.error(e); process.exit(1); });
