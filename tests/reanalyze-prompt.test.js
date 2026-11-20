/* apk-v67 node tests for the prompt after a photo is added to an
 * existing draft (static/app.js). Loads the REAL app.js in a vm with
 * a REAL-browser-faithful DOM, same pattern as
 * tests/description-condition-note.test.js, plus a recording global
 * confirm/alert and a FormData stub (the vm sandbox has no bare
 * confirm, alert, or FormData; the real WebView has all three).
 * Covers: the prompt is hidden on a fresh draft load; it appears
 * after a successful photo add (and only then — not on a failed
 * upload, not when nothing was added); its button runs the same
 * reanalyze flow as "Ask AI again" with the honest confirm text
 * (the five fields reanalyze replaces, the typed price kept); the
 * prompt clears after a successful reanalyze and shows again when
 * another photo is added; cancelling the confirm runs nothing and
 * leaves the prompt up; the v65 condition note survives a reanalyze
 * triggered from the prompt (new AI description, note still there
 * exactly once).
 * Run: node tests/reanalyze-prompt.test.js (exit 0 = pass). */
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

class FormDataStub {
  constructor() { this.entries = []; }
  append(k, v, name) { this.entries.push([k, v, name]); }
}

function newContext(source) {
  const registry = {};
  const fetchCalls = [];
  const pending = [];
  const unhandled = [];
  const confirmMessages = [];
  const alerts = [];
  let confirmAnswer = true;
  const onUnhandled = (err) => unhandled.push(err);
  process.on("unhandledRejection", onUnhandled);
  const confirm = (msg) => { confirmMessages.push(msg); return confirmAnswer; };
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
      confirm,
    },
    confirm,
    alert: (msg) => { alerts.push(msg); },
    FormData: FormDataStub,
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
  const flush = async () => { await tick(); await tick(); await tick(); };
  const fire = (el, type, event) =>
    (el._listeners[type] || []).forEach((f) => f(event || {}));
  const setConfirmAnswer = (v) => { confirmAnswer = v; };
  const dispose = () => process.removeListener("unhandledRejection", onUnhandled);
  return { sandbox, $, ctx, resolveFetch, tick, flush, fire,
    fetchCalls, pending, unhandled, confirmMessages, alerts,
    setConfirmAnswer, dispose };
}

let failures = 0;
function check(name, cond, extra) {
  if (!cond) { failures++; console.log("FAIL: " + name + (extra ? " -- " + extra : "")); }
  else { console.log("ok:   " + name); }
}

function countNote(desc) {
  return (desc.match(/Condition: /g) || []).length;
}

const CONFIRM_TEXT = "Ask the AI to look at all the photos again? " +
  "The title, description, category, details, and AI notes will be " +
  "replaced with the AI's new version. Your typed price is kept.";

function baseDraft(over = {}) {
  return Object.assign({
    id: "d1", title: "Old title", description: "Old AI text.",
    category: "Dolls", attributes: {}, identification_notes: "",
    condition: "USED_EXCELLENT", condition_details: "chips",
    price: "5.00", quantity: "1",
    photos: ["d1/1.jpg"],
  }, over);
}

/* A fresh context with a loaded draft and the prompt hidden, exactly
   as the template ships it (class "note hidden"). */
function loadedContext(over) {
  const c = newContext(SRC);
  c.$("draft-reanalyze-prompt").classList.add("hidden");
  c.ctx("draft = " + JSON.stringify(baseDraft(over)) + ";");
  c.ctx("loadDraftIntoForm(draft);");
  return c;
}

async function main() {
  console.log("== prompt state on load ==");
  {
    const c = loadedContext();
    check("prompt is hidden on a fresh draft load",
      c.$("draft-reanalyze-prompt").classList.contains("hidden"));
    check("no unhandled rejection", c.unhandled.length === 0);
    c.dispose();
  }

  console.log("== prompt appears after a successful photo add ==");
  {
    const c = loadedContext();
    const p = c.ctx("uploadDraftPhotos([{name:'b.jpg'}], true)");
    c.resolveFetch("/photos", {
      draft: baseDraft({ photos: ["d1/1.jpg", "d1/2.jpg"] }), added: 1 });
    await p;
    check("prompt is visible after a photo lands",
      !c.$("draft-reanalyze-prompt").classList.contains("hidden"));
    check("the add note still says what happened",
      c.$("draft-photo-note").textContent === "Added 1 photo.");
    check("no unhandled rejection", c.unhandled.length === 0);
    c.dispose();
  }

  console.log("== prompt button runs reanalyze with the honest confirm ==");
  {
    const c = loadedContext();
    const p = c.ctx("uploadDraftPhotos([{name:'b.jpg'}], true)");
    c.resolveFetch("/photos", {
      draft: baseDraft({ photos: ["d1/1.jpg", "d1/2.jpg"] }), added: 1 });
    await p;
    check("prompt is up before the tap",
      !c.$("draft-reanalyze-prompt").classList.contains("hidden"));
    c.fire(c.$("btn-draft-reanalyze"), "click");
    await c.flush();
    check("confirm asks once, naming the replaced fields and the kept price",
      c.confirmMessages.length === 1 &&
      c.confirmMessages[0] === CONFIRM_TEXT,
      JSON.stringify(c.confirmMessages));
    check("reanalyze was posted for this draft",
      c.fetchCalls.some((f) => String(f.url) === "/api/drafts/d1/reanalyze" &&
        f.opts.method === "POST"));
    c.resolveFetch("/reanalyze", { draft: baseDraft({
      title: "New AI title", description: "New AI text about the contents.",
      identification_notes: "AI saw the contents.",
      photos: ["d1/1.jpg", "d1/2.jpg"] }) });
    await c.flush();
    check("the form shows the new AI title",
      c.$("f-title").value === "New AI title");
    check("the typed price survived the reanalyze",
      c.$("f-price").value === "5.00");
    check("prompt clears after a successful reanalyze",
      c.$("draft-reanalyze-prompt").classList.contains("hidden"));
    check("no alert fired", c.alerts.length === 0);
    check("no unhandled rejection", c.unhandled.length === 0);
    // And it shows again when another photo is added later.
    const p2 = c.ctx("uploadDraftPhotos([{name:'c.jpg'}], true)");
    c.resolveFetch("/photos", {
      draft: baseDraft({ photos: ["d1/1.jpg", "d1/2.jpg", "d1/3.jpg"] }),
      added: 1 });
    await p2;
    check("prompt shows again on the next photo add",
      !c.$("draft-reanalyze-prompt").classList.contains("hidden"));
    check("no unhandled rejection", c.unhandled.length === 0);
    c.dispose();
  }

  console.log("== Ask AI again runs the same flow ==");
  {
    const c = loadedContext();
    c.fire(c.$("btn-reanalyze"), "click");
    await c.flush();
    check("same confirm text from Ask AI again",
      c.confirmMessages.length === 1 &&
      c.confirmMessages[0] === CONFIRM_TEXT);
    check("reanalyze was posted",
      c.fetchCalls.some((f) => String(f.url) === "/api/drafts/d1/reanalyze"));
    c.resolveFetch("/reanalyze", { draft: baseDraft({ title: "New AI title" }) });
    await c.flush();
    check("the form shows the new AI title",
      c.$("f-title").value === "New AI title");
    check("no unhandled rejection", c.unhandled.length === 0);
    c.dispose();
  }

  console.log("== cancelling the confirm runs nothing ==");
  {
    const c = loadedContext();
    const p = c.ctx("uploadDraftPhotos([{name:'b.jpg'}], true)");
    c.resolveFetch("/photos", {
      draft: baseDraft({ photos: ["d1/1.jpg", "d1/2.jpg"] }), added: 1 });
    await p;
    c.setConfirmAnswer(false);
    c.fire(c.$("btn-draft-reanalyze"), "click");
    await c.flush();
    check("no reanalyze fetch on cancel",
      !c.fetchCalls.some((f) => String(f.url).includes("/reanalyze")));
    check("prompt stays up on cancel",
      !c.$("draft-reanalyze-prompt").classList.contains("hidden"));
    check("title is untouched on cancel",
      c.$("f-title").value === "Old title");
    check("no unhandled rejection", c.unhandled.length === 0);
    c.dispose();
  }

  console.log("== no prompt on a failed upload or when nothing was added ==");
  {
    const c = loadedContext();
    const p = c.ctx("uploadDraftPhotos([{name:'b.jpg'}], true)");
    c.resolveFetch("/photos",
      { error: "The server could not save those photos." }, false);
    await p;
    check("prompt stays hidden on a failed upload",
      c.$("draft-reanalyze-prompt").classList.contains("hidden"));
    check("the error is shown instead",
      !c.$("draft-photo-error").classList.contains("hidden") &&
      c.$("draft-photo-error").textContent ===
        "The server could not save those photos.");
    check("no reanalyze fetch on a failed upload",
      !c.fetchCalls.some((f) => String(f.url).includes("/reanalyze")));
    check("no unhandled rejection", c.unhandled.length === 0);
    c.dispose();
  }
  {
    const c = loadedContext();
    await c.ctx("uploadDraftPhotos([], true)");
    check("prompt stays hidden when nothing was added",
      c.$("draft-reanalyze-prompt").classList.contains("hidden"));
    check("no fetch at all when nothing was added",
      !c.fetchCalls.some((f) => String(f.url).includes("/photos")));
    check("no unhandled rejection", c.unhandled.length === 0);
    c.dispose();
  }

  console.log("== v65 condition note survives a reanalyze from the prompt ==");
  {
    const c = loadedContext();
    check("note is in the description once after load",
      countNote(c.$("f-description").value) === 1 &&
      c.$("f-description").value.indexOf("Condition: chips") !== -1);
    const p = c.ctx("uploadDraftPhotos([{name:'b.jpg'}], true)");
    c.resolveFetch("/photos", {
      draft: baseDraft({ photos: ["d1/1.jpg", "d1/2.jpg"] }), added: 1 });
    await p;
    c.fire(c.$("btn-draft-reanalyze"), "click");
    await c.flush();
    // The server's new AI description carries no note of its own;
    // loadDraftIntoForm re-applies it from the condition details.
    c.resolveFetch("/reanalyze", { draft: baseDraft({
      title: "New AI title", description: "New AI text about the contents.",
      identification_notes: "AI saw the contents.",
      photos: ["d1/1.jpg", "d1/2.jpg"] }) });
    await c.flush();
    const desc = c.$("f-description").value;
    check("new AI description is in the field",
      desc.indexOf("New AI text about the contents.") !== -1);
    check("old AI description is gone",
      desc.indexOf("Old AI text.") === -1);
    check("condition note is still there, exactly once",
      countNote(desc) === 1 && desc.indexOf("Condition: chips") !== -1);
    check("condition details field is untouched",
      c.$("f-condition-details").value === "chips");
    check("no unhandled rejection", c.unhandled.length === 0);
    c.dispose();
  }

  console.log(failures === 0 ? "ALL PASS" : failures + " FAILURES");
  process.exit(failures === 0 ? 0 : 1);
}

main().catch((e) => { console.error(e); process.exit(1); });
