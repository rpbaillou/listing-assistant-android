/* v52: node tests for the 2K photo cap in static/app.js.
   The real downscaleFile runs end to end with a stubbed DOM: canvas
   records the size it is given, createImageBitmap returns a fake
   bitmap of a size the test sets, so the canvas size IS the downscale
   result. Run: node tests/photo-2048.test.js (exit 0 = pass). */
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
    setAttribute(k, v) { el.dataset[k] = v; },
    getAttribute(k) { return el.dataset[k]; },
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

function noopCtx() {
  return new Proxy({}, {
    get(t, p) {
      if (typeof p === "string") return () => {};
      return undefined;
    },
    set(t, p, v) { t[p] = v; return true; },
  });
}

/* The canvas under test: width/height are what downscaleFile chose. */
let lastCanvas = null;
function fakeCanvas() {
  const c = {
    width: 0,
    height: 0,
    getContext() { return noopCtx(); },
    toBlob(cb) { cb(new Blob(["fake-jpeg"], { type: "image/jpeg" })); },
  };
  lastCanvas = c;
  return c;
}

/* Bitmap size the test sets before each downscale. */
let bitmapSize = { width: 4096, height: 3072 };

const registry = {};
const sandbox = {
  document: {
    getElementById(id) {
      if (!registry[id]) registry[id] = makeEl();
      return registry[id];
    },
    createElement(tag) {
      if (tag === "canvas") return fakeCanvas();
      return makeEl();
    },
    querySelectorAll() { return []; },
    addEventListener() {},
    body: makeEl(),
  },
  window: {
    addEventListener() {},
    location: { search: "" },
    history: {},
    innerWidth: 400,
    createImageBitmap: async () => ({ ...bitmapSize, close() {} }),
  },
  createImageBitmap: async () => ({ ...bitmapSize, close() {} }),
  fetch: async () => ({
    ok: true, status: 200,
    text: async () => "{}",
    blob: async () => new Blob(["fake-jpeg"], { type: "image/jpeg" }),
  }),
  URL: {
    createObjectURL: () => "blob:fake-" + Math.random(),
    revokeObjectURL() {},
  },
  URLSearchParams,
  File,
  Blob,
  FormData,
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
const fakeFile = (name) => new File(["x"], name, { type: "image/jpeg" });

(async () => {
  // The cap itself is pinned in the source: 2048, not the old 1600.
  check("MAX_EDGE is 2048 in the loaded script",
    vm.runInContext("MAX_EDGE", sandbox) === 2048);

  // A 4096x3072 photo shrinks to 2048 on the long edge, aspect kept.
  bitmapSize = { width: 4096, height: 3072 };
  let r = await sandbox.downscaleFile(fakeFile("big.jpg"), 0);
  check("4096x3072 downscales to 2048x1536",
    !r.skipped && lastCanvas.width === 2048 && lastCanvas.height === 1536);

  // A tall photo shrinks on its long edge too.
  bitmapSize = { width: 1800, height: 2400 };
  r = await sandbox.downscaleFile(fakeFile("tall.jpg"), 0);
  check("1800x2400 downscales to 1536x2048",
    !r.skipped && lastCanvas.width === 1536 && lastCanvas.height === 2048);

  // Exactly at the cap: untouched.
  bitmapSize = { width: 2048, height: 1536 };
  r = await sandbox.downscaleFile(fakeFile("atcap.jpg"), 0);
  check("2048x1536 is left alone",
    !r.skipped && lastCanvas.width === 2048 && lastCanvas.height === 1536);

  // Smaller than the cap: untouched (no upscaling).
  bitmapSize = { width: 800, height: 600 };
  r = await sandbox.downscaleFile(fakeFile("small.jpg"), 0);
  check("800x600 is left alone",
    !r.skipped && lastCanvas.width === 800 && lastCanvas.height === 600);

  if (failures) { console.log(failures + " FAILURES"); process.exit(1); }
  console.log("photo-2048: all checks passed");
})();
