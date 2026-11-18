/* apk-v56 node tests for the full in-app photo editor (static/app.js).
 * Loads the REAL app.js in a vm with a REAL-browser-faithful DOM,
 * same pattern as tests/sold-image.test.js, plus a recording canvas
 * 2d context, a fake Image, and the editor's filter chips and crop
 * presets pre-created (the real page has them in the HTML; the
 * harness creates elements lazily, so they must exist before app.js
 * binds its listeners at load time).
 * Covers: step-1 Edit opens the in-app editor; every adjustment
 * (brightness, contrast, saturation, warmth, sharpness, filters,
 * auto enhance) reaches the render; crop presets, rotate, straighten
 * and flip change the frame; Reset restores, Cancel discards, Apply
 * saves into the same slot; draft Edit opens the in-app editor (never
 * the old Pixel handoff) and its Apply saves through the replace
 * endpoint plus a thumbnail refresh.
 * Run: node tests/photo-editor.test.js (exit 0 = pass). */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const APP_JS = path.join(__dirname, "..",
  "app/src/main/python/static/app.js");
const SRC = fs.readFileSync(APP_JS, "utf8");

/* ---- faithful HTMLCollection stand-in (from sold-image.test.js) ---- */
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
  const list = {
    add(c) { set.add(c); },
    remove(c) { set.delete(c); },
    toggle(c, force) {
      if (force === undefined) { set.has(c) ? set.delete(c) : set.add(c); }
      else if (force) set.add(c);
      else set.delete(c);
    },
    contains(c) { return set.has(c); },
    _set: set,
  };
  return list;
}

const ALL_ELS = [];
const ALL_CTX = [];
const ALL_CANVASES = [];

/* Recording 2d context: every call lands in ctx.calls, and property
   writes that the editor depends on (filter, fillStyle,
   globalCompositeOperation) are recorded too. */
function makeCtx() {
  const calls = [];
  const ctx = {
    calls,
    lineWidth: 1,
    save() { calls.push(["save"]); },
    restore() { calls.push(["restore"]); },
    translate(x, y) { calls.push(["translate", x, y]); },
    rotate(a) { calls.push(["rotate", a]); },
    scale(x, y) { calls.push(["scale", x, y]); },
    clearRect(...a) { calls.push(["clearRect", ...a]); },
    fillRect(...a) { calls.push(["fillRect", ...a]); },
    strokeRect(...a) { calls.push(["strokeRect", ...a]); },
    drawImage(...a) { calls.push(["drawImage", ...a]); },
    getImageData(x, y, w, h) {
      calls.push(["getImageData", w, h]);
      const data = new Uint8ClampedArray(w * h * 4);
      for (let i = 0; i < w * h; i++) {
        data[i * 4] = 60; data[i * 4 + 1] = 60;
        data[i * 4 + 2] = 60; data[i * 4 + 3] = 255;
      }
      return { width: w, height: h, data };
    },
    putImageData(...a) { calls.push(["putImageData"]); },
  };
  let _filter = "";
  Object.defineProperty(ctx, "filter", {
    get() { return _filter; },
    set(v) { _filter = v; calls.push(["filter", v]); },
  });
  let _fill = "";
  Object.defineProperty(ctx, "fillStyle", {
    get() { return _fill; },
    set(v) { _fill = v; calls.push(["fillStyle", v]); },
  });
  let _gco = "source-over";
  Object.defineProperty(ctx, "globalCompositeOperation", {
    get() { return _gco; },
    set(v) { _gco = v; calls.push(["gco", v]); },
  });
  ALL_CTX.push(ctx);
  return ctx;
}

function makeCanvas() {
  const ctx = makeCtx();
  const canvas = {
    width: 0, height: 0, _ctx: ctx,
    getContext() { return ctx; },
    toBlob(cb) { cb(new Blob(["fake-jpeg"], { type: "image/jpeg" })); },
  };
  ALL_CANVASES.push(canvas);
  return canvas;
}

function makeEl(id, tag) {
  const kids = [];
  const listeners = {};
  const attrs = {};
  const classList = makeClassList();
  let _ctx = null;
  const el = {
    _kids: kids,
    _listeners: listeners,
    _attrs: attrs,
    _parent: null,
    _value: "",
    id: id || "",
    tagName: (tag || "").toUpperCase(),
    textContent: "",
    checked: false,
    disabled: false,
    style: {},
    dataset: {},
    classList,
    type: "",
    addEventListener(t, fn) { (listeners[t] = listeners[t] || []).push(fn); },
    removeEventListener() {},
    setAttribute(k, v) { attrs[k] = String(v); },
    getAttribute(k) { return attrs[k] === undefined ? null : attrs[k]; },
    appendChild(child) {
      kids.push(child);
      child._parent = el;
      return child;
    },
    querySelector() { return makeEl("", "div"); },
    querySelectorAll() { return []; },
    getContext() {
      if (!_ctx) _ctx = makeCtx();
      return _ctx;
    },
    getBoundingClientRect() { return { left: 0, top: 0, width: 100, height: 100 }; },
    scrollIntoView() {},
    setPointerCapture() {},
    releasePointerCapture() {},
    focus() {},
    click() { (listeners.click || []).forEach((f) => f({ preventDefault() {} })); },
  };
  Object.defineProperty(el, "className", {
    get() { return [...classList._set].join(" "); },
    set(v) {
      classList._set.clear();
      String(v || "").split(/\s+/).filter(Boolean)
        .forEach((c) => classList._set.add(c));
    },
    enumerable: true, configurable: true,
  });
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
  ALL_ELS.push(el);
  return el;
}

function matchesPart(el, part) {
  if (!el) return false;
  if (part.startsWith("#")) return el.id === part.slice(1);
  if (part.startsWith(".")) return el.classList.contains(part.slice(1));
  return el.tagName === part.toUpperCase();
}
function matchesSelector(el, parts) {
  if (!matchesPart(el, parts[parts.length - 1])) return false;
  let ancestor = el._parent;
  for (let i = parts.length - 2; i >= 0; i--) {
    while (ancestor && !matchesPart(ancestor, parts[i])) {
      ancestor = ancestor._parent;
    }
    if (!ancestor) return false;
    ancestor = ancestor._parent;
  }
  return true;
}

/* Fake Image: 800x600 source, fires onload on the next tick. */
class FakeImage {
  constructor() {
    this.naturalWidth = 800;
    this.naturalHeight = 600;
    this.onload = null;
    this.onerror = null;
    this._src = "";
  }
  set src(v) {
    this._src = v;
    setTimeout(() => { if (this.onload) this.onload(); }, 0);
  }
  get src() { return this._src; }
}

function newContext(source) {
  const registry = {};
  const fetchCalls = [];
  const pending = [];
  const unhandled = [];
  const opened = [];
  const onUnhandled = (err) => unhandled.push(err);
  process.on("unhandledRejection", onUnhandled);
  let blobN = 0;
  const sandbox = {
    document: {
      getElementById(id) {
        if (!registry[id]) registry[id] = makeEl(id, "");
        return registry[id];
      },
      createElement(tag) {
        return tag === "canvas" ? makeCanvas() : makeEl("", tag);
      },
      querySelectorAll(selector) {
        const parts = String(selector).split(/\s+/).filter(Boolean);
        return ALL_ELS.filter((el) => matchesSelector(el, parts));
      },
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
      open(url) { opened.push(url); },
    },
    fetch: (url, opts = {}) => {
      fetchCalls.push({ url, opts });
      return new Promise((resolve) => { pending.push({ url, opts, resolve }); });
    },
    URLSearchParams,
    URL: {
      createObjectURL() { return "blob:fake-" + (++blobN); },
      revokeObjectURL() {},
    },
    Blob,
    FormData,
    Image: FakeImage,
    console,
    setTimeout,
    clearTimeout,
  };
  sandbox.window.document = sandbox.document;
  vm.createContext(sandbox);
  /* Mirror the template: the editor's filter chips and crop presets
     live in the HTML, so they must exist before app.js binds its
     listeners at load time. */
  [["original", null], ["vivid", null], ["warm", null], ["cool", null],
   ["mono", null]].forEach(([name]) => {
    const b = makeEl("", "button");
    b.className = "edit-filter secondary";
    b.dataset.filter = name;
    b.setAttribute("aria-pressed", name === "original" ? "true" : "false");
  });
  [[null, "Original"], ["1", "Square"], ["1.3333", "4:3"],
   ["1.7778", "16:9"]].forEach(([ratio]) => {
    const b = makeEl("", "button");
    b.className = "edit-crop-preset secondary";
    if (ratio) b.dataset.ratio = ratio;
  });
  vm.runInContext(source, sandbox);
  const $ = (id) => sandbox.document.getElementById(id);
  const ctx = (code) => vm.runInContext(code, sandbox);
  const resolveFetch = (urlPart, json, ok = true, blob) => {
    const i = pending.findIndex((p) => String(p.url).includes(urlPart));
    if (i < 0) throw new Error("no pending fetch for " + urlPart +
      " (pending: " + pending.map((p) => p.url).join(", ") + ")");
    const [p] = pending.splice(i, 1);
    p.resolve({ ok, status: ok ? 200 : 500,
      text: async () => JSON.stringify(json === null ? {} : json),
      blob: async () => blob || new Blob(["fake"], { type: "image/jpeg" }) });
  };
  // app.js fires GET /api/config on load; drain it.
  const ci = pending.findIndex((p) => String(p.url).includes("/api/config"));
  if (ci >= 0) {
    const [p] = pending.splice(ci, 1);
    p.resolve({ ok: true, status: 200, text: async () => "{}",
      blob: async () => new Blob([]) });
  }
  const tick = () => new Promise((r) => setTimeout(r, 0));
  const fire = (el, type, event) =>
    (el._listeners[type] || []).forEach((f) => f(event || {}));
  const dispose = () => process.removeListener("unhandledRejection", onUnhandled);
  return { sandbox, $, ctx, resolveFetch, tick, fire,
    fetchCalls, pending, unhandled, opened, dispose };
}

let failures = 0;
function check(name, ok, extra) {
  if (ok) console.log("ok:  ", name);
  else {
    failures++;
    console.log("FAIL:", name, extra === undefined ? "" : extra);
  }
}
function hasCall(pred) {
  for (let i = ALL_CTX.length - 1; i >= 0; i--) {
    if (ALL_CTX[i].calls.some(pred)) return true;
  }
  return false;
}
function hasFilter(str) {
  return hasCall((c) => c[0] === "filter" && String(c[1]).includes(str));
}
function lastRotate() {
  /* v58: the most recent rotate angle any render drew, or null if no
     render ran yet. Render canvases are created in order, so the
     newest context holds the newest render. */
  for (let i = ALL_CTX.length - 1; i >= 0; i--) {
    const calls = ALL_CTX[i].calls;
    for (let j = calls.length - 1; j >= 0; j--) {
      if (calls[j][0] === "rotate") return calls[j][1];
    }
  }
  return null;
}
function cropEq(a, b) {
  /* v58: drags do float math, so compare crop rects within a hair. */
  return a && b && ["x", "y", "w", "h"].every((k) => Math.abs(a[k] - b[k]) < 0.01);
}

const DRAFT = { id: "d9", photos: ["d9/1.jpg", "d9/2.jpg"] };
const fakeBlob = new Blob(["photo-bytes"], { type: "image/jpeg" });
const chip = (name) => ALL_ELS.find((e) => e.dataset.filter === name);
const cropPreset = (ratio) =>
  ALL_ELS.find((e) => e.classList.contains("edit-crop-preset") &&
    (e.dataset.ratio || null) === ratio);

(async () => {
  const { sandbox, $, ctx, resolveFetch, tick, fire,
    fetchCalls, unhandled, dispose } = newContext(SRC);
  sandbox.fakeBlob = fakeBlob;
  ctx("selectedFiles = [{ blob: fakeBlob, name: 'photo1.jpg', url: 'blob:seed-1' }];");

  /* ---------- 1. Step-1 Edit opens the in-app editor ---------- */
  $("editor").classList.add("hidden");
  ctx("openEditor(0);");
  await tick(); await tick();
  check("step-1 Edit opens the in-app editor",
    !$("editor").classList.contains("hidden"));
  check("editor loads the photo at full frame",
    ctx("editImg !== null") &&
    JSON.stringify(ctx("editCrop")) === JSON.stringify({ x: 0, y: 0, w: 800, h: 600 }));
  check("editor opens with zeroed sliders and Original pressed",
    ctx("editAdj.brightness") === 0 && $("edit-brightness").value === "0" &&
    ctx("editFilter") === "original");

  /* ---------- 2. Every adjustment reaches the render ---------- */
  const slide = async (id, value) => {
    $(id).value = String(value);
    fire($(id), "input");
    await tick(); await tick();
  };
  await slide("edit-brightness", 40);
  check("brightness slider moves state", ctx("editAdj.brightness") === 40);
  check("brightness reaches the preview render", hasFilter("brightness(1.400)"));
  check("manual slider clears the active filter", ctx("editFilter") === "");
  await slide("edit-contrast", -20);
  check("contrast reaches the preview render", hasFilter("contrast(0.800)"));
  await slide("edit-saturation", 30);
  check("saturation reaches the preview render", hasFilter("saturate(1.300)"));
  await slide("edit-warmth", 50);
  check("warmth paints a warm tint overlay",
    hasCall((c) => c[0] === "gco" && c[1] === "overlay") &&
    hasCall((c) => c[0] === "fillStyle" &&
      String(c[1]).includes("rgba(255,128,0,0.090)")));
  await slide("edit-sharpness", 50);
  check("sharpness runs a pixel pass on the render",
    hasCall((c) => c[0] === "getImageData") &&
    hasCall((c) => c[0] === "putImageData"));
  chip("vivid").click();
  await tick(); await tick();
  check("Vivid filter sets saturation 25 and contrast 10",
    ctx("editAdj.saturation") === 25 && ctx("editAdj.contrast") === 10 &&
    ctx("editFilter") === "vivid");
  check("Vivid reaches the render", hasFilter("saturate(1.250)"));
  chip("mono").click();
  await tick(); await tick();
  check("Mono filter kills saturation",
    ctx("editAdj.saturation") === -100 && hasFilter("saturate(0.000)"));
  $("btn-edit-auto").click();
  await tick(); await tick();
  check("Auto enhance sets values from the photo itself",
    ctx("editAdj.brightness") === 29 && ctx("editAdj.contrast") === 22 &&
    ctx("editAdj.saturation") === 12);
  /* v58: Auto enhance never brightens an already-bright photo — a
     bright white box with faint print must keep its whites and the
     print readable. */
  const brightVals = ctx("autoEnhanceValues(189, 71)");
  check("Auto enhance never brightens an already-bright photo",
    brightVals.brightness === -30 && brightVals.brightness <= 0);
  const brightFilter = ctx("adjustmentFilter(autoEnhanceValues(189, 71))");
  const bf = Number(brightFilter.match(/brightness\(([\d.]+)\)/)[1]);
  const cf = Number(brightFilter.match(/contrast\(([\d.]+)\)/)[1]);
  const whiteAfter = 127.5 + (255 * bf - 127.5) * cf;
  const faintAfter = 127.5 + (200 * bf - 127.5) * cf;
  check("Auto enhance on a bright white box keeps the whites and the faint print",
    bf <= 1 && whiteAfter < 255 && whiteAfter - faintAfter > 20);
  /* v58: a failed probe leaves the photo alone, never a blind boost. */
  $("btn-edit-reset").click();
  const realCreate = sandbox.document.createElement;
  sandbox.document.createElement = (tag) => {
    if (tag === "canvas") {
      const c = realCreate(tag);
      c.getContext = () => null;
      return c;
    }
    return realCreate(tag);
  };
  $("btn-edit-auto").click();
  sandbox.document.createElement = realCreate;
  check("a failed Auto enhance probe applies no boost",
    ctx("editAdj.brightness") === 0 && ctx("editAdj.contrast") === 0 &&
    ctx("editAdj.saturation") === 0 &&
    $("edit-brightness").value === "0");
  await tick(); await tick();

  /* ---------- 3. Crop, rotate, straighten, flip ---------- */
  cropPreset("1").click();
  check("Square crop preset centers a square crop",
    JSON.stringify(ctx("editCrop")) ===
      JSON.stringify({ x: 100, y: 0, w: 600, h: 600 }));
  $("btn-edit-rotr").click();
  check("rotate right turns the frame and resets the crop",
    ctx("editRot") === 90 &&
    JSON.stringify(ctx("editCrop")) ===
      JSON.stringify({ x: 0, y: 0, w: 600, h: 800 }));
  $("btn-edit-flip").click();
  check("flip mirrors the photo",
    ctx("editFlip") === true &&
    hasCall((c) => c[0] === "scale" && c[1] === -1 && c[2] === 1));
  $("btn-edit-reset").click();
  await slide("edit-straighten", 10);
  check("straighten tilts and insets the crop out of the corners",
    ctx("editStraighten") === 10 &&
    JSON.stringify(ctx("editCrop")) ===
      JSON.stringify({ x: 139, y: 139, w: 614, h: 452 }));

  /* ---------- 4. Reset restores, Cancel discards ---------- */
  await slide("edit-brightness", 40);
  $("btn-edit-rotr").click();
  $("btn-edit-reset").click();
  check("Reset restores the photo as opened",
    ctx("editAdj.brightness") === 0 && ctx("editRot") === 0 &&
    ctx("editFlip") === false && ctx("editStraighten") === 0 &&
    JSON.stringify(ctx("editCrop")) ===
      JSON.stringify({ x: 0, y: 0, w: 800, h: 600 }) &&
    $("edit-brightness").value === "0" && $("edit-straighten").value === "0");
  const beforeCancel = ctx("selectedFiles[0].blob");
  await slide("edit-brightness", 40);
  $("btn-edit-cancel").click();
  check("Cancel closes the editor and leaves the photo alone",
    $("editor").classList.contains("hidden") &&
    ctx("selectedFiles[0].blob") === beforeCancel);
  ctx("openEditor(0);");
  await tick(); await tick();
  check("reopening after Cancel starts clean",
    ctx("editAdj.brightness") === 0 &&
    !$("editor").classList.contains("hidden"));

  /* ---------- 4b. v58: Straighten must never leak into a fresh open,
     and the crop must grow back ---------- */
  await slide("edit-straighten", 14);
  check("straighten 14 tilts the render",
    ctx("editStraighten") === 14 &&
    lastRotate() === 14 * Math.PI / 180);
  $("btn-edit-cancel").click();
  check("Cancel leaves no Straighten behind",
    ctx("editStraighten") === 0 && $("edit-straighten").value === "0");
  /* Worst case: a stale value is forced back onto the slider, the way
     a WebView form-state restore would, then a fresh open. */
  $("edit-straighten").value = "14";
  ctx("openEditor(0);");
  check("fresh open zeroes the sliders before the photo loads",
    ctx("editStraighten") === 0 && $("edit-straighten").value === "0");
  fire($("edit-straighten"), "input");
  check("a stray input while opening cannot re-tilt the photo",
    ctx("editStraighten") === 0);
  await tick(); await tick();
  check("fresh open renders untilted with a full-frame crop",
    lastRotate() === 0 &&
    JSON.stringify(ctx("editCrop")) ===
      JSON.stringify({ x: 0, y: 0, w: 800, h: 600 }));
  /* Straighten out and back grows the crop back. */
  await slide("edit-straighten", 10);
  check("straighten 10 insets the crop",
    JSON.stringify(ctx("editCrop")) ===
      JSON.stringify({ x: 139, y: 139, w: 614, h: 452 }));
  await slide("edit-straighten", 0);
  check("straighten back to 0 grows the crop back to the full frame",
    JSON.stringify(ctx("editCrop")) ===
      JSON.stringify({ x: 0, y: 0, w: 800, h: 600 }));
  /* Corner drag grows a shrunken crop back to the full frame. The
     canvas shows 336x252 for the 800x600 photo, so client coords
     scale by 3.36 into canvas coords. */
  const drag = async (fromX, fromY, toX, toY) => {
    fire($("editor-canvas"), "pointerdown",
      { clientX: fromX, clientY: fromY, preventDefault() {} });
    fire($("editor-canvas"), "pointermove",
      { clientX: toX, clientY: toY, preventDefault() {} });
    fire($("editor-canvas"), "pointerup", {});
    await tick();
  };
  await drag(9, 9, 30, 30);
  check("dragging the top-left corner in shrinks the crop",
    cropEq(ctx("editCrop"), { x: 168, y: 126, w: 632, h: 474 }));
  await drag(21, 21, 0, 0);
  check("dragging the corner back out grows the crop to the full frame",
    cropEq(ctx("editCrop"), { x: 0, y: 0, w: 800, h: 600 }));
  /* A tap 30px inside the bottom-right corner grabs the corner,
     not a move of the whole box. */
  fire($("editor-canvas"), "pointerdown",
    { clientX: 91, clientY: 91, preventDefault() {} });
  check("a near-corner tap grabs the corner instead of moving the box",
    ctx("editDrag && editDrag.mode") === "se");
  fire($("editor-canvas"), "pointerup", {});

  /* ---------- 5. Apply saves into the same step-1 slot ---------- */
  ctx("autoDraftId = 'a1';");
  await slide("edit-brightness", 20);
  const origBlob = ctx("selectedFiles[0].blob");
  fetchCalls.length = 0;
  $("btn-edit-apply").click();
  await tick(); await tick(); await tick();
  check("Apply posts the edited photo to the auto draft replace endpoint",
    fetchCalls.some((c) => String(c.url) === "/api/drafts/a1/photos/0/replace"));
  resolveFetch("/api/drafts/a1/photos/0/replace",
    { draft: { id: "a1", photos: ["a1/1.jpg"] } });
  await tick(); await tick(); await tick();
  check("Apply closes the editor and swaps in the edited photo",
    $("editor").classList.contains("hidden") &&
    ctx("selectedFiles[0].blob") !== origBlob);

  /* ---------- 6. Draft Edit opens the in-app editor ---------- */
  const androidCalls = [];
  sandbox.window.Android = { editDraftPhoto: (...a) => { androidCalls.push(a); } };
  ctx("draft = " + JSON.stringify(DRAFT) + ";");
  $("draft-photo-error").classList.add("hidden");
  ctx("openDraftEditor(1);");
  await tick();
  check("draft Edit fetches the photo instead of calling the bridge",
    fetchCalls.some((c) => String(c.url) === "/photos/d9/2.jpg") &&
    androidCalls.length === 0);
  resolveFetch("/photos/d9/2.jpg", null, true, fakeBlob);
  await tick(); await tick();
  check("draft Edit opens the same in-app editor",
    !$("editor").classList.contains("hidden") &&
    ctx("editSource.kind") === "draft" &&
    $("draft-photo-error").classList.contains("hidden"));

  /* ---------- 7. Draft Apply saves and refreshes ---------- */
  fetchCalls.length = 0;
  $("btn-edit-apply").click();
  await tick(); await tick(); await tick();
  check("draft Apply posts to the draft replace endpoint",
    fetchCalls.some((c) => String(c.url) === "/api/drafts/d9/photos/1/replace"));
  const DRAFT2 = { id: "d9", photos: ["d9/1.jpg", "d9/2.jpg"] };
  resolveFetch("/api/drafts/d9/photos/1/replace", { draft: DRAFT2 });
  await tick(); await tick();
  resolveFetch("/api/drafts/d9", { draft: DRAFT2 });
  await tick(); await tick(); await tick();
  check("draft Apply closes the editor and refreshes the thumbnails",
    $("editor").classList.contains("hidden") &&
    $("draft-photos").children.length === 2);

  /* ---------- 8. A draft photo that cannot be fetched says so ---------- */
  ctx("openDraftEditor(0);");
  await tick();
  resolveFetch("/photos/d9/1.jpg", null, false);
  await tick(); await tick();
  check("draft Edit that cannot fetch the photo says so plainly",
    !$("draft-photo-error").classList.contains("hidden") &&
    $("draft-photo-error").textContent.includes("Couldn't open that photo"));

  check("no unhandled promise rejections", unhandled.length === 0,
    unhandled.map((e) => String(e && e.stack || e)).join(" | "));
  dispose();
  console.log(failures === 0 ? "photo-editor: all pass"
    : "photo-editor: " + failures + " FAILURES");
  process.exit(failures === 0 ? 0 : 1);
})().catch((e) => { console.log("ERROR: " + (e && e.stack || e)); process.exit(1); });
