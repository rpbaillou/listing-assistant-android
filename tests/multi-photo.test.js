/* v36: node tests for multi-photo capture in static/app.js.
   The app is a browser script, so this stubs the DOM with STATEFUL
   elements (same pattern as shipping-chooser.test.js): values, text,
   and hidden toggles persist across $() calls. Canvas + image decode
   are stubbed (createImageBitmap returns a fake bitmap, canvas toBlob
   returns a fake JPEG blob), so the real intake functions run end to
   end: two sequential photos append, the count shows, the cap never
   drops silently, Choose-on-step-1 still replaces, and listing-page
   intake appends to the draft. Run: node tests/multi-photo.test.js
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

/* Noop canvas context: any method call succeeds, any property set sticks. */
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
  /* Browser semantics: window.createImageBitmap IS the global, and
     decodeFile calls the bare name after checking window's. */
  createImageBitmap: async () => ({ width: 120, height: 90, close() {} }),
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
const hidden = (id) => $(id).classList.contains("hidden");
const ctx = (code) => vm.runInContext(code, sandbox);
const fakeFile = (name) => new File(["x"], name, { type: "image/jpeg" });

(async () => {
  ctx("maxPhotos = 24;");

  // Pure helpers.
  check("room shrinks with current count",
    sandbox.photoRoomFor(0) === 24 && sandbox.photoRoomFor(23) === 1);
  check("room floors at zero", sandbox.photoRoomFor(24) === 0 && sandbox.photoRoomFor(30) === 0);
  check("count text is plain words", sandbox.photoCountTextFor(3) === "3 of 24 photos");

  // Two sequential photos append (the v36 headline case).
  ctx("selectedFiles = [];");
  await sandbox.handlePickedFiles([fakeFile("a.jpg")], false);
  await sandbox.handlePickedFiles([fakeFile("b.jpg")], false);
  check("two sequential photos append to 2", ctx("selectedFiles.length") === 2);
  check("count line shows 2 of 24", $("photo-count").textContent === "2 of 24 photos");
  check("Take another button visible after first photo", !hidden("btn-take-another"));

  // Cap: never a silent drop.
  ctx("maxPhotos = 2;");
  await sandbox.handlePickedFiles([fakeFile("c.jpg")], false);
  check("third photo at cap is not added", ctx("selectedFiles.length") === 2);
  check("cap shows a plain full message, not a silent drop",
    !hidden("skip-note") && $("skip-note").textContent.includes("already has 2 photos"));
  ctx("maxPhotos = 24;");

  // Choose photos on step 1 still replaces, as designed.
  await sandbox.handlePickedFiles([fakeFile("d.jpg")], true);
  check("Choose on step 1 still replaces the set", ctx("selectedFiles.length") === 1);

  // Listing-page render: every photo gets an Edit button + count.
  sandbox.renderDraftPhotos({ photos: ["d1/1.jpg", "d1/2.jpg"] });
  const wraps = $("draft-photos").children;
  check("draft render shows both photos", wraps.length === 2);
  check("every draft photo gets an Edit button",
    wraps.every((w) => w.children.length === 2 && w.children[1].textContent === "Edit"));
  check("draft count line shows 2 of 24", $("draft-photo-count").textContent === "2 of 24 photos");

  // Listing-page upload appends (never replaces) + note.
  ctx("draft = { id: 'd9', photos: ['d9/1.jpg'] };");
  fetchCalls.length = 0;
  fetchQueue.push({ draft: { id: "d9", photos: ["d9/1.jpg", "d9/2.jpg"] }, added: 1, dropped: 0 });
  await sandbox.uploadDraftPhotos([fakeFile("e.jpg")]);
  check("draft upload POSTs to the draft photos endpoint",
    fetchCalls.some((c) => String(c.url) === "/api/drafts/d9/photos" && c.opts.method === "POST"));
  check("draft upload re-renders appended photos", $("draft-photos").children.length === 2);
  check("draft upload note says Added 1 photo",
    !$("draft-photo-note").classList.contains("hidden") &&
    $("draft-photo-note").textContent.includes("Added 1 photo."));

  // Dropped-at-cap note on the listing page.
  ctx("draft = { id: 'd9', photos: ['d9/1.jpg'] };");
  fetchQueue.push({ draft: { id: "d9", photos: ["d9/1.jpg", "d9/2.jpg"] }, added: 1, dropped: 1 });
  await sandbox.uploadDraftPhotos([fakeFile("f.jpg"), fakeFile("g.jpg")]);
  check("draft upload note names photos that did not fit",
    $("draft-photo-note").textContent.includes("did not fit"));

  // Camera routing: step-1 target appends, draft target uploads.
  const cameraCalls = [];
  sandbox.window.Android = { openCamera: () => { cameraCalls.push(1); } };
  ctx("selectedFiles = [];");
  fetchCalls.length = 0;
  sandbox.takeStep1Photo();
  check("step-1 Take photo sets step-1 target + calls the bridge",
    ctx("cameraTarget") === "step1" && cameraCalls.length === 1);
  await sandbox.onCameraPhoto("data:image/jpeg;base64,xxxx");
  /* Step-1 onCameraPhoto fires the append without awaiting it (fine in
     production: the UI updates when it lands); flush here before asserting. */
  await new Promise((r) => setTimeout(r, 20));
  check("step-1 camera snap appends, never posts to a draft",
    ctx("selectedFiles.length") === 1 &&
    !fetchCalls.some((c) => String(c.url).includes("/api/drafts/")));
  ctx("draft = { id: 'd9', photos: ['d9/1.jpg'] };");
  fetchCalls.length = 0;
  fetchQueue.push({ draft: { id: "d9", photos: ["d9/1.jpg", "d9/2.jpg"] }, added: 1, dropped: 0 });
  vm.runInContext("cameraTarget = 'draft';", sandbox);
  await sandbox.onCameraPhoto("data:image/jpeg;base64,xxxx");
  check("draft camera snap uploads to the draft endpoint",
    fetchCalls.some((c) => String(c.url) === "/api/drafts/d9/photos"));

  // Pixel-editor handoff (Nov 3 decision): draft Edit hands the photo to
  // the phone's editor via the bridge — never the in-app editor — with a
  // plain fallback when the bridge is absent, and a photos-only refresh
  // the app calls after a good edit returns.
  ctx("draft = { id: 'd9', photos: ['d9/1.jpg', 'd9/2.jpg'] };");
  const editCalls = [];
  sandbox.window.Android = { editDraftPhoto: (id, rel, i) => { editCalls.push([id, rel, i]); } };
  sandbox.editDraftPhotoInPixel(1);
  check("draft Edit calls the phone-editor bridge with id, photo, and slot",
    editCalls.length === 1 && editCalls[0][0] === "d9" &&
    editCalls[0][1] === "d9/2.jpg" && editCalls[0][2] === 1);
  $("editor").classList.add("hidden");
  sandbox.editDraftPhotoInPixel(1);
  check("draft Edit never opens the in-app editor", hidden("editor"));
  delete sandbox.window.Android;
  sandbox.editDraftPhotoInPixel(0);
  check("draft Edit without the bridge shows a plain error, never silent",
    !$("draft-photo-error").classList.contains("hidden") &&
    $("draft-photo-error").textContent.includes("needs the app"));
  fetchQueue.push({ draft: { id: "d9", photos: ["d9/1.jpg", "d9/2.jpg", "d9/3.jpg"] } });
  await sandbox.reloadDraftPhotosOnly();
  check("photos-only refresh re-renders thumbnails from the draft fetch",
    $("draft-photos").children.length === 3);

  console.log(failures === 0 ? "multi-photo: all pass" : "multi-photo: " + failures + " FAILURES");
  process.exit(failures === 0 ? 0 : 1);
})().catch((e) => { console.log("ERROR: " + (e && e.stack || e)); process.exit(1); });
