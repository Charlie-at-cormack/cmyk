"use strict";
/* Sign-in and first-start setup for a CMYK server install (see cmyk/auth.py). */

const $ = (id) => document.getElementById(id);

async function post(path, body) {
  const r = await fetch(path, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (r.ok) return null;
  try { return (await r.json()).detail || r.statusText; } catch (_) { return r.statusText; }
}

/* collect() returns the request body, or an error message to show instead of sending. */
function wire(formId, errId, collect) {
  const form = $(formId);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = collect();
    if (typeof body === "string") { $(errId).textContent = body; return; }
    const btn = form.querySelector("button");
    btn.disabled = true; $(errId).textContent = "";
    const err = await post(form.dataset.api, body);
    btn.disabled = false;
    if (err) $(errId).textContent = err;
    else location.replace("/");
  });
}

async function init() {
  const s = await (await fetch("/api/auth/state")).json();
  if (!s.server || s.email) { location.replace("/"); return; }
  const form = $(s.setup ? "setup-form" : "login-form");
  form.hidden = false;
  form.querySelector("input").focus();
}

wire("setup-form", "s-err", () => ($("s-pass").value !== $("s-pass2").value
  ? "The two passwords don't match."
  : { code: $("s-code").value, email: $("s-email").value, password: $("s-pass").value }));
wire("login-form", "l-err", () => ({ email: $("l-email").value, password: $("l-pass").value }));
init();
