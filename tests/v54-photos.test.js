/* v54 node tests: the save line drains, and one listing never splits
   into two drafts. Run: node tests/v54-photos.test.js (exit 0 = pass).
   The app is a browser script, so this stubs the DOM with STATEFUL
   elements (same pattern as multi-photo.test.js / v50-photo-resume.test.js)
   and a small in-memory server behind fetch. The harness is factored
   into makeSandbox so a restart can be simulated with a FRESH vm
   context sharing the same server: the load-time resume restore runs
   again, and the photos must all come back.

   Covered:
   - 8 rapid camera photos all land in the auto draft (no restart),
     the "Saving N photos…" note shows while they are in flight and
     hides when the line is empty, and the camera path skips the
     second JS downscale (0 canvases) while a gallery pick still
     shrinks (1 canvas).
   - After the queue drains, a restart restores every photo and the
     next photo lands in the SAME draft — no second auto draft.
   - A failed append retries the SAME draft (bounded); a failure that
     keeps failing keeps the id (stale, not split); the next photo
     after the server recovers lands in the same draft. A deleted
     draft ("Draft not found") is the one case that clears the id,
     and the photo after that starts exactly one fresh draft.
   - A photo taken while the launch-time restore is still loading
     waits for it and lands in the resume draft — no second draft. */
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
    set() { return true; },
  });
}

/* Small in-memory server behind fetch. drafts holds the server-side
   truth; resumeId is the remembered draft; failAppend fails every
   photo append; failAppendCount fails the next N appends, then
   recovers; holdAppends parks append POSTs until releaseAppends();
   resumeDelayMs delays the resume-record read (a slow launch). */
const server = {
  drafts: {},
  resumeId: null,
  seq: 0,
  fail: new Set(),
  failAppend: false,
  failAppendCount: 0,
  holdAppends: false,
  resumeDelayMs: 0,
  _appendWaits: [],
};
const fetchCalls = [];

function blankDraft(id) {
  return { id: id, title: "", description: "", category: "", photos: [],
           price: "", condition: "", condition_details: "",
           best_offer: false, best_offer_auto_decline: "",
           best_offer_auto_accept: "", attributes: {},
           identification_notes: "" };
}

function route(url, opts) {
  const method = (opts && opts.method) || "GET";
  const u = String(url);
  const key = method + " " + u;
  if (server.fail.has(key)) return { status: 500, body: { error: "boom" } };
  if (method === "POST" && u === "/api/auto-draft") {
    const id = "auto" + (++server.seq);
    server.drafts[id] = blankDraft(id);
    return { status: 200, body: { draft: server.drafts[id] } };
  }
  if (server.failAppendCount > 0 && method === "POST" && /\/photos$/.test(u)) {
    server.failAppendCount--;
    return { status: 500, body: { error: "boom" } };
  }
  if (server.failAppend && method === "POST" && /\/photos$/.test(u)) {
    return { status: 500, body: { error: "boom" } };
  }
  let m = u.match(/^\/api\/drafts\/([^/]+)\/photos$/);
  if (method === "POST" && m) {
    const d = server.drafts[m[1]];
    if (!d) return { status: 404, body: { error: "Draft not found." } };
    const n = (opts.body && opts.body.getAll)
      ? opts.body.getAll("photos").length : 0;
    for (let i = 0; i < n; i++) d.photos.push(d.id + "/" + (d.photos.length + 1) + ".jpg");
    return { status: 200, body: { draft: d, added: n, dropped: 0 } };
  }
  m = u.match(/^\/api\/drafts\/([^/]+)$/);
  if (m && method === "GET") {
    const d = server.drafts[m[1]];
    if (!d) return { status: 404, body: { error: "Draft not found." } };
    return { status: 200, body: { draft: d } };
  }
  if (m && method === "DELETE") {
    delete server.drafts[m[1]];
    if (server.resumeId === m[1]) server.resumeId = null;
    return { status: 200, body: { ok: true } };
  }
  if (method === "GET" && u === "/api/resume-draft") {
    const d = server.drafts[server.resumeId];
    if (!d) return { status: 200, body: { draft_id: null } };
    return { status: 200, body: { draft_id: d.id, title: d.title,
                                  photo_count: d.photos.length, updated: "" } };
  }
  if (method === "POST" && u === "/api/resume-draft") {
    const parsed = JSON.parse(opts.body || "{}");
    server.resumeId = parsed.draft_id || null;
    return { status: 200, body: { draft_id: server.resumeId } };
  }
  return { status: 200, body: {} };
}

function releaseAppends() {
  server.holdAppends = false;
  const waits = server._appendWaits || [];
  server._appendWaits = [];
  waits.forEach((r) => r());
}

function resetServer() {
  server.drafts = {};
  server.resumeId = null;
  server.seq = 0;
  server.fail.clear();
  server.failAppend = false;
  server.failAppendCount = 0;
  server.holdAppends = false;
  server.resumeDelayMs = 0;
  server._appendWaits = [];
  fetchCalls.length = 0;
}

const SRC = fs.readFileSync(
  path.join(__dirname, "..", "app", "src", "main", "python", "static", "app.js"), "utf8");

function makeSandbox() {
  const registry = {};
  let canvasCount = 0;
  const sandbox = {
    document: {
      getElementById(id) {
        if (!registry[id]) registry[id] = makeEl();
        return registry[id];
      },
      createElement(tag) {
        if (tag === "canvas") { canvasCount++; return {
          width: 0, height: 0,
          getContext() { return noopCtx(); },
          toBlob(cb) { cb(new Blob(["fake-jpeg"], { type: "image/jpeg" })); },
        }; }
        return makeEl();
      },
      querySelector() { return makeEl(); },
      querySelectorAll() { return []; },
      addEventListener() {},
      body: makeEl(),
    },
    window: {
      addEventListener() {},
      location: { search: "" },
      history: {},
      innerWidth: 400,
      scrollTo() {},
      createImageBitmap: async () => ({ width: 120, height: 90, close() {} }),
    },
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
      if (server.resumeDelayMs && u === "/api/resume-draft") {
        await new Promise((r) => setTimeout(r, server.resumeDelayMs));
      }
      const method = (opts && opts.method) || "GET";
      if (server.holdAppends && method === "POST" && /\/photos$/.test(u)) {
        await new Promise((resolve) => {
          (server._appendWaits = server._appendWaits || []).push(resolve);
        });
      }
      const r = route(u, opts);
      return {
        ok: r.status >= 200 && r.status < 300, status: r.status,
        text: async () => JSON.stringify(r.body),
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
  vm.runInContext(SRC, sandbox);
  /* Mirror index.html: the save note starts hidden. The stub elements
     start with an empty class list, so without this the note reads as
     visible in a fresh context even when nothing is pending. */
  sandbox.document.getElementById("save-note").classList.add("hidden");
  const ctx = (code) => vm.runInContext(code, sandbox);
  const $ = (id) => sandbox.document.getElementById(id);
  const hidden = (id) => $(id).classList.contains("hidden");
  return { sandbox, ctx, $, hidden, canvasCount: () => canvasCount };
}

const flush = (ms) => new Promise((r) => setTimeout(r, ms || 20));
async function drain(ctx) {
  /* Let any in-flight intake (a fire-and-forget onCameraPhoto) enqueue
     its queue step first — otherwise the tail read below can catch
     the queue before the step exists and return early. */
  await flush();
  await ctx("autoDraftQueue.catch(() => {})");
  await flush();
}
const fakeFile = (name) => new File(["x"], name, { type: "image/jpeg" });
const autoDraftCreates = () =>
  fetchCalls.filter((c) => String(c.url) === "/api/auto-draft" &&
    (c.opts.method || "GET") === "POST").length;
const appendAttempts = () =>
  fetchCalls.filter((c) => /\/photos$/.test(String(c.url)) &&
    (c.opts.method || "GET") === "POST").length;

let failures = 0;
function check(name, cond) {
  if (!cond) { failures++; console.log("FAIL: " + name); }
  else { console.log("ok: " + name); }
}

(async () => {
  // ---- A. 8 rapid camera photos all land; the Saving note tracks them ----
  resetServer();
  const a = makeSandbox();
  a.ctx("maxPhotos = 24;");
  await flush(); /* load-time resume check against an empty server */
  server.holdAppends = true;
  for (let i = 0; i < 8; i++) a.sandbox.onCameraPhoto("data:image/jpeg;base64,AAAA");
  await flush(30);
  check("8 rapid camera photos join the grid right away",
    a.ctx("selectedFiles.length") === 8);
  check("Saving note shows the 8 photos still in flight",
    !a.hidden("save-note") &&
    a.$("save-note").textContent.includes("Saving 8 photos"));
  const draftA = a.ctx("autoDraftId");
  check("first photo created the auto draft, none saved yet (held)",
    !!draftA && server.drafts[draftA] && server.drafts[draftA].photos.length === 0);
  check("camera path skips the second JS downscale (no canvases)",
    a.canvasCount() === 0);
  releaseAppends();
  await drain(a.ctx);
  check("all 8 land in the auto draft once the line drains",
    server.drafts[draftA].photos.length === 8);
  check("Saving note hides when the line is empty",
    a.hidden("save-note") && a.ctx("savePendingPhotos") === 0);
  check("8 rapid photos made exactly one auto draft",
    autoDraftCreates() === 1);
  await a.sandbox.handlePickedFiles([fakeFile("g.jpg")], false);
  check("a gallery pick still shrinks (one canvas)",
    a.canvasCount() === 1);
  await drain(a.ctx);
  check("gallery pick appends to the same draft",
    server.drafts[draftA].photos.length === 9 && autoDraftCreates() === 1);

  // ---- B. Restart after the drain loses nothing ----
  const b = makeSandbox(); /* fresh page, same server */
  b.ctx("maxPhotos = 24;");
  await flush();
  await drain(b.ctx);
  check("restart restores all 9 photos into the grid",
    b.ctx("selectedFiles.length") === 9 && b.ctx("autoDraftId") === draftA);
  check("restart shows no Saving note (nothing pending)",
    b.hidden("save-note"));
  b.sandbox.onCameraPhoto("data:image/jpeg;base64,AAAA");
  await drain(b.ctx);
  check("a photo after restart lands in the same draft, no second draft",
    server.drafts[draftA].photos.length === 10 && autoDraftCreates() === 1);

  // ---- C. A failed save retries the same draft; a deleted draft is the
  // one case that starts a fresh one ----
  resetServer();
  const c = makeSandbox();
  c.ctx("maxPhotos = 24;");
  await flush();
  server.failAppendCount = 2; /* first two attempts fail, third lands */
  c.sandbox.onCameraPhoto("data:image/jpeg;base64,AAAA");
  await drain(c.ctx);
  const draftC = c.ctx("autoDraftId");
  check("an append that fails twice lands on the retry, same draft",
    !!draftC && server.drafts[draftC].photos.length === 1 &&
    appendAttempts() === 3 && autoDraftCreates() === 1);
  check("a recovered save clears the stale flag",
    c.ctx("autoDraftStale") === false);
  server.failAppend = true; /* every append fails now */
  c.sandbox.onCameraPhoto("data:image/jpeg;base64,AAAA");
  await drain(c.ctx);
  check("a save that keeps failing marks stale but KEEPS the draft id",
    c.ctx("autoDraftStale") === true && c.ctx("autoDraftId") === draftC);
  check("Saving note hides even when the save failed",
    c.hidden("save-note") && c.ctx("savePendingPhotos") === 0);
  server.failAppend = false; /* the server recovers */
  c.sandbox.onCameraPhoto("data:image/jpeg;base64,AAAA");
  await drain(c.ctx);
  check("the next photo after recovery lands in the SAME draft",
    server.drafts[draftC].photos.length === 2 && autoDraftCreates() === 1 &&
    c.ctx("autoDraftStale") === false);
  delete server.drafts[draftC]; /* he deleted the draft in My drafts */
  c.sandbox.onCameraPhoto("data:image/jpeg;base64,AAAA");
  await drain(c.ctx);
  check("an append to a deleted draft clears the id (fresh draft is right)",
    c.ctx("autoDraftId") === null && c.ctx("autoDraftStale") === true);
  c.sandbox.onCameraPhoto("data:image/jpeg;base64,AAAA");
  await drain(c.ctx);
  const draftC2 = c.ctx("autoDraftId");
  check("the photo after a deleted draft starts exactly one fresh draft",
    !!draftC2 && draftC2 !== draftC && autoDraftCreates() === 2 &&
    server.drafts[draftC2].photos.length === 1);

  // ---- D. A photo taken while the restore is still loading waits for it ----
  resetServer();
  server.drafts.resume1 = blankDraft("resume1");
  server.drafts.resume1.photos = ["resume1/1.jpg", "resume1/2.jpg"];
  server.resumeId = "resume1";
  server.resumeDelayMs = 150; /* a slow launch */
  const d = makeSandbox(); /* load starts the delayed restore */
  d.ctx("maxPhotos = 24;");
  d.sandbox.onCameraPhoto("data:image/jpeg;base64,AAAA"); /* taken at once */
  await d.ctx("resumeCheck");
  await flush();
  await drain(d.ctx);
  server.resumeDelayMs = 0;
  check("a photo taken during the restore lands in the resume draft",
    d.ctx("autoDraftId") === "resume1" &&
    server.drafts.resume1.photos.length === 3);
  check("no second auto draft was created while the restore ran",
    autoDraftCreates() === 0);
  check("the grid holds the restored photos plus the new one",
    d.ctx("selectedFiles.length") === 3);

  console.log(failures === 0 ? "v54-photos: all pass" : "v54-photos: " + failures + " FAILURES");
  process.exit(failures === 0 ? 0 : 1);
})().catch((e) => { console.log("ERROR: " + (e && e.stack || e)); process.exit(1); });
