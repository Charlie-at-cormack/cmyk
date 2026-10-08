"use strict";
/* Settings: your own password; for admins, users and outgoing email (SMTP). API: /api/auth/*, /api/admin/* in cmyk/app.py. */

const $ = (id) => document.getElementById(id);
const ROLE = { admin: "Admin", user: "Member" };
let ME = null;
let SMTP_READY = false;

function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) if (kid != null) el.append(kid.nodeType ? kid : document.createTextNode(kid));
  return el;
}

async function call(method, path, body) {
  const r = await fetch(path, {
    method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined,
  });
  if (r.status === 401) { location.replace("/login.html"); throw new Error("Signed out"); }
  let data = null;
  try { data = await r.json(); } catch (_) {}
  if (!r.ok) throw new Error((data && data.detail) || r.statusText);
  return data;
}

function say(id, text, ok) {
  const el = $(id); el.textContent = text;
  el.classList.toggle("ok-text", !!ok); el.classList.toggle("err", !ok);
}

/* Same alphabet as the server's generator: no 0/O/o/1/l/I, so passwords read back without mistakes. */
const ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789";
function generatePassword(n = 16) {
  const limit = Math.floor(2 ** 32 / ALPHABET.length) * ALPHABET.length;  // avoid modulo bias
  const out = [];
  while (out.length < n) {
    for (const v of crypto.getRandomValues(new Uint32Array(n))) {
      if (v < limit && out.length < n) out.push(ALPHABET[v % ALPHABET.length]);
    }
  }
  return out.join("");
}

/* One-time view of a generated password, with what happened to the email. */
function showSecret(title, text, res) {
  $("dlg-title").textContent = title;
  $("dlg-text").textContent = text;
  $("dlg-pass").textContent = res.password;
  const mail = $("dlg-mail");
  mail.className = "small";
  if (res.emailed) { mail.textContent = "The login details have been emailed."; mail.classList.add("ok-text"); }
  else if (res.email_error) { mail.textContent = `Not emailed: ${res.email_error} Copy the password and send it yourself.`; mail.classList.add("err"); }
  else { mail.textContent = "Copy the password and send it to them yourself. It won't be shown again."; mail.classList.add("muted"); }
  $("dlg-copy").textContent = "Copy";
  $("pw-dialog").showModal();
}

/* ---------- your account ---------- */
function wireAccount() {
  $("pw-gen").addEventListener("click", () => {
    const p = generatePassword();
    ["pw-new", "pw-new2"].forEach((id) => { $(id).value = p; $(id).type = "text"; });
    say("pw-msg", "Generated. Keep a copy before you save it.", true);
  });
  $("pw-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    if ($("pw-new").value !== $("pw-new2").value) { say("pw-msg", "The two new passwords don't match."); return; }
    try {
      await call("POST", "/api/auth/password", { current: $("pw-current").value, new: $("pw-new").value });
      e.target.reset(); ["pw-new", "pw-new2"].forEach((id) => ($(id).type = "password"));
      say("pw-msg", "Password changed.", true);
    } catch (err) { say("pw-msg", err.message); }
  });
}

/* ---------- users (admins) ---------- */
async function loadUsers() {
  const d = await call("GET", "/api/admin/users");
  SMTP_READY = d.smtp_configured;
  $("add-send").disabled = !SMTP_READY;
  if (!SMTP_READY) $("add-send").checked = false;
  $("add-send-hint").textContent = SMTP_READY ? "" : "(set up email below first)";
  const tb = $("users"); tb.replaceChildren();
  d.users.forEach((u) => {
    const self = u.email === ME.email;
    const role = h("select", { "aria-label": `Role for ${u.email}` },
      h("option", { value: "user" }, ROLE.user), h("option", { value: "admin" }, ROLE.admin));
    role.value = u.role; role.disabled = self;
    role.addEventListener("change", async () => {
      try { await call("PUT", `/api/admin/users/${encodeURIComponent(u.email)}`, { role: role.value }); }
      catch (err) { alert(err.message); }
      loadUsers();
    });
    const actions = h("td", { class: "actions" });
    if (!self) {
      actions.append(h("button", { type: "button", onclick: () => resetUser(u) }, "Reset password"), " ",
        h("button", { type: "button", class: "danger", onclick: () => removeUser(u) }, "Remove"));
    }
    tb.append(h("tr", {},
      h("td", {}, u.name || "–"),
      h("td", {}, u.email, self ? h("span", { class: "you" }, "You") : null),
      h("td", {}, role),
      h("td", { class: "muted" }, u.created || ""),
      actions));
  });
}

async function resetUser(u) {
  const how = SMTP_READY ? " and email it to them" : "";
  if (!confirm(`Generate a new password for ${u.email}${how}? Their current password stops working straight away.`)) return;
  try {
    const res = await call("POST", `/api/admin/users/${encodeURIComponent(u.email)}/reset`, { send_email: SMTP_READY });
    showSecret("New password", `For ${u.email}`, res);
  } catch (err) { alert(err.message); }
}

async function removeUser(u) {
  if (!confirm(`Remove ${u.email}? They are signed out straight away.`)) return;
  try { await call("DELETE", `/api/admin/users/${encodeURIComponent(u.email)}`); loadUsers(); }
  catch (err) { alert(err.message); }
}

function wireUsers() {
  $("add-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    $("add-msg").textContent = "";
    try {
      const res = await call("POST", "/api/admin/users", {
        email: $("add-email").value, name: $("add-name").value, role: $("add-role").value, send_email: $("add-send").checked,
      });
      showSecret("User added", `Login for ${res.user.email}`, res);
      e.target.reset(); loadUsers();
    } catch (err) { say("add-msg", err.message); }
  });
  $("dlg-copy").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText($("dlg-pass").textContent); $("dlg-copy").textContent = "Copied"; }
    catch (_) { getSelection().selectAllChildren($("dlg-pass")); }
  });
  $("dlg-close").addEventListener("click", () => $("pw-dialog").close());
}

/* ---------- email (admins) ---------- */
async function loadSmtp() {
  const s = await call("GET", "/api/admin/smtp");
  $("smtp-host").value = s.host; $("smtp-port").value = s.port; $("smtp-sec").value = s.security;
  $("smtp-user").value = s.username; $("smtp-from").value = s.from_email; $("smtp-from-name").value = s.from_name;
  $("smtp-pass").value = "";
  $("smtp-pass").placeholder = s.password_set ? "Saved. Leave blank to keep it" : "";
  $("smtp-clear-row").hidden = !s.password_set; $("smtp-clear").checked = false;
  const ready = !!(s.host && s.from_email);
  $("smtp-status").textContent = ready ? "Set up" : "Not set up";
  $("smtp-status").className = "pill " + (ready ? "pass" : "warning");
}

function wireSmtp() {
  $("smtp-sec").addEventListener("change", () => {  // keep the usual port for the chosen security
    const port = $("smtp-port");
    if ($("smtp-sec").value === "ssl" && port.value === "587") port.value = "465";
    if ($("smtp-sec").value === "starttls" && port.value === "465") port.value = "587";
  });
  $("smtp-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await call("PUT", "/api/admin/smtp", {
        host: $("smtp-host").value.trim(), port: Number($("smtp-port").value) || 587, security: $("smtp-sec").value,
        username: $("smtp-user").value.trim(), password: $("smtp-pass").value || null,
        clear_password: $("smtp-clear").checked, from_email: $("smtp-from").value.trim(), from_name: $("smtp-from-name").value.trim(),
      });
      say("smtp-msg", "Saved.", true);
      await Promise.all([loadSmtp(), loadUsers()]);
    } catch (err) { say("smtp-msg", err.message); }
  });
  $("test-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const to = $("test-to").value.trim();
    say("test-msg", "Sending…", true);
    try { await call("POST", "/api/admin/smtp/test", { to }); say("test-msg", `Test email sent to ${to}.`, true); }
    catch (err) { say("test-msg", err.message); }
  });
}

/* ---------- start-up ---------- */
async function init() {
  ME = await call("GET", "/api/auth/state");
  if (!ME.server || !ME.email) { location.replace("/"); return; }
  const label = ME.name || ME.email;
  $("who-name").textContent = label;
  $("who-sub").textContent = ROLE[ME.role] || "";
  $("who").title = ME.email;
  $("avatar").textContent = label.trim().charAt(0).toUpperCase();
  $("acct-line").textContent = [ME.name, ME.email, ROLE[ME.role]].filter(Boolean).join(" · ");
  $("btn-logout").addEventListener("click", async () => {
    await fetch("/api/auth/logout", { method: "POST" }); location.replace("/login.html");
  });
  wireAccount();
  if (ME.role === "admin") {
    $("card-users").hidden = $("card-smtp").hidden = false;
    wireUsers(); wireSmtp();
    await Promise.all([loadUsers(), loadSmtp()]);
  }
}
init();
