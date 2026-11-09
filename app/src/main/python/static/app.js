/* Listing Assistant v2.8 - front end. Plain steps, no framework. */
let draft = null;
let selectedFiles = [];
let saveTimer = null;
let saveDirty = false; /* v5: true while edits are waiting for the debounced save */
let maxPhotos = 10; /* v8: overwritten by /api/config (eBay allows 24 free) */

/* v50: the auto draft — a blank draft made on the server the moment
   the first photo is picked or taken, so closing the app loses
   nothing. autoDraftId is its id (null until the first photo);
   autoDraftStale means a save to it failed, so the photos on this
   screen are the truth and Analyze/Quick post must upload them
   directly instead of reusing the draft. autoDraftQueue serializes
   every server op on the auto draft (create, append, delete, move,
   replace) so rapid snaps can never race each other on photo
   numbering. */
let autoDraftId = null;
let autoDraftStale = false;
let autoDraftQueue = Promise.resolve();
let resumeDraftId = null;

const $ = (id) => document.getElementById(id);

function show(stepId) {
  /* v5: never switch sections with unsaved edits pending — flush first so
     leaving the draft (My drafts, back button, sold-price pop-up) cannot
     lose what he just typed. */
  flushSave();
  document.querySelectorAll("main .step").forEach((s) => s.classList.add("hidden"));
  $(stepId).classList.remove("hidden");
  window.scrollTo(0, 0);
}

function showError(msg) {
  const el = $("analyze-error");
  el.textContent = msg;
  el.classList.remove("hidden");
}
function hideError() { $("analyze-error").classList.add("hidden"); }

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  const text = await r.text();
  let data = {};
  let isJson = false;
  try { data = text ? JSON.parse(text) : {}; isJson = true; }
  catch (e) { isJson = false; }
  if (!r.ok) {
    if (isJson && data.error) throw new Error(data.error);
    // Non-JSON error body (e.g. Cloudflare's 502 page when the tunnel times
    // out on a huge upload): say what to do, not the status code.
    if (!isJson) throw new Error("The request took too long or the connection dropped. Try fewer photos.");
    throw new Error("Request failed (" + r.status + ")");
  }
  return data;
}

/* ---------- step 1: photos ---------- */
/* v2.8: shrink each photo on the phone before upload (max 1600px long edge,
   JPEG q85) so 10 photos stay well under the tunnel timeout. Files the
   browser cannot decode (e.g. HEIC) are skipped with a plain-words note. */
const MAX_EDGE = 1600;

function decodeFile(file) {
  if (window.createImageBitmap) {
    return createImageBitmap(file).catch(() => null);
  }
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => { URL.revokeObjectURL(url); resolve(img); };
    img.onerror = () => { URL.revokeObjectURL(url); resolve(null); };
    img.src = url;
  });
}

async function downscaleFile(file, idx) {
  const bmp = await decodeFile(file);
  if (!bmp) return { skipped: true };
  const scale = Math.min(1, MAX_EDGE / Math.max(bmp.width, bmp.height));
  const w = Math.max(1, Math.round(bmp.width * scale));
  const h = Math.max(1, Math.round(bmp.height * scale));
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  canvas.getContext("2d").drawImage(bmp, 0, 0, w, h);
  if (bmp.close) bmp.close();
  const blob = await new Promise((res) => canvas.toBlob(res, "image/jpeg", 0.85));
  if (!blob) return { skipped: true };
  return { blob: blob, name: "photo" + (idx + 1) + ".jpg" };
}

/* v5: every chosen photo gets an X button so a wrong pick can be removed
   before identifying. Each entry carries its own preview URL so removal
   revokes exactly the right one and never disturbs the rest. */
let skippedCount = 0;

function clearPreviews() {
  selectedFiles.forEach((e) => { if (e.url) URL.revokeObjectURL(e.url); });
  selectedFiles = [];
  skippedCount = 0;
  $("thumbs").innerHTML = "";
  $("skip-note").classList.add("hidden");
  updateAnalyzeButton();
}

function updateAnalyzeButton() {
  $("btn-analyze").disabled = selectedFiles.length === 0;
  /* v24: quick post needs photos too, same rule. */
  $("btn-quick-post").disabled = selectedFiles.length === 0;
}

function renderThumbs() {
  const box = $("thumbs");
  box.innerHTML = "";
  selectedFiles.forEach((entry, i) => {
    const wrap = document.createElement("div");
    wrap.className = "thumb-wrap";
    const frame = document.createElement("div");
    frame.className = "thumb-img";
    const img = document.createElement("img");
    img.src = entry.url;
    img.alt = "Chosen photo " + (i + 1);
    const x = document.createElement("button");
    x.type = "button";
    x.className = "thumb-x";
    x.textContent = "×";
    x.setAttribute("aria-label", "Remove this photo");
    x.addEventListener("click", () => removePhoto(i));
    /* v18: Edit opens the crop/rotate editor for this photo.
       v49: pencil glyph on the pill, as in design 4. */
    const e = document.createElement("button");
    e.type = "button";
    e.className = "thumb-edit";
    e.textContent = "✎ Edit";
    e.setAttribute("aria-label", "Edit this photo");
    e.addEventListener("click", () => openEditor(i));
    frame.appendChild(img);
    frame.appendChild(x);
    frame.appendChild(e);
    wrap.appendChild(frame);
    /* v44: drag to reorder — the frame is the drag handle (see
       attachThumbDrag below); the v40 ◀ ▶ buttons are gone. */
    attachThumbDrag(frame, wrap, box, i, reorderPhoto);
    box.appendChild(wrap);
  });
  for (let s = 0; s < skippedCount; s++) {
    const ph = document.createElement("div");
    ph.className = "thumb-skip";
    ph.textContent = "can't preview";
    box.appendChild(ph);
  }
  /* v36: visible count, so multi-snap never looks like it stopped
     at one. v49: the "Take another photo" button is gone — Take photo
     is always visible above, so it covers the same tap. */
  $("photo-count").textContent = photoCountTextFor(selectedFiles.length);
  updateAnalyzeButton();
}

function removePhoto(i) {
  const entry = selectedFiles[i];
  if (!entry) return;
  if (entry.url) URL.revokeObjectURL(entry.url);
  selectedFiles.splice(i, 1);
  renderThumbs();
  hideError();
  /* v50: the same photo leaves the auto draft. */
  removeAutoDraftPhoto(i);
}

/* ---------- v44: drag to reorder photos (touch + mouse) ---------- */
/* The v40 ◀ ▶ buttons are gone: press a photo and drag it to its new
   spot, on either strip. Pointer events cover touch and mouse with one
   code path (HTML5 drag-and-drop does not fire on touch). A tap still
   clicks the X / Edit buttons — a drag only starts after the pointer
   moves 8px, and pointerdowns that start on a button never drag. The
   order after a drop is the order publish sends (first = main). */

/* Pure helper, unit-tested: move the item at `from` to final index
   `to`. Returns false and leaves the array alone on a bad index. */
function moveArrayItem(arr, from, to) {
  if (!arr || from < 0 || from >= arr.length) return false;
  if (to < 0) to = 0;
  if (to >= arr.length) to = arr.length - 1;
  if (to === from) return true;
  const item = arr.splice(from, 1)[0];
  arr.splice(to, 0, item);
  return true;
}

/* Pure helper, unit-tested: given the wraps' rects in strip order, the
   dragged item's index, and the pointer, return the final index the
   dragged item should land at. The nearest wrap decides: left half
   means before it, right half means after it, adjusted for the dragged
   item leaving its own slot (so dragging an item over itself is a
   no-op, never a shift by one). */
function dragFinalIndex(rects, from, x, y) {
  const n = rects.length;
  if (n === 0 || from < 0 || from >= n) return from;
  let k = 0;
  let best = Infinity;
  for (let i = 0; i < n; i++) {
    const r = rects[i];
    const cx = r.left + r.width / 2;
    const cy = r.top + r.height / 2;
    const d = (x - cx) * (x - cx) + (y - cy) * (y - cy);
    if (d < best) { best = d; k = i; }
  }
  const r = rects[k];
  const leftHalf = x < r.left + r.width / 2;
  let to = leftHalf ? (k > from ? k - 1 : k) : (k < from ? k + 1 : k);
  if (to < 0) to = 0;
  if (to > n - 1) to = n - 1;
  return to;
}

/* One drag session shared by both strips (only one drag at a time). */
let thumbDrag = null;

function thumbDragWraps(box) {
  return Array.from(box.children).filter(
    (c) => c.className && c.className.indexOf("thumb-wrap") !== -1);
}

function attachThumbDrag(frame, wrap, box, from, commit) {
  frame.addEventListener("pointerdown", (e) => {
    // Buttons on the photo (X / Edit) are taps, never a drag start.
    if (e.target && e.target.closest && e.target.closest("button")) return;
    if (e.button !== undefined && e.button !== 0) return;
    thumbDrag = {
      from: from, wrap: wrap, box: box, commit: commit,
      startX: e.clientX, startY: e.clientY,
      active: false, pointerId: e.pointerId, to: from,
    };
  });
  frame.addEventListener("pointermove", (e) => {
    const d = thumbDrag;
    if (!d || d.wrap !== wrap) return;
    const dx = e.clientX - d.startX;
    const dy = e.clientY - d.startY;
    if (!d.active) {
      if (dx * dx + dy * dy < 64) return; // 8px: a tap is not a drag
      d.active = true;
      d.wrap.classList.add("dragging");
      try { frame.setPointerCapture(e.pointerId); } catch (err) { /* no capture: moves still arrive while over the frame */ }
    }
    if (e.cancelable) e.preventDefault();
    const wraps = thumbDragWraps(d.box);
    const rects = wraps.map((w) => w.getBoundingClientRect());
    const to = dragFinalIndex(rects, d.from, e.clientX, e.clientY);
    d.to = to;
    wraps.forEach((w, i) => {
      w.classList.remove("drop-before", "drop-after");
      if (i === to && to !== d.from) {
        // The landing edge faces the direction the photo came from.
        w.classList.add(to < d.from ? "drop-before" : "drop-after");
      }
    });
  });
  const endDrag = (commitIt) => {
    const d = thumbDrag;
    if (!d || d.wrap !== wrap) return;
    thumbDrag = null;
    thumbDragWraps(d.box).forEach(
      (w) => w.classList.remove("dragging", "drop-before", "drop-after"));
    if (commitIt && d.active && d.to !== d.from) {
      d.commit(d.from, d.to);
    }
  };
  frame.addEventListener("pointerup", () => endDrag(true));
  frame.addEventListener("pointercancel", () => endDrag(false));
}

/* v44: step-1 reorder by drag (client-side only — Identify uploads
   in this order, first = main). v50: the same move lands in the auto
   draft, so the saved order stays the order on this screen. */
function reorderPhoto(from, to) {
  if (moveArrayItem(selectedFiles, from, to)) renderThumbs();
  moveAutoDraftPhoto(from, to);
}

/* ---------- v18: photo editor (crop + rotate) ---------- */
/* Photos arrive here already downscaled to 1600px (intake), so canvas
   work stays small. The editor works on the entry's current blob, so
   tapping Edit again on an already-edited photo re-edits that version.
   Save replaces the entry's blob, so Identify uploads the edited photo
   through the existing pipeline. No original-restore stack in v18: if he
   over-crops, he removes the photo and re-picks or re-takes it. */

/* Pure helpers, unit-tested in tests/editor-math.test.js. */
function rotatedDims(w, h, rot) {
  const r = ((rot % 360) + 360) % 360;
  return (r === 90 || r === 270) ? { w: h, h: w } : { w: w, h: h };
}
function clampCropRect(rect, bw, bh, minSize) {
  const w = Math.max(minSize, Math.min(rect.w, bw));
  const h = Math.max(minSize, Math.min(rect.h, bh));
  const x = Math.max(0, Math.min(rect.x, bw - w));
  const y = Math.max(0, Math.min(rect.y, bh - h));
  return { x: x, y: y, w: w, h: h };
}

let editIndex = -1;
let editImg = null;      /* full-res source image (<=1600px) */
let editImgUrl = null;   /* object URL for editImg, revoked on close */
let editRot = 0;         /* 0/90/180/270, clockwise */
let editCrop = null;     /* {x,y,w,h} in ROTATED image coordinates */
let editScale = 1;       /* display px per rotated-image px */
let editDrag = null;     /* {mode, startX, startY, crop} in canvas px */
const EDIT_MIN_CROP = 50; /* min crop edge, rotated-image px */

function editorDisplaySize(rw, rh) {
  const maxEdge = Math.min(900, Math.max(280, window.innerWidth - 64));
  const scale = Math.min(1, maxEdge / Math.max(rw, rh));
  return { w: Math.max(1, Math.round(rw * scale)),
           h: Math.max(1, Math.round(rh * scale)), scale: scale };
}

function drawEditor() {
  if (!editImg) return;
  const canvas = $("editor-canvas");
  const ctx = canvas.getContext("2d");
  const srcW = editImg.naturalWidth, srcH = editImg.naturalHeight;
  const dims = rotatedDims(srcW, srcH, editRot);
  const disp = editorDisplaySize(dims.w, dims.h);
  editScale = disp.scale;
  canvas.width = disp.w;
  canvas.height = disp.h;
  ctx.save();
  ctx.clearRect(0, 0, disp.w, disp.h);
  ctx.translate(disp.w / 2, disp.h / 2);
  ctx.rotate(editRot * Math.PI / 180);
  ctx.drawImage(editImg, -srcW * disp.scale / 2, -srcH * disp.scale / 2,
                 srcW * disp.scale, srcH * disp.scale);
  ctx.restore();
  /* Dim everything outside the crop box, then outline it + handles. */
  const s = disp.scale, c = editCrop;
  const dx = c.x * s, dy = c.y * s, dw = c.w * s, dh = c.h * s;
  ctx.fillStyle = "rgba(0,0,0,0.55)";
  ctx.fillRect(0, 0, disp.w, dy);
  ctx.fillRect(0, dy + dh, disp.w, disp.h - dy - dh);
  ctx.fillRect(0, dy, dx, dh);
  ctx.fillRect(dx + dw, dy, disp.w - dx - dw, dh);
  ctx.strokeStyle = "#fff";
  ctx.lineWidth = 2;
  ctx.strokeRect(dx, dy, dw, dh);
  const hs = 14;
  ctx.fillStyle = "#fff";
  ctx.strokeStyle = "#2f6fed";
  [[dx, dy], [dx + dw, dy], [dx, dy + dh], [dx + dw, dy + dh]].forEach(([hx, hy]) => {
    ctx.fillRect(hx - hs, hy - hs, hs * 2, hs * 2);
    ctx.strokeRect(hx - hs, hy - hs, hs * 2, hs * 2);
  });
}

function editorCanvasPos(e) {
  const canvas = $("editor-canvas");
  const r = canvas.getBoundingClientRect();
  return {
    x: (e.clientX - r.left) * canvas.width / r.width,
    y: (e.clientY - r.top) * canvas.height / r.height
  };
}

function editorHitTest(px, py) {
  const canvas = $("editor-canvas");
  const r = canvas.getBoundingClientRect();
  const tol = 28 * canvas.width / r.width; /* ~28px touch target */
  const s = editScale, c = editCrop;
  const corners = { nw: [c.x * s, c.y * s], ne: [(c.x + c.w) * s, c.y * s],
                    sw: [c.x * s, (c.y + c.h) * s], se: [(c.x + c.w) * s, (c.y + c.h) * s] };
  for (const k of Object.keys(corners)) {
    const [hx, hy] = corners[k];
    if (Math.abs(px - hx) <= tol && Math.abs(py - hy) <= tol) return k;
  }
  if (px >= c.x * s && px <= (c.x + c.w) * s &&
      py >= c.y * s && py <= (c.y + c.h) * s) return "move";
  return null;
}

$("editor-canvas").addEventListener("pointerdown", (e) => {
  if (!editImg) return;
  const p = editorCanvasPos(e);
  const mode = editorHitTest(p.x, p.y);
  if (!mode) return;
  e.preventDefault();
  try { $("editor-canvas").setPointerCapture(e.pointerId); } catch (_e) {}
  editDrag = { mode: mode, startX: p.x, startY: p.y,
               crop: Object.assign({}, editCrop) };
});

$("editor-canvas").addEventListener("pointermove", (e) => {
  if (!editDrag || !editImg) return;
  const p = editorCanvasPos(e);
  const dx = (p.x - editDrag.startX) / editScale;
  const dy = (p.y - editDrag.startY) / editScale;
  const dims = rotatedDims(editImg.naturalWidth, editImg.naturalHeight, editRot);
  const s0 = editDrag.crop, MIN = EDIT_MIN_CROP;
  let next;
  if (editDrag.mode === "move") {
    next = { x: s0.x + dx, y: s0.y + dy, w: s0.w, h: s0.h };
  } else {
    /* Corner drags anchor the opposite corner; the dragged corner may
       not cross the anchor minus the minimum size (no inversion). */
    const ax = s0.x + s0.w, ay = s0.y + s0.h;
    next = { x: s0.x, y: s0.y, w: s0.w, h: s0.h };
    if (editDrag.mode === "nw") {
      next.x = Math.min(s0.x + dx, ax - MIN);
      next.y = Math.min(s0.y + dy, ay - MIN);
      next.w = ax - next.x; next.h = ay - next.y;
    } else if (editDrag.mode === "ne") {
      next.y = Math.min(s0.y + dy, ay - MIN);
      next.w = Math.max(MIN, s0.w + dx); next.h = ay - next.y;
    } else if (editDrag.mode === "sw") {
      next.x = Math.min(s0.x + dx, ax - MIN);
      next.w = ax - next.x; next.h = Math.max(MIN, s0.h + dy);
    } else { /* se */
      next.w = Math.max(MIN, s0.w + dx); next.h = Math.max(MIN, s0.h + dy);
    }
  }
  editCrop = clampCropRect(next, dims.w, dims.h, MIN);
  drawEditor();
});

["pointerup", "pointercancel"].forEach((t) =>
  $("editor-canvas").addEventListener(t, () => { editDrag = null; }));

function rotateEditor(delta) {
  if (!editImg) return;
  editRot = (((editRot + delta) % 360) + 360) % 360;
  /* Rotating resets the crop to the full rotated frame: simpler and
     predictable than remapping a rect across a dimension swap. */
  const dims = rotatedDims(editImg.naturalWidth, editImg.naturalHeight, editRot);
  editCrop = { x: 0, y: 0, w: dims.w, h: dims.h };
  drawEditor();
}
$("btn-edit-rotl").addEventListener("click", () => rotateEditor(-90));
$("btn-edit-rotr").addEventListener("click", () => rotateEditor(90));

/* v36: hand a listing photo to the phone's own photo editor (Nov 3
   decision: his Pixel camera app editor, NOT a limited in-app editor).
   The app opens the system edit screen for that photo; when the editor
   returns, the edited photo replaces the original in the same slot
   (see reloadDraftPhotosOnly, called by the app after a good save).
   The photo is never edited in place here — the original stays until
   the edited version comes back. */
function editDraftPhotoInPixel(i) {
  const p = (draft && draft.photos) ? draft.photos[i] : null;
  if (!p || !draft.id) return;
  if (window.Android && typeof window.Android.editDraftPhoto === "function") {
    window.Android.editDraftPhoto(draft.id, p, i);
  } else {
    showDraftPhotoError("Photo editing needs the app — please open this page in the Listing Assistant app.");
  }
}

/* v36: re-read the draft's photos after the phone's editor returns a
   good edit (called by the app, not by any button). Only the thumbnails
   refresh — typed fields are never touched, so unsaved edits survive. */
async function reloadDraftPhotosOnly() {
  if (!draft || !draft.id) return;
  try {
    const res = await api("/api/drafts/" + draft.id);
    draft = res.draft;
    renderDraftPhotos(draft);
  } catch (e) {
    showDraftPhotoError("Couldn't refresh the photos. Reopen the listing to see the edited photo.");
  }
}

function openEditor(i) {
  const entry = selectedFiles[i];
  if (!entry) return;
  editIndex = i;
  editRot = 0;
  editDrag = null;
  const url = URL.createObjectURL(entry.blob);
  const img = new Image();
  img.onload = () => {
    editImg = img;
    editImgUrl = url;
    editCrop = { x: 0, y: 0, w: img.naturalWidth, h: img.naturalHeight };
    $("editor").classList.remove("hidden");
    document.body.style.overflow = "hidden";
    drawEditor();
  };
  img.onerror = () => {
    URL.revokeObjectURL(url);
    showError("Couldn't open that photo for editing.");
  };
  img.src = url;
}

function closeEditor() {
  $("editor").classList.add("hidden");
  document.body.style.overflow = "";
  if (editImgUrl) URL.revokeObjectURL(editImgUrl);
  editImg = null;
  editImgUrl = null;
  editIndex = -1;
  editDrag = null;
}
$("btn-edit-cancel").addEventListener("click", closeEditor);

$("btn-edit-save").addEventListener("click", async () => {
  if (!editImg || editIndex < 0) return;
  const btn = $("btn-edit-save");
  btn.disabled = true;
  try {
    /* Step 1: rotate the full-res source onto an offscreen canvas.
       Step 2: cut the crop rect out of it. Two steps keeps the math
       simple and correct across dimension swaps. */
    const srcW = editImg.naturalWidth, srcH = editImg.naturalHeight;
    const dims = rotatedDims(srcW, srcH, editRot);
    const rc = document.createElement("canvas");
    rc.width = dims.w; rc.height = dims.h;
    const rctx = rc.getContext("2d");
    rctx.save();
    rctx.translate(dims.w / 2, dims.h / 2);
    rctx.rotate(editRot * Math.PI / 180);
    rctx.drawImage(editImg, -srcW / 2, -srcH / 2, srcW, srcH);
    rctx.restore();
    const cx = Math.round(editCrop.x), cy = Math.round(editCrop.y);
    const cw = Math.max(1, Math.round(editCrop.w)), ch = Math.max(1, Math.round(editCrop.h));
    const oc = document.createElement("canvas");
    oc.width = cw; oc.height = ch;
    oc.getContext("2d").drawImage(rc, cx, cy, cw, ch, 0, 0, cw, ch);
    const blob = await new Promise((res) => oc.toBlob(res, "image/jpeg", 0.85));
    if (!blob) throw new Error("save failed");
    const entry = selectedFiles[editIndex];
    if (entry.url) URL.revokeObjectURL(entry.url);
    entry.blob = blob;
    entry.url = URL.createObjectURL(blob);
    renderThumbs();
    /* v50: the edited photo replaces the saved one in the auto draft. */
    replaceAutoDraftPhoto(editIndex, blob);
    closeEditor();
    hideError();
  } catch (e) {
    showError("Couldn't save that edit. Try again.");
  } finally {
    btn.disabled = false;
  }
});

/* v36: shared photo-count helpers (pure, unit-tested). roomFor is how
   many more photos fit; countText is the plain-words count line. */
function photoRoomFor(current) { return Math.max(0, maxPhotos - current); }
function photoCountTextFor(current) {
  return current + " of " + maxPhotos + " photos";
}

/* v36: which screen the native camera result belongs to. "step1" is the
   Pick photos flow; "draft" is the listing page (added photos upload
   to the draft instead of joining the step-1 set). Set by whichever
   Take photo button was tapped, read by onCameraPhoto. */
let cameraTarget = "step1";

/* Shared photo intake: "Choose photos" replaces the set, "Take photo"
   appends one more (so he can snap several angles in a row). v36: a
   photo is NEVER silently dropped at the cap — when files do not fit,
   the note says so in plain words. */
async function handlePickedFiles(fileList, replace) {
  const room = replace ? maxPhotos : photoRoomFor(selectedFiles.length);
  const all = Array.from(fileList || []);
  const picked = all.slice(0, room);
  const dropped = all.length - picked.length;
  if (replace) clearPreviews();
  skippedCount = 0;
  const addedEntries = [];
  for (let i = 0; i < picked.length; i++) {
    const r = await downscaleFile(picked[i], selectedFiles.length + i);
    if (r.skipped) { skippedCount++; continue; }
    const u = URL.createObjectURL(r.blob);
    const entry = { blob: r.blob, name: r.name, url: u };
    selectedFiles.push(entry);
    addedEntries.push(entry);
  }
  renderThumbs();
  const notes = [];
  if (skippedCount > 0) {
    notes.push(skippedCount + (skippedCount === 1 ? " photo" : " photos") +
      " couldn't be read and " + (skippedCount === 1 ? "was" : "were") + " skipped.");
  }
  if (dropped > 0) {
    notes.push(room === 0
      ? "This listing already has " + maxPhotos + " photos."
      : dropped + " more photo" + (dropped === 1 ? "" : "s") +
        " did not fit (" + maxPhotos + " max).");
  }
  if (notes.length > 0) {
    const n = $("skip-note");
    n.textContent = notes.join(" ");
    n.classList.remove("hidden");
  }
  hideError();
  /* v50: save each photo to the server the moment it is picked or
     taken, so closing the app loses nothing. Fire and forget — the
     queue keeps the order, and a failed save only marks the draft
     stale (Analyze falls back to the photos on this screen). */
  syncPickedPhotosToAutoDraft(addedEntries, replace);
}

/* ---------- v50: auto draft + return to draft ---------- */
/* Photos used to live only in memory until "Draft my listing with AI"
   or Quick post was tapped, so closing the app lost them — and
   relaunch forgot the open draft entirely. Now every photo is saved
   to the server the moment it arrives (a blank auto draft, made on
   the first photo, no AI call), and the open draft is remembered so
   launch can offer it back in one tap. */

function queueAutoDraft(fn) {
  /* Serial queue for every server op on the auto draft. The .then(fn,
     fn) shape keeps the chain alive after a failure, so one failed
     save never blocks the next one. */
  autoDraftQueue = autoDraftQueue.then(fn, fn);
  return autoDraftQueue;
}

function showAutoDraftError() {
  /* Plain words when a photo did not save yet. The photos are still
     on this screen, and Analyze/Quick post upload them directly,
     so nothing is lost — the draft is just not the source of truth
     until the next save lands. */
  showError("Your photos didn't save to the app yet — they are still on this screen and will go with your listing.");
}

function recordResumeDraft(draftId) {
  /* Remember the open draft so launch can offer it back. Fire and
     forget: a failed record just means no resume card, never a
     broken listing page. null clears it ("Start a new item"). */
  api("/api/resume-draft", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ draft_id: draftId || null }),
  }).catch(() => {});
}

async function syncPickedPhotosToAutoDraft(entries, replace) {
  /* Save picked/taken photos to the auto draft, in queue order.
     Choose photos replaces the set, so the old auto draft is
     deleted with it (no orphan draft lingers); Take photo appends.
     The first photo creates the draft. On any failure the draft is
     abandoned (autoDraftId cleared, stale set) and the photos on
     this screen stay the truth — Analyze/Quick post upload them
     directly, and the abandoned draft stays visible in My drafts,
     deletable there. */
  if (!entries.length && !autoDraftId) return;
  return queueAutoDraft(async () => {
    try {
      if (replace && autoDraftId) {
        await api("/api/drafts/" + autoDraftId, { method: "DELETE" });
        autoDraftId = null;
      }
      if (!entries.length) return;
      if (!autoDraftId) {
        const created = await api("/api/auto-draft", { method: "POST" });
        autoDraftId = created.draft.id;
        recordResumeDraft(autoDraftId);
      }
      const fd = new FormData();
      entries.forEach((e) => fd.append("photos", e.blob, e.name));
      await api("/api/drafts/" + autoDraftId + "/photos",
                { method: "POST", body: fd });
      autoDraftStale = false;
    } catch (e) {
      autoDraftId = null;
      autoDraftStale = true;
      showAutoDraftError();
    }
  });
}

function removeAutoDraftPhoto(i) {
  /* The same photo leaves the auto draft, so the saved set stays the
     set on this screen. The index is captured now but the delete
     runs in queue order, after any append still in flight, so it
     always targets the right photo. */
  if (!autoDraftId) return;
  const draftId = autoDraftId;
  queueAutoDraft(async () => {
    try {
      await api("/api/drafts/" + draftId + "/photos/" + i,
                { method: "DELETE" });
    } catch (e) {
      autoDraftStale = true;
      showAutoDraftError();
    }
  });
}

function moveAutoDraftPhoto(from, to) {
  /* The same move lands in the auto draft, so the saved order stays
     the order on this screen (first photo is the main one). */
  if (!autoDraftId || from === to) return;
  const draftId = autoDraftId;
  queueAutoDraft(async () => {
    try {
      await api("/api/drafts/" + draftId + "/photos/" + from + "/move", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ to: to }),
      });
    } catch (e) {
      autoDraftStale = true;
      showAutoDraftError();
    }
  });
}

function replaceAutoDraftPhoto(index, blob) {
  /* An edited photo replaces the saved one in the auto draft, in
     place — order and publish inclusion are unchanged. */
  if (!autoDraftId) return;
  const draftId = autoDraftId;
  queueAutoDraft(async () => {
    try {
      const fd = new FormData();
      fd.append("photo", blob, "edited.jpg");
      await api("/api/drafts/" + draftId + "/photos/" + index + "/replace",
                { method: "POST", body: fd });
    } catch (e) {
      autoDraftStale = true;
      showAutoDraftError();
    }
  });
}

async function discardAutoDraft() {
  /* The auto draft is redundant once Analyze/Quick post made the
     real draft from the same photos — delete it so My drafts never
     shows the same photos twice. Best effort: a failed delete just
     leaves a visible, deletable draft behind. */
  if (!autoDraftId) return;
  const draftId = autoDraftId;
  autoDraftId = null;
  try {
    await api("/api/drafts/" + draftId, { method: "DELETE" });
  } catch (e) { /* leave it in My drafts; he can delete it there */ }
}

function showResumeCard(d) {
  /* The one-tap way back to the open draft. A titled draft names
     itself; a photo-only draft says how many photos are waiting. */
  resumeDraftId = d.id;
  const photos = (d.photos || []).length;
  const title = String(d.title || "").trim();
  $("resume-text").textContent = title
    ? "Continue your draft: " + title
    : "You have photos waiting (" + photos +
      (photos === 1 ? " photo" : " photos") + "), no title yet.";
  $("resume-card").classList.remove("hidden");
}

async function restoreAutoDraftPhotos(d) {
  /* A photo-only draft (no title yet) puts its photos back in the
     grid, so closing the app mid-picking loses nothing — he is back
     where he was, and Analyze works on the restored set. One
     unreadable photo is skipped; the rest still return. Returns
     false when nothing could be restored, so the caller can fall
     back to the resume card instead. */
  const photos = d.photos || [];
  if (!photos.length) return false;
  const entries = [];
  for (const rel of photos) {
    try {
      const res = await fetch("/photos/" + rel);
      if (!res.ok) continue;
      const blob = await res.blob();
      entries.push({ blob: blob,
                     name: String(rel).split("/").pop() || "photo.jpg",
                     url: URL.createObjectURL(blob) });
    } catch (e) { /* skip one unreadable photo */ }
  }
  if (!entries.length) return false;
  selectedFiles = entries;
  autoDraftId = d.id;
  autoDraftStale = false;
  renderThumbs();
  return true;
}

async function checkResumeDraft() {
  /* On launch, offer the draft he had open. A titled draft gets the
     one-tap Continue card; a photo-only draft restores its photos
     into the grid instead. No record, a listed draft, or a deleted
     draft means step 1 opens fresh, as before. A lost edit result
     (the process died while the photo editor was open) needs no
     special handling here: the original photo is unchanged on the
     server, the draft reopens with its saved fields, and no stale
     error is shown — the error element starts hidden. */
  let r;
  try {
    r = await api("/api/resume-draft");
  } catch (e) { return; }
  if (!r || !r.draft_id) return;
  let d;
  try {
    d = (await api("/api/drafts/" + r.draft_id)).draft;
  } catch (e) { return; }
  if (!d) return;
  if (String(d.title || "").trim()) {
    showResumeCard(d);
    return;
  }
  if (!(await restoreAutoDraftPhotos(d))) showResumeCard(d);
}

/* v20: update check and Edit keys moved to the app's native top-right
   menu (three dots). The web header is a plain title bar now, so these
   listeners are gone — the native menu calls the same Android handlers. */

$("photo-input").addEventListener("change", (e) => { handlePickedFiles(e.target.files, true); });

/* v14: Take photo calls the camera directly through the app's JS interface.
   v12 used <input capture="camera">, but his phone's WebView ignores the
   capture attribute, so both buttons opened the gallery. This bypasses the
   WebView file picker entirely. v36: both step-1 Take buttons share this
   path and set the camera target, so the native result routes back here. */
function takeStep1Photo() {
  cameraTarget = "step1";
  if (window.Android && typeof window.Android.openCamera === "function") {
    window.Android.openCamera();
  } else {
    showError("The camera needs the app — please use Choose photos instead.");
  }
}
$("btn-take-photo").addEventListener("click", takeStep1Photo);

/* Called by native code after a camera snap. dataUrl is a natively
   downscaled JPEG (1600px, q85). Appends, so he can snap several angles.
   v36: routes by cameraTarget — on the listing page the snap uploads to
   the draft instead of joining the step-1 set. */
async function onCameraPhoto(dataUrl) {
  try {
    const blob = await (await fetch(dataUrl)).blob();
    const file = new File([blob], "camera-" + Date.now() + ".jpg", { type: "image/jpeg" });
    if (cameraTarget === "draft") {
      await uploadDraftPhotos([file]);
      return;
    }
    handlePickedFiles([file], false);
  } catch (e) {
    showError("Couldn't read that photo.");
  }
}

/* v50: the Analyze flow, as a named function so node tests can call
   it directly. Reuses the auto draft when it holds the photos
   (reanalyze — no second upload, no second draft). If the auto draft
   is stale or the reuse fails, falls back to uploading the photos on
   this screen, and deletes the redundant auto draft. */
async function analyzePhotos() {
  hideError();
  $("spinner").classList.remove("hidden");
  $("btn-analyze").disabled = true;
  try {
    await autoDraftQueue.catch(() => {});
    let done = false;
    if (autoDraftId && !autoDraftStale) {
      try {
        const { draft: d } = await api("/api/drafts/" + autoDraftId + "/reanalyze",
                                       { method: "POST" });
        autoDraftId = null;
        loadDraftIntoForm(d);
        await prefillListing();
        show("step-draft");
        done = true;
      } catch (e) {
        autoDraftStale = true; /* fall through: upload the photos on this screen */
      }
    }
    if (!done) {
      const fd = new FormData();
      selectedFiles.forEach((f) => fd.append("photos", f.blob, f.name));
      const { draft: d } = await api("/api/analyze", { method: "POST", body: fd });
      await discardAutoDraft();
      loadDraftIntoForm(d);
      await prefillListing();
      show("step-draft");
    }
  } catch (err) {
    showError(err.message);
  } finally {
    $("spinner").classList.add("hidden");
    $("btn-analyze").disabled = selectedFiles.length === 0;
  }
}

$("btn-analyze").addEventListener("click", analyzePhotos);

/* ---------- v24: quick post (skip AI) ---------- */
/* Same photo upload as Identify, but the server starts a BLANK draft —
   no Gemini call, no AI wait. He types the title himself in step 2,
   then the listing page takes over, prefilled with his usual settings. */
/* v50: the Quick post flow, as a named function so node tests can
   call it directly. Same reuse as Analyze — the auto draft already
   holds the photos, so it opens directly instead of uploading them
   again into a second draft. Stale or failed reuse falls back to the
   old upload path. */
async function quickPostPhotos() {
  hideError();
  $("btn-quick-post").disabled = true;
  try {
    await autoDraftQueue.catch(() => {});
    let done = false;
    if (autoDraftId && !autoDraftStale) {
      try {
        const { draft: d } = await api("/api/drafts/" + autoDraftId);
        autoDraftId = null;
        loadDraftIntoForm(d);
        await prefillListing();
        show("step-draft");
        done = true;
      } catch (e) {
        autoDraftStale = true; /* fall through: upload the photos on this screen */
      }
    }
    if (!done) {
      const fd = new FormData();
      selectedFiles.forEach((f) => fd.append("photos", f.blob, f.name));
      const { draft: d } = await api("/api/quick-post", { method: "POST", body: fd });
      await discardAutoDraft();
      loadDraftIntoForm(d);
      await prefillListing();
      show("step-draft");
    }
  } catch (err) {
    showError(err.message);
  } finally {
    $("btn-quick-post").disabled = selectedFiles.length === 0;
  }
}

$("btn-quick-post").addEventListener("click", quickPostPhotos);

/* ---------- step 2: edit draft ---------- */
function loadDraftIntoForm(d) {
  draft = d;
  saveDirty = false; /* v5: a fresh load is the clean baseline */
  clearTimeout(saveTimer);
  saveTimer = null;
  $("f-title").value = d.title || "";
  $("f-category").value = d.category || "";
  $("f-description").value = d.description || "";
  $("f-condition").value = d.condition || "";
  $("f-condition-details").value = d.condition_details || "";
  $("f-price").value = d.price || "";
  updateCount();
  renderDraftPhotos(d);
  renderAttrs(d.attributes || {});
  const notes = $("ai-notes");
  if (d.identification_notes) {
    notes.textContent = "AI notes: " + d.identification_notes;
    notes.classList.remove("hidden");
  } else notes.classList.add("hidden");
  $("btn-photos-zip").href = "/api/drafts/" + d.id + "/photos.zip";
  /* v50: the open draft is the one launch returns to. */
  recordResumeDraft(d.id);
}

/* v36: draft thumbnails on the listing page. Every photo — existing or
   added — gets an Edit button into the same editor step 1 uses, so added
   photos are never second-class. Count line mirrors the step-1 count.
   v49: Edit sits under the photo as a blue text-style button (design
   4), not overlaid on it — a real button, min-height 26px like the
   v44 Edit sizing, so it stays tappable. The frame stays the drag
   handle; taps on Edit never start a drag. */
function renderDraftPhotos(d) {
  const box = $("draft-photos");
  box.innerHTML = "";
  (d.photos || []).forEach((p, i) => {
    const wrap = document.createElement("div");
    wrap.className = "thumb-wrap";
    const frame = document.createElement("div");
    frame.className = "thumb-img";
    const img = document.createElement("img");
    img.src = "/photos/" + p;
    img.alt = "Listing photo " + (i + 1);
    const x = document.createElement("button");
    x.type = "button";
    x.className = "thumb-x";
    x.textContent = "×";
    x.setAttribute("aria-label", "Delete this photo");
    x.addEventListener("click", () => deleteDraftPhoto(i));
    frame.appendChild(img);
    frame.appendChild(x);
    wrap.appendChild(frame);
    const e = document.createElement("button");
    e.type = "button";
    e.className = "thumb-edit-below";
    e.textContent = "Edit";
    e.setAttribute("aria-label", "Edit this photo");
    e.addEventListener("click", () => editDraftPhotoInPixel(i));
    wrap.appendChild(e);
    /* v44: drag to reorder — the frame is the drag handle (see
       attachThumbDrag); the v40 ◀ ▶ buttons are gone. */
    attachThumbDrag(frame, wrap, box, i, reorderDraftPhoto);
    box.appendChild(wrap);
  });
  $("draft-photo-count").textContent = photoCountTextFor((d.photos || []).length);
}

/* v40: delete + arrange on the listing page. Delete removes the photo
   from the draft immediately (same precedent as the step-1 X: no confirm
   dialog — a removed photo is re-added with Choose photos or Take
   photo). v44: arrange is drag to reorder (see attachThumbDrag) — one
   POST moves the photo to its final slot; publish sends imageUrls in
   this order, so the first photo is the main one. */
async function deleteDraftPhoto(i) {
  if (!draft || !draft.id) return;
  hideDraftPhotoError();
  try {
    const res = await api("/api/drafts/" + draft.id + "/photos/" + i, { method: "DELETE" });
    draft = res.draft;
    renderDraftPhotos(draft);
    $("draft-photo-note").textContent = "Photo removed.";
    $("draft-photo-note").classList.remove("hidden");
  } catch (e) {
    showDraftPhotoError(e.message);
  }
}

async function reorderDraftPhoto(from, to) {
  if (!draft || !draft.id) return;
  if (from === to) return;
  hideDraftPhotoError();
  try {
    const res = await api("/api/drafts/" + draft.id + "/photos/" + from + "/move", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ to: to }),
    });
    draft = res.draft;
    renderDraftPhotos(draft);
  } catch (e) {
    showDraftPhotoError(e.message);
  }
}

function showDraftPhotoError(msg) {
  const el = $("draft-photo-error");
  el.textContent = msg;
  el.classList.remove("hidden");
}
function hideDraftPhotoError() { $("draft-photo-error").classList.add("hidden"); }

/* v36: append files to the loaded draft (listing-page intake). Appends
   only — an append never replaces existing photos (reorder via the v40
   move endpoint), and the 24 cap is enforced there with a plain
   message, not a silent drop. */
async function uploadDraftPhotos(files) {
  if (!draft || !draft.id) return;
  hideDraftPhotoError();
  $("draft-photo-note").classList.add("hidden");
  const downscaled = [];
  for (const f of files) {
    const r = await downscaleFile(f, (draft.photos || []).length + downscaled.length);
    if (!r.skipped) downscaled.push(r);
  }
  if (downscaled.length === 0) {
    showDraftPhotoError("Couldn't read those photos.");
    return;
  }
  const fd = new FormData();
  downscaled.forEach((r) => fd.append("photos", r.blob, r.name));
  try {
    const res = await api("/api/drafts/" + draft.id + "/photos", { method: "POST", body: fd });
    draft = res.draft;
    renderDraftPhotos(draft);
    const notes = [];
    if (res.added) notes.push("Added " + res.added + " photo" + (res.added === 1 ? "" : "s") + ".");
    if (res.dropped) notes.push(res.dropped + " more did not fit (" + maxPhotos + " max).");
    if (notes.length > 0) {
      $("draft-photo-note").textContent = notes.join(" ");
      $("draft-photo-note").classList.remove("hidden");
    }
  } catch (e) {
    showDraftPhotoError(e.message);
  }
}

$("draft-photo-input").addEventListener("change", (e) => {
  uploadDraftPhotos(Array.from(e.target.files || []));
  e.target.value = ""; /* so choosing the same files again re-fires */
});

$("btn-draft-take-photo").addEventListener("click", () => {
  cameraTarget = "draft";
  if (window.Android && typeof window.Android.openCamera === "function") {
    window.Android.openCamera();
  } else {
    showDraftPhotoError("The camera needs the app — please use Choose photos instead.");
  }
});

function renderAttrs(attrs) {
  const box = $("attrs");
  box.innerHTML = "";
  Object.entries(attrs).forEach(([k, v]) => box.appendChild(attrRow(k, v)));
  if (Object.keys(attrs).length === 0) box.appendChild(attrRow("", ""));
  updateAttrsSummary();
}

function attrRow(k, v) {
  const row = document.createElement("div");
  row.className = "attr-row";
  row.innerHTML =
    '<input type="text" class="ak" placeholder="Name (Brand)" value="">' +
    '<input type="text" class="av" placeholder="Value" value="">' +
    '<button type="button" title="Remove">×</button>';
  row.querySelector(".ak").value = k;
  row.querySelector(".av").value = v;
  row.querySelector("button").addEventListener("click", () => {
    row.remove();
    scheduleSave();
    updateAttrsSummary();
  });
  row.querySelectorAll("input").forEach((i) =>
    i.addEventListener("input", () => { scheduleSave(); updateAttrsSummary(); }));
  return row;
}

$("btn-add-attr").addEventListener("click", () => {
  $("attrs").appendChild(attrRow("", ""));
  updateAttrsSummary();
});

function collectForm() {
  const attrs = {};
  document.querySelectorAll("#attrs .attr-row").forEach((row) => {
    const k = row.querySelector(".ak").value.trim();
    const v = row.querySelector(".av").value.trim();
    if (k) attrs[k] = v;
  });
  const cid = $("p-category").value.trim();
  return {
    title: $("f-title").value.trim(),
    category: $("f-category").value.trim(),
    description: $("f-description").value.trim(),
    condition: $("f-condition").value,
    condition_details: $("f-condition-details").value.trim(),
    price: $("f-price").value.trim(),
    attributes: attrs,
    /* v29: the listing page holds every eBay field, so the draft
       autosave carries them all — category number + name (the name
       only when the number still equals the picked one, so a hand-typed
       number never inherits a stale name), the three policy numbers,
       and Best Offer. No separate panel-save chain exists anymore. */
    category_id: cid,
    category_name: cid && cid === pickedCat.id ? pickedCat.name : "",
    fulfillment_policy_id: $("p-fulfillment").value.trim(),
    payment_policy_id: $("p-payment").value.trim(),
    return_policy_id: $("p-return").value.trim(),
    best_offer: $("p-best-offer").checked,
    best_offer_auto_decline: $("p-bo-decline").value.trim(),
    best_offer_auto_accept: $("p-bo-accept").value.trim(),
    /* v42: package weight + size, per draft (framed and unframed
       parcels differ, so never a usual setting). Weight is pounds +
       ounces boxes now; the carrier + service pick saves here too,
       so it restores — it is a convenience that resolves to a
       policy, never the publish itself. Saved by the same draft
       autosave; the package block is sent only for calculated
       shipping policies. */
    package_weight_lb: $("p-weight-lb").value.trim(),
    package_weight_oz: $("p-weight-oz").value.trim(),
    package_length: $("p-length").value.trim(),
    package_width: $("p-width").value.trim(),
    package_height: $("p-height").value.trim(),
    ship_carrier: $("ship-carrier").value,
    ship_service_code: shipServiceCode(),
  };
}

/* v6: plain-words label for a condition value (reads the step-2 dropdown). */
function conditionLabel(value) {
  if (!value) return "";
  const o = $("f-condition").querySelector('option[value="' + value + '"]');
  return o ? o.textContent : value;
}

function scheduleSave() {
  saveDirty = true;
  clearTimeout(saveTimer);
  saveTimer = setTimeout(saveDraft, 1200);
}

async function saveDraft() {
  if (!draft) return;
  clearTimeout(saveTimer);
  saveTimer = null;
  try {
    const { draft: d } = await api("/api/drafts/" + draft.id, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(collectForm()),
    });
    draft = d;
    saveDirty = false;
  } catch (e) { /* autosave is best-effort; the Review step saves too */ }
}

/* v5: last-chance save when the page is hidden or closed (phone back button,
   app switch, screen off). Uses keepalive so the request survives the page
   going away. Fire-and-forget: nothing to await here. */
function flushSave() {
  if (!saveDirty || !draft) {
    clearTimeout(saveTimer);
    saveTimer = null;
    saveDirty = false;
    return;
  }
  clearTimeout(saveTimer);
  saveTimer = null;
  saveDirty = false;
  try {
    fetch("/api/drafts/" + draft.id, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(collectForm()),
      keepalive: true,
    }).catch(() => {});
  } catch (e) { /* best-effort */ }
}

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden") flushSave();
});
window.addEventListener("pagehide", flushSave);

/* v5: hooks the native app uses (sold-price pop-up "Use this price"). */
window.LA_getDraftId = () => (draft ? draft.id : "");
window.LA_refreshDraft = async () => {
  if (!draft) return;
  try {
    const { draft: d } = await api("/api/drafts/" + draft.id);
    loadDraftIntoForm(d);
  } catch (e) { /* refresh is best-effort */ }
};

["f-title", "f-category", "f-description", "f-price"].forEach((id) => {
  $(id).addEventListener("input", () => { updateCount(); scheduleSave(); });
});
/* v6: condition fields join the autosave. The select fires "change"
   reliably; "input" covers the textarea. Double-firing is harmless
   (scheduleSave is debounced). */
["f-condition", "f-condition-details"].forEach((id) => {
  $(id).addEventListener("input", scheduleSave);
  $(id).addEventListener("change", scheduleSave);
});

function updateCount() {
  $("title-count").textContent = $("f-title").value.length + "/80";
}

/* v5: save first — the pop-up opens over the draft, and "Use this price"
   later refreshes the form from the server, so the server must be current. */
$("btn-solds").addEventListener("click", async () => {
  await saveDraft();
  const q = encodeURIComponent($("f-title").value.trim() || "vintage item");
  window.open(
    "https://www.ebay.com/sch/i.html?_nkw=" + q + "&LH_Sold=1&LH_Complete=1",
    "_blank"
  );
});

$("btn-reanalyze").addEventListener("click", async () => {
  if (!draft || !confirm("Ask the AI to look at the photos again? Your typed price is kept.")) return;
  try {
    const { draft: d } = await api("/api/drafts/" + draft.id + "/reanalyze", { method: "POST" });
    loadDraftIntoForm(d);
  } catch (err) { alert(err.message); }
});


function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

/* v29: the Review step is gone — the listing page itself is the review,
   and the Post button's confirmation dialog is the final stop. */
async function copyText(text, okMsg) {
  try {
    await navigator.clipboard.writeText(text);
  } catch (e) {
    const ta = document.createElement("textarea");
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand("copy"); } catch (_e) {}
    ta.remove();
  }
  const el = $("copy-ok");
  el.textContent = okMsg || "Copied. Paste it into eBay.";
  el.classList.remove("hidden");
  setTimeout(() => el.classList.add("hidden"), 2500);
}

function fullListingText() {
  const d = Object.assign({}, draft, collectForm());
  let t = d.title + "\n\n" + d.description;
  const cond = conditionLabel(d.condition);
  if (cond) t += "\n\nCondition: " + cond;
  if (d.condition_details) t += "\nCondition details: " + d.condition_details;
  const keys = Object.keys(d.attributes || {});
  if (keys.length) {
    t += "\n\nItem specifics:\n";
    keys.forEach((k) => { t += k + ": " + d.attributes[k] + "\n"; });
  }
  if (d.price) t += "\nPrice: $" + d.price;
  return t;
}

$("btn-copy-title").addEventListener("click", () => copyText($("f-title").value, "Title copied. Paste it into eBay."));
$("btn-copy-desc").addEventListener("click", () => copyText($("f-description").value, "Description copied. Paste it into eBay."));
$("btn-copy-all").addEventListener("click", () => copyText(fullListingText(), "Everything copied. Paste it into eBay."));

$("btn-new").addEventListener("click", () => {
  flushSave(); /* v5: save any last-second edits before abandoning the form */
  draft = null;
  selectedFiles = [];
  /* v50: starting fresh abandons the auto draft (it stays in My
     drafts, deletable there) and clears the resume — there is no
     open draft to return to. */
  autoDraftId = null;
  autoDraftStale = false;
  recordResumeDraft(null);
  $("photo-input").value = "";
  clearPreviews();
  $("btn-analyze").disabled = true;
  hideError();
  show("step-photos");
});

/* v24: render an ISO timestamp as two nowrap spans (date, time) so a
   narrow row can break cleanly BETWEEN them, never mid-string. The
   v21 rule stands: no mid-string break, ever. */
function dateHtml(iso) {
  const s = String(iso || "");
  const t = s.indexOf("T");
  if (t < 0) return '<span class="d-date">' + esc(s) + "</span>";
  return '<span class="d-date">' + esc(s.slice(0, t)) + "</span> " +
    '<span class="d-date">' + esc(s.slice(t + 1)) + "</span>";
}

/* ---------- drafts panel ---------- */
/* v20: "My drafts" is a quiet link above step 1 now, not a header button.
   Same panel, same handler; preventDefault keeps the "#" link from
   touching the URL. */
/* v46: one row builder, shared by both drafts sections (working drafts
   on top, already-live drafts in the collapsible section below). */
function makeDraftRow(d) {
  const row = document.createElement("div");
  row.className = "draft-item";
  /* v21: buttons live in one .draft-actions unit so they wrap below the
     text together instead of one clipping off the right edge. The date
     renders as two nowrap spans (dateHtml: date, time) so the row can
     break cleanly between them, never mid-string.
     v24: a tick box (batch posting) and a Post button (straight to
     the listing page) join the row. Listed drafts get no Post button — they are
     already live, and the publish step refuses them anyway. v49: no
     "Listed on eBay" pill on any row — listed rows live ONLY in the
     bottom "Listed on eBay" section, so a pill inside the Drafts
     section would be a lie; Delete is a grey button with a trash
     icon (design 4), Open stays the outlined button. */
  row.innerHTML =
    '<input type="checkbox" class="draft-check" data-id="' + d.id +
    '" aria-label="Select for batch posting">' +
    (d.thumb ? '<img src="/photos/' + d.thumb + '">' : "") +
    '<div class="t"><b>' + esc(d.title || "(untitled)") + "</b>" + dateHtml(d.updated) +
    "</div>" +
    '<div class="draft-actions"><button type="button" class="secondary">Open</button>' +
    (d.listed ? "" : '<button type="button" class="primary post">Post</button>') +
    '<button type="button" class="del">🗑 Delete</button></div>';
  row.querySelector(".secondary").addEventListener("click", async () => {
    const { draft: full } = await api("/api/drafts/" + d.id);
    loadDraftIntoForm(full);
    await prefillListing();
    show("step-draft");
  });
  const postBtn = row.querySelector(".post");
  if (postBtn) {
    postBtn.addEventListener("click", async () => {
      /* v29: straight to the listing page — photos, title, price and
         everything else come from the draft, no re-picking, no AI.
         Nothing goes live from here: the listing page's Post button
         plus its confirmation dialog still stand between him and a
         live listing. */
      const { draft: full } = await api("/api/drafts/" + d.id);
      loadDraftIntoForm(full);
      await prefillListing();
      const notes = [];
      if (!String($("f-price").value || "").trim())
        notes.push("Price is not set — add it before listing.");
      if (!$("p-category").value.trim())
        notes.push("Category is not set — pick one with Suggest below.");
      const note = $("listing-note");
      if (notes.length) {
        note.textContent = notes.join(" ");
        note.classList.remove("hidden");
      } else {
        note.classList.add("hidden");
      }
      show("step-draft");
    });
  }
  row.querySelector(".del").addEventListener("click", async () => {
    if (!confirm("Delete this draft and its photos?")) return;
    await api("/api/drafts/" + d.id, { method: "DELETE" });
    $("btn-drafts").click();
  });
  return row;
}
/* v49: the design-4 back arrows on step 1 and the listing page go
   back to My drafts, the app's hub. Clicking btn-drafts runs the
   same load the toolbar button does, and show() flushes any pending
   draft save first. */
$("btn-back-photos").addEventListener("click", () => $("btn-drafts").click());
$("btn-back-draft").addEventListener("click", () => $("btn-drafts").click());

$("btn-drafts").addEventListener("click", async (e) => {
  e.preventDefault();
  const list = $("drafts-list");
  list.innerHTML = "<p class='hint'>Loading…</p>";
  show("drafts-panel");
  try {
    const { drafts } = await api("/api/drafts");
    if (!drafts.length) {
      list.innerHTML = "<p class='hint'>No drafts yet. Start one from step 1.</p>";
      return;
    }
    list.innerHTML = "";
    /* v46: two sections. Working drafts on top, newest first (server
       order, v45); drafts already live on eBay in a collapsible
       section at the bottom, closed by default, so the working drafts
       stay front and center. The section itself is the indication —
       no badge needed. */
    const unlisted = drafts.filter((d) => !d.listed);
    const listed = drafts.filter((d) => d.listed);
    const top = document.createElement("div");
    top.className = "draft-section";
    const topHead = document.createElement("h3");
    topHead.textContent = "Drafts (" + unlisted.length + ")";
    top.appendChild(topHead);
    if (unlisted.length) {
      unlisted.forEach((d) => top.appendChild(makeDraftRow(d)));
    } else {
      const hint = document.createElement("p");
      hint.className = "hint";
      hint.textContent = "No drafts yet. Start one from step 1.";
      top.appendChild(hint);
    }
    list.appendChild(top);
    if (listed.length) {
      const bottom = document.createElement("div");
      bottom.className = "draft-section";
      const toggle = document.createElement("button");
      toggle.type = "button";
      toggle.className = "draft-section-toggle";
      toggle.setAttribute("aria-expanded", "false");
      const body = document.createElement("div");
      body.className = "hidden";
      listed.forEach((d) => body.appendChild(makeDraftRow(d)));
      const setToggle = (open) => {
        body.classList.toggle("hidden", !open);
        toggle.setAttribute("aria-expanded", open ? "true" : "false");
        toggle.textContent = (open ? "▾ " : "▸ ") + "Listed on eBay (" + listed.length + ")";
      };
      toggle.addEventListener("click", () =>
        setToggle(body.classList.contains("hidden")));
      setToggle(false);
      bottom.appendChild(toggle);
      bottom.appendChild(body);
      list.appendChild(bottom);
    }
  } catch (e) {
    list.innerHTML = "<p class='error'>" + esc(e.message) + "</p>";
  }
});
/* v5: Back from My drafts goes to the very beginning (choose photos).
   The "My drafts" link sits above step 1, so this is the only way
   back to the start once you are inside a draft. Pending edits are flushed
   by show() before the switch. */
$("btn-close-drafts").addEventListener("click", () => show("step-photos"));

/* ---------- v24: batch posting ---------- */
/* One confirmation list IS the review for the batch: nothing posts
   without it. Drafts without a price, or already live, are excluded
   with a plain reason. One category number applies to the whole batch
   (drafts now carry a saved number from the listing page, but batch
   still uses its own entered number for the whole batch). */
let batchIncluded = [];

/* v49: the confirm card heading and button carry the live count —
   "N drafts, 1 category" and "Post all N" (design 4). The Post
   button stays disabled until at least one draft is in the batch
   and a category number is entered. */
function updateBatchConfirm() {
  const n = batchIncluded.length;
  $("batch-confirm-heading").textContent = n + " draft" + (n === 1 ? "" : "s") + ", 1 category";
  $("btn-batch-confirm").textContent = "Post all" + (n ? " " + n : "");
  $("btn-batch-confirm").disabled = n === 0 || !$("b-category").value.trim();
}

function openBatchPanel(fulls) {
  batchIncluded = [];
  const excluded = [];
  fulls.forEach((d) => {
    if (d.ebay_listing_id) {
      excluded.push({ d: d, reason: "Already live on eBay." });
    } else if (parseMoney(d.price) === null) {
      excluded.push({ d: d, reason: "No price set." });
    } else {
      batchIncluded.push(d);
    }
  });
  const list = $("batch-review-list");
  list.innerHTML = "";
  /* v49: each row is a checkbox, a thumb, a title and a date
     (design 4). Unticking a row drops that draft from the batch and
     updates the confirm card; ticking it again puts it back. The
     price is not shown on the row — it lives on the listing page. */
  batchIncluded.forEach((d) => {
    const row = document.createElement("div");
    row.className = "batch-row";
    const box = document.createElement("input");
    box.type = "checkbox";
    box.className = "draft-check";
    box.checked = true;
    box.setAttribute("aria-label", "Include in batch");
    box.addEventListener("change", () => {
      if (box.checked) {
        if (!batchIncluded.some((x) => x.id === d.id)) batchIncluded.push(d);
      } else {
        batchIncluded = batchIncluded.filter((x) => x.id !== d.id);
      }
      updateBatchConfirm();
    });
    row.appendChild(box);
    if (d.photos && d.photos[0]) {
      const img = document.createElement("img");
      img.src = "/photos/" + d.photos[0];
      img.alt = "";
      row.appendChild(img);
    }
    const bt = document.createElement("div");
    bt.className = "bt";
    bt.innerHTML = "<b>" + esc(d.title || "(untitled)") + "</b>" + dateHtml(d.updated);
    row.appendChild(bt);
    list.appendChild(row);
  });
  if (!batchIncluded.length) {
    list.innerHTML = "<p class='hint'>Nothing ready to post. Fix the skipped drafts below first.</p>";
  }
  const exc = $("batch-excluded");
  exc.innerHTML = "";
  excluded.forEach(({ d, reason }) => {
    const row = document.createElement("div");
    row.className = "batch-row";
    row.innerHTML = '<div class="bt">' + esc(d.title || "(untitled)") + "</div>" +
      '<div class="bp batch-skip">Skipped: ' + esc(reason) + "</div>";
    exc.appendChild(row);
  });
  $("b-keywords").value = batchIncluded.length ? (batchIncluded[0].title || "") : "";
  $("b-category").value = "";
  $("batch-suggestions").classList.add("hidden");
  $("batch-note").classList.add("hidden");
  $("batch-results").innerHTML = "";
  $("batch-progress").classList.add("hidden");
  $("batch-confirm").classList.remove("hidden");
  $("btn-batch-confirm").classList.remove("hidden");
  $("btn-batch-cancel").classList.remove("hidden");
  $("btn-batch-done").classList.add("hidden");
  updateBatchConfirm();
  show("batch-panel");
}

$("btn-batch-open").addEventListener("click", async () => {
  const ids = Array.from(document.querySelectorAll(".draft-check:checked"))
    .map((c) => c.dataset.id);
  if (!ids.length) {
    alert("Tick at least one draft first.");
    return;
  }
  const fulls = [];
  for (const id of ids) {
    try {
      const { draft: d } = await api("/api/drafts/" + id);
      fulls.push(d);
    } catch (e) { /* a draft that will not load is left out */ }
  }
  if (!fulls.length) {
    alert("Those drafts could not be loaded. Try again.");
    return;
  }
  openBatchPanel(fulls);
});

$("b-category").addEventListener("input", updateBatchConfirm);

$("btn-batch-suggest").addEventListener("click", async () => {
  const box = $("batch-suggestions");
  box.classList.remove("hidden");
  box.innerHTML = "<p class='hint'>Asking eBay…</p>";
  try {
    const { suggestions } = await api("/api/ebay/category-suggest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ q: $("b-keywords").value.trim() }),
    });
    if (!suggestions.length) {
      box.innerHTML = "<p class='hint'>No suggestions. Try simpler keywords.</p>";
      return;
    }
    box.innerHTML = "";
    suggestions.forEach((s) => {
      const b = document.createElement("button");
      b.type = "button"; b.className = "ghost sug";
      b.textContent = s.name + " (" + s.id + ")";
      b.addEventListener("click", () => {
        $("b-category").value = s.id;
        box.classList.add("hidden");
        updateBatchConfirm();
      });
      box.appendChild(b);
    });
  } catch (e) {
    box.innerHTML = "<p class='error'>" + esc(e.message) + "</p>";
  }
});

$("btn-batch-cancel").addEventListener("click", () => $("btn-drafts").click());
$("btn-batch-done").addEventListener("click", () => $("btn-drafts").click());

/* v31: one batch run. Returns the follow-up signals, if any, so the
   caller can show the one-time ship-from address form (empty-location
   wall), the policy pick form (unusable shipping policy), or the
   scope box (sign-in granted before sell.account) instead of the done
   button. One draft stopping on a shared wall stops the whole run —
   the rest would hit the same wall. */
async function runBatch(shipFrom, fulfillmentOverride) {
  const { results } = await api("/api/ebay/batch-publish", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      draft_ids: batchIncluded.map((d) => d.id),
      category_id: $("b-category").value.trim(),
      ...(shipFrom ? { ship_from: shipFrom } : {}),
      ...(fulfillmentOverride ? { fulfillment_policy_id: fulfillmentOverride,
                                  /* v34: pick re-run — each draft's
                                     pre-pick offer is discarded first. */
                                  policy_pick: true } : {}),
    }),
  });
  const box = $("batch-results");
  box.innerHTML = "<h4>Results</h4>";
  const signals = { needsAddress: false, needsScope: false, needsPick: false,
                    policies: [], pickError: "" };
  (results || []).forEach((r) => {
    const row = document.createElement("div");
    row.className = "batch-row";
    if (r.ok) {
      row.innerHTML = '<div class="bt">' + esc(r.title) + "</div>" +
        '<div class="bp"><a href="' + esc(r.listing_url) + '" target="_blank">Live</a></div>';
    } else if (r.needs_ship_from) {
      signals.needsAddress = true;
      row.innerHTML = '<div class="bt">' + esc(r.title) + "</div>" +
        '<div class="bp batch-skip">Needs a shop location: enter your ship-from address below.</div>';
    } else if (r.needs_scope_reconnect || isScopeReconnectText(r.error)) {
      /* v33: the text check is defense in depth — a row carrying the
         scope message without the flag still reveals the batch
         Connect button (the live v1.31 gap, batch side). */
      signals.needsScope = true;
      row.innerHTML = '<div class="bt">' + esc(r.title) + "</div>" +
        '<div class="bp batch-skip">' + esc(r.error || "Needs a new eBay permission.") + "</div>";
    } else if (r.needs_policy_pick) {
      signals.needsPick = true;
      signals.policies = r.policies || [];
      signals.pickError = r.error || "";
      row.innerHTML = '<div class="bt">' + esc(r.title) + "</div>" +
        '<div class="bp batch-skip">Needs a working shipping policy: pick one below.</div>';
    } else {
      row.innerHTML = '<div class="bt">' + esc(r.title) + "</div>" +
        '<div class="bp batch-skip">Failed: ' + esc(r.error || "unknown error") + "</div>";
    }
    box.appendChild(row);
  });
  return signals;
}

/* v32: hide every batch follow-up form/box, then show the one the
   signals call for. Returns true when something still needs the
   seller (so the caller keeps cancel visible instead of Done). */
function showBatchFollowups(signals) {
  $("batch-ship-from-form").classList.add("hidden");
  $("batch-policy-pick-form").classList.add("hidden");
  $("batch-scope-box").classList.add("hidden");
  if (signals.needsAddress) {
    /* Keep an exit open: cancel stays visible and enabled so the
       seller can bail to drafts instead of filling the form. */
    $("batch-ship-from-form").classList.remove("hidden");
    $("batch-confirm").classList.remove("hidden");
    $("btn-batch-cancel").classList.remove("hidden");
    $("btn-batch-cancel").disabled = false;
    return true;
  }
  if (signals.needsPick) {
    renderBatchPolicyPick(signals.policies, signals.pickError);
    $("batch-policy-pick-form").classList.remove("hidden");
    $("batch-confirm").classList.remove("hidden");
    $("btn-batch-cancel").classList.remove("hidden");
    $("btn-batch-cancel").disabled = false;
    return true;
  }
  if (signals.needsScope) {
    $("batch-scope-box").classList.remove("hidden");
    $("batch-confirm").classList.remove("hidden");
    $("btn-batch-cancel").classList.remove("hidden");
    $("btn-batch-cancel").disabled = false;
    return true;
  }
  return false;
}

function renderBatchPolicyPick(policies, message) {
  $("batch-policy-pick-msg").textContent = message ||
    "That shipping policy number no longer works. Pick one below.";
  const list = $("batch-policy-pick-list");
  list.innerHTML = "";
  $("batch-policy-pick-error").classList.add("hidden");
  if (!policies.length) {
    list.innerHTML = "<p class='hint'>No shipping policies found on your account. Create one in Seller Hub first.</p>";
    return;
  }
  policies.forEach((p) => {
    const b = document.createElement("button");
    b.type = "button"; b.className = "ghost";
    b.textContent = (p.name || "Unnamed policy") + " — " +
      policyTypeLabel(p.cost_type); /* v43: no policy ID on the button
      (the number still travels in the click handler). A policy name
      is a label; the ID is noise, same rule as the single-listing
      dropdown fixed in v35. */
    b.addEventListener("click", () => pickBatchPolicy(p.id));
    list.appendChild(b);
  });
}

/* v32: batch policy pick. The batch was already confirmed, so this
   re-run carries exactly those drafts with the picked policy — no
   second confirmation dialog. */
async function pickBatchPolicy(policyId) {
  const errEl = $("batch-policy-pick-error");
  errEl.classList.add("hidden");
  $("batch-progress").classList.remove("hidden");
  try {
    const signals = await runBatch(null, policyId);
    if (!showBatchFollowups(signals)) {
      $("btn-batch-done").classList.remove("hidden");
    }
  } catch (e) {
    errEl.textContent = e.message;
    errEl.classList.remove("hidden");
  } finally {
    $("batch-progress").classList.add("hidden");
  }
}

$("btn-batch-confirm").addEventListener("click", async () => {
  const cid = $("b-category").value.trim();
  if (!cid || !batchIncluded.length) return;
  if (!confirm("Post " + batchIncluded.length + " draft(s) to eBay now? Each becomes a real, live listing.")) return;
  $("btn-batch-confirm").disabled = true;
  $("btn-batch-cancel").disabled = true;
  $("batch-progress").classList.remove("hidden");
  $("batch-results").innerHTML = "";
  $("batch-ship-from-form").classList.add("hidden");
  $("batch-policy-pick-form").classList.add("hidden");
  $("batch-scope-box").classList.add("hidden");
  try {
    const signals = await runBatch(null, null);
    if (!showBatchFollowups(signals)) {
      $("btn-batch-done").classList.remove("hidden");
    }
  } catch (e) {
    $("batch-results").innerHTML = "<p class='error'>" + esc(e.message) + "</p>";
    /* v33: a scope message reaching this generic display must still
       reveal the batch Connect button. */
    if (isScopeReconnectText(e.message)) {
      $("batch-scope-box").classList.remove("hidden");
    }
    $("btn-batch-done").classList.remove("hidden");
  } finally {
    $("batch-progress").classList.add("hidden");
    $("btn-batch-confirm").classList.add("hidden");
    if ($("batch-ship-from-form").classList.contains("hidden") &&
        $("batch-policy-pick-form").classList.contains("hidden") &&
        $("batch-scope-box").classList.contains("hidden")) {
      $("btn-batch-cancel").classList.add("hidden");
    }
    /* v49: hide the whole confirm card once both of its buttons are
       hidden, so it does not sit empty after a run. When a follow-up
       form is showing, Cancel stays visible, so the card stays. */
    if ($("btn-batch-confirm").classList.contains("hidden") &&
        $("btn-batch-cancel").classList.contains("hidden")) {
      $("batch-confirm").classList.add("hidden");
    }
  }
});

/* v31: batch ship-from submit. The batch was already confirmed above,
   and this re-run carries exactly those drafts plus the address, so
   no second confirmation dialog is shown — the submit itself
   authorizes creating the location and continuing. */
$("btn-batch-ship-from").addEventListener("click", async () => {
  const errEl = $("batch-ship-from-error");
  errEl.classList.add("hidden");
  const parsed = readShipFrom("bsf");
  if (parsed.error) {
    errEl.textContent = parsed.error;
    errEl.classList.remove("hidden");
    return;
  }
  $("btn-batch-ship-from").disabled = true;
  $("batch-progress").classList.remove("hidden");
  try {
    const signals = await runBatch(parsed.data, null);
    if (signals.needsAddress) {
      /* Cannot normally recur with an address supplied; keep the form
         open with a plain message if it ever does. */
      errEl.textContent = "eBay still has no shop location. Check the address and try again.";
      errEl.classList.remove("hidden");
    } else if (!showBatchFollowups(signals)) {
      /* v32: the address fixed the location wall, and no policy or
         scope wall followed — hide the form and finish. */
      $("batch-ship-from-form").classList.add("hidden");
      $("btn-batch-done").classList.remove("hidden");
    } else {
      /* A policy or scope wall followed the location fix: that form
         is showing now, so the address form can hide. */
      $("batch-ship-from-form").classList.add("hidden");
    }
  } catch (e) {
    $("batch-results").innerHTML = "<p class='error'>" + esc(e.message) + "</p>";
    $("batch-ship-from-form").classList.add("hidden");
    $("btn-batch-done").classList.remove("hidden");
  } finally {
    $("batch-progress").classList.add("hidden");
    $("btn-batch-ship-from").disabled = false;
  }
});

/* ---------- init ---------- */
api("/api/config").then((c) => {
  if (!c.gemini_configured) $("key-warning").classList.remove("hidden");
  if (c.max_photos) {
    maxPhotos = c.max_photos;
    document.querySelector("#step-photos .hint").textContent =
      "Choose up to " + c.max_photos + " photos of one item from your photo library.";
    renderThumbs(); /* v36: refresh the count line for the real cap */
  }
}).catch(() => {});

/* v50: return to the draft he had open. Runs after the config fetch
   so the photo cap is already set when a photo-only draft restores
   its photos into the grid. */
checkResumeDraft();

/* v50: the resume Continue flow, as a named function so node tests
   can call it directly. Opens the remembered draft in the listing
   page; a failure says so plainly and leaves My drafts as the way
   back. */
async function openResumeDraft() {
  if (!resumeDraftId) return;
  $("btn-resume").disabled = true;
  try {
    const { draft: d } = await api("/api/drafts/" + resumeDraftId);
    $("resume-card").classList.add("hidden");
    resumeDraftId = null;
    loadDraftIntoForm(d);
    await prefillListing();
    show("step-draft");
  } catch (e) {
    showError("Couldn't open that draft. Find it in My drafts.");
  } finally {
    $("btn-resume").disabled = false;
  }
}

function dismissResumeCard() {
  $("resume-card").classList.add("hidden");
  resumeDraftId = null;
  recordResumeDraft(null);
}

$("btn-resume").addEventListener("click", openResumeDraft);

$("btn-resume-dismiss").addEventListener("click", dismissResumeCard);

/* ---------- v2: eBay one-tap listing ---------- */
let ebayStatus = null;

// v22: the OAuth-enabled sign-in name eBay accepts. A RuName is a public
// identifier (it appears in every authorize URL), not a secret — showing
// it and comparing against it here is safe. The Client Secret never
// appears in this file.
const WORKING_RUNAME = "Reynard_Baillou-ReynardB-Mr-PRD-eayxussz";

/* v29: pure listing-page prefill. Precedence is draft first, then the
   "usual settings" preset, then .env defaults — so a category number or
   policy number saved into the draft survives an app restart or update,
   and the preset only fills fields the draft lacks (the "applied" list
   names exactly what the preset filled, never what the draft supplied).
   form carries the listing-page DOM values (condition, price): a keystroke
   typed moments before opening the listing page is newer than the last autosave,
   so it wins over the draft for those two fields. Pure on purpose:
   node tests pin this precedence without a DOM. */
function listingPrefill(draftObj, preset, defaults, form) {
  const d = draftObj || {};
  const p = preset || {};
  const def = defaults || {};
  const f = form || {};
  const nonEmpty = (v) => String(v == null ? "" : v).trim() !== "";
  const applied = [];
  const out = {};
  out.condition = nonEmpty(f.condition) ? f.condition
    : nonEmpty(d.condition) ? d.condition
    : nonEmpty(p.condition) ? (applied.push("condition"), p.condition) : "";
  out.category_id = nonEmpty(d.category_id) ? String(d.category_id).trim()
    : nonEmpty(p.category_id) ? (applied.push("category"), String(p.category_id).trim()) : "";
  out.category_name = nonEmpty(d.category_id) ? String(d.category_name || "").trim() : "";
  out.price = nonEmpty(f.price) ? f.price : String(d.price == null ? "" : d.price);
  const draftBO = !!d.best_offer || nonEmpty(d.best_offer_auto_decline) ||
    nonEmpty(d.best_offer_auto_accept);
  out.best_offer = draftBO ? !!d.best_offer : !!p.best_offer;
  out.best_offer_auto_decline = draftBO ? String(d.best_offer_auto_decline || "")
    : String(p.best_offer_auto_decline || "");
  out.best_offer_auto_accept = draftBO ? String(d.best_offer_auto_accept || "")
    : String(p.best_offer_auto_accept || "");
  if (draftBO === false && p.best_offer) applied.push("Best Offer");
  let policiesFromPreset = false;
  [["fulfillment", "fulfillment_policy_id"], ["payment", "payment_policy_id"],
   ["return", "return_policy_id"]].forEach(([outKey, presetKey]) => {
    if (nonEmpty(d[presetKey])) {
      out[outKey] = String(d[presetKey]).trim();
    } else if (nonEmpty(p[presetKey])) {
      out[outKey] = String(p[presetKey]).trim();
      policiesFromPreset = true;
    } else {
      out[outKey] = String(def[outKey] || "");
    }
  });
  if (policiesFromPreset) applied.push("policies");
  out.applied = applied;
  return out;
}

/* v29: pickedCat tracks the last Suggest pick (or restored saved pair)
   so the display name survives re-saves when the number is untouched;
   the name is only sent when the input still equals the picked number,
   so a hand-typed number never inherits a stale name. All listing-page
   fields save through the draft's own autosave now — there is no
   separate panel-save chain anymore. */
let pickedCat = { id: "", name: "" };

async function prefillListing(opts) {
  /* v29: fills the listing page's eBay fields when a draft opens and
     renders the eBay status line. Draft first, preset second, .env
     defaults last (listingPrefill). Never throws: a failed status or
     preset fetch leaves the page as loaded from the draft.
     v43: opts.keepPublishOk skips hiding the fresh "It is live!"
     note when this runs right after a successful publish (postPublish
     calls it to refresh the page state). Without the flag the note
     set in the same tick is hidden again before the seller ever sees
     it. The hide stays correct on a plain draft open: it clears a
     stale note from an earlier visit. */
  if (!draft) return;
  $("publish-error").classList.add("hidden");
  if (!opts || !opts.keepPublishOk) $("publish-ok").classList.add("hidden");
  $("listing-note").classList.add("hidden");
  const box = $("ebay-status");
  try {
    ebayStatus = await api("/api/ebay/status");
  } catch (e) {
    box.innerHTML = "<p class='hint' style='margin:0'>Could not check eBay status.</p>";
    $("btn-publish").classList.remove("hidden");
    return;
  }
  if (!ebayStatus.ebay_configured) {
    box.innerHTML = "<p style='margin:0'><b>eBay is not set up yet.</b></p>" +
      "<p class='hint'>Add your eBay App ID and Cert ID to the .env file " +
      "(setup guide: Connect eBay), then restart the app. The manual copy buttons below still work.</p>";
    $("btn-publish").classList.add("hidden");
    return;
  }
  if (!ebayStatus.connected) {
    // v22: the sign-in name in use is always shown here, so a wrong one
    // can never hide again. When it is anything but the working name,
    // offer the one-tap fix instead of a text field: the button rewrites
    // the saved name, restarts the app, and starts the connect flow.
    // The buttons live in a wrapping row (v21 narrow-safe rule) so they
    // can never clip off a narrow phone screen.
    const runame = (ebayStatus.runame || "").trim();
    let html = "<p style='margin:0'><b>eBay is not connected.</b></p>" +
      "<p class='hint'>Tap the button below in this browser, sign in to eBay, " +
      "and approve access. You only do this once.</p>" +
      "<p class='hint runame-line'>Sign-in name in use: " + esc(runame) + "</p>" +
      "<div class='btnrow wrap'>" +
      "<button id='btn-ebay-connect' class='primary' type='button'>Connect eBay</button>";
    if (runame && runame !== WORKING_RUNAME) {
      html += "<button id='btn-use-working-runame' class='secondary' type='button'>Use the working name</button>";
    }
    html += "</div>";
    box.innerHTML = html;
    $("btn-ebay-connect").addEventListener("click", () => {
      window.location.href = "/api/ebay/connect";
    });
    const fixBtn = $("btn-use-working-runame");
    if (fixBtn) {
      fixBtn.addEventListener("click", () => {
        if (window.Android && Android.fixRunameAndConnect) {
          Android.fixRunameAndConnect();
        } else {
          // Off the APK (plain browser run): no bridge to restart through,
          // so say exactly what to change instead of a dead button.
          fixBtn.outerHTML = "<p class='hint'>On this device, use Edit keys " +
            "in the menu and set the sign-in name to " + esc(WORKING_RUNAME) + ".</p>";
        }
      });
    }
    $("btn-publish").classList.add("hidden");
    return;
  }
  const alreadyLive = !!(draft && draft.ebay_listing_id);
  if (alreadyLive) {
    box.innerHTML = "<p style='margin:0'><b>This draft is already live on eBay.</b></p>" +
      "<p class='hint'><a href='" + draft.ebay_listing_url + "' target='_blank'>View your listing</a>" +
      " · item " + esc(draft.ebay_listing_id) + "</p>";
    $("btn-publish").classList.add("hidden");
  } else {
    let statusHtml = "<p style='margin:0'><b>eBay connected.</b> Ready to list.</p>" +
      "<p class='hint runame-line'>Sign-in name in use: " + esc((ebayStatus.runame || "").trim()) + "</p>";
    if (!ebayStatus.policies_configured) {
      // v29: unlike the old panel, the listing page lets him TYPE the
      // missing policy numbers right here, so the Post button stays —
      // the server still refuses a post with numbers missing.
      statusHtml += "<p class='hint'>Still needed: shop policy numbers — type them under Shop policy numbers below, or set them in .env.</p>";
    }
    box.innerHTML = statusHtml;
    $("btn-publish").classList.remove("hidden");
  }
  // Prefill the listing page's eBay fields: a category number saved into
  // the draft is restored here, so Post works straight away after an
  // update or restart — no re-pick.
  const cur = collectForm();
  let preset = null;
  try {
    const r = await api("/api/preset");
    preset = r.preset;
  } catch (e) { preset = null; }
  const pre = listingPrefill(draft || {}, preset, ebayStatus.policy_defaults || {},
    { condition: cur.condition, price: cur.price });
  $("f-condition").value = pre.condition;
  $("f-price").value = pre.price;
  $("p-best-offer").checked = pre.best_offer;
  $("p-bo-decline").value = pre.best_offer_auto_decline;
  $("p-bo-accept").value = pre.best_offer_auto_accept;
  $("best-offer-fields").classList.toggle("hidden", !pre.best_offer);
  $("p-category").value = pre.category_id;
  $("cat-suggestions").classList.add("hidden");
  $("p-fulfillment").value = pre.fulfillment;
  $("p-payment").value = pre.payment;
  $("p-return").value = pre.return;
  /* v42: package fields restore from the draft (per draft, never from
     the preset — framed and unframed parcels differ). Weight is
     pounds + ounces boxes now; an old draft that only has the legacy
     single value + unit converts it (2 lb, or 40 oz becomes 2 lb
     8 oz). The carrier + service pick restores too. */
  const lbOz = draftPackageLbOz(draft);
  $("p-weight-lb").value = lbOz.lb;
  $("p-weight-oz").value = lbOz.oz;
  $("p-length").value = draft.package_length || "";
  $("p-width").value = draft.package_width || "";
  $("p-height").value = draft.package_height || "";
  $("ship-carrier").value = draft.ship_carrier || "";
  populateShipService();
  restoreShipService(draft.ship_service_code || "");
  /* v34: the Shipping dropdown follows the restored policy number, and
     the policy list refreshes every time the listing page opens.
     v35: the Condition dropdown likewise follows the restored
     category. */
  refreshShippingChooser();
  fetchShippingPolicies();
  fetchCarrierServices();
  fetchCategoryConditions();
  /* Remember the restored category pair so later saves keep its display
     name when the number is untouched. */
  pickedCat = pre.category_id
    ? { id: pre.category_id, name: pre.category_name } : { id: "", name: "" };
  const catSaved = $("cat-saved");
  if (pre.category_name) {
    catSaved.textContent = "Saved category: " + pre.category_name;
    catSaved.classList.remove("hidden");
  } else {
    catSaved.classList.add("hidden");
  }
  const note = $("preset-note");
  note.classList.add("hidden");
  if (pre.applied.length) {
    note.textContent = "Usual settings applied (" + pre.applied.join(", ") +
      ") — change anything before listing.";
    note.classList.remove("hidden");
  }
  /* A restored category number also restores its required-details
     section (prefilled from the draft's saved specifics). */
  if (pre.category_id) loadCategoryAspects();
  /* v49: the collapsed-row summaries follow the restored values. */
  updateListingSummaries();
}

/* v24: save the current listing-page values as the one usual-settings
   preset. Re-saving overwrites it. The note confirms what was saved. */
$("btn-save-preset").addEventListener("click", async () => {
  const note = $("preset-note");
  try {
    await api("/api/preset", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        condition: $("f-condition").value,
        category_id: $("p-category").value.trim(),
        fulfillment_policy_id: $("p-fulfillment").value.trim(),
        payment_policy_id: $("p-payment").value.trim(),
        return_policy_id: $("p-return").value.trim(),
        best_offer: $("p-best-offer").checked,
        best_offer_auto_decline: $("p-bo-decline").value.trim(),
        best_offer_auto_accept: $("p-bo-accept").value.trim(),
      }),
    });
    note.textContent = "Saved as your usual settings. Next time they fill in by themselves.";
    note.classList.remove("hidden");
  } catch (e) {
    note.textContent = "Could not save those settings: " + e.message;
    note.classList.remove("hidden");
  }
});

$("btn-cat-suggest").addEventListener("click", async () => {
  const box = $("cat-suggestions");
  box.classList.remove("hidden");
  box.innerHTML = "<p class='hint'>Asking eBay…</p>";
  try {
    const { suggestions } = await api("/api/ebay/category-suggest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ q: $("f-title").value.trim() }),
    });
    if (!suggestions.length) {
      box.innerHTML = "<p class='hint'>No suggestions. Try a simpler title.</p>";
      return;
    }
    box.innerHTML = "";
    suggestions.forEach((s) => {
      const b = document.createElement("button");
      b.type = "button"; b.className = "ghost sug";
      b.textContent = s.name + " (" + s.id + ")";
      b.addEventListener("click", async () => {
        $("p-category").value = s.id;
        pickedCat = { id: s.id, name: s.name };
        const cs = $("cat-saved");
        cs.textContent = "Saved category: " + s.name;
        cs.classList.remove("hidden");
        box.classList.add("hidden");
        loadCategoryAspects(); /* v8: fetch what eBay requires for this category */
        fetchCategoryConditions(); /* v35: fetch what conditions this category takes */
        await saveDraft(); /* v29: a Suggest pick saves immediately, through the draft autosave */
      });
      box.appendChild(b);
    });
  } catch (e) {
    box.innerHTML = "<p class='error'>" + esc(e.message) + "</p>";
  }
});

/* v8: also check when a category number is typed in by hand. */
/* v29: a hand-edited number hides the saved-name line (the name belongs
   to the old number); the draft save itself only sends a name when the
   input still equals the picked number, so a typed number never
   inherits one. */
function onCategoryEdited() {
  if ($("p-category").value.trim() !== pickedCat.id) {
    $("cat-saved").classList.add("hidden");
  }
  scheduleSave();
}
$("p-category").addEventListener("change", () => {
  onCategoryEdited();
  loadCategoryAspects();
  fetchCategoryConditions(); /* v35 */
});
$("p-category").addEventListener("input", onCategoryEdited);

/* v29: the listing page's eBay fields join the draft autosave.
   Condition and price already autosave through their f-* listeners. */
["p-fulfillment", "p-payment", "p-return"].forEach((id) => {
  $(id).addEventListener("input", scheduleSave);
});
/* v35: the dropdown is the shipping control; picking one saves it
   (selectShippingPolicy), and a hand-typed number re-syncs the
   dropdown, the type line, and the package fields. */
$("shipping-select").addEventListener("change", () => {
  const v = $("shipping-select").value;
  if (v) selectShippingPolicy(v);
});
/* v42: carrier + service picker. A new carrier refills the service
   list and clears the service (a service belongs to its carrier);
   a picked service finds or creates the policy. Typing in the
   Other code box re-runs the find, so a typed code works too. */
$("ship-carrier").addEventListener("change", () => {
  populateShipService();
  $("ship-service-note").classList.add("hidden");
  fetchCarrierServices();
  updateShippingSummary();
  scheduleSave();
});
$("ship-service").addEventListener("change", () => {
  const v = $("ship-service").value;
  $("ship-service-other").classList.toggle("hidden", v !== "__OTHER__");
  if (v === "__OTHER__") $("ship-service-other").focus();
  else onShipServicePicked();
  updateShippingSummary();
});
$("ship-service-other").addEventListener("change", () => {
  if ($("ship-service").value === "__OTHER__") onShipServicePicked();
  updateShippingSummary();
});
/* v49: the package boxes drive the Shipping summary live, and they
   now autosave on input (before, they only saved when another field
   or the publish step saved the draft). */
["p-weight-lb", "p-weight-oz", "p-length", "p-width", "p-height"].forEach((id) => {
  $(id).addEventListener("input", () => { updateShippingSummary(); scheduleSave(); });
});
$("p-fulfillment").addEventListener("input", refreshShippingChooser);
$("required-aspects").addEventListener("input", scheduleSave);

/* v9: best offer toggle shows/hides the auto-price fields; all three join
   the draft autosave (checkbox fires "change", prices fire "input"). */
$("p-best-offer").addEventListener("change", () => {
  $("best-offer-fields").classList.toggle("hidden", !$("p-best-offer").checked);
  scheduleSave();
});
["p-bo-decline", "p-bo-accept"].forEach((id) => {
  $(id).addEventListener("input", scheduleSave);
});

/* v9: client-side mirror of the server's best-offer rules. Returns a
   plain-words error or "". The server validates again at publish. */
function parseMoney(s) {
  s = String(s || "").trim().replace(/[$,]/g, "");
  if (!s) return null;
  const v = Math.round(parseFloat(s) * 100) / 100;
  return (isNaN(v) || v <= 0) ? null : v;
}
function checkBestOffer(on, declineRaw, acceptRaw, price) {
  if (!on) return "";
  const decline = parseMoney(declineRaw);
  const accept = parseMoney(acceptRaw);
  if (declineRaw.trim() && decline === null)
    return "The auto-decline price isn't a valid amount. Clear it or type a number like 25.00.";
  if (acceptRaw.trim() && accept === null)
    return "The auto-accept price isn't a valid amount. Clear it or type a number like 40.00.";
  if (decline !== null && decline >= price)
    return "The auto-decline price has to be below your listing price.";
  if (accept !== null && accept >= price)
    return "The auto-accept price has to be below your listing price.";
  if (decline !== null && accept !== null && accept <= decline)
    return "The auto-accept price has to be higher than the auto-decline price.";
  return "";
}

/* ---------- v8: required item specifics ---------- */
let categoryAspects = { required: [], recommended: [] };

function draftAttrValue(name) {
  /* Case-insensitive lookup in the draft's AI-guessed attributes so eBay's
     exact aspect names get pre-filled where they match. */
  const attrs = ((draft && draft.attributes) || {});
  const lower = String(name).toLowerCase();
  for (const k of Object.keys(attrs)) {
    if (String(k).toLowerCase() === lower) return attrs[k];
  }
  return "";
}

function aspectInput(name, values, required, prefill) {
  const wrap = document.createElement("div");
  wrap.className = "req-aspect";
  const label = document.createElement("label");
  label.textContent = name + (required ? " *" : "");
  const input = document.createElement("input");
  input.type = "text";
  input.dataset.aspectName = name;
  input.value = prefill || "";
  input.placeholder = required ? "Required by eBay" : "Optional";
  if (values && values.length) {
    const dl = document.createElement("datalist");
    dl.id = "dl-" + Math.random().toString(36).slice(2);
    values.slice(0, 30).forEach((v) => {
      const o = document.createElement("option");
      o.value = v;
      dl.appendChild(o);
    });
    wrap.appendChild(dl);
    input.setAttribute("list", dl.id);
  }
  wrap.appendChild(label);
  wrap.appendChild(input);
  return wrap;
}

async function loadCategoryAspects() {
  const box = $("required-aspects");
  const cid = $("p-category").value.trim();
  box.innerHTML = "";
  categoryAspects = { required: [], recommended: [] };
  if (!cid) return;
  box.innerHTML = "<p class='hint'>Checking what details eBay wants for this category…</p>";
  let data;
  try {
    data = await api("/api/ebay/category-aspects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ category_id: cid }),
    });
  } catch (e) {
    data = null;
  }
  if (!data || data.unavailable) {
    box.innerHTML = "<p class='hint'>Could not check eBay's required details — listing with what you have.</p>";
    return;
  }
  categoryAspects = data;
  box.innerHTML = "";
  const req = data.required || [];
  const rec = (data.recommended || []).slice(0, 8);
  if (!req.length && !rec.length) {
    box.innerHTML = "<p class='hint'>eBay has no special required details for this category.</p>";
    return;
  }
  if (req.length) {
    const h = document.createElement("h4");
    h.textContent = "Details eBay requires for this category";
    box.appendChild(h);
    req.forEach((a) => box.appendChild(aspectInput(a.name, a.values, true, draftAttrValue(a.name))));
  }
  if (rec.length) {
    const det = document.createElement("details");
    const sum = document.createElement("summary");
    sum.textContent = "More details eBay suggests (optional)";
    det.appendChild(sum);
    rec.forEach((a) => det.appendChild(aspectInput(a.name, a.values, false, draftAttrValue(a.name))));
    box.appendChild(det);
  }
}

/* v35: category-aware Condition dropdown. The category's allowed
   conditions come from the Metadata API; the dropdown offers ONLY
   those, labeled with eBay's plain names. The seller's current pick
   is never silently changed: if it is not in the allowed set, it
   stays selected and marked, so the pre-publish check can flag it
   with the allowed choices instead of rewriting the choice. When the
   fetch fails, the hardcoded fallback options stay and a plain hint
   shows. */
let lastConditionCategory = "";
async function fetchCategoryConditions() {
  const cid = ($("p-category").value || "").trim();
  const sel = $("f-condition");
  const hint = $("condition-fetch-hint");
  if (!cid || cid === lastConditionCategory) return;
  lastConditionCategory = cid;
  let data = null;
  try {
    data = await api("/api/ebay/category-conditions?category_id=" +
      encodeURIComponent(cid));
  } catch (e) {
    data = null;
  }
  if (!data || !data.available || !data.conditions || !data.conditions.length) {
    hint.classList.remove("hidden");
    return;
  }
  hint.classList.add("hidden");
  const current = sel.value;
  let currentLabel = current;
  const currentOpt = sel.querySelector('option[value="' + current + '"]');
  if (currentOpt && currentOpt.textContent) currentLabel = currentOpt.textContent;
  sel.innerHTML = "";
  const ph = document.createElement("option");
  ph.value = "";
  ph.textContent = "— Pick one —";
  sel.appendChild(ph);
  data.conditions.forEach((c) => {
    const o = document.createElement("option");
    o.value = c.value;
    o.textContent = c.label;
    sel.appendChild(o);
  });
  if (current && data.conditions.some((c) => c.value === current)) {
    sel.value = current;
  } else if (current) {
    const o = document.createElement("option");
    o.value = current;
    o.textContent = currentLabel + " (not allowed for this category)";
    sel.appendChild(o);
    sel.value = current;
  } else {
    sel.value = "";
  }
}

function collectAspects() {
  /* Values from the step-4 aspect fields, keyed by eBay's exact aspect name. */
  const out = {};
  document.querySelectorAll("#required-aspects input[data-aspect-name]").forEach((i) => {
    const v = i.value.trim();
    if (v) out[i.dataset.aspectName] = v;
  });
  return out;
}

function validateAspects() {
  const missing = [];
  const vals = collectAspects();
  (categoryAspects.required || []).forEach((a) => {
    const v = (vals[a.name] || "").trim();
    if (!v || v.toLowerCase() === "unknown") missing.push(a.name);
  });
  return missing;
}

$("btn-publish").addEventListener("click", async () => {
  hidePublishMsg();
  if (!draft) return;
  /* v29: save the draft first so what posts is exactly what is on disk. */
  await saveDraft();
  /* v8: eBay's required item specifics must be filled before listing. */
  const missing = validateAspects();
  if (missing.length) {
    const el = $("publish-error");
    el.textContent = "eBay requires " + missing.join(", ") +
      " for this category. Fill them in under 'Details eBay requires' above, then try again.";
    el.classList.remove("hidden");
    return;
  }
  /* v9: best offer sanity checks, plain words, before the confirm dialog. */
  const boErr = checkBestOffer($("p-best-offer").checked,
                               $("p-bo-decline").value,
                               $("p-bo-accept").value,
                               parseFloat($("f-price").value));
  if (boErr) {
    const el = $("publish-error");
    el.textContent = boErr;
    el.classList.remove("hidden");
    return;
  }
  /* v29: the final confirmation IS the review stop — title, price and
     category, confirmed before any eBay call creates a listing. */
  const catNum = $("p-category").value.trim();
  const catLabel = (catNum && catNum === pickedCat.id && pickedCat.name)
    ? pickedCat.name + " (" + catNum + ")" : (catNum || "(not set)");
  if (!confirm("Create a REAL live eBay listing?\n\n" +
      ($("f-title").value.trim() || "(no title)") + "\n" +
      "Price: $" + $("f-price").value.trim() + "\n" +
      "Category: " + catLabel + "\n\n" +
      "This goes live immediately.")) return;
  $("publish-spinner").classList.remove("hidden");
  $("btn-publish").disabled = true;
  try {
    const body = {
      draft_id: draft.id,
      title: $("f-title").value.trim(),
      description: $("f-description").value.trim(),
      price: $("f-price").value.trim(),
      condition: $("f-condition").value,
      condition_details: $("f-condition-details").value.trim(),
      category_id: $("p-category").value.trim(),
      fulfillment_policy_id: $("p-fulfillment").value.trim(),
      payment_policy_id: $("p-payment").value.trim(),
      return_policy_id: $("p-return").value.trim(),
      /* v8: listing-page aspect fields overlay the draft's attributes server-side. */
      attributes: Object.assign({}, draft.attributes || {}, collectAspects()),
      /* v9: best offer settings. */
      best_offer: $("p-best-offer").checked,
      best_offer_auto_decline: $("p-bo-decline").value.trim(),
      best_offer_auto_accept: $("p-bo-accept").value.trim(),
      /* v42: package weight + size, pounds + ounces boxes. Saved
         per draft; sent only for calculated shipping policies. */
      package_weight_lb: $("p-weight-lb").value.trim(),
      package_weight_oz: $("p-weight-oz").value.trim(),
      package_length: $("p-length").value.trim(),
      package_width: $("p-width").value.trim(),
      package_height: $("p-height").value.trim(),
    };
    await postPublish(body);
  } catch (e) {
    const el = $("publish-error");
    el.textContent = e.message;
    el.classList.remove("hidden");
    /* v33: same defense as postPublish — a scope message reaching
       this generic display must still reveal the Connect button. */
    if (isScopeReconnectText(e.message)) {
      $("scope-reconnect-box").classList.remove("hidden");
    }
  } finally {
    $("publish-spinner").classList.add("hidden");
    $("btn-publish").disabled = false;
  }
});

/* v31: one-time ship-from address form. Shown only when eBay has no
   shop location yet. The inputs start empty and no code ever writes an
   address into them — only the seller's keystrokes fill them. */
function readShipFrom(prefix) {
  const street = $(prefix + "-street").value.trim();
  const city = $(prefix + "-city").value.trim();
  const state = $(prefix + "-state").value.trim().toUpperCase();
  const zip = $(prefix + "-zip").value.trim().replace(/\s+/g, "");
  if (!street || !city || !state || !zip)
    return { error: "Fill in all four: street, city, state, ZIP code." };
  if (!/^[A-Z]{2}$/.test(state))
    return { error: "Use the 2-letter state code, e.g. TX." };
  if (!/^\d{5}(-\d{4})?$/.test(zip))
    return { error: "Enter a 5-digit ZIP code." };
  return { data: { street, city, state, zip } };
}

/* The body of the publish the user already confirmed (title, price,
   category). Kept so the address-form submit re-posts exactly that
   confirmed listing, plus the address — nothing else changes. */
let pendingPublishBody = null;

/* v33: defense in depth — the scope message text itself must reveal
   the Connect button, even if a server response ever carries it
   without the needs_scope_reconnect flag (the live v1.31 gap: the
   message showed but the button stayed hidden). The substring mirrors
   the server's SCOPE_RECONNECT_MSG; the wording is unchanged
   server-side, so contains-matching also survives any wrapping. */
function isScopeReconnectText(text) {
  return typeof text === "string" &&
    text.indexOf("needs one new permission to read your shipping policies") !== -1;
}

async function postPublish(body) {
  let r;
  try {
    r = await api("/api/ebay/publish", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (e) {
    /* v33: a thrown scope message (flag missing) still shows the
       message + the Connect button, then reports needs-scope like the
       flag path, so every postPublish caller behaves the same. */
    if (isScopeReconnectText(e.message)) {
      const el = $("publish-error");
      el.textContent = e.message;
      el.classList.remove("hidden");
      $("scope-reconnect-box").classList.remove("hidden");
      return "needs-scope";
    }
    throw e;
  }
  if (r.needs_ship_from) {
    pendingPublishBody = body;
    $("ship-from-form").classList.remove("hidden");
    return "needs-address";
  }
  /* v32: the stored sign-in was granted before the app could read
     shipping policies. Show the plain message + the one-tap Connect
     button; nothing is wiped, and the publish can be retried after
     the fresh consent. */
  if (r.needs_scope_reconnect) {
    const el = $("publish-error");
    el.textContent = r.error || "The app needs one new permission to read your shipping policies.";
    el.classList.remove("hidden");
    $("scope-reconnect-box").classList.remove("hidden");
    return "needs-scope";
  }
  /* v34: the shipping policy number is unusable. The Shipping dropdown
     opens with the current policies; picking one re-posts the
     confirmed publish with the picked number. v35: compliance problems
     found alongside ride along, so the pick screen shows them too. */
  if (r.needs_policy_pick) {
    pendingPublishBody = body;
    openShippingPick(r.policies || [], r.error || "", r.problems || []);
    return "needs-pick";
  }
  draft.ebay_listing_id = r.listing_id;
  draft.ebay_listing_url = r.listing_url;
  const ok = $("publish-ok");
  ok.innerHTML = "It is live! <a href='" + r.listing_url + "' target='_blank'>View your eBay listing</a>" +
    "<br>Item number: " + esc(r.listing_id);
  ok.classList.remove("hidden");
  /* v43: refresh the page (status, already-live state) but keep the
     fresh success note visible — prefillListing hides it by default,
     which is only right when opening a draft. */
  prefillListing({ keepPublishOk: true });
  return "listed";
}

/* v32: plain label for a policy cost type, for the pick list. */
function policyTypeLabel(costType) {
  if (costType === "CALCULATED") return "calculated (needs package weight)";
  if (costType === "FLAT_RATE") return "flat rate";
  if (costType === "NOT_SPECIFIED") return "local pickup only";
  return "unknown type";
}

/* v35: Shipping dropdown state. shippingPolicies holds the account's
   current policies (chooser/gate entry shape: id, name, cost_type);
   shippingPickMode is true only while a failed publish waits on a
   pick, in which case selecting one re-posts that confirmed publish.
   The dropdown shows policy NAMES only — never ID numbers — and the
   closed dropdown IS the current-selection display, so there is no
   separate selected line anymore. */
let shippingPolicies = [];
let shippingPickMode = false;
let shippingFetchInFlight = false;

function shippingTypeLine(costType) {
  /* v35: one short plain line under the dropdown. */
  if (costType === "CALCULATED") return "Charges by weight.";
  if (costType === "FLAT_RATE") return "Flat rate.";
  if (costType === "NOT_SPECIFIED") return "Local pickup only.";
  return "Unknown type.";
}

async function fetchShippingPolicies() {
  /* v35: fetched every time the listing page opens, so the dropdown
     always shows the account's current policies. On 403 the token
     lacks sell.account: the plain message + Connect link show here,
     the same message the publish path shows. */
  if (shippingFetchInFlight) return;
  shippingFetchInFlight = true;
  const sel = $("shipping-select");
  const errEl = $("shipping-error");
  errEl.classList.add("hidden");
  sel.disabled = true;
  try {
    const r = await api("/api/ebay/fulfillment-policies");
    if (r.needs_scope_reconnect) {
      errEl.innerHTML = "<p style='margin:0'>" + esc(r.error ||
        "The app needs one new permission to read your shipping policies.") +
        "</p><p class='hint'><a class='primary btnlink' href='/api/ebay/connect'>Connect eBay</a></p>";
      errEl.classList.remove("hidden");
      /* v49: the error lives inside the collapsed Shipping row — open
         the row so it is actually seen. */
      openListingRow("row-shipping");
      return;
    }
    shippingPolicies = r.policies || [];
    renderShippingDropdown();
    refreshShippingChooser();
  } catch (e) {
    errEl.innerHTML = "<p style='margin:0'>" + esc(e.message) + "</p>" +
      "<p class='hint'><button id='btn-shipping-retry' class='ghost' type='button'>Try again</button></p>";
    errEl.classList.remove("hidden");
    openListingRow("row-shipping");
    const retry = $("btn-shipping-retry");
    if (retry) retry.addEventListener("click", fetchShippingPolicies);
  } finally {
    sel.disabled = false;
    shippingFetchInFlight = false;
  }
}

/* v49: open one collapsed listing row by id. The Shipping row opens
   itself whenever a shipping error, pick screen or create form needs
   to be seen — a closed row would hide the very thing the seller
   has to act on. */
function openListingRow(id) {
  const row = $(id);
  if (row) row.open = true;
}

function renderShippingDropdown() {
  /* v35: one option per current policy, NAME ONLY. The id rides in
     the option value for the app, but it is never displayed — the
     v34 buttons showed "name — type (id)", which read as bare text
     with number noise. A policy with no shippable service is still
     shown (its type line says so); the publish gate says why it
     cannot ship if it is picked. */
  const sel = $("shipping-select");
  const current = ($("p-fulfillment").value || "").trim();
  sel.innerHTML = "";
  const ph = document.createElement("option");
  ph.value = "";
  ph.textContent = "— Pick a shipping policy —";
  sel.appendChild(ph);
  shippingPolicies.forEach((p) => {
    const o = document.createElement("option");
    o.value = p.id;
    o.textContent = p.name || "Unnamed policy";
    sel.appendChild(o);
  });
  sel.value = shippingPolicies.some((p) => p.id === current) ? current : "";
}

function refreshShippingChooser() {
  /* v35: the dropdown value + type line follow the shipping number
     box. v42: the package fields are ALWAYS visible now (they hold
     the weight for calculated policies and the seller's own record
     for flat/free), so nothing here hides them anymore. A hand-typed
     number that matches a fetched policy selects it in the
     dropdown; one that does not match leaves the placeholder, and
     the publish gate flags it with the pick message. */
  const selected = ($("p-fulfillment").value || "").trim();
  const sel = $("shipping-select");
  const line = $("shipping-type-line");
  const match = shippingPolicies.find((p) => p.id === selected);
  if (shippingPolicies.length) {
    sel.value = match ? selected : "";
  }
  if (!selected) {
    line.textContent = "";
  } else if (match) {
    /* v41: a pickup-only policy has no cost type; label it honestly
       instead of "Unknown type." (the gate still flags it at List). */
    line.textContent = match.cost_type ? shippingTypeLine(match.cost_type)
      : (match.local_pickup ? "Local pickup only." : "Unknown type.");
  } else {
    line.textContent = "Details not loaded yet.";
  }
  /* v42: the carrier + service pickers follow the selected policy
     (its first domestic service), so the two controls never
     disagree. A carrier pick still waiting on its policy (the
     create form is open) is left alone: no policy is selected yet. */
  if (match && match.service_code) {
    syncShipPickers(match.carrier, match.service_code);
  }
  updateShippingSummary();
}

/* v49: live summary lines for the collapsed listing rows (design 4).
   Shipping shows the current carrier, service, weight in lb and oz,
   and dimensions, so the choice is visible without opening the row;
   item specifics shows the row names, falling back to "(n rows)";
   shop policy numbers and the manual block are fixed labels in the
   HTML. updateListingSummaries() runs on every change that can move
   a summary: carrier/service pick, package input, policy pick,
   item-specifics edit, and listing-page prefill. */
function shippingSummaryText() {
  const carrier = ($("ship-carrier").value || "").trim();
  const code = shipServiceCode();
  const lb = ($("p-weight-lb").value || "").trim();
  const oz = ($("p-weight-oz").value || "").trim();
  const len = ($("p-length").value || "").trim();
  const wid = ($("p-width").value || "").trim();
  const hei = ($("p-height").value || "").trim();
  const parts = [];
  if (carrier) parts.push(carrier);
  if (code) parts.push(createServiceLabel(code));
  const weight = [lb ? lb + " lb" : "", oz ? oz + " oz" : ""].filter(Boolean).join(" ");
  if (weight) parts.push(weight);
  const dims = [len, wid, hei].filter(Boolean);
  if (dims.length) parts.push(dims.join(" × ") + " in");
  if (parts.length) return parts.join(" · ");
  const policyId = ($("p-fulfillment").value || "").trim();
  const match = shippingPolicies.find((p) => p.id === policyId);
  if (match) {
    const type = match.cost_type ? shippingTypeLine(match.cost_type)
      : (match.local_pickup ? "Local pickup only." : "Unknown type.");
    return (match.name || "Unnamed policy") + " · " + type;
  }
  return "Not set yet.";
}

/* Pure helper, unit-tested: the item-specifics summary from the row
   names (first three, then an ellipsis) or "(n rows)" when no name
   is filled in yet. */
function attrsSummaryText(names, rowCount) {
  const filled = (names || []).map((n) => String(n || "").trim()).filter(Boolean);
  if (filled.length) {
    return filled.slice(0, 3).join(" · ") + (filled.length > 3 ? " …" : "");
  }
  return "(" + rowCount + " row" + (rowCount === 1 ? "" : "s") + ")";
}

function updateShippingSummary() {
  const el = $("shipping-summary");
  if (el) el.textContent = shippingSummaryText();
}

function updateAttrsSummary() {
  const el = $("attrs-summary");
  if (!el) return;
  const names = Array.from(document.querySelectorAll("#attrs .attr-row .ak"))
    .map((input) => input.value);
  const rows = document.querySelectorAll("#attrs .attr-row").length;
  el.textContent = attrsSummaryText(names, rows);
}

function updateListingSummaries() {
  updateShippingSummary();
  updateAttrsSummary();
}

/* v42: carrier + service picker (like eBay's lister). The carrier
   picker drives the service picker; picking a service finds the
   matching policy on the account (a domestic-only policy that
   charges by weight and offers that service) or opens the v41
   create form prefilled with it. The pickers are a convenience that
   resolves to a policy — the policy number in the box below is
   still what List sends. Top-level function declarations (not const
   arrows) so the node harness can call them directly, like the v35
   chooser functions. */
let carrierServices = { USPS: [], UPS: [], FedEx: [] };
let carrierServicesOther = [];
let carrierServicesFetched = false;
let carrierServicesInFlight = false;
let createFromCarrierPick = false;
let pendingShipService = "";

function carrierServiceLabels() {
  /* v43: two harvested-only codes his policies carry, labeled from
     eBay's own data — FedExSmartPost is the eBay API code for FedEx
     Ground Economy (isdntek eBay shipping-code table; his orphan
     policy is named "Calculated: FedExSmartPost"); UPSGroundSaver is
     the eBay code for UPS Ground Saver (SolidCommerce GetEbayShipService
     sample enum; eBay's own label screens offer "UPS Ground Saver").
     Labels are display-only: the code itself is always sent verbatim,
     so a label can never change what eBay receives. These two are
     NOT added to the 11-code picker catalog (DOCUMENTED_DOMESTIC_SERVICES):
     they are harvested labels only.
     v49: the catalog is 10 codes now, not 11. USPSGroundAdvantage
     is gone (eBay's LSAS checker rejects it on this account — the
     Nov 8 live create failed with 20403 UNKNOWN_SHIPPING_SERVICE);
     Ground Advantage is offered as USPSParcel only, labeled
     "USPS Ground Advantage", and the separate "USPS Parcel Post"
     entry is gone because it was the same code for the same service. */
  return { "USPSParcel": "USPS Ground Advantage",
           "USPSPriority": "USPS Priority Mail",
           "UPSGround": "UPS Ground",
           "UPSGroundSaver": "UPS Ground Saver",
           "UPS3rdDay": "UPS 3 Day Select",
           "UPS2DayAirAM": "UPS 2nd Day Air",
           "UPSNextDay": "UPS Next Day Air Saver",
           "FedExGround": "FedEx Ground",
           "FedExHomeDelivery": "FedEx Home Delivery",
           "FedEx2Day": "FedEx 2 Day",
           "FedExExpressSaver": "FedEx Express Saver",
           "FedExSmartPost": "FedEx Ground Economy" };
}

/* v49: one eBay service code -> the code this account must send.
   Mirrors _canonical_service_code on the server: USPSGroundAdvantage
   is rejected by eBay's LSAS checker on this account (Nov 8 live
   create, 20403 UNKNOWN_SHIPPING_SERVICE), and eBay's own code for
   the same Ground Advantage service is USPSParcel. Used by the
   create form (createServiceCodeFor), by the prefilled create form,
   and by restoreShipService so a draft saved with the old code
   still restores onto the USPSParcel option. FedExSmartPost is
   left as-is: whether create accepts it is unproven, and the retry
   test settles it. */
/* v50: one spelling for a service code or label — lowercased, runs
   of spaces collapsed — so "uspsparcel", "USPSParcel", and "USPS
   Ground Advantage" all look the same key up. Mirror of the server's
   _normalize_service_spelling. */
function normalizeServiceSpelling(text) {
  return String(text || "").trim().toLowerCase().replace(/\s+/g, " ");
}

/* v50: every known service code, keyed by a normalized spelling.
   Mirror of the server's _known_service_code_lookup: codes first,
   then display labels, both mapping to the code eBay must receive.
   The rejected code maps to the code eBay accepts, so a typed
   USPSGroundAdvantage never survives. The shared label "USPS Ground
   Advantage" keeps its first mapping (USPSParcel, first in
   carrierServiceLabels()). */
function knownServiceCodeLookup() {
  const out = {};
  const labels = carrierServiceLabels();
  Object.keys(labels).forEach((code) => {
    out[normalizeServiceSpelling(code)] = code;
  });
  Object.keys(labels).forEach((code) => {
    const key = normalizeServiceSpelling(labels[code]);
    if (!(key in out)) out[key] = code;
  });
  out[normalizeServiceSpelling("USPSGroundAdvantage")] = "USPSParcel";
  return out;
}

function canonicalServiceCode(code) {
  const c = String(code || "").trim();
  if (!c) return c;
  /* v50: the match is now case-insensitive and accepts the display
     label too ("uspsparcel", "USPS Ground Advantage"), so a typed
     code is never sent verbatim. Anything unknown passes through
     unchanged — callers that must not send an unknown code check
     resolveTypedServiceCode first. */
  const known = knownServiceCodeLookup()[normalizeServiceSpelling(c)];
  return known || c;
}

/* v50: a code typed into an Other box, resolved against the known
   list. Returns {code, known}: known is false when nothing matches,
   so the caller can warn instead of sending a code eBay will fail
   with 20403. Mirror of the server's _resolve_typed_service_code. */
function resolveTypedServiceCode(text) {
  const typed = String(text || "").trim();
  if (!typed) return { code: "", known: false };
  const known = knownServiceCodeLookup()[normalizeServiceSpelling(typed)];
  return known ? { code: known, known: true } : { code: typed, known: false };
}

/* v50: service codes held back from the create form. UPSGroundSaver
   is NOT in eBay's Trading API ShippingServiceCodeType enum and is
   unproven on create. It stays offered for matching existing
   policies and for display, but a new policy must never be created
   with it. FedExSmartPost is deliberately NOT here: whether create
   accepts it is unproven, and the live retry on 1.48 settles it.
   Mirror of the server's CREATE_EXCLUDED_SERVICE_CODES. */
const CREATE_EXCLUDED_SERVICE_CODES = ["UPSGroundSaver"];
function isCreateExcludedCode(code) {
  return CREATE_EXCLUDED_SERVICE_CODES.indexOf(canonicalServiceCode(code)) >= 0;
}

async function fetchCarrierServices() {
  /* One fetch per listing page open, alongside the policy list. A
     failure is non-fatal: the service picker keeps its placeholder
     plus the Other box, and the saved-policy dropdown below still
     works. The next carrier change retries the fetch. */
  if (carrierServicesFetched || carrierServicesInFlight) return;
  carrierServicesInFlight = true;
  try {
    const r = await api("/api/ebay/shipping-services");
    if (r.carriers) {
      carrierServices = { USPS: r.carriers.USPS || [], UPS: r.carriers.UPS || [],
                          FedEx: r.carriers.FedEx || [] };
      carrierServicesOther = r.other || [];
    } else if (r.domestic) {
      const labels = carrierServiceLabels();
      carrierServices = { USPS: [], UPS: [], FedEx: [] };
      (r.domestic || []).forEach((s) => {
        const carrier = (s.code || "").indexOf("USPS") === 0 ? "USPS"
          : (s.code || "").indexOf("UPS") === 0 ? "UPS"
          : (s.code || "").indexOf("FedEx") === 0 ? "FedEx" : "";
        if (carrier) carrierServices[carrier].push(s);
        else carrierServicesOther.push(s);
      });
    }
    carrierServicesFetched = true;
    populateShipService();
    if (pendingShipService) {
      const code = pendingShipService;
      pendingShipService = "";
      restoreShipService(code);
    }
  } catch (e) {
    /* Leave the picker as-is; the note below the service box says so
       only once the seller picks a carrier. */
  } finally {
    carrierServicesInFlight = false;
  }
}

function serviceListed(code) {
  const sel = $("ship-service");
  return Array.from(sel.children || []).some((o) => o.value === code);
}

function populateShipService() {
  /* Fill the service dropdown for the picked carrier: that carrier's
     services (harvested from the account first, documented fallback
     after — the server orders them), plus any unclassified account
     services, plus the Other box, same pattern as the v41 create
     form, so no needed service is unreachable. */
  const carrier = $("ship-carrier").value;
  const sel = $("ship-service");
  sel.innerHTML = "";
  const ph = document.createElement("option");
  ph.value = "";
  ph.textContent = carrier ? "— Pick a service —" : "— Pick a carrier first —";
  sel.appendChild(ph);
  const list = (carrierServices[carrier] || []).slice();
  (carrierServicesOther || []).forEach((s) => {
    if (!list.some((x) => x.code === s.code)) list.push(s);
  });
  list.forEach((s) => {
    const o = document.createElement("option");
    o.value = s.code;
    o.textContent = s.label || createServiceLabel(s.code);
    sel.appendChild(o);
  });
  const other = document.createElement("option");
  other.value = "__OTHER__";
  other.textContent = "Other (type the code)";
  sel.appendChild(other);
  sel.value = "";
  $("ship-service-other").value = "";
  $("ship-service-other").classList.add("hidden");
}

function shipServiceCode() {
  /* The effective service code: the dropdown value, or the typed
     code when Other is picked. "" when nothing is picked. */
  const v = $("ship-service").value;
  if (v === "__OTHER__") return ($("ship-service-other").value || "").trim();
  return v || "";
}

function restoreShipService(code) {
  /* Select a service code in the picker: the dropdown value when
     the code is listed, else the Other box with the code typed in.
     When the service list has not loaded yet, park the code and
     apply it once the fetch lands.
     v49: the code is canonicalized first, so a draft saved with
     USPSGroundAdvantage restores onto the USPSParcel option the
     picker actually offers. */
  code = canonicalServiceCode(code);
  const sel = $("ship-service");
  const other = $("ship-service-other");
  if (!code) {
    sel.value = "";
    other.value = "";
    other.classList.add("hidden");
    return;
  }
  if (!carrierServicesFetched) { pendingShipService = code; return; }
  if (serviceListed(code)) {
    sel.value = code;
    other.value = "";
    other.classList.add("hidden");
  } else {
    sel.value = "__OTHER__";
    other.value = code;
    other.classList.remove("hidden");
  }
}

function syncShipPickers(carrier, code) {
  /* The selected policy's carrier + service, shown in the pickers.
     Only runs when the policy actually carries a service. */
  if (!carrier || !code) return;
  if ($("ship-carrier").value !== carrier) {
    $("ship-carrier").value = carrier;
    populateShipService();
  }
  restoreShipService(code);
}

function draftPackageLbOz(draft) {
  /* v42: pounds + ounces for the weight boxes. New drafts carry
     package_weight_lb/oz; an old draft with only the legacy single
     value + unit converts it (POUND 2 -> 2 lb; OUNCE 40 -> 2 lb
     8 oz). A bad saved value restores empty, never a wrong weight. */
  const lb = ((draft.package_weight_lb || "").toString()).trim();
  const oz = ((draft.package_weight_oz || "").toString()).trim();
  if (lb || oz) return { lb: lb, oz: oz };
  const value = ((draft.package_weight_value || "").toString()).trim();
  const unit = ((draft.package_weight_unit || "OUNCE").toString()).trim().toUpperCase();
  if (!value) return { lb: "", oz: "" };
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return { lb: "", oz: "" };
  if (unit === "POUND") return { lb: String(n), oz: "" };
  const whole = Math.floor(n / 16);
  const rest = n - whole * 16;
  return { lb: whole ? String(whole) : "", oz: rest ? String(rest) : "" };
}

function shippingPackageComplete() {
  /* Weight (pounds or ounces) plus all three sizes filled. */
  return !!($("p-weight-lb").value.trim() || $("p-weight-oz").value.trim()) &&
    !!$("p-length").value.trim() && !!$("p-width").value.trim() &&
    !!$("p-height").value.trim();
}

async function onShipServicePicked() {
  /* Carrier + service picked: find the matching policy. A match
     selects it (no review — the policy already exists). No match
     opens the v41 create form prefilled with this service, and the
     review-before-create screen still stands. */
  const carrier = $("ship-carrier").value;
  const code = shipServiceCode();
  const note = $("ship-service-note");
  note.classList.add("hidden");
  if (!carrier || !code) return;
  await saveDraft();
  let r;
  try {
    r = await api("/api/ebay/policy-for-service",
                  { method: "POST", body: JSON.stringify({ carrier: carrier, service_code: code }) });
  } catch (e) {
    note.textContent = "Could not check your policies (" + e.message + "). Pick one from your usual policies below.";
    note.classList.remove("hidden");
    return;
  }
  if (r.needs_scope_reconnect) {
    note.innerHTML = "<p style='margin:0'>" + esc(r.error ||
      "The app needs one new permission to read your shipping policies.") +
      "</p><p class='hint'><a class='primary btnlink' href='/api/ebay/connect'>Connect eBay</a></p>";
    note.classList.remove("hidden");
    return;
  }
  if (r.policy_id) {
    note.textContent = "Using your policy: " + (r.name || "that service") + ".";
    note.classList.remove("hidden");
    await selectShippingPolicy(r.policy_id);
    return;
  }
  /* v43: the matcher is calculated-only by design, so a service
     carried only by a flat policy must not be reported as "no
     policy". Offer the flat policy honestly; the seller picks it,
     the app never switches the pricing model on its own. */
  if (r.flat_policy_id) {
    note.innerHTML = "";
    note.textContent = "Your flat policy " + (r.flat_name || "on your account") +
      " already offers " + createServiceLabel(code) + ".";
    const useFlat = document.createElement("button");
    useFlat.type = "button";
    useFlat.id = "btn-use-flat-policy";
    useFlat.className = "ghost";
    useFlat.textContent = "Use that policy";
    useFlat.addEventListener("click", async () => {
      note.classList.add("hidden");
      await selectShippingPolicy(r.flat_policy_id);
    });
    note.appendChild(useFlat);
    note.classList.remove("hidden");
    return;
  }
  /* v50: same honest treatment for a policy whose price type is not
     set on eBay — it still offers the service, so offer it by name
     instead of claiming no policy exists and opening the create form
     toward a duplicate. The publish gate still decides whether it
     can list, so offering it never silently lists on a policy that
     cannot ship. */
  if (r.not_specified_policy_id) {
    note.innerHTML = "";
    note.textContent = "Your policy " + (r.not_specified_name || "on your account") +
      " already offers " + createServiceLabel(code) + " (its price type isn't set on eBay).";
    const useIt = document.createElement("button");
    useIt.type = "button";
    useIt.id = "btn-use-not-specified-policy";
    useIt.className = "ghost";
    useIt.textContent = "Use that policy";
    useIt.addEventListener("click", async () => {
      note.classList.add("hidden");
      await selectShippingPolicy(r.not_specified_policy_id);
    });
    note.appendChild(useIt);
    note.classList.remove("hidden");
    return;
  }
  note.textContent = "No policy with that service yet — check the details below and confirm to create one.";
  note.classList.remove("hidden");
  openCreateFormPrefilled(carrier, code);
}

async function openCreateFormPrefilled(carrier, code) {
  /* The v41 create form, prefilled from a carrier + service pick:
     calculated, that service, domestic only, no self-ship
     international (eBay International Shipping covers it). The name
     is a suggestion the seller can change; handling time is left
     empty for the seller to fill. Review-before-create still stands. */
  await openCreateForm();
  createFromCarrierPick = true;
  /* v49: canonicalize first, so a Ground Advantage pick prefills
     the USPSParcel option (and the name reads "USPS Ground
     Advantage"), never the rejected code in the Other box. */
  code = canonicalServiceCode(code);
  const label = createServiceLabel(code);
  const excluded = isCreateExcludedCode(code);
  if (!excluded && !$("pc-name").value) $("pc-name").value = "Calculated " + label;
  $("pc-dom-cost-type").value = "CALCULATED";
  const dom1 = $("pc-dom-service-1");
  const listed = Array.from(dom1.children || []).some((o) => o.value === code);
  if (excluded) {
    /* v50: UPS Ground Saver cannot be used for a new policy yet —
       eBay has not confirmed that code. Leave the service blank and
       say so plainly, instead of prefilling a code create would
       refuse. The seller can pick another service, or use the policy
       that already carries it. */
    dom1.value = "";
    $("pc-dom-service-1-other").value = "";
    $("pc-dom-service-1-other").classList.add("hidden");
    const errEl = $("policy-create-error");
    errEl.textContent = label + " cannot be used for a new policy yet. eBay has not confirmed that code, so pick a different service or use a policy you already have.";
    errEl.classList.remove("hidden");
  } else if (listed) {
    dom1.value = code;
    $("pc-dom-service-1-other").value = "";
    $("pc-dom-service-1-other").classList.add("hidden");
  } else {
    dom1.value = "__OTHER__";
    $("pc-dom-service-1-other").value = code;
    $("pc-dom-service-1-other").classList.remove("hidden");
  }
  $("pc-international").checked = false;
  $("pc-local-pickup").checked = false;
  $("pc-pickup-only").checked = false;
  refreshCreateFormVisibility();
  updatePolicyCreateCounts();
}

function openShippingPick(policies, message, problems) {
  /* v35: a failed publish with an unusable saved policy opens this
     same dropdown: the fresh policy list, the plain problem message,
     any compliance problems found alongside it, and pick mode on, so
     the next selection re-posts the confirmed publish. */
  shippingPolicies = policies || [];
  shippingPickMode = true;
  renderShippingDropdown();
  refreshShippingChooser();
  const msg = $("shipping-pick-msg");
  let text = message ||
    "That shipping policy number no longer works. Pick one below.";
  if (problems && problems.length) {
    text += " Also fix before listing: " + problems.join(" ");
  }
  msg.textContent = text;
  msg.classList.remove("hidden");
  /* v49: the pick screen lives inside the collapsed Shipping row —
     open the row first, or the seller never sees it. */
  openListingRow("row-shipping");
  $("shipping-section").scrollIntoView();
  $("shipping-select").focus();
}

async function selectShippingPolicy(policyId) {
  /* v34: picking a policy saves it to the draft (so it sticks across
     reloads) and refreshes the chooser. In pick mode — a failed publish
     is waiting — it also re-posts that already-confirmed publish with
     the picked number, which (v34 discard) replaces the old offer with
     a fresh one carrying the picked policy. No second confirmation
     dialog: the listing was already confirmed before the first
     attempt. The save is the listing page's own autosave (best-effort,
     like every other field here); the re-post body carries the picked
     number regardless, so a pick always reaches eBay. */
  const errEl = $("shipping-error");
  errEl.classList.add("hidden");
  $("p-fulfillment").value = policyId;
  await saveDraft();
  refreshShippingChooser(); /* v35: the dropdown value follows the number box */
  if (!shippingPickMode || !pendingPublishBody) return;
  $("publish-spinner").classList.remove("hidden");
  try {
    const body = Object.assign({}, pendingPublishBody,
                               { fulfillment_policy_id: policyId,
                                 /* v34: a pick re-post must discard the
                                    pre-pick offer, so the fresh offer
                                    carries the picked policy. */
                                 policy_pick: true });
    const outcome = await postPublish(body);
    if (outcome === "listed") {
      shippingPickMode = false;
      $("shipping-pick-msg").classList.add("hidden");
    }
    /* "needs-pick" can recur only if the picked policy itself turns
       out unusable; the chooser stays in pick mode with the fresh
       list then. */
  } catch (e) {
    errEl.textContent = e.message;
    errEl.classList.remove("hidden");
  } finally {
    $("publish-spinner").classList.add("hidden");
  }
}

$("btn-ship-from").addEventListener("click", async () => {
  const errEl = $("ship-from-error");
  errEl.classList.add("hidden");
  const parsed = readShipFrom("sf");
  if (parsed.error) {
    errEl.textContent = parsed.error;
    errEl.classList.remove("hidden");
    return;
  }
  if (!pendingPublishBody) return;
  /* The listing was already confirmed (title, price, category) before
     the first attempt, and this re-post carries exactly that body plus
     the address — so the address submit itself authorizes creating the
     shop location and continuing that confirmed publish, and no second
     confirmation dialog is shown. */
  $("publish-spinner").classList.remove("hidden");
  $("btn-ship-from").disabled = true;
  try {
    const body = Object.assign({}, pendingPublishBody,
                               { ship_from: parsed.data });
    const outcome = await postPublish(body);
    if (outcome === "listed") $("ship-from-form").classList.add("hidden");
    /* "needs-address" cannot recur with an address supplied: the server
       either creates the location or answers with an error. */
  } catch (e) {
    errEl.textContent = e.message;
    errEl.classList.remove("hidden");
  } finally {
    $("publish-spinner").classList.add("hidden");
    $("btn-ship-from").disabled = false;
  }
});

/* v41: create a shipping policy in the app (choose-or-create). The
   form is full options, all editable, nothing defaulted from a name:
   flat rate or calculated, US plus worldwide self-ship
   international, local pickup. eBay International Shipping is an
   account setting, not a policy field, so the form says so instead
   of faking it. Review-before-create is mandatory: nothing is
   created until the seller confirms the exact policy. The server
   re-validates before any POST, so a client/server mismatch only
   costs a round trip, never a bad policy. Top-level function
   declarations (not const arrows) so the node harness can call them
   directly, like the v35 chooser functions. */
let createServicesFetched = false;
let createServices = { domestic: [], international: [] };
let pendingCreateForm = null;
let createRetry = false;

function createServiceLabel(code) {
  /* v43: UPSGroundSaver and FedExSmartPost labeled — see the note on
     carrierServiceLabels() for the attestation. Harvested labels
     only; not added to the picker catalog.
     v49: USPSParcel reads as "USPS Ground Advantage" (it IS that
     service on this account), and USPSGroundAdvantage is kept here
     for READING only — an old policy or saved draft may still carry
     it, and it must display as the same service, never as a raw
     code. Neither label ever sends a code; the code itself is
     canonicalized before anything leaves (canonicalServiceCode). */
  const labels = { "USPSParcel": "USPS Ground Advantage",
                   "USPSGroundAdvantage": "USPS Ground Advantage",
                   "USPSPriority": "USPS Priority Mail",
                   "UPSGround": "UPS Ground",
                   "UPSGroundSaver": "UPS Ground Saver",
                   "UPS3rdDay": "UPS 3 Day Select",
                   "UPS2DayAirAM": "UPS 2nd Day Air",
                   "UPSNextDay": "UPS Next Day Air Saver",
                   "FedExGround": "FedEx Ground",
                   "FedExHomeDelivery": "FedEx Home Delivery",
                   "FedEx2Day": "FedEx 2 Day",
                   "FedExExpressSaver": "FedEx Express Saver",
                   "FedExSmartPost": "FedEx Ground Economy" };
  return labels[code] || code;
}

function populateServiceSelect(selId, services) {
  const sel = $(selId);
  sel.innerHTML = "";
  const ph = document.createElement("option");
  ph.value = "";
  ph.textContent = "— Pick a service —";
  sel.appendChild(ph);
  (services || []).forEach((s) => {
    const o = document.createElement("option");
    o.value = s.code;
    o.textContent = s.label || createServiceLabel(s.code);
    sel.appendChild(o);
  });
  const other = document.createElement("option");
  other.value = "__OTHER__";
  other.textContent = "Other (type the code)";
  sel.appendChild(other);
  sel.value = "";
}

function refreshCreateFormVisibility() {
  const pickupOnly = $("pc-pickup-only").checked;
  $("pc-domestic").classList.toggle("hidden", pickupOnly);
  $("pc-intl").classList.toggle("hidden", pickupOnly || !$("pc-international").checked);
  $("pc-handling-wrap").classList.toggle("hidden", pickupOnly);
  const domFlat = $("pc-dom-cost-type").value !== "CALCULATED";
  $("pc-dom-cost-1-wrap").classList.toggle("hidden", !domFlat);
  $("pc-dom-cost-2-wrap").classList.toggle("hidden", !domFlat);
  $("pc-intl-cost-1-wrap").classList.toggle("hidden", $("pc-intl-cost-type").value === "CALCULATED");
  $("pc-intl-cost-2-wrap").classList.toggle("hidden", $("pc-intl-cost-type").value === "CALCULATED");
  ["dom-service-1", "dom-service-2", "intl-service-1", "intl-service-2"].forEach((key) => {
    $("pc-" + key + "-other").classList.toggle("hidden", $("pc-" + key).value !== "__OTHER__");
  });
}

function validCreateCost(v) {
  if (v === "" || v === null || v === undefined) return false;
  const n = Number(v);
  return Number.isFinite(n) && n > 0 && n <= 10000;
}

function updatePolicyCreateCounts() {
  /* v48: live character counters on the two capped create-form
     fields, same pattern as the title counter (updateCount).
     Called on input and on every path that sets the fields, so the
     count is never stale: form open, carrier prefill, Back from
     review, and cancel. */
  $("pc-name-count").textContent = $("pc-name").value.length + "/64";
  $("pc-description-count").textContent = $("pc-description").value.length + "/250";
}

function collectPolicyCreateForm() {
  /* Reads the create form into the server's form JSON + errors.
     Mirrors the server validation; empty stays empty and errors
     instead of being defaulted. */
  const errors = [];
  const form = {};
  form.name = ($("pc-name").value || "").trim();
  if (!form.name) errors.push("Give the new policy a name.");
  /* v47: eBay caps policy names at 64 characters; mirror the server
     cap so the form blocks before POST, instead of eBay answering
     20403 on a long name. */
  if (form.name.length > 64) errors.push("Keep the policy name under 64 characters. That is eBay's limit.");
  form.description = ($("pc-description").value || "").trim();
  if (form.description.length > 250) errors.push("Keep the policy description under 250 characters.");
  form.pickup_only = !!$("pc-pickup-only").checked;
  form.local_pickup = !!$("pc-local-pickup").checked;
  form.ship_international = !!$("pc-international").checked && !form.pickup_only;
  if (!form.pickup_only) {
    form.dom_cost_type = $("pc-dom-cost-type").value;
    form.dom_service_1 = $("pc-dom-service-1").value || "";
    form.dom_service_1_other = ($("pc-dom-service-1-other").value || "").trim();
    form.dom_service_2 = $("pc-dom-service-2").value || "";
    form.dom_service_2_other = ($("pc-dom-service-2-other").value || "").trim();
    form.dom_cost_1 = ($("pc-dom-cost-1").value || "").trim();
    form.dom_cost_2 = ($("pc-dom-cost-2").value || "").trim();
    form.dom_free_shipping = form.dom_cost_type === "FLAT_RATE" && !!$("pc-dom-free-1").checked;
    if (!createServiceResolved("dom", 1, form)) errors.push("Pick a shipping service for US shipping.");
    const dom1Err = createServiceTypedError("dom", 1, form);
    if (dom1Err) errors.push(dom1Err);
    if (form.dom_service_2) {
      const dom2Err = createServiceTypedError("dom", 2, form);
      if (dom2Err) errors.push(dom2Err);
    }
    if (form.dom_cost_type === "FLAT_RATE") {
      if (!form.dom_free_shipping && !validCreateCost(form.dom_cost_1)) errors.push("Enter the cost for US service 1, or check Free shipping.");
      if (form.dom_service_2 && !validCreateCost(form.dom_cost_2)) errors.push("Enter the cost for US service 2.");
    }
    if (form.ship_international) {
      form.intl_cost_type = $("pc-intl-cost-type").value;
      form.intl_service_1 = $("pc-intl-service-1").value || "";
      form.intl_service_1_other = ($("pc-intl-service-1-other").value || "").trim();
      form.intl_service_2 = $("pc-intl-service-2").value || "";
      form.intl_service_2_other = ($("pc-intl-service-2-other").value || "").trim();
      form.intl_cost_1 = ($("pc-intl-cost-1").value || "").trim();
      form.intl_cost_2 = ($("pc-intl-cost-2").value || "").trim();
      if (!createServiceResolved("intl", 1, form)) errors.push("Pick a shipping service for international shipping.");
      const intl1Err = createServiceTypedError("intl", 1, form);
      if (intl1Err) errors.push(intl1Err);
      if (form.intl_service_2) {
        const intl2Err = createServiceTypedError("intl", 2, form);
        if (intl2Err) errors.push(intl2Err);
      }
      if (form.intl_cost_type === "FLAT_RATE") {
        if (!validCreateCost(form.intl_cost_1)) errors.push("Enter the cost for international service 1.");
        if (form.intl_service_2 && !validCreateCost(form.intl_cost_2)) errors.push("Enter the cost for international service 2.");
      }
    }
    form.handling_days = ($("pc-handling").value || "").trim();
    const days = Number(form.handling_days);
    if (form.handling_days === "" || !Number.isInteger(days) || days < 0 || days > 30) {
      errors.push("Enter the handling time in business days (0 to 30).");
    }
  }
  return { form: form, errors: errors };
}

function createServiceResolved(prefix, n, form) {
  const v = form[prefix + "_service_" + n] || "";
  if (v === "__OTHER__") return !!(form[prefix + "_service_" + n + "_other"] || "").trim();
  return !!v;
}

/* v50: a code typed into an Other box must be one eBay knows, and
   must not be one held back from create. Returns the plain-words
   error, or "" when the slot is fine. A dropdown pick is always
   known, so only a typed code (or a held-back code) can fail this.
   Mirror of the server's _validate_create_service_code — the server
   re-checks, so a client/server mismatch only costs a round trip,
   never a bad policy. */
function createServiceTypedError(prefix, n, form) {
  const v = form[prefix + "_service_" + n] || "";
  const excludedMsg = (code) => createServiceLabel(code) +
    " cannot be used for a new policy yet. eBay has not confirmed that code, so pick a different service or use a policy you already have.";
  if (v === "__OTHER__") {
    const typed = (form[prefix + "_service_" + n + "_other"] || "").trim();
    if (!typed) return "";
    const resolved = resolveTypedServiceCode(typed);
    if (!resolved.known) {
      return "eBay may not know the shipping code \"" + typed + "\". Pick a service from the list, or check the code.";
    }
    if (isCreateExcludedCode(resolved.code)) return excludedMsg(resolved.code);
    return "";
  }
  if (v && isCreateExcludedCode(v)) return excludedMsg(canonicalServiceCode(v));
  return "";
}

function createServiceCodeFor(prefix, n, form) {
  /* v49: the code is canonicalized here too (the server maps it
     again, so both ends agree): a form carrying USPSGroundAdvantage
     — picked before v49, or typed in Other — builds a body sending
     USPSParcel, never the code eBay rejects on this account. */
  const v = form[prefix + "_service_" + n] || "";
  if (v === "__OTHER__") {
    return canonicalServiceCode(form[prefix + "_service_" + n + "_other"] || "");
  }
  return canonicalServiceCode(v);
}

function createServiceDisplay(prefix, n, form) {
  const code = createServiceCodeFor(prefix, n, form);
  if (!code) return "";
  const list = prefix === "dom" ? createServices.domestic : createServices.international;
  const found = (list || []).find((s) => s.code === code);
  return found ? (found.label || createServiceLabel(code)) : code;
}

function createFlatCostText(form, prefix, n) {
  if (form[prefix + "_cost_type"] === "CALCULATED") return " (calculated)";
  if (prefix === "dom" && n === 1 && form.dom_free_shipping) return " (free shipping)";
  const v = Number(form[prefix + "_cost_" + n]);
  return " ($" + (Number.isFinite(v) ? v.toFixed(2) : form[prefix + "_cost_" + n]) + ")";
}

function createReviewLines(form) {
  /* Plain summary lines for the review screen, in the order the
     policy will be created. Seller-typed text is escaped: it is
     rendered as HTML. */
  const lines = [];
  lines.push("Name: " + esc(form.name));
  if (form.description) lines.push("Description: " + esc(form.description));
  if (form.pickup_only) {
    lines.push("Local pickup only (no shipping).");
    return lines;
  }
  lines.push("US shipping: " + (form.dom_cost_type === "CALCULATED" ? "calculated (by weight)" : "flat rate") + ".");
  lines.push("US service 1: " + esc(createServiceDisplay("dom", 1, form)) + createFlatCostText(form, "dom", 1) + ".");
  if (form.dom_service_2) lines.push("US service 2: " + esc(createServiceDisplay("dom", 2, form)) + createFlatCostText(form, "dom", 2) + ".");
  if (form.local_pickup) lines.push("Also offers local pickup.");
  if (form.ship_international) {
    lines.push("International shipping (worldwide, you ship it yourself): " + (form.intl_cost_type === "CALCULATED" ? "calculated (by weight)" : "flat rate") + ".");
    lines.push("International service 1: " + esc(createServiceDisplay("intl", 1, form)) + createFlatCostText(form, "intl", 1) + ".");
    if (form.intl_service_2) lines.push("International service 2: " + esc(createServiceDisplay("intl", 2, form)) + createFlatCostText(form, "intl", 2) + ".");
  }
  lines.push("Handling time: " + esc(form.handling_days) + " business days.");
  return lines;
}

function showCreateReview() {
  const collected = collectPolicyCreateForm();
  const errEl = $("policy-create-error");
  errEl.classList.add("hidden");
  if (collected.errors.length) {
    errEl.textContent = collected.errors.join(" ");
    errEl.classList.remove("hidden");
    return;
  }
  pendingCreateForm = collected.form;
  createRetry = false;
  $("pc-review-text").innerHTML = createReviewLines(collected.form).map((l) => "<p style='margin:0'>" + l + "</p>").join("");
  const warn = $("pc-review-warning");
  if (collected.form.pickup_only) {
    warn.textContent = "Local pickup only — this policy cannot ship a listing. The publish check will flag it if you try to list with it.";
    warn.classList.remove("hidden");
  } else if (collected.form.dom_cost_type === "CALCULATED"
             && !shippingPackageComplete()) {
    warn.textContent = "Calculated: enter this item's package weight and size under Shipping before listing, or the publish check will stop you.";
    warn.classList.remove("hidden");
  } else {
    warn.classList.add("hidden");
  }
  $("policy-create-form").classList.add("hidden");
  $("policy-create-review").classList.remove("hidden");
}

function cancelCreateForm() {
  /* Cancel discards: no policy created, draft untouched, form cleared.
     v42: when the form was opened from a carrier + service pick, the
     pick is cleared too — a carrier choice with no policy behind it
     must not sit on the listing page looking chosen. */
  pendingCreateForm = null;
  createRetry = false;
  $("policy-create-form").classList.add("hidden");
  $("policy-create-review").classList.add("hidden");
  $("policy-create-error").classList.add("hidden");
  if (createFromCarrierPick) {
    createFromCarrierPick = false;
    $("ship-carrier").value = "";
    populateShipService();
    $("ship-service-note").classList.add("hidden");
    scheduleSave();
  }
  ["pc-name", "pc-description", "pc-dom-service-1-other", "pc-dom-service-2-other",
   "pc-dom-cost-1", "pc-dom-cost-2", "pc-intl-service-1-other", "pc-intl-service-2-other",
   "pc-intl-cost-1", "pc-intl-cost-2", "pc-handling"].forEach((id) => { $(id).value = ""; });
  ["pc-pickup-only", "pc-local-pickup", "pc-international", "pc-dom-free-1"].forEach((id) => { $(id).checked = false; });
  $("pc-dom-cost-type").value = "FLAT_RATE";
  $("pc-intl-cost-type").value = "FLAT_RATE";
  ["pc-dom-service-1", "pc-dom-service-2", "pc-intl-service-1", "pc-intl-service-2"].forEach((id) => { $(id).value = ""; });
  refreshCreateFormVisibility();
  updatePolicyCreateCounts();
}

async function openCreateForm() {
  /* A plain open (the Create button) is not a carrier pick; the
     prefilled path sets the flag again after this returns. */
  createFromCarrierPick = false;
  const errEl = $("policy-create-error");
  errEl.classList.add("hidden");
  $("policy-create-note").classList.add("hidden");
  $("policy-create-review").classList.add("hidden");
  $("policy-create-form").classList.remove("hidden");
  /* v49: the create form lives inside the collapsed Shipping row —
     open the row first, or the form is never seen. */
  openListingRow("row-shipping");
  $("policy-create-form").scrollIntoView();
  updatePolicyCreateCounts();
  if (!createServicesFetched) {
    try {
      const r = await api("/api/ebay/shipping-services");
      if (r.needs_scope_reconnect) {
        errEl.innerHTML = "<p style='margin:0'>" + esc(r.error ||
          "The app needs one new permission to read your shipping policies.") +
          "</p><p class='hint'><a class='primary btnlink' href='/api/ebay/connect'>Connect eBay</a></p>";
        errEl.classList.remove("hidden");
        return;
      }
      /* v50: the create form offers everything except codes held back
         from create (UPSGroundSaver); the matcher keeps the full
         lists. A server without the new keys falls back to the full
         lists, so an old response never breaks the form. */
      createServices = { domestic: r.create_domestic || r.domestic || [],
                         international: r.create_international || r.international || [] };
      createServicesFetched = true;
      populateServiceSelect("pc-dom-service-1", createServices.domestic);
      populateServiceSelect("pc-dom-service-2", createServices.domestic);
      populateServiceSelect("pc-intl-service-1", createServices.international);
      populateServiceSelect("pc-intl-service-2", createServices.international);
    } catch (e) {
      errEl.innerHTML = "<p style='margin:0'>" + esc(e.message) +
        "</p><p class='hint'><button id='btn-pc-services-retry' class='ghost' type='button'>Try again</button></p>";
      errEl.classList.remove("hidden");
      const retry = $("btn-pc-services-retry");
      if (retry) retry.addEventListener("click", () => { createServicesFetched = false; openCreateForm(); });
    }
  }
  refreshCreateFormVisibility();
}

async function confirmCreatePolicy() {
  /* Direct fetch, not api(): api() throws away the response flags
     this flow needs (name_taken, unconfirmed, needs_scope_reconnect).
     The review screen already froze the form, so this sends exactly
     what was confirmed — Back + Review rebuilds it after any edit. */
  if (!pendingCreateForm) return;
  const errEl = $("policy-create-error");
  errEl.classList.add("hidden");
  const btn = $("btn-pc-confirm");
  btn.disabled = true;
  try {
    const body = Object.assign({}, pendingCreateForm, { retry: createRetry });
    const r = await fetch("/api/ebay/fulfillment-policies/create",
                          { method: "POST", body: JSON.stringify(body) });
    const text = await r.text();
    let data = {};
    try { data = text ? JSON.parse(text) : {}; } catch (e) { data = {}; }
    if (!r.ok) {
      if (data.needs_scope_reconnect) {
        errEl.innerHTML = "<p style='margin:0'>" + esc(data.error ||
          "The app needs one new permission to read your shipping policies.") +
          "</p><p class='hint'><a class='primary btnlink' href='/api/ebay/connect'>Connect eBay</a></p>";
        errEl.classList.remove("hidden");
        $("policy-create-review").classList.add("hidden");
        $("policy-create-form").classList.remove("hidden");
        return;
      }
      if (data.name_taken) {
        /* Name taken: back to the form WITH values intact, so he
           renames instead of retyping. */
        errEl.textContent = data.error || "That name is already used on your eBay account. Pick a different name.";
        errEl.classList.remove("hidden");
        $("policy-create-review").classList.add("hidden");
        $("policy-create-form").classList.remove("hidden");
        $("pc-name").focus();
        return;
      }
      if (data.unconfirmed) {
        /* Lost reply: stay on the review, arm the retry flag — the
           next Create tap re-reads by name first, so it reuses a
           lost success instead of duplicating. */
        createRetry = true;
        errEl.textContent = data.error;
        errEl.classList.remove("hidden");
        return;
      }
      throw new Error(data.error || ("Request failed (" + r.status + ")"));
    }
    if (data.needs_scope_reconnect) {
      errEl.innerHTML = "<p style='margin:0'>" + esc(data.error ||
        "The app needs one new permission to read your shipping policies.") +
        "</p><p class='hint'><a class='primary btnlink' href='/api/ebay/connect'>Connect eBay</a></p>";
      errEl.classList.remove("hidden");
      $("policy-create-review").classList.add("hidden");
      $("policy-create-form").classList.remove("hidden");
      return;
    }
    if (!data.policy_id) throw new Error(data.error || "eBay did not return the new policy.");
    shippingPolicies = data.policies || [];
    renderShippingDropdown();
    pendingCreateForm = null;
    createRetry = false;
    createFromCarrierPick = false;
    $("policy-create-review").classList.add("hidden");
    $("policy-create-form").classList.add("hidden");
    /* v43: a select failure after a successful create must never
       report the create itself as failed — the policy exists on
       eBay either way. Say so plainly and let him pick it from the
       usual-policies dropdown below. */
    let selected = false;
    try {
      await selectShippingPolicy(data.policy_id);
      selected = true;
    } catch (e) { selected = false; }
    const note = $("policy-create-note");
    note.textContent = selected ? "Policy created and selected."
      : "Policy created. Pick it from your usual policies below.";
    note.classList.remove("hidden");
  } catch (e) {
    errEl.textContent = e.message;
    errEl.classList.remove("hidden");
    $("policy-create-review").classList.add("hidden");
    $("policy-create-form").classList.remove("hidden");
  } finally {
    btn.disabled = false;
  }
}

$("btn-create-policy").addEventListener("click", openCreateForm);
$("btn-pc-review").addEventListener("click", showCreateReview);
$("btn-pc-confirm").addEventListener("click", confirmCreatePolicy);
$("btn-pc-back").addEventListener("click", () => {
  $("policy-create-review").classList.add("hidden");
  $("policy-create-form").classList.remove("hidden");
  updatePolicyCreateCounts();
});
$("btn-pc-cancel").addEventListener("click", cancelCreateForm);
$("btn-pc-cancel-2").addEventListener("click", cancelCreateForm);
["pc-pickup-only", "pc-local-pickup", "pc-international", "pc-dom-cost-type", "pc-intl-cost-type",
 "pc-dom-service-1", "pc-dom-service-2", "pc-intl-service-1", "pc-intl-service-2"].forEach((id) => {
  $(id).addEventListener("change", refreshCreateFormVisibility);
});
/* v48: live counters on the two capped create-form fields, same
   pattern as the title counter. */
["pc-name", "pc-description"].forEach((id) => {
  $(id).addEventListener("input", updatePolicyCreateCounts);
});

function hidePublishMsg() {
  $("publish-error").classList.add("hidden");
  $("publish-ok").classList.add("hidden");
  /* v34: fresh attempt, so the scope box and chooser pick message
     reset too (the dropdown itself stays — it is always
     visible). */
  $("scope-reconnect-box").classList.add("hidden");
  $("shipping-pick-msg").classList.add("hidden");
  shippingPickMode = false;
}

/* ---------- v2: eBay connect banner ---------- */
(function () {
  const q = new URLSearchParams(window.location.search);
  const el = $("ebay-banner");
  let shown = false;
  // v22: the launch-time auto-fix flag (MainActivity appends it when it
  // replaced the known-bad sign-in name). Brief note on the main screen,
  // then the flag is cleared so it shows exactly once.
  if (q.get("runame_fixed") === "1") {
    el.textContent = "Sign-in name updated to the working one.";
    el.classList.remove("hidden");
    shown = true;
  }
  const flag = q.get("ebay");
  if (flag) {
    if (flag === "connected") {
      el.textContent = "eBay is connected. You can now list straight from the listing page.";
    } else {
      el.textContent = "eBay connection did not finish. Try Connect eBay again in this browser.";
      el.className = "banner";
      el.style.margin = "0.8rem 1rem 0 1rem";
    }
    el.classList.remove("hidden");
    shown = true;
  }
  if (shown) window.history.replaceState({}, "", window.location.pathname);
})();
