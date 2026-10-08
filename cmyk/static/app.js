"use strict";
/* CMYK front end. All text from PDFs is inserted with textContent (never innerHTML). */

const $ = (id) => document.getElementById(id);
const SVGNS = "http://www.w3.org/2000/svg";
const MM = 72 / 25.4;
const ICON = { pass: "✓", warning: "!", fail: "✕", review: "?", unchecked: "–", info: "i" };
const LABEL = { pass: "Pass", warning: "Warning", fail: "Fail", review: "Needs review", info: "Info" };
const STATES = { open: "Not reviewed", confirmed: "Confirmed", intentional: "Intentional", ignored: "Ignored" };

const S = {
  job: null, profiles: [], profileId: null, overrides: {},
  page: 1, zoom: 1, stageFilter: null, selected: null, tool: "select", poll: null, saveTimer: null,
};

function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v === true) el.setAttribute(k, "");
    else if (v !== false && v != null) el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) if (kid != null) el.append(kid.nodeType ? kid : document.createTextNode(kid));
  return el;
}
function svg(tag, attrs = {}) {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
}
const toLogin = () => location.replace("/login.html");  // server install: session ended

/* CMYK pill meter: a 0..1 fraction fills C, then M, Y and K. */
function setPills(el, frac) {
  el.querySelectorAll("i").forEach((p, i) => p.style.setProperty("--f", Math.min(Math.max(frac * 4 - i, 0), 1)));
}

/* Welcome screen: stays until the app is ready, and for at least one pill cycle on a session's first visit. */
const SPLASH_MIN = (() => {
  try {
    if (sessionStorage.getItem("cmyk-welcomed")) return 0;
    sessionStorage.setItem("cmyk-welcomed", "1");
  } catch (_) {}
  return 1800;
})();
const splashStart = performance.now();
function hideSplash() {
  setTimeout(() => {
    const s = $("splash"); if (!s) return;
    s.classList.add("done");
    setTimeout(() => s.remove(), 600);
  }, Math.max(0, SPLASH_MIN - (performance.now() - splashStart)));
}

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  if (r.status === 401) { toLogin(); return new Promise(() => {}); }
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (_) {}
    throw new Error(msg);
  }
  return r.json();
}

/* ---------- start-up ---------- */
async function init() {
  const who = await api("/api/auth/state");
  if (who.server) {
    const label = who.name || who.email || "";
    $("who-name").textContent = label;
    $("who-sub").textContent = who.role === "admin" ? "Admin" : "Member";
    $("who").title = who.email || "";
    $("avatar").textContent = label.trim().charAt(0).toUpperCase();
    $("sb-local").hidden = true;
    $("sb-nav").hidden = $("avatar").hidden = $("who").hidden = $("btn-logout").hidden = false;
    $("btn-logout").addEventListener("click", async () => {
      await fetch("/api/auth/logout", { method: "POST" }); toLogin();
    });
    $("privacy").textContent = "Files are uploaded to this private CMYK server. Close the job when you're done to delete it.";
  }
  const p = await api("/api/profiles");
  S.profiles = p.profiles; S.profileId = p.default;
  const sel = $("profile");
  p.profiles.forEach((x) => sel.append(h("option", { value: x.id }, x.name)));
  sel.value = S.profileId;
  sel.addEventListener("change", () => {
    S.profileId = sel.value; S.overrides = {};
    if (S.job) runCheck();
  });

  const box = $("dropbox");
  ["dragenter", "dragover"].forEach((e) => box.addEventListener(e, (ev) => { ev.preventDefault(); box.classList.add("over"); }));
  ["dragleave", "drop"].forEach((e) => box.addEventListener(e, (ev) => { ev.preventDefault(); box.classList.remove("over"); }));
  box.addEventListener("drop", (ev) => { const f = ev.dataTransfer.files[0]; if (f) upload(f); });
  $("file").addEventListener("change", (ev) => { if (ev.target.files[0]) upload(ev.target.files[0]); });

  $("btn-new").addEventListener("click", closeJob);
  $("pg-prev").addEventListener("click", () => gotoPage(S.page - 1));
  $("pg-next").addEventListener("click", () => gotoPage(S.page + 1));
  $("z-in").addEventListener("click", () => setZoom(S.zoom * 1.25));
  $("z-out").addEventListener("click", () => setZoom(S.zoom / 1.25));
  $("show-guides").addEventListener("change", renderOverlay);
  $("show-issues").addEventListener("change", renderOverlay);
  $("tool-marker").addEventListener("click", () => {
    S.tool = S.tool === "marker" ? "select" : "marker";
    $("tool-marker").classList.toggle("active", S.tool === "marker");
    $("pagewrap").classList.toggle("drawing", S.tool === "marker");
  });
  $("btn-next-issue").addEventListener("click", reviewNext);
  $("btn-settings").addEventListener("click", openSettings);
  $("set-close").addEventListener("click", () => ($("settings").hidden = true));
  $("set-apply").addEventListener("click", applySettings);
  $("reviewer").addEventListener("input", (e) => { rev().reviewer = e.target.value; queueSave(); });
  $("overall").addEventListener("input", (e) => { rev().comment = e.target.value; queueSave(); });
  $("btn-sign").addEventListener("click", () => setOutcome("signed_off"));
  $("btn-changes").addEventListener("click", () => setOutcome("changes_required"));
  document.addEventListener("keydown", (e) => {
    if (/INPUT|TEXTAREA|SELECT/.test(e.target.tagName)) return;
    if (e.key === "ArrowRight") gotoPage(S.page + 1);
    if (e.key === "ArrowLeft") gotoPage(S.page - 1);
    if (e.key === "Escape") { S.selected = null; renderAll(); }
  });
}

/* ---------- upload / run ---------- */
function upload(file) {
  $("uperr").textContent = "";
  const bar = $("upbar"); bar.hidden = false; setPills(bar, 0);
  const fd = new FormData(); fd.append("file", file);
  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/jobs");
  xhr.upload.onprogress = (e) => { if (e.lengthComputable) setPills(bar, e.loaded / e.total); };
  xhr.onload = () => {
    bar.hidden = true;
    if (xhr.status === 401) { toLogin(); return; }
    if (xhr.status !== 200) {
      let m = "Upload failed"; try { m = JSON.parse(xhr.responseText).detail; } catch (_) {}
      $("uperr").textContent = m; return;
    }
    S.job = JSON.parse(xhr.responseText); S.page = 1; S.selected = null; S.stageFilter = null;
    $("drop").hidden = true; $("work").hidden = $("sb-job").hidden = false;
    $("btn-new").hidden = false; $("btn-settings").disabled = false;
    runCheck();
  };
  xhr.onerror = () => { bar.hidden = true; $("uperr").textContent = "Upload failed"; };
  xhr.send(fd);
}
async function runCheck() {
  $("progress").hidden = false; setPills($("progbar"), 0);
  S.job = await api(`/api/jobs/${S.job.id}/check`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ profile_id: S.profileId, overrides: S.overrides }),
  });
  clearInterval(S.poll);
  S.poll = setInterval(pollJob, 400);
  renderAll();
}
async function pollJob() {
  const j = await api(`/api/jobs/${S.job.id}`);
  const p = j.progress || { page: 0, total: 0 };
  setPills($("progbar"), p.total ? p.page / p.total : 0);
  $("proglabel").textContent = `Analysing page ${p.page} of ${p.total}`;
  if (j.status === "done" || j.status === "error") {
    clearInterval(S.poll); $("progress").hidden = true;
    S.job = j;
    if (j.status === "error") alert("Analysis failed: " + j.error);
    renderAll();
  }
}
async function closeJob() {
  if (S.job) await fetch(`/api/jobs/${S.job.id}`, { method: "DELETE" });
  clearInterval(S.poll);
  S.job = null; S.overrides = {};
  $("work").hidden = $("sb-job").hidden = true; $("drop").hidden = false;
  $("btn-new").hidden = true; $("btn-settings").disabled = true;
  $("fileinfo").textContent = ""; $("file").value = "";
}

/* ---------- review state ---------- */
const rev = () => S.job.review;
const res = () => S.job.result;
function queueSave() {
  clearTimeout(S.saveTimer);
  S.saveTimer = setTimeout(saveReview, 350);
}
async function saveReview() {
  try {
    const j = await api(`/api/jobs/${S.job.id}/review`, {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(rev()),
    });
    S.job.blockers = j.blockers; S.job.review.outcome_at = j.review.outcome_at;
    renderSignoff();
  } catch (e) { alert(e.message); }
}
function issueState(id) { return (rev().issues[id] || {}).state || "open"; }
function setIssue(id, patch) {
  rev().issues[id] = { ...(rev().issues[id] || { state: "open", note: "" }), ...patch };
  recomputeBlockers(); renderAll(); queueSave();
}
function recomputeBlockers() { /* server is authoritative; this just keeps the UI snappy */
  const b = [];
  let open = 0, fails = 0;
  (res()?.issues || []).forEach((i) => {
    if (i.severity === "info") return;
    const st = issueState(i.id);
    if (st === "open") open++; else if (st === "confirmed" && i.severity === "fail") fails++;
  });
  if (open) b.push(`${open} finding(s) not reviewed yet.`);
  if (fails) b.push(`${fails} confirmed failure(s) still unresolved.`);
  (res()?.profile.manual_checks || []).forEach((m) => {
    if ((rev().manual[m.id] || {}).status !== "correct") b.push(`Manual check not marked correct: ${m.label}`);
  });
  S.job.blockers = b;
}
async function setOutcome(o) {
  rev().outcome = o;
  try { await saveReview(); } catch (_) {}
  const j = await api(`/api/jobs/${S.job.id}`); S.job = j; renderAll();
  if (o !== "draft" && j.review.outcome === o) window.open(`/api/jobs/${S.job.id}/report.html`, "_blank");
}

/* ---------- rendering ---------- */
function renderAll() {
  if (!S.job) return;
  const m = S.job.meta;
  $("fileinfo").textContent = `${m.filename} · ${m.pages} ${m.pages === 1 ? "page" : "pages"} · ${m.size_mb.toFixed(1)} MB`;
  renderStages(); renderManual(); renderIssueList(); renderDetail(); renderSignoff(); renderPreview();
}
function stageStatusOf(s) {
  // Marker-only or manual states do not change automatic stage status
  return s.status;
}
function renderStages() {
  const ul = $("stages"); ul.replaceChildren();
  const r = res(); const sum = $("summary"); sum.replaceChildren();
  if (!r) { ul.append(h("li", {}, "Waiting for analysis…")); return; }
  const cnt = { pass: 0, warning: 0, fail: 0, review: 0 };
  r.stages.forEach((s) => cnt[s.status]++);
  Object.entries(cnt).forEach(([k, v]) => { if (v) sum.append(h("span", { class: "pill " + k }, `${v} ${LABEL[k].toLowerCase()}`)); });
  r.stages.forEach((s) => {
    const n = s.counts.fail + s.counts.warning + s.counts.review;
    ul.append(h("li", {
      class: S.stageFilter === s.id ? "sel" : "", tabindex: 0, role: "button",
      onclick: () => { S.stageFilter = S.stageFilter === s.id ? null : s.id; renderAll(); },
      onkeydown: (e) => { if (e.key === "Enter") e.currentTarget.click(); },
    },
      h("span", { class: "light " + s.status, title: LABEL[s.status] }, ICON[s.status]),
      h("span", { class: "name" }, s.label),
      h("span", { class: "count" }, n ? String(n) : "")));
  });
}
function renderManual() {
  const list = res()?.profile.manual_checks || [];
  $("manual-sec").hidden = !list.length;
  const ul = $("manual"); ul.replaceChildren();
  list.forEach((mc) => {
    const st = rev().manual[mc.id] || { status: "unchecked", comment: "" };
    const set = (patch) => { rev().manual[mc.id] = { ...st, ...patch, by: rev().reviewer, at: new Date().toISOString() }; recomputeBlockers(); renderAll(); queueSave(); };
    const light = st.status === "correct" ? "pass" : st.status === "incorrect" ? "fail" : "unchecked";
    const ta = h("textarea", { rows: 2, placeholder: st.status === "incorrect" ? "Comment (required)" : "Comment (optional)" });
    ta.value = st.comment || "";
    ta.addEventListener("input", () => { rev().manual[mc.id] = { ...(rev().manual[mc.id] || st), comment: ta.value }; queueSave(); });
    ul.append(h("li", { class: "mc" },
      h("div", { class: "row" }, h("span", { class: "light " + light }, ICON[light]), h("span", { class: "name" }, mc.label)),
      h("div", { class: "seg" },
        h("button", { class: st.status === "correct" ? "on-correct" : "", onclick: () => set({ status: "correct" }) }, "Correct"),
        h("button", { class: st.status === "incorrect" ? "on-incorrect" : "", onclick: () => set({ status: "incorrect" }) }, "Incorrect")),
      ta));
  });
}
function visibleIssues() {
  const r = res(); if (!r) return [];
  return r.issues.filter((i) => !S.stageFilter || i.stage === S.stageFilter);
}
function renderIssueList() {
  const ul = $("issues"); ul.replaceChildren();
  const stage = res()?.stages.find((s) => s.id === S.stageFilter);
  $("list-title").textContent = stage ? `Findings – ${stage.label}` : "Findings";
  const items = visibleIssues();
  const markers = rev().markers.filter((m) => !S.stageFilter);
  if (!items.length && !markers.length) ul.append(h("li", { class: "muted" }, res() ? "Nothing to report here." : ""));
  items.forEach((i) => {
    const st = issueState(i.id);
    ul.append(h("li", {
      class: `${i.severity} ${S.selected === i.id ? "sel" : ""} ${st !== "open" && i.severity !== "info" ? "done" : ""}`,
      onclick: () => select(i.id),
    },
      h("div", { class: "t" }, i.title),
      h("div", { class: "s" }, `${LABEL[i.severity]}${i.page ? " · page " + i.page : ""}${i.measured ? " · " + i.measured : ""}`),
      i.severity === "info" ? null : h("div", { class: "state" }, STATES[st])));
  });
  markers.forEach((m, idx) => ul.append(h("li", {
    class: `marker ${S.selected === m.id ? "sel" : ""}`, onclick: () => select(m.id),
  }, h("div", { class: "t" }, `Marker ${idx + 1}`), h("div", { class: "s" }, `Page ${m.page}${m.comment ? " · " + m.comment.slice(0, 50) : ""}`))));
}
function findSelected() {
  const i = res()?.issues.find((x) => x.id === S.selected);
  if (i) return { kind: "issue", item: i };
  const m = rev().markers.find((x) => x.id === S.selected);
  return m ? { kind: "marker", item: m } : null;
}
function select(id) {
  S.selected = id;
  const f = findSelected();
  if (f && f.item.page && f.item.page !== S.page) S.page = f.item.page;
  renderAll();
  if (f && f.item.bbox) scrollToBox(f.item.bbox);
}
function renderDetail() {
  const box = $("detail"); box.replaceChildren();
  const f = findSelected();
  if (!f) return;
  const it = f.item;
  if (f.kind === "marker") {
    const ta = h("textarea", { rows: 3, placeholder: "What should be changed here?" }); ta.value = it.comment || "";
    ta.addEventListener("input", () => { it.comment = ta.value; queueSave(); });
    box.append(h("h3", {}, "Reviewer marker"), h("div", { class: "muted small" }, `Page ${it.page}`), ta,
      h("div", { class: "btnrow" }, h("button", { onclick: () => { rev().markers = rev().markers.filter((m) => m.id !== it.id); S.selected = null; renderAll(); queueSave(); } }, "Delete marker")));
    return;
  }
  const st = rev().issues[it.id] || { state: "open", note: "" };
  const kv = h("dl", { class: "kv" });
  [["Severity", LABEL[it.severity]], ["Page", it.page || "–"], ["Measured", it.measured], ["Expected", it.expected], ["Detail", it.detail]]
    .forEach(([k, v]) => { if (v) kv.append(h("dt", {}, k), h("dd", {}, String(v))); });
  const note = h("textarea", { rows: 2, placeholder: "Reviewer note" }); note.value = st.note || "";
  note.addEventListener("input", () => { rev().issues[it.id] = { ...(rev().issues[it.id] || { state: "open" }), note: note.value }; queueSave(); });
  box.append(h("h3", {}, it.title), kv);
  if (it.severity !== "info") {
    box.append(h("div", { class: "btnrow" },
      h("button", { class: st.state === "confirmed" ? "active" : "", onclick: () => setIssue(it.id, { state: "confirmed" }) }, "Confirm issue"),
      h("button", { class: st.state === "intentional" ? "active" : "", onclick: () => setIssue(it.id, { state: "intentional" }) }, "Intentional"),
      h("button", { class: st.state === "ignored" ? "active" : "", onclick: () => setIssue(it.id, { state: "ignored" }) }, "Ignore")), h("div", { style: "margin-top:8px" }, note));
  }
}
function renderSignoff() {
  if (!S.job?.result) return;
  const r = rev();
  if (document.activeElement !== $("reviewer")) $("reviewer").value = r.reviewer || "";
  if (document.activeElement !== $("overall")) $("overall").value = r.comment || "";
  const ul = $("blockers"); ul.replaceChildren();
  (S.job.blockers || []).forEach((b) => ul.append(h("li", {}, b)));
  $("btn-sign").disabled = (S.job.blockers || []).length > 0;
  $("btn-report").href = `/api/jobs/${S.job.id}/report.html`;
  $("btn-json").href = `/api/jobs/${S.job.id}/report.json`;
  const o = r.outcome;
  $("outcome-line").textContent = o === "draft" ? "" :
    `${o === "signed_off" ? "Signed off" : "Changes required"}${r.outcome_at ? " · " + r.outcome_at : ""}`;
}

/* ---------- preview ---------- */
function pageInfo() { return res()?.pages[S.page - 1]; }
function gotoPage(n) {
  const max = S.job?.meta.pages || 1;
  S.page = Math.min(Math.max(1, n), max); renderPreview();
}
function setZoom(z) { S.zoom = Math.min(Math.max(z, 0.4), 6); renderPreview(); }
function renderPreview() {
  const m = S.job.meta;
  $("pg-label").textContent = `Page ${S.page} / ${m.pages}`;
  $("z-label").textContent = Math.round(S.zoom * 100) + "%";
  const wrap = $("pagewrap");
  const pi = pageInfo();
  const ratio = pi ? pi.width / pi.height : 0.707;
  const avail = $("scroller").clientWidth - 32;
  const availH = $("scroller").clientHeight - 32;
  const fitW = Math.min(avail, availH * ratio);
  wrap.style.width = Math.max(200, fitW * S.zoom) + "px";
  const scale = S.zoom > 1.4 ? 4 : 2;
  const src = `/api/jobs/${S.job.id}/pages/${S.page}.png?scale=${scale}`;
  let img = wrap.querySelector("img");
  if (!img) { img = h("img", { alt: "Page preview" }); wrap.prepend(img); }
  if (img.getAttribute("src") !== src) img.setAttribute("src", src);
  renderOverlay();
}
function rectEl(b, cls, extra = {}) {
  return svg("rect", { x: b[0], y: b[1], width: b[2] - b[0], height: b[3] - b[1], class: cls, ...extra });
}
function renderOverlay() {
  const wrap = $("pagewrap"); wrap.querySelector("svg")?.remove();
  const pi = pageInfo(); if (!pi) return;
  const el = svg("svg", { viewBox: `0 0 ${pi.width} ${pi.height}`, preserveAspectRatio: "none" });
  const prof = res().profile;
  if ($("show-guides").checked) {
    if (pi.trim) {
      el.append(rectEl(pi.trim, "gd trim"));
      const bl = pi.bleed || (prof.bleed_mm ? [pi.trim[0] - prof.bleed_mm * MM, pi.trim[1] - prof.bleed_mm * MM, pi.trim[2] + prof.bleed_mm * MM, pi.trim[3] + prof.bleed_mm * MM] : null);
      if (bl) el.append(rectEl(bl, "gd bleed"));
    }
    const t = pi.trim || pi.page, sm = (prof.safe_margin_mm || 0) * MM;
    if (sm) el.append(rectEl([t[0] + sm, t[1] + sm, t[2] - sm, t[3] - sm], "gd safe"));
  }
  if ($("show-issues").checked) {
    visibleIssues().filter((i) => i.page === S.page && i.bbox && i.severity !== "info").forEach((i) => {
      const done = issueState(i.id) !== "open";
      const r = rectEl(i.bbox, `ov ${i.severity} ${S.selected === i.id ? "sel" : ""} ${done ? "done" : ""}`);
      r.append(svg("title")); r.lastChild.textContent = i.title;
      r.addEventListener("click", (e) => { if (S.tool === "select") { e.stopPropagation(); select(i.id); } });
      el.append(r);
    });
    rev().markers.filter((m) => m.page === S.page).forEach((m) => {
      const r = rectEl(m.bbox, `ov marker ${S.selected === m.id ? "sel" : ""}`);
      r.addEventListener("click", (e) => { if (S.tool === "select") { e.stopPropagation(); select(m.id); } });
      el.append(r);
    });
  }
  attachDraw(el, pi);
  wrap.append(el);
}
function attachDraw(el, pi) {
  let start = null, draft = null;
  const pt = (e) => { const r = el.getBoundingClientRect(); return [(e.clientX - r.left) / r.width * pi.width, (e.clientY - r.top) / r.height * pi.height]; };
  el.addEventListener("mousedown", (e) => {
    if (S.tool !== "marker") return;
    e.preventDefault(); start = pt(e);
    draft = svg("rect", { class: "draft", x: start[0], y: start[1], width: 0, height: 0 }); el.append(draft);
  });
  el.addEventListener("mousemove", (e) => {
    if (!start) return;
    const p = pt(e);
    draft.setAttribute("x", Math.min(start[0], p[0])); draft.setAttribute("y", Math.min(start[1], p[1]));
    draft.setAttribute("width", Math.abs(p[0] - start[0])); draft.setAttribute("height", Math.abs(p[1] - start[1]));
  });
  const end = (e) => {
    if (!start) return;
    const p = pt(e); const b = [Math.min(start[0], p[0]), Math.min(start[1], p[1]), Math.max(start[0], p[0]), Math.max(start[1], p[1])];
    start = null; draft?.remove(); draft = null;
    if (b[2] - b[0] < 4 || b[3] - b[1] < 4) return;
    const id = "m" + Date.now().toString(36);
    rev().markers.push({ id, page: S.page, bbox: b.map((v) => Math.round(v * 100) / 100), comment: "" });
    S.tool = "select"; $("tool-marker").classList.remove("active"); $("pagewrap").classList.remove("drawing");
    queueSave(); select(id);
    setTimeout(() => $("detail").querySelector("textarea")?.focus(), 50);
  };
  el.addEventListener("mouseup", end);
  el.addEventListener("mouseleave", (e) => { if (start) end(e); });
}
function scrollToBox(b) {
  const pi = pageInfo(); if (!pi) return;
  requestAnimationFrame(() => {
    const sc = $("scroller"), w = $("pagewrap");
    const k = w.clientWidth / pi.width;
    sc.scrollTo({ left: Math.max(0, w.offsetLeft + (b[0] + b[2]) / 2 * k - sc.clientWidth / 2),
                  top: Math.max(0, w.offsetTop + (b[1] + b[3]) / 2 * k - sc.clientHeight / 2), behavior: "smooth" });
  });
}
function reviewNext() {
  const list = (res()?.issues || []).filter((i) => i.severity !== "info" && issueState(i.id) === "open");
  if (!list.length) { alert("All findings have been reviewed."); return; }
  const idx = list.findIndex((i) => i.id === S.selected);
  select(list[(idx + 1) % list.length].id);
}

/* ---------- profile settings (per-job overrides) ---------- */
const FIELDS = [
  ["bleed_mm", "Required bleed (mm)", "number"], ["safe_margin_mm", "Safe margin from trim (mm)", "number"],
  ["dpi_warn", "Image DPI – warn below", "number"], ["dpi_fail", "Image DPI – fail below", "number"],
  ["max_spot_colours", "Max spot colours (blank = no limit)", "number"],
  ["rgb", "RGB content", ["fail", "warning", "ignore"]], ["overprint", "Overprint", ["fail", "warning", "ignore"]],
  ["fonts_embedded", "Non-embedded fonts", ["fail", "warning", "ignore"]],
  ["crop_marks", "Crop marks", ["required", "forbidden", "ignore"]],
];
function openSettings() {
  const prof = res()?.profile || S.profiles.find((p) => p.id === S.profileId);
  const form = $("set-form"); form.replaceChildren();
  FIELDS.forEach(([key, label, type]) => {
    let input;
    if (Array.isArray(type)) {
      input = h("select", { name: key }, type.map((o) => h("option", { value: o }, o)));
      input.value = prof[key];
    } else {
      input = h("input", { name: key, type: "number", step: "any" });
      input.value = prof[key] ?? "";
    }
    form.append(h("label", { class: "field" }, label, input));
  });
  $("settings").hidden = false;
}
function applySettings() {
  const o = {};
  FIELDS.forEach(([key, , type]) => {
    const v = $("set-form").elements[key].value;
    o[key] = Array.isArray(type) ? v : (v === "" ? null : Number(v));
  });
  S.overrides = o; $("settings").hidden = true; runCheck();
}

window.addEventListener("resize", () => { if (S.job?.result) renderPreview(); });
init().finally(hideSplash);
