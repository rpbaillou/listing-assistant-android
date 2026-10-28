/* Listing Assistant v2.8 - front end. Plain steps, no framework. */
let draft = null;
let selectedFiles = [];
let saveTimer = null;
let saveDirty = false; /* v5: true while edits are waiting for the debounced save */
let maxPhotos = 10; /* v8: overwritten by /api/config (eBay allows 24 free) */

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
    const img = document.createElement("img");
    img.src = entry.url;
    img.alt = "Chosen photo " + (i + 1);
    const x = document.createElement("button");
    x.type = "button";
    x.className = "thumb-x";
    x.textContent = "×";
    x.setAttribute("aria-label", "Remove this photo");
    x.addEventListener("click", () => removePhoto(i));
    /* v18: Edit opens the crop/rotate editor for this photo. */
    const e = document.createElement("button");
    e.type = "button";
    e.className = "thumb-edit";
    e.textContent = "Edit";
    e.setAttribute("aria-label", "Edit this photo");
    e.addEventListener("click", () => openEditor(i));
    wrap.appendChild(img);
    wrap.appendChild(x);
    wrap.appendChild(e);
    box.appendChild(wrap);
  });
  for (let s = 0; s < skippedCount; s++) {
    const ph = document.createElement("div");
    ph.className = "thumb-skip";
    ph.textContent = "can't preview";
    box.appendChild(ph);
  }
  updateAnalyzeButton();
}

function removePhoto(i) {
  const entry = selectedFiles[i];
  if (!entry) return;
  if (entry.url) URL.revokeObjectURL(entry.url);
  selectedFiles.splice(i, 1);
  renderThumbs();
  hideError();
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
    closeEditor();
    hideError();
  } catch (e) {
    showError("Couldn't save that edit. Try again.");
  } finally {
    btn.disabled = false;
  }
});

/* Shared photo intake: "Choose photos" replaces the set, "Take photo"
   appends one more (so he can snap several angles in a row). */
async function handlePickedFiles(fileList, replace) {
  const room = replace ? maxPhotos : Math.max(0, maxPhotos - selectedFiles.length);
  const picked = Array.from(fileList || []).slice(0, room);
  if (replace) clearPreviews();
  skippedCount = 0;
  for (let i = 0; i < picked.length; i++) {
    const r = await downscaleFile(picked[i], selectedFiles.length + i);
    if (r.skipped) { skippedCount++; continue; }
    const u = URL.createObjectURL(r.blob);
    selectedFiles.push({ blob: r.blob, name: r.name, url: u });
  }
  renderThumbs();
  if (skippedCount > 0) {
    const n = $("skip-note");
    n.textContent = skippedCount + (skippedCount === 1 ? " photo" : " photos") +
      " couldn't be read and " + (skippedCount === 1 ? "was" : "were") + " skipped.";
    n.classList.remove("hidden");
  }
  hideError();
}

/* v20: update check and Edit keys moved to the app's native top-right
   menu (three dots). The web header is a plain title bar now, so these
   listeners are gone — the native menu calls the same Android handlers. */

$("photo-input").addEventListener("change", (e) => { handlePickedFiles(e.target.files, true); });

/* v14: Take photo calls the camera directly through the app's JS interface.
   v12 used <input capture="camera">, but his phone's WebView ignores the
   capture attribute, so both buttons opened the gallery. This bypasses the
   WebView file picker entirely. */
$("btn-take-photo").addEventListener("click", () => {
  if (window.Android && typeof window.Android.openCamera === "function") {
    window.Android.openCamera();
  } else {
    showError("The camera needs the app — please use Choose photos instead.");
  }
});

/* Called by native code after a camera snap. dataUrl is a natively
   downscaled JPEG (1600px, q85). Appends, so he can snap several angles. */
async function onCameraPhoto(dataUrl) {
  try {
    const blob = await (await fetch(dataUrl)).blob();
    const file = new File([blob], "camera-" + Date.now() + ".jpg", { type: "image/jpeg" });
    handlePickedFiles([file], false);
  } catch (e) {
    showError("Couldn't read that photo.");
  }
}

$("btn-analyze").addEventListener("click", async () => {
  hideError();
  $("spinner").classList.remove("hidden");
  $("btn-analyze").disabled = true;
  try {
    const fd = new FormData();
    selectedFiles.forEach((f) => fd.append("photos", f.blob, f.name));
    const { draft: d } = await api("/api/analyze", { method: "POST", body: fd });
    loadDraftIntoForm(d);
    show("step-draft");
  } catch (err) {
    showError(err.message);
  } finally {
    $("spinner").classList.add("hidden");
    $("btn-analyze").disabled = selectedFiles.length === 0;
  }
});

/* ---------- v24: quick post (skip AI) ---------- */
/* Same photo upload as Identify, but the server starts a BLANK draft —
   no Gemini call, no AI wait. He types the title himself in step 2,
   then the normal Review → step 4 flow takes over. */
$("btn-quick-post").addEventListener("click", async () => {
  hideError();
  $("btn-quick-post").disabled = true;
  try {
    const fd = new FormData();
    selectedFiles.forEach((f) => fd.append("photos", f.blob, f.name));
    const { draft: d } = await api("/api/quick-post", { method: "POST", body: fd });
    loadDraftIntoForm(d);
    show("step-draft");
  } catch (err) {
    showError(err.message);
  } finally {
    $("btn-quick-post").disabled = selectedFiles.length === 0;
  }
});

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
  const box = $("draft-photos");
  box.innerHTML = "";
  (d.photos || []).forEach((p) => {
    const img = document.createElement("img");
    img.src = "/photos/" + p;
    box.appendChild(img);
  });
  renderAttrs(d.attributes || {});
  const notes = $("ai-notes");
  if (d.identification_notes) {
    notes.textContent = "AI notes: " + d.identification_notes;
    notes.classList.remove("hidden");
  } else notes.classList.add("hidden");
  $("btn-photos-zip").href = "/api/drafts/" + d.id + "/photos.zip";
}

function renderAttrs(attrs) {
  const box = $("attrs");
  box.innerHTML = "";
  Object.entries(attrs).forEach(([k, v]) => box.appendChild(attrRow(k, v)));
  if (Object.keys(attrs).length === 0) box.appendChild(attrRow("", ""));
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
  });
  row.querySelectorAll("input").forEach((i) =>
    i.addEventListener("input", scheduleSave));
  return row;
}

$("btn-add-attr").addEventListener("click", () => {
  $("attrs").appendChild(attrRow("", ""));
});

function collectForm() {
  const attrs = {};
  document.querySelectorAll("#attrs .attr-row").forEach((row) => {
    const k = row.querySelector(".ak").value.trim();
    const v = row.querySelector(".av").value.trim();
    if (k) attrs[k] = v;
  });
  return {
    title: $("f-title").value.trim(),
    category: $("f-category").value.trim(),
    description: $("f-description").value.trim(),
    condition: $("f-condition").value,
    condition_details: $("f-condition-details").value.trim(),
    price: $("f-price").value.trim(),
    attributes: attrs,
    /* v9: best offer lives in step 4 (p- ids) but joins the same autosave —
       the elements always exist in the DOM, visible or not. */
    best_offer: $("p-best-offer").checked,
    best_offer_auto_decline: $("p-bo-decline").value.trim(),
    best_offer_auto_accept: $("p-bo-accept").value.trim(),
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
  /* v27: flush a pending panel save too, but only if the panel was
     actually shown this session — never from unopened defaults. */
  if (publishDirty && ebayPanelShown && draft) {
    publishDirty = false;
    clearTimeout(publishTimer);
    publishTimer = null;
    try {
      fetch("/api/drafts/" + draft.id + "/publish-fields", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(collectPublishFields()),
        keepalive: true,
      }).catch(() => {});
    } catch (e) { /* best-effort */ }
  }
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

$("btn-to-review").addEventListener("click", async () => {
  await saveDraft();
  renderReview();
  show("step-review");
});

/* ---------- step 3: review ---------- */
function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function renderReview() {
  const d = Object.assign({}, draft, collectForm());
  let attrs = "";
  Object.entries(d.attributes || {}).forEach(([k, v]) => {
    attrs += "<dt>" + esc(k) + "</dt><dd>" + esc(v) + "</dd>";
  });
  $("review-card").innerHTML =
    "<h3>" + esc(d.title || "(no title)") + "</h3>" +
    "<p><b>Price:</b> " + (d.price ? "$" + esc(d.price) : "(not set)") + "</p>" +
    "<p><b>Category:</b> " + esc(d.category || "(not set)") + "</p>" +
    "<p><b>Condition:</b> " + esc(conditionLabel(d.condition) || "(not set)") + "</p>" +
    (d.condition_details ? "<p><b>Condition details:</b> " + esc(d.condition_details).replace(/\n/g, "<br>") + "</p>" : "") +
    /* v9: show best offer status in the review so it's confirmed before step 4. */
    (d.best_offer ? "<p><b>Best Offer:</b> on" +
      (d.best_offer_auto_decline ? " (auto-decline under $" + esc(d.best_offer_auto_decline) + ")" : "") +
      (d.best_offer_auto_accept ? " (auto-accept $" + esc(d.best_offer_auto_accept) + "+)" : "") +
      "</p>" : "") +
    "<p>" + esc(d.description).replace(/\n/g, "<br>") + "</p>" +
    (attrs ? "<dl>" + attrs + "</dl>" : "") +
    "<p><b>Photos:</b> " + (d.photos || []).length + "</p>";
}

$("btn-back-edit").addEventListener("click", () => show("step-draft"));
$("btn-to-handoff").addEventListener("click", () => { renderEbayStep(); show("step-handoff"); });

/* ---------- step 4: handoff ---------- */
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
    drafts.forEach((d) => {
      const row = document.createElement("div");
      row.className = "draft-item";
      /* v21: buttons live in one .draft-actions unit so they wrap below the
         text together instead of one clipping off the right edge. The date
         renders as two nowrap spans (dateHtml: date, time) so the row can
         break cleanly between them, never mid-string. "Listed on eBay"
         is a separate span so it can wrap to the next line cleanly.
         v24: a tick box (batch posting) and a Post button (straight to
         review) join the row. Listed drafts get no Post button — they are
         already live, and the publish step refuses them anyway. */
      row.innerHTML =
        '<input type="checkbox" class="draft-check" data-id="' + d.id +
        '" aria-label="Select for batch posting">' +
        (d.thumb ? '<img src="/photos/' + d.thumb + '">' : "") +
        '<div class="t"><b>' + esc(d.title || "(untitled)") + "</b>" + dateHtml(d.updated) +
        (d.listed ? ' <span class="d-listed">· Listed on eBay</span>' : "") + "</div>" +
        '<div class="draft-actions"><button type="button" class="secondary">Open</button>' +
        (d.listed ? "" : '<button type="button" class="primary post">Post</button>') +
        '<button type="button" class="del">Delete</button></div>';
      row.querySelector(".secondary").addEventListener("click", async () => {
        const { draft: full } = await api("/api/drafts/" + d.id);
        loadDraftIntoForm(full);
        show("step-draft");
      });
      const postBtn = row.querySelector(".post");
      if (postBtn) {
        postBtn.addEventListener("click", async () => {
          /* v24: straight to the Review screen — photos, title, price and
             everything else come from the draft, no re-picking, no AI.
             Nothing goes live from here: Review → step 4 → List button
             still stand between him and a live listing. */
          const { draft: full } = await api("/api/drafts/" + d.id);
          loadDraftIntoForm(full);
          renderReview();
          const notes = [];
          if (!String(full.price || "").trim())
            notes.push("Price is not set — add it before listing.");
          if (!String(full.category || "").trim())
            notes.push("Category is not set — pick one with Suggest in step 4.");
          if (notes.length) {
            const card = $("review-card");
            const note = document.createElement("div");
            note.className = "note";
            note.textContent = notes.join(" ");
            card.insertBefore(note, card.firstChild);
          }
          show("step-review");
        });
      }
      row.querySelector(".del").addEventListener("click", async () => {
        if (!confirm("Delete this draft and its photos?")) return;
        await api("/api/drafts/" + d.id, { method: "DELETE" });
        $("btn-drafts").click();
      });
      list.appendChild(row);
    });
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
   (drafts never store a category number, so it is picked here). */
let batchIncluded = [];

function updateBatchConfirm() {
  $("btn-batch-confirm").disabled =
    batchIncluded.length === 0 || !$("b-category").value.trim();
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
  if (batchIncluded.length) {
    const h = document.createElement("h4");
    h.textContent = "Ready to post (" + batchIncluded.length + ")";
    list.appendChild(h);
    batchIncluded.forEach((d) => {
      const row = document.createElement("div");
      row.className = "batch-row";
      row.innerHTML = '<div class="bt">' + esc(d.title || "(untitled)") + "</div>" +
        '<div class="bp">$' + esc(String(d.price)) + "</div>";
      list.appendChild(row);
    });
  } else {
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

$("btn-batch-confirm").addEventListener("click", async () => {
  const cid = $("b-category").value.trim();
  if (!cid || !batchIncluded.length) return;
  if (!confirm("Post " + batchIncluded.length + " draft(s) to eBay now? Each becomes a real, live listing.")) return;
  $("btn-batch-confirm").disabled = true;
  $("btn-batch-cancel").disabled = true;
  $("batch-progress").classList.remove("hidden");
  $("batch-results").innerHTML = "";
  try {
    const { results } = await api("/api/ebay/batch-publish", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        draft_ids: batchIncluded.map((d) => d.id),
        category_id: cid,
      }),
    });
    const box = $("batch-results");
    box.innerHTML = "<h4>Results</h4>";
    (results || []).forEach((r) => {
      const row = document.createElement("div");
      row.className = "batch-row";
      if (r.ok) {
        row.innerHTML = '<div class="bt">' + esc(r.title) + "</div>" +
          '<div class="bp"><a href="' + esc(r.listing_url) + '" target="_blank">Live</a></div>';
      } else {
        row.innerHTML = '<div class="bt">' + esc(r.title) + "</div>" +
          '<div class="bp batch-skip">Failed: ' + esc(r.error || "unknown error") + "</div>";
      }
      box.appendChild(row);
    });
  } catch (e) {
    $("batch-results").innerHTML = "<p class='error'>" + esc(e.message) + "</p>";
  } finally {
    $("batch-progress").classList.add("hidden");
    $("btn-batch-confirm").classList.add("hidden");
    $("btn-batch-cancel").classList.add("hidden");
    $("btn-batch-done").classList.remove("hidden");
  }
});

/* ---------- init ---------- */
api("/api/config").then((c) => {
  if (!c.gemini_configured) $("key-warning").classList.remove("hidden");
  if (c.max_photos) {
    maxPhotos = c.max_photos;
    document.querySelector("#step-photos .hint").textContent =
      "Choose up to " + c.max_photos + " photos of one item from your photo library.";
  }
}).catch(() => {});

/* ---------- v2: eBay one-tap listing ---------- */
let ebayStatus = null;

// v22: the OAuth-enabled sign-in name eBay accepts. A RuName is a public
// identifier (it appears in every authorize URL), not a secret — showing
// it and comparing against it here is safe. The Client Secret never
// appears in this file.
const WORKING_RUNAME = "Reynard_Baillou-ReynardB-Mr-PRD-eayxussz";

/* v27: pure publish-panel prefill. Precedence is draft first, then the
   "usual settings" preset, then .env defaults — so a category number or
   policy number saved into the draft survives an app restart or update,
   and the preset only fills fields the draft lacks (the "applied" list
   names exactly what the preset filled, never what the draft supplied).
   form carries the step-2 DOM values (condition, price): a keystroke
   typed moments before opening step 4 is newer than the last autosave,
   so it wins over the draft for those two fields. Pure on purpose:
   node tests pin this precedence without a DOM. */
function panelPrefill(draftObj, preset, defaults, form) {
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

/* v27: publish-panel persistence state. pickedCat tracks the last
   Suggest pick (or restored saved pair) so the display name survives
   re-saves when the number is untouched; the name is only sent when the
   input still equals the picked number, so a hand-typed number never
   inherits a stale name. ebayPanelShown guards the page-hide flush:
   panel fields must only ever save after the panel has actually been
   shown in this session, never from their unopened defaults. */
let pickedCat = { id: "", name: "" };
let ebayPanelShown = false;
let publishDirty = false;
let publishTimer = null;

function collectPublishFields() {
  const cid = $("p-category").value.trim();
  return {
    category_id: cid,
    category_name: cid && cid === pickedCat.id ? pickedCat.name : "",
    condition: $("p-condition").value,
    price: $("p-price").value.trim(),
    fulfillment_policy_id: $("p-fulfillment").value.trim(),
    payment_policy_id: $("p-payment").value.trim(),
    return_policy_id: $("p-return").value.trim(),
    best_offer: $("p-best-offer").checked,
    best_offer_auto_decline: $("p-bo-decline").value.trim(),
    best_offer_auto_accept: $("p-bo-accept").value.trim(),
    attributes: collectAspects(),
  };
}

async function savePublishFields() {
  if (!draft || !ebayPanelShown) return;
  clearTimeout(publishTimer);
  publishTimer = null;
  try {
    const { draft: d } = await api("/api/drafts/" + draft.id + "/publish-fields", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(collectPublishFields()),
    });
    draft = d;
    publishDirty = false;
    /* Keep the step-2 form in sync so a later step-2 autosave cannot
       resurrect the pre-panel condition or price. */
    const fc = $("f-condition");
    if (fc &&
        Array.from(fc.options || []).some((o) => o.value === $("p-condition").value)) {
      fc.value = $("p-condition").value;
    }
    if ($("f-price")) $("f-price").value = $("p-price").value;
  } catch (e) { /* panel saves are best-effort; List still sends everything */ }
}

function schedulePublishSave() {
  publishDirty = true;
  clearTimeout(publishTimer);
  publishTimer = setTimeout(savePublishFields, 1200);
}

async function renderEbayStep() {
  const box = $("ebay-status");
  $("publish-panel").classList.add("hidden");
  $("publish-error").classList.add("hidden");
  $("publish-ok").classList.add("hidden");
  try {
    ebayStatus = await api("/api/ebay/status");
  } catch (e) {
    box.innerHTML = "<p class='hint' style='margin:0'>Could not check eBay status.</p>";
    return;
  }
  if (!ebayStatus.ebay_configured) {
    box.innerHTML = "<p style='margin:0'><b>eBay is not set up yet.</b></p>" +
      "<p class='hint'>Add your eBay App ID and Cert ID to the .env file " +
      "(setup guide: Connect eBay), then restart the app. The manual copy buttons below still work.</p>";
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
    return;
  }
  if (draft && draft.ebay_listing_id) {
    box.innerHTML = "<p style='margin:0'><b>This draft is already live on eBay.</b></p>" +
      "<p class='hint'><a href='" + draft.ebay_listing_url + "' target='_blank'>View your listing</a>" +
      " · item " + esc(draft.ebay_listing_id) + "</p>";
    return;
  }
  const missing = [];
  if (!ebayStatus.policies_configured) missing.push("shop policy numbers");
  if (missing.length) {
    box.innerHTML = "<p style='margin:0'><b>eBay is connected.</b> Still needed: " +
      esc(missing.join(" and ")) + ". See the setup guide (Connect eBay).</p>";
    return;
  }
  box.innerHTML = "<p style='margin:0'><b>eBay connected.</b> Ready to list.</p>" +
    "<p class='hint runame-line'>Sign-in name in use: " + esc((ebayStatus.runame || "").trim()) + "</p>";
  // prefill the publish panel
  const sel = $("p-condition");
  sel.innerHTML = "";
  const pick = document.createElement("option");
  pick.value = ""; pick.textContent = "— Pick one —";
  sel.appendChild(pick);
  (ebayStatus.conditions || []).forEach((c) => {
    const o = document.createElement("option");
    o.value = c.value; o.textContent = c.label;
    sel.appendChild(o);
  });
  // v27: draft first, preset second, .env defaults last (panelPrefill).
  // A category number saved into the draft is restored here, so List
  // works straight away after an update or restart — no re-pick.
  const cur = collectForm();
  let preset = null;
  try {
    const r = await api("/api/preset");
    preset = r.preset;
  } catch (e) { preset = null; }
  const pre = panelPrefill(draft || {}, preset, ebayStatus.policy_defaults || {},
    { condition: cur.condition, price: $("f-price").value || "" });
  sel.value = pre.condition;
  $("condition-reminder").classList.toggle("hidden", !!pre.condition);
  $("p-price").value = pre.price;
  $("p-best-offer").checked = pre.best_offer;
  $("p-bo-decline").value = pre.best_offer_auto_decline;
  $("p-bo-accept").value = pre.best_offer_auto_accept;
  $("best-offer-fields").classList.toggle("hidden", !pre.best_offer);
  $("p-category").value = pre.category_id;
  $("cat-suggestions").classList.add("hidden");
  $("p-fulfillment").value = pre.fulfillment;
  $("p-payment").value = pre.payment;
  $("p-return").value = pre.return;
  /* v27: remember the restored category pair so later panel saves keep
     its display name when the number is untouched. */
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
  ebayPanelShown = true;
  publishDirty = false;
  $("publish-panel").classList.remove("hidden");
  /* v27: a restored category number also restores its required-details
     section (prefilled from the draft's saved specifics). */
  if (pre.category_id) loadCategoryAspects();
}

/* v24: save the current publish-panel values as the one usual-settings
   preset. Re-saving overwrites it. The note confirms what was saved. */
$("btn-save-preset").addEventListener("click", async () => {
  const note = $("preset-note");
  try {
    await api("/api/preset", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        condition: $("p-condition").value,
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
      b.addEventListener("click", () => {
        $("p-category").value = s.id;
        pickedCat = { id: s.id, name: s.name };
        const cs = $("cat-saved");
        cs.textContent = "Saved category: " + s.name;
        cs.classList.remove("hidden");
        box.classList.add("hidden");
        loadCategoryAspects(); /* v8: fetch what eBay requires for this category */
        savePublishFields(); /* v27: a Suggest pick saves immediately */
      });
      box.appendChild(b);
    });
  } catch (e) {
    box.innerHTML = "<p class='error'>" + esc(e.message) + "</p>";
  }
});

/* v8: also check when a category number is typed in by hand. */
/* v27: a hand-edited number hides the saved-name line (the name belongs
   to the old number); the save itself only sends a name when the input
   still equals the picked number, so a typed number never inherits one. */
function onCategoryEdited() {
  if ($("p-category").value.trim() !== pickedCat.id) {
    $("cat-saved").classList.add("hidden");
  }
  schedulePublishSave();
}
$("p-category").addEventListener("change", () => {
  onCategoryEdited();
  loadCategoryAspects();
});
$("p-category").addEventListener("input", onCategoryEdited);

/* v27: every other panel field persists into the draft on change. */
$("p-condition").addEventListener("change", schedulePublishSave);
$("p-price").addEventListener("input", schedulePublishSave);
["p-fulfillment", "p-payment", "p-return"].forEach((id) => {
  $(id).addEventListener("input", schedulePublishSave);
});
$("required-aspects").addEventListener("input", schedulePublishSave);

/* v9: best offer toggle shows/hides the auto-price fields; all three join
   the draft autosave (checkbox fires "change", prices fire "input"). */
$("p-best-offer").addEventListener("change", () => {
  $("best-offer-fields").classList.toggle("hidden", !$("p-best-offer").checked);
  scheduleSave();
  schedulePublishSave();
});
["p-bo-decline", "p-bo-accept"].forEach((id) => {
  $(id).addEventListener("input", () => { scheduleSave(); schedulePublishSave(); });
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
  /* v27: persist the panel first, fire-and-forget — List never waits
     for the save (its payload already carries every panel value). */
  savePublishFields();
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
                               parseFloat($("p-price").value));
  if (boErr) {
    const el = $("publish-error");
    el.textContent = boErr;
    el.classList.remove("hidden");
    return;
  }
  if (!confirm("Create a REAL live eBay listing for this item now?")) return;
  $("publish-spinner").classList.remove("hidden");
  $("btn-publish").disabled = true;
  try {
    const body = {
      draft_id: draft.id,
      title: $("f-title").value.trim(),
      description: $("f-description").value.trim(),
      price: $("p-price").value.trim(),
      condition: $("p-condition").value,
      condition_details: $("f-condition-details").value.trim(),
      category_id: $("p-category").value.trim(),
      fulfillment_policy_id: $("p-fulfillment").value.trim(),
      payment_policy_id: $("p-payment").value.trim(),
      return_policy_id: $("p-return").value.trim(),
      /* v8: step-4 aspect fields overlay the draft's attributes server-side. */
      attributes: Object.assign({}, draft.attributes || {}, collectAspects()),
      /* v9: best offer settings. */
      best_offer: $("p-best-offer").checked,
      best_offer_auto_decline: $("p-bo-decline").value.trim(),
      best_offer_auto_accept: $("p-bo-accept").value.trim(),
    };
    const r = await api("/api/ebay/publish", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    draft.ebay_listing_id = r.listing_id;
    draft.ebay_listing_url = r.listing_url;
    const ok = $("publish-ok");
    ok.innerHTML = "It is live! <a href='" + r.listing_url + "' target='_blank'>View your eBay listing</a>" +
      "<br>Item number: " + esc(r.listing_id);
    ok.classList.remove("hidden");
    renderEbayStep();
  } catch (e) {
    const el = $("publish-error");
    el.textContent = e.message;
    el.classList.remove("hidden");
  } finally {
    $("publish-spinner").classList.add("hidden");
    $("btn-publish").disabled = false;
  }
});

function hidePublishMsg() {
  $("publish-error").classList.add("hidden");
  $("publish-ok").classList.add("hidden");
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
      el.textContent = "eBay is connected. You can now list straight from step 4.";
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
