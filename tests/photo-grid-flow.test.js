/* v66 node tests: the step-1 photo grid flows (layout must flow,
   nothing overlaps). Nov 20 he reported the grid overlapping the
   elements below it — the photo-count line and Take/Choose buttons
   covered, the bottom row of photos sitting on the Draft button —
   with 6 Barbie photos. The cause, proven in a real Chromium layout:
   the wrap carried aspect-ratio 1/1, so its height was locked to its
   width while its content (frame + an Undo edit button, ~31px) was
   taller. The grid's rows were sized from the locked height, so
   every row's content spilled ~31px past its row — accumulating to
   ~92px below the grid box for 3 rows — and the locked height
   transferred back into width, widening the columns ~31px so the
   grid ran off the right edge of the screen. The fix moves the
   square onto the frame (aspect-ratio on .thumb-img); the wrap is
   height:auto, so it grows with its content and the grid — and
   everything below it — flows.
   Run: node tests/photo-grid-flow.test.js (exit 0 = pass). The app
   is a browser script, so this stubs the DOM (same pattern as
   multi-photo.test.js) and runs the REAL renderThumbs, plus it reads
   the REAL style.css and asserts the sizing contract that keeps the
   grid flowing. The geometric proof (grid box contains its content,
   Analyze sits below the grid, no sideways scroll) lives in
   tests/test_photo_grid_flow.py, which measures the same page in a
   real Chromium. */
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

function fakeCanvas() {
  return {
    width: 0,
    height: 0,
    getContext() { return noopCtx(); },
    toBlob(cb) { cb(new Blob(["fake-jpeg"], { type: "image/jpeg" })); },
  };
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
    createImageBitmap: async () => ({ width: 120, height: 90, close() {} }),
  },
  createImageBitmap: async () => ({ width: 120, height: 90, close() {} }),
  Image: class {
    constructor() {
      this.naturalWidth = 800; this.naturalHeight = 600;
      this.onload = null; this.onerror = null; this._src = "";
    }
    set src(v) {
      this._src = v;
      setTimeout(() => { if (this.onload) this.onload(); }, 0);
    }
    get src() { return this._src; }
  },
  fetch: async (url, opts = {}) => {
    fetchCalls.push({ url, opts });
    const u = String(url);
    if (u.startsWith("data:") || u.startsWith("/photos/")) {
      return {
        ok: true, status: 200,
        text: async () => "{}",
        blob: async () => new Blob(["fake-jpeg"], { type: "image/jpeg" }),
      };
    }
    const next = fetchQueue.length ? fetchQueue.shift() : {};
    return {
      ok: true, status: 200,
      text: async () => JSON.stringify(next),
      blob: async () => new Blob(["fake-jpeg"], { type: "image/jpeg" }),
    };
  },
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
const $ = (id) => sandbox.document.getElementById(id);
const ctx = (code) => vm.runInContext(code, sandbox);

/* ---- the real stylesheet, parsed the same way fit_check does ---- */
function parseRules(css) {
  const rules = {};
  css = css.replace(/\/\*[\s\S]*?\*\//g, "");
  const re = /([^{}]+)\{([^{}]*)\}/g;
  let m;
  while ((m = re.exec(css)) !== null) {
    const selector = m[1].split(/\s+/).join(" ");
    const props = {};
    for (const decl of m[2].split(";")) {
      if (decl.indexOf(":") !== -1) {
        const i = decl.indexOf(":");
        props[decl.slice(0, i).trim()] = decl.slice(i + 1).trim();
      }
    }
    for (const sel of selector.split(",")) {
      rules[sel.trim()] = Object.assign(rules[sel.trim()] || {}, props);
    }
  }
  return rules;
}
const css = fs.readFileSync(
  path.join(__dirname, "..", "app", "src", "main", "python", "static", "style.css"), "utf8");
const rules = parseRules(css);
/* px() at the 17px root (body font-size 17px). */
function px(v) {
  if (v === undefined || v === null) return null;
  const m = /^(-?[\d.]+)(px|rem)$/.exec(v);
  if (!m) return null;
  return parseFloat(m[1]) * (m[2] === "rem" ? 17 : 1);
}

(async () => {
  ctx("maxPhotos = 24;");
  // The reported state: 6 photos, 3 edited (Undo buttons present).
  ctx("selectedFiles = [" +
    "{url:'u0', _undoBlob: new Blob(['x'])}," +
    "{url:'u1', _undoBlob: new Blob(['x'])}," +
    "{url:'u2', _undoBlob: new Blob(['x'])}," +
    "{url:'u3'},{url:'u4'},{url:'u5'}];");
  ctx("skippedCount = 0;");
  sandbox.renderThumbs();
  const wraps = $("thumbs").children;
  check("grid renders all 6 photos", wraps.length === 6);
  check("edited photos carry an Undo button inside the wrap, after the frame",
    [0, 1, 2].every((i) => wraps[i].children.length === 2 &&
      wraps[i].children[1].className === "thumb-edit-below" &&
      wraps[i].children[1].textContent === "Undo edit"));
  check("unedited photos have no Undo button",
    [3, 4, 5].every((i) => wraps[i].children.length === 1));
  check("every wrap starts with the square frame (img + X + Edit pill)",
    wraps.every((w) => w.children[0].className === "thumb-img" &&
      w.children[0].children.length === 3 &&
      w.children[0].children[1].className === "thumb-x" &&
      w.children[0].children[1].textContent === "×" &&
      w.children[0].children[2].className === "thumb-edit" &&
      w.children[0].children[2].textContent.includes("Edit")));
  check("count line shows 6 of 24", $("photo-count").textContent === "6 of 24 photos");

  // No edited photos: no Undo buttons anywhere in the grid.
  ctx("selectedFiles = [{url:'a'},{url:'b'}];");
  sandbox.renderThumbs();
  check("unedited set renders no Undo buttons",
    $("thumbs").children.every((w) => w.children.length === 1));

  // The sizing contract that keeps the grid flowing. The wrap must
  // NOT carry aspect-ratio (that locked its height to its width while
  // its content was taller); the FRAME carries the square instead.
  const wrap = rules[".thumbs.photo-grid .thumb-wrap"] || {};
  const frame = rules[".thumbs.photo-grid .thumb-img"] || {};
  const grid = rules[".thumbs.photo-grid"] || {};
  const undo = rules[".thumb-edit-below"] || {};
  const baseWrap = rules[".thumb-wrap"] || {};
  check("grid wrap has no aspect-ratio and no fixed height",
    !("aspect-ratio" in wrap) && !("height" in wrap) && !("max-height" in wrap));
  check("grid frame is the square (aspect-ratio 1/1, height auto)",
    frame["aspect-ratio"] === "1 / 1" && frame.height === "auto" &&
    frame.width === "100%");
  check("grid itself has no fixed height",
    !("height" in grid) && !("max-height" in grid));
  check("grid stays 2 equal columns",
    grid["grid-template-columns"] === "1fr 1fr");
  check("base wrap (draft strip) has no aspect-ratio either",
    !("aspect-ratio" in baseWrap));
  // The hanging Edit pill (bottom -8px) must clear the Undo button
  // below the frame: the Undo button's top margin has to beat 8px.
  const pillOverhang = Math.abs(px((rules[".thumb-edit"] || {}).bottom) || 0);
  const undoMargin = px(undo["margin-top"]);
  check("Undo button top margin clears the Edit pill overhang",
    undoMargin !== null && pillOverhang === 8 && undoMargin > pillOverhang,
    "margin-top " + undo["margin-top"] + " vs pill overhang 8px");
  // The X overhang (8px) must clear into the grid gap, not a neighbor.
  const gap = px(grid.gap);
  const xOverhang = Math.abs(px((rules[".thumb-x"] || {}).top) || 0);
  check("grid gap clears the X overhang",
    gap !== null && xOverhang === 8 && gap > xOverhang,
    "gap " + grid.gap + " vs X overhang 8px");

  console.log(failures === 0 ? "photo-grid-flow: all pass" : "photo-grid-flow: " + failures + " FAILURES");
  process.exit(failures === 0 ? 0 : 1);
})().catch((e) => { console.log("ERROR: " + (e && e.stack || e)); process.exit(1); });
