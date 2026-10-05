"use strict";
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path, opts = {}) {
  const res = await fetch(path, {
    method: opts.method || "GET",
    headers: { "Content-Type": "application/json", "X-OldHome": "1" },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
    credentials: "same-origin",
  });
  const data = await res.json().catch(() => ({ error: "رد غير متوقع من الخادم" }));
  if (res.status === 401 && path !== "/api/login") { showLogin(); throw new Error("سجّل الدخول"); }
  if (!res.ok || data.error) throw new Error(data.error || res.statusText);
  return data;
}

function toast(msg, bad = false) {
  const t = $("#toast");
  t.textContent = msg; t.className = bad ? "bad" : ""; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), 4000);
}

async function busy(btn, fn) {
  btn.disabled = true;
  try { return await fn(); } finally { btn.disabled = false; }
}

const fmtBytes = (b) => { const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0; while (b >= 1024 && i < 4) { b /= 1024; i++; } return b.toFixed(i ? 1 : 0) + " " + u[i]; };
const fmtUptime = (s) => { const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60); return (d ? d + " يوم " : "") + h + " س " + m + " د"; };
const pct = (a, b) => (b ? Math.round((a / b) * 100) : 0);

// ---------- auth ----------
function showLogin() { $("#app").hidden = true; $("#login").hidden = false; $("#login-pw").focus(); }
function showApp() { $("#login").hidden = true; $("#app").hidden = false; switchTab(location.hash.slice(1) || "dash"); }

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("#login-err").hidden = true;
  try { await api("/api/login", { method: "POST", body: { password: $("#login-pw").value } }); $("#login-pw").value = ""; showApp(); }
  catch (err) { $("#login-err").textContent = err.message; $("#login-err").hidden = false; }
});
$("#logout").addEventListener("click", async () => { await api("/api/logout", { method: "POST" }).catch(() => {}); showLogin(); });

// ---------- tabs ----------
let refreshTimer;
function switchTab(tab) {
  if (!$(`[data-pane="${tab}"]`)) tab = "dash";
  $$("#tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === tab));
  $$("[data-pane]").forEach((p) => (p.hidden = p.dataset.pane !== tab));
  history.replaceState(null, "", "#" + tab);
  clearInterval(refreshTimer);
  ({ dash: loadDash, setup: loadSetup, sites: loadSites, domains: loadAccounts, logs: loadLogSources, settings: loadSettings })[tab]();
  if (tab === "dash") refreshTimer = setInterval(loadDash, 5000);
}
$$("#tabs button").forEach((b) => b.addEventListener("click", () => switchTab(b.dataset.tab)));

// ---------- dashboard ----------
async function loadDash() {
  let d;
  try { d = await api("/api/overview"); } catch (e) { return toast(e.message, true); }
  const s = d.stats;
  $("#hostname").textContent = "· " + s.hostname;
  const tile = (label, value, sub, p) => `<div class="card stat"><div class="muted small">${label}</div><div class="v">${value}</div>
    <div class="muted small">${sub}</div>${p == null ? "" : `<div class="bar"><i class="${p > 85 ? "hot" : ""}" style="width:${p}%"></i></div>`}</div>`;
  $("#stats").innerHTML =
    tile("المعالج", s.cpu + "%", s.cores + " أنوية · الحمل " + s.load[0].toFixed(2), s.cpu) +
    tile("الذاكرة", pct(s.mem_used, s.mem_total) + "%", fmtBytes(s.mem_used) + " من " + fmtBytes(s.mem_total), pct(s.mem_used, s.mem_total)) +
    tile("القرص", pct(s.disk_used, s.disk_total) + "%", fmtBytes(s.disk_used) + " من " + fmtBytes(s.disk_total), pct(s.disk_used, s.disk_total)) +
    tile("الحرارة / التشغيل", s.temp == null ? "—" : s.temp + "°", "يعمل منذ " + fmtUptime(s.uptime), null);

  $("#services").innerHTML = d.services.map((x) => `<div class="card svc"><span><span class="dot ${x.ok ? "ok" : ""}"></span>${esc(x.name)}</span>
    ${x.id === "ssh" ? "" : `<button class="btn small ghost" data-restart="${esc(x.id)}">إعادة تشغيل</button>`}</div>`).join("");

  $("#tunnels").innerHTML = d.tunnels.length ? d.tunnels.map((t) => {
    const cls = t.service === "active" && t.connections ? "ok" : t.service === "active" ? "warn" : "";
    return `<div class="card"><div class="svc"><b><span class="dot ${cls}"></span>${esc(t.account)}</b>
      <button class="btn small ghost" data-restart="tunnel:${esc(t.account)}">إعادة تشغيل</button></div>
      <div class="muted small">التونل: ${esc(t.tunnel)} · الاتصالات: ${t.connections}</div>
      <div class="muted small" dir="ltr" style="text-align:right">${t.zones.map(esc).join(", ")}</div></div>`;
  }).join("") : `<p class="muted">لا يوجد حسابات مربوطة.</p>`;
}

document.addEventListener("click", async (e) => {
  const b = e.target.closest("[data-restart]");
  if (!b) return;
  await busy(b, async () => {
    try { await api(`/api/services/${encodeURIComponent(b.dataset.restart)}/restart`, { method: "POST" }); toast("تمت إعادة التشغيل ✓"); setTimeout(loadDash, 1500); }
    catch (err) { toast(err.message, true); }
  });
});

// ---------- setup ----------
async function loadSetup() {
  let r;
  try { r = await api("/api/setup"); } catch (e) { return toast(e.message, true); }
  const ap = r.aapanel, job = r.job;
  $("#ap-installed").hidden = !ap.installed;
  $("#ap-form").hidden = ap.installed || job.state === "running";
  if (ap.installed) { $("#ap-ver").textContent = ap.version ? "· " + ap.version : ""; $("#ap-lan").href = ap.lan_url; $("#ap-lan").textContent = ap.lan_url; }
  $("#ap-job").hidden = job.state === "none";
  if (job.state !== "none") {
    $("#ap-job-title").innerHTML = job.state === "running" ? `<span class="dot warn"></span>جارِ تثبيت aaPanel… (5–10 دقائق — يمكنك إغلاق الصفحة والعودة)`
      : job.state === "done" ? `<span class="dot ok"></span>اكتمل التثبيت` : `<span class="dot"></span>فشل التثبيت — راجع السجل`;
    const c = job.credentials || {};
    $("#ap-creds").innerHTML = job.state === "done" && c.username
      ? `<p><b>بيانات دخول aaPanel — احفظها الآن:</b></p><div class="creds">URL: ${esc(ap.lan_url || "")}<br>User: ${esc(c.username)}<br>Pass: ${esc(c.password || "")}</div>` : "";
    const pre = $("#ap-log"), atEnd = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 20;
    pre.textContent = job.log || "…";
    if (atEnd) pre.scrollTop = pre.scrollHeight;
  }
  if (job.state === "running") refreshTimer = setTimeout(() => { if (!$('[data-pane="setup"]').hidden) loadSetup(); }, 3000);
}
$("#ap-go").addEventListener("click", (e) => busy(e.target, async () => {
  $("#ap-err").hidden = true;
  try { await api("/api/setup/aapanel", { method: "POST", body: { command: $("#ap-cmd").value } }); toast("بدأ التثبيت"); loadSetup(); }
  catch (err) { $("#ap-err").textContent = err.message; $("#ap-err").hidden = false; }
}));

// ---------- sites ----------
let sitesData;
const KIND = { site: "موقع", panel: "اللوحة", ssh: "SSH", manager: "المدير" };

async function loadSites() {
  const tb = $("#sites-table tbody");
  tb.innerHTML = `<tr><td colspan="5" class="muted">جارِ التحميل…</td></tr>`;
  try { sitesData = await api("/api/sites"); } catch (e) { tb.innerHTML = ""; return toast(e.message, true); }
  tb.innerHTML = sitesData.sites.map((s) => {
    const url = s.kind === "ssh" ? esc(s.hostname) : `<a href="https://${esc(s.hostname)}${s.kind === "panel" ? "" : "/"}" target="_blank" rel="noopener" dir="ltr">${esc(s.hostname)}</a>`;
    let status;
    if (!s.published) status = `<span class="dot warn"></span>غير منشور`;
    else if (s.kind !== "site") status = `<span class="dot ok"></span>منشور`;
    else if (!s.panel) status = `<span class="dot warn"></span>غير موجود في aaPanel`;
    else if (s.needs_fix) status = `<span class="dot warn"></span>Force HTTPS مُفعّل — يحتاج إصلاح`;
    else status = `<span class="dot ${/^[23]/.test(s.http) ? "ok" : ""}"></span>منشور · ${esc(s.http)}`;
    let actions = "";
    if (!s.locked) {
      if (!s.published && s.publishable) actions += `<button class="btn small primary" data-pub="${esc(s.hostname)}">نشر</button>`;
      if (!s.published && !s.publishable) actions += `<span class="muted small">الدومين غير مربوط بحساب</span>`;
      if (s.needs_fix) actions += `<button class="btn small primary" data-pub="${esc(s.hostname)}" title="توجيه التونل إلى المنفذ 443">إصلاح</button>`;
      if (s.published) actions += `<button class="btn small ghost" data-unpub="${esc(s.hostname)}">إلغاء النشر</button>`;
      actions += `<button class="btn small ghost" data-del="${esc(s.hostname)}">حذف</button>`;
    } else actions = `<span class="muted small">🔒 محمي</span>`;
    return `<tr><td>${url}${s.label ? `<div class="muted small">${esc(s.label)}</div>` : ""}</td>
      <td><span class="tag">${KIND[s.kind] || s.kind}</span></td><td>${esc(s.account || "—")}</td>
      <td>${status}</td><td><div class="actions">${actions}</div></td></tr>`;
  }).join("") || `<tr><td colspan="5" class="muted">لا توجد مواقع بعد.</td></tr>`;
}

document.addEventListener("click", async (e) => {
  const pub = e.target.closest("[data-pub]"), unpub = e.target.closest("[data-unpub]"), del = e.target.closest("[data-del]");
  if (pub) await busy(pub, async () => {
    try { const r = await api(`/api/sites/${pub.dataset.pub}/publish`, { method: "POST" }); toast("تم النشر: " + r.url); loadSites(); }
    catch (err) { toast(err.message, true); }
  });
  if (unpub) await busy(unpub, async () => {
    try { await api(`/api/sites/${unpub.dataset.unpub}/unpublish`, { method: "POST" }); toast("تم إلغاء النشر (الموقع باقٍ في aaPanel)"); loadSites(); }
    catch (err) { toast(err.message, true); }
  });
  if (del) openDelete(del.dataset.del);
});

// add site
$("#open-add").addEventListener("click", () => {
  if (!sitesData) return;
  $("#add-zone").innerHTML = sitesData.zones.map((z) => `<option>${esc(z)}</option>`).join("");
  $("#add-php").innerHTML = sitesData.php.map((v) => `<option value="${v}">PHP ${v[0]}.${v[1]}</option>`).join("") || `<option value="00">ثابت (بدون PHP)</option>`;
  $("#add-form").reset(); $("#add-err").hidden = true; $("#add-dns").textContent = "";
  $("#add-dlg").showModal(); $("#add-sub").focus(); checkDns();
});
const addHost = () => { const sub = $("#add-sub").value.trim().toLowerCase(); return sub ? sub + "." + $("#add-zone").value : $("#add-zone").value; };
let dnsTimer;
async function checkDns() {
  const h = addHost(), out = $("#add-dns");
  if (!$("#add-sub").checkValidity()) { out.textContent = ""; return; }
  out.textContent = "جارِ التحقق من " + h + "…";
  try {
    const r = await api("/api/dns-check?hostname=" + encodeURIComponent(h));
    out.innerHTML = r.status === "free" ? `✅ <span dir="ltr">${esc(h)}</span> متاح`
      : r.status === "ours" ? `ℹ️ مربوط بهذا الخادم مسبقاً`
      : `⛔ مستخدم في مكان آخر (${esc(r.record.type)} ← <span dir="ltr">${esc(r.record.content)}</span>)`;
  } catch (err) { out.textContent = err.message; }
}
["input", "change"].forEach((ev) => { $("#add-sub").addEventListener(ev, () => { clearTimeout(dnsTimer); dnsTimer = setTimeout(checkDns, 500); }); });
$("#add-zone").addEventListener("change", checkDns);

$("#add-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("#add-err").hidden = true;
  const btn = $("#add-go"), label = btn.textContent;
  btn.textContent = "جارِ الإنشاء…";
  await busy(btn, async () => {
    try {
      const r = await api("/api/sites", { method: "POST", body: { hostname: addHost(), php: $("#add-php").value, db: $("#add-db").checked, note: $("#add-note").value } });
      $("#add-dlg").close();
      let html = `<h3>🎉 الموقع جاهز</h3><p><a href="${esc(r.url)}" target="_blank" rel="noopener" dir="ltr">${esc(r.url)}</a></p>
        <p class="muted small">قد يستغرق ظهوره دقيقة حتى ينتشر سجل DNS.</p>`;
      if (r.db) html += `<p><b>بيانات قاعدة البيانات — احفظها الآن:</b></p><div class="creds">DB: ${esc(r.db.name)}<br>User: ${esc(r.db.user)}<br>Pass: ${esc(r.db.password)}</div>`;
      $("#msg-body").innerHTML = html; $("#msg-dlg").showModal();
      loadSites();
    } catch (err) { $("#add-err").textContent = err.message; $("#add-err").hidden = false; }
  });
  btn.textContent = label;
});

// delete site
function openDelete(host) {
  $("#del-form").reset(); $("#del-err").hidden = true;
  $("#del-name").textContent = host; $("#del-form").dataset.host = host;
  $("#del-dlg").showModal(); $("#del-confirm").focus();
}
$("#del-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const host = e.target.dataset.host, btn = e.submitter;
  await busy(btn, async () => {
    try {
      await api(`/api/sites/${host}`, { method: "DELETE", body: { confirm: $("#del-confirm").value.trim(), files: $("#del-files").checked, db: $("#del-db").checked } });
      $("#del-dlg").close(); toast("تم حذف " + host); loadSites();
    } catch (err) { $("#del-err").textContent = err.message; $("#del-err").hidden = false; }
  });
});

$$("[data-close]").forEach((b) => b.addEventListener("click", () => b.closest("dialog").close()));

// ---------- domains / accounts ----------
async function loadAccounts() {
  try {
    const r = await api("/api/accounts");
    $("#accounts").innerHTML = r.accounts.map((a) => {
      const cls = a.service === "active" && a.connections ? "ok" : a.service === "active" ? "warn" : "";
      return `<div class="card"><div class="svc"><b><span class="dot ${cls}"></span>${esc(a.name)}</b>
        <span class="muted small">${a.connections} اتصالات</span></div>
        <div class="muted small">التونل: ${esc(a.tunnel)}</div>
        <ul dir="ltr" style="text-align:right;margin:.5rem 0 0;padding:0 1.2rem 0 0">${a.zones.map((z) => `<li>${esc(z)}</li>`).join("")}</ul></div>`;
    }).join("") || `<p class="muted">لا توجد حسابات.</p>`;
    if (r.login.state === "pending" || r.login.state === "authorized") resumeLink(r.login);
    loadServerRoutes();
  } catch (e) { toast(e.message, true); }
}

async function loadServerRoutes() {
  const tb = $("#sr-table tbody");
  try {
    const r = await api("/api/server-routes");
    srZones = r.zones;
    tb.innerHTML = r.routes.map((x) => {
      const href = x.kind === "ssh" ? "" : `https://${x.hostname}${x.kind === "panel" ? x.admin_path : "/"}`;
      const link = href ? `<a href="${esc(href)}" target="_blank" rel="noopener" dir="ltr">${esc(x.hostname)}</a>` : `<span dir="ltr">${esc(x.hostname)}</span>`;
      const prot = x.kind === "manager" ? (x.protected ? " 🛡️" : x.password_only ? " 🔑" : " ⚠️") : "";
      return `<tr><td>${link}${prot}</td><td>${esc(x.label)}</td><td>${esc(x.account || "—")}</td>
        <td><div class="actions"><button class="btn small ghost" data-srdel="${esc(x.hostname)}" data-kind="${esc(x.kind)}">حذف</button></div></td></tr>`;
    }).join("") || `<tr><td colspan="4" class="muted">لا توجد روابط — أضف رابطاً للوحة aaPanel أو SSH.</td></tr>`;
  } catch (e) { tb.innerHTML = ""; toast(e.message, true); }
}
let srZones = [];
const srHost = () => $("#sr-sub").value.trim().toLowerCase() + "." + $("#sr-zone").value;
function srUpdate() {
  const k = $("#sr-kind").value;
  const prot = (document.querySelector('input[name="sr-prot"]:checked') || {}).value || "access";
  $("#sr-prot").hidden = k !== "manager";
  $("#sr-access").hidden = !(k === "manager" && prot === "access");
  $("#sr-host-hint").textContent = srHost();
  $("#sr-warn").textContent = k === "ssh" ? "للاتصال: cloudflared access ssh --hostname " + srHost()
    : k === "panel" ? "سيفتح لوحة aaPanel مع مسار الدخول الأمني." : "";
}
$("#open-sr").addEventListener("click", () => {
  if (!srZones.length) return toast("اربط دوميناً أولاً", true);
  $("#sr-form").reset(); $("#sr-err").hidden = true;
  $("#sr-zone").innerHTML = srZones.map((z) => `<option>${esc(z)}</option>`).join("");
  srUpdate(); $("#sr-dlg").showModal(); $("#sr-sub").focus();
});
["input", "change"].forEach((ev) => ["#sr-kind", "#sr-sub", "#sr-zone"].forEach((id) => $(id).addEventListener(ev, srUpdate)));
$$('input[name="sr-prot"]').forEach((r) => r.addEventListener("change", srUpdate));
$("#sr-form").addEventListener("submit", async (e) => {
  e.preventDefault(); $("#sr-err").hidden = true;
  const protection = (document.querySelector('input[name="sr-prot"]:checked') || {}).value || "access";
  const btn = $("#sr-go"), label = btn.textContent, manager = $("#sr-kind").value === "manager" && protection === "access";
  btn.textContent = manager ? "جارِ التحقق من Access… (حتى 30 ثانية)" : "جارِ الإضافة…";
  await busy(btn, async () => {
    try {
      const r = await api("/api/server-routes", { method: "POST", body: { kind: $("#sr-kind").value, hostname: srHost(), protection } });
      $("#sr-dlg").close(); toast("تمت الإضافة: " + r.url); loadServerRoutes();
    } catch (err) { $("#sr-err").textContent = err.message; $("#sr-err").hidden = false; }
  });
  btn.textContent = label;
});
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-srdel]");
  if (!b) return;
  const host = b.dataset.srdel, kind = b.dataset.kind;
  const warn = kind === "ssh" ? "<p>⚠️ إذا كنت متصلاً عبر هذا الرابط سينقطع اتصالك، ولن تستطيع الدخول عبره بعد الحذف.</p>"
    : kind === "manager" ? "<p>⚠️ لن تعمل اللوحة عبر هذا الرابط بعد الحذف (يبقى الدخول عبر SSH).</p>" : "";
  $("#msg-body").innerHTML = `<h3>حذف الرابط</h3>${warn}<label>للتأكيد اكتب: <b dir="ltr">${esc(host)}</b>
    <input id="srdel-confirm" dir="ltr" autocomplete="off"></label><p id="srdel-err" class="err" hidden></p>
    <div class="row end"><button class="btn danger" id="srdel-go">حذف</button></div>`;
  $("#msg-dlg").showModal();
  $("#srdel-go").onclick = (ev) => busy(ev.target, async () => {
    try {
      await api(`/api/server-routes/${host}`, { method: "DELETE", body: { confirm: $("#srdel-confirm").value.trim() } });
      $("#msg-dlg").close(); toast("تم حذف " + host); loadServerRoutes();
    } catch (err) { $("#srdel-err").textContent = err.message; $("#srdel-err").hidden = false; }
  });
});

let linkPoll;
function linkStep(n) { [1, 2, 3].forEach((i) => ($("#link-step" + i).hidden = i !== n)); }
$("#open-link").addEventListener("click", () => { linkStep(1); $("#link-dlg").showModal(); });
$("#link-start").addEventListener("click", (e) => busy(e.target, async () => {
  try { const r = await api("/api/accounts/login", { method: "POST" }); resumeLink({ state: "pending", url: r.url }); }
  catch (err) { toast(err.message, true); }
}));
function resumeLink(st) {
  if (!$("#link-dlg").open) $("#link-dlg").showModal();
  if (st.state === "authorized") return showAuthorized(st);
  $("#link-url").href = st.url; linkStep(2);
  clearInterval(linkPoll);
  linkPoll = setInterval(async () => {
    try {
      const s = await api("/api/accounts/login");
      if (s.state === "authorized") { clearInterval(linkPoll); showAuthorized(s); }
      else if (s.state === "failed" || s.state === "none") { clearInterval(linkPoll); $("#link-wait").textContent = "❌ " + (s.detail || "توقف التفويض"); }
    } catch (_) {}
  }, 2500);
}
function showAuthorized(s) {
  linkStep(3); $("#link-err").hidden = true;
  if (s.already_linked) { $("#link-found").innerHTML = `ℹ️ الدومين <b dir="ltr">${esc(s.zone)}</b> مربوط مسبقاً.`; $("#link-name-wrap").hidden = true; }
  else if (s.existing_account) { $("#link-found").innerHTML = `✅ <b dir="ltr">${esc(s.zone)}</b> — في حساب موجود (<b>${esc(s.existing_account)}</b>)، سيُضاف إلى نفس التونل.`; $("#link-name-wrap").hidden = true; }
  else { $("#link-found").innerHTML = `✅ <b dir="ltr">${esc(s.zone)}</b> — حساب Cloudflare جديد، سيُنشأ له تونل مستقل.`; $("#link-name-wrap").hidden = false; $("#link-name").value = s.zone.split(".")[0].toLowerCase().replace(/[^a-z0-9-]/g, ""); }
}
$("#link-finish").addEventListener("click", (e) => busy(e.target, async () => {
  try {
    const r = await api("/api/accounts/finish", { method: "POST", body: { name: $("#link-name").value.trim() } });
    $("#link-dlg").close();
    toast(`تم ربط ${r.zone}` + (r.new_account ? ` (حساب جديد: ${r.account})` : ""));
    loadAccounts();
  } catch (err) { $("#link-err").textContent = err.message; $("#link-err").hidden = false; }
}));
async function cancelLink() { clearInterval(linkPoll); await api("/api/accounts/cancel", { method: "POST" }).catch(() => {}); $("#link-dlg").close(); }
$("#link-cancel").addEventListener("click", cancelLink);
$("#link-cancel2").addEventListener("click", cancelLink);

// ---------- logs ----------
async function loadLogSources() {
  try {
    const r = await api("/api/logs");
    const cur = $("#log-src").value;
    $("#log-src").innerHTML = r.sources.map((s) => `<option value="${esc(s.id)}">${esc(s.name)}</option>`).join("");
    if (cur) $("#log-src").value = cur;
    loadLog();
  } catch (e) { toast(e.message, true); }
}
async function loadLog() {
  const pre = $("#log-text");
  pre.textContent = "…";
  try {
    const r = await api(`/api/logs?source=${encodeURIComponent($("#log-src").value)}&lines=${$("#log-lines").value}`);
    pre.textContent = r.text || "(فارغ)"; pre.scrollTop = pre.scrollHeight;
  } catch (e) { pre.textContent = e.message; }
}
$("#log-src").addEventListener("change", loadLog);
$("#log-lines").addEventListener("change", loadLog);
$("#log-refresh").addEventListener("click", loadLog);

// ---------- settings ----------
async function loadSettings() {
  try { const d = await api("/api/overview"); $("#realip").checked = d.realip; } catch (e) { toast(e.message, true); }
  try { showDirect(await api("/api/settings/direct")); } catch (e) { toast(e.message, true); }
}
function showDirect(d) {
  $$('input[name="direct"]').forEach((r) => (r.checked = r.value === d.mode));
  $("#direct-urls").innerHTML = d.mode === "off" ? "" :
    d.urls.map((u) => `<a href="${esc(u)}" target="_blank" rel="noopener">${esc(u)}</a>`).join("<br>") + (d.active ? "" : " ⚠️ الخدمة متوقفة");
}
$$('input[name="direct"]').forEach((r) => r.addEventListener("change", async () => {
  try { showDirect(await api("/api/settings/direct", { method: "POST", body: { mode: r.value } })); toast("تم الحفظ ✓"); }
  catch (err) { toast(err.message, true); loadSettings(); }
}));
$("#realip").addEventListener("change", async (e) => {
  const want = e.target.checked;
  try { await api("/api/settings/realip", { method: "POST", body: { enable: want } }); toast(want ? "تم التفعيل ✓" : "تم الإيقاف"); }
  catch (err) { e.target.checked = !want; toast(err.message, true); }
});

$("#pw-form").addEventListener("submit", async (e) => {
  e.preventDefault(); $("#pw-err").hidden = true;
  const btn = e.submitter;
  await busy(btn, async () => {
    try {
      await api("/api/settings/password", { method: "POST", body: { current: $("#pw-cur").value, new: $("#pw-new").value, confirm: $("#pw-new2").value } });
      e.target.reset(); toast("تم تغيير كلمة المرور ✓ — الأجهزة الأخرى سُجّل خروجها");
    } catch (err) { $("#pw-err").textContent = err.message; $("#pw-err").hidden = false; }
  });
});

// ---------- boot ----------
api("/api/me").then((r) => (r.ok ? showApp() : showLogin())).catch(showLogin);
