"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const state = {
  tab: "entries",
  entries: [],
  containers: [],
  images: [],
  openLogs: new Set(),
  editing: null,
  pollTimer: null,
  registries: [],
};

// ---------- API ----------
async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", credentials: "same-origin", headers: {} };
  if (opts.body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.body);
  }
  const r = await fetch(path, init);
  let data = null;
  try { data = await r.json(); } catch { data = null; }
  if (r.status === 401 && path !== "/api/login") {
    showLogin();
    throw new Error("Sesión vencida");
  }
  if (!r.ok) throw new Error((data && data.detail) || r.statusText);
  return data;
}

function toast(msg, isErr = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (isErr ? " err" : "");
  t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.hidden = true), isErr ? 6000 : 3000);
}

function confirmBox(text, yesLabel = "Confirmar") {
  const d = $("#confirm-dialog");
  $("#confirm-text").textContent = text;
  $("#confirm-yes").textContent = yesLabel;
  d.returnValue = "";
  d.showModal();
  return new Promise((res) => d.addEventListener("close", () => res(d.returnValue === "yes"), { once: true }));
}

// ---------- formato ----------
function ago(ts) {
  if (!ts) return "nunca";
  const s = Math.round(Date.now() / 1000 - ts);
  if (s < 0) return "en " + dur(-s);
  if (s < 45) return "recién";
  return "hace " + dur(s);
}
function until(ts) {
  if (!ts) return "";
  const s = Math.round(ts - Date.now() / 1000);
  return s <= 30 ? "en instantes" : "en " + dur(s);
}
function dur(s) {
  if (s < 90) return Math.max(1, Math.round(s)) + " s";
  const m = Math.round(s / 60);
  if (m < 90) return m + " min";
  const h = Math.round(m / 60);
  if (h < 36) return h + " h";
  return Math.round(h / 24) + " días";
}
function bytes(n) {
  if (!n) return "0 B";
  const u = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(u.length - 1, Math.floor(Math.log(n) / Math.log(1000)));
  return (n / 1000 ** i).toFixed(i ? 1 : 0) + " " + u[i];
}
function dateStr(ts) {
  if (!ts) return "—";
  const t = typeof ts === "number" ? ts * 1000 : Date.parse(ts);
  return new Date(t).toLocaleString("es-AR", { dateStyle: "short", timeStyle: "short" });
}
const unitTxt = { m: "min", h: "h", d: "días" };
const STATUS = {
  uptodate: "Al día",
  available: "Versión nueva disponible",
  pending: "Falta recrear contenedores",
  running: "Trabajando…",
  unknown: "Sin chequear",
  missing: "Imagen no descargada",
  error: "Error",
};

// ---------- sesión ----------
function showLogin(msg) {
  stopPolling();
  $("#app").hidden = true;
  $("#login").hidden = false;
  if (msg) $("#login-msg").textContent = msg;
  setTimeout(() => $("#login-pass").focus(), 50);
}
async function boot() {
  const s = await api("/api/session");
  if (!s.password_configured) {
    showLogin("Falta definir la variable PULLPANEL_PASSWORD en el compose. Editá la app en ZimaOS, agregala y volvé a abrir.");
    $("#login-pass").disabled = true;
    return;
  }
  if (!s.authenticated) return showLogin();
  $("#login").hidden = true;
  $("#app").hidden = false;
  switchTab(location.hash.slice(1) || "entries");
}
$("#login-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  $("#login-error").hidden = true;
  try {
    await api("/api/login", { method: "POST", body: { password: $("#login-pass").value } });
    $("#login-pass").value = "";
    boot();
  } catch (e) {
    $("#login-error").textContent = e.message;
    $("#login-error").hidden = false;
  }
});
$("#logout").addEventListener("click", async () => {
  await api("/api/logout", { method: "POST" });
  showLogin("Sesión cerrada.");
});

// ---------- pestañas ----------
function switchTab(tab) {
  if (!["entries", "images", "settings"].includes(tab)) tab = "entries";
  state.tab = tab;
  history.replaceState(null, "", "#" + tab);
  $$(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  $$("[data-panel]").forEach((p) => (p.hidden = p.dataset.panel !== tab));
  if (tab === "entries") loadEntries();
  if (tab === "images") loadImages();
  if (tab === "settings") loadSettings();
}
$$(".tab").forEach((b) => b.addEventListener("click", () => switchTab(b.dataset.tab)));

// ---------- entradas ----------
function stopPolling() { clearTimeout(state.pollTimer); }
function schedulePoll(busy) {
  stopPolling();
  if (state.tab !== "entries" || document.hidden) return;
  state.pollTimer = setTimeout(loadEntries, busy ? 1500 : 20000);
}
document.addEventListener("visibilitychange", () => { if (!document.hidden && state.tab === "entries") loadEntries(); });

async function loadEntries() {
  try {
    const data = await api("/api/entries");
    state.entries = data.entries;
    const banner = $("#docker-error");
    banner.hidden = !data.docker_error;
    banner.textContent = data.docker_error ? "No se pudo hablar con Docker: " + data.docker_error + ". ¿Está montado /var/run/docker.sock?" : "";
    renderEntries();
    schedulePoll(data.busy);
  } catch (e) {
    if (e.message !== "Sesión vencida") { toast(e.message, true); schedulePoll(false); }
  }
}

function renderEntries() {
  const wrap = $("#entries");
  // conservar scroll de logs abiertos
  const scroll = {};
  $$(".log", wrap).forEach((l) => {
    scroll[l.dataset.id] = { top: l.scrollTop, atBottom: l.scrollTop + l.clientHeight >= l.scrollHeight - 8 };
  });
  $("#entries-empty").hidden = state.entries.length > 0;
  wrap.innerHTML = state.entries.map(entryCard).join("");
  $$(".log", wrap).forEach((l) => {
    const s = scroll[l.dataset.id];
    if (!s || s.atBottom) l.scrollTop = l.scrollHeight; else l.scrollTop = s.top;
  });
}

function entryCard(e) {
  const running = e.status === "running";
  const job = e.job;
  const chips = (e.containers_info || []).map((c) => {
    const cls = !c.exists ? "missing" : c.outdated ? "warn" : "";
    const title = !c.exists ? "No existe en este equipo" : c.outdated ? "Corre una imagen anterior" : c.state;
    return `<span class="chip ${cls}" title="${esc(title)}"><span class="dot ${c.state === "running" ? "running" : ""}"></span>${esc(c.name)}${c.is_self ? " (esta app)" : ""}${c.outdated ? " · desactualizado" : ""}${!c.exists ? " · no existe" : ""}</span>`;
  }).join("") || `<span class="chip missing">Sin contenedores asociados (solo pull)</span>`;

  const auto = e.auto && e.auto.enabled
    ? `<span>Automático: <b>cada ${e.auto.every} ${unitTxt[e.auto.unit]}</b>${e.next_auto_at ? " · próximo " + until(e.next_auto_at) : ""}</span>`
    : `<span>Automático: <b>no</b></span>`;
  const checkErr = e.last_check_error ? `<span class="error">Último chequeo falló: ${esc(e.last_check_error)}</span>` : "";

  let logHtml = "";
  if (job) {
    const open = state.openLogs.has(e.id) || running;
    const lines = job.log.map((l) => (/ERROR|falló/.test(l) ? `<span class="err">${esc(l)}</span>` : esc(l))).join("\n");
    const result = running ? "en curso" : job.state === "ok" ? "terminó bien" : "terminó con error";
    logHtml = `
      <div class="card-actions" style="margin-top:10px">
        <button class="linkish" data-act="toggle-log" data-id="${e.id}">${open ? "Ocultar" : "Ver"} registro</button>
        <span class="muted small">${esc(job.label)}${job.trigger === "auto" ? " (automática)" : ""} · ${result} · ${ago(job.started)}</span>
      </div>
      ${open ? `<pre class="log" data-id="${e.id}">${lines}</pre>` : ""}`;
  } else if (e.last_error && e.last_result === "error") {
    logHtml = `<p class="error small">Última tarea falló: ${esc(e.last_error)}</p>`;
  }

  return `
  <article class="card" data-id="${e.id}">
    <div class="card-head">
      <div>
        <h3>${esc(e.name)}</h3>
        <div class="ref">${esc(e.image)}</div>
      </div>
      <span class="badge b-${e.status}">${STATUS[e.status] || e.status}</span>
    </div>
    <div class="chips">${chips}</div>
    <div class="meta">
      <span>Último chequeo: <b>${ago(e.last_check_at)}</b></span>
      <span>Última actualización: <b>${ago(e.last_update_at)}</b></span>
      ${auto}
      ${e.local_digest ? `<span title="${esc(e.local_digest)}">Local: <code>${esc(e.local_digest.slice(7, 19))}</code></span>` : ""}
      ${e.remote_digest && e.update_available ? `<span title="${esc(e.remote_digest)}">Registro: <code>${esc(e.remote_digest.slice(7, 19))}</code></span>` : ""}
      ${checkErr}
    </div>
    <div class="card-actions">
      <button class="btn primary" data-act="update" data-id="${e.id}" ${running ? "disabled" : ""} title="Pull + recrear contenedores">Actualizar</button>
      <button class="btn" data-act="check" data-id="${e.id}" ${running ? "disabled" : ""} title="Comparar con el registro sin descargar">Chequear</button>
      <button class="btn" data-act="pull" data-id="${e.id}" ${running ? "disabled" : ""} title="Solo descargar la imagen">Solo pull</button>
      <span class="spacer"></span>
      <button class="btn ghost small" data-act="edit" data-id="${e.id}" ${running ? "disabled" : ""}>Editar</button>
      <button class="btn ghost small danger" data-act="delete" data-id="${e.id}" ${running ? "disabled" : ""}>Quitar</button>
    </div>
    ${logHtml}
  </article>`;
}

$("#entries").addEventListener("click", async (ev) => {
  const b = ev.target.closest("[data-act]");
  if (!b) return;
  const id = b.dataset.id;
  const entry = state.entries.find((x) => x.id === id);
  const act = b.dataset.act;
  if (act === "toggle-log") {
    state.openLogs.has(id) ? state.openLogs.delete(id) : state.openLogs.add(id);
    return renderEntries();
  }
  if (act === "edit") return openEntryDialog(entry);
  if (act === "delete") {
    const ok = await confirmBox(`¿Quitar “${entry.name}” de PullPanel? No se borra ningún contenedor ni imagen.`, "Quitar");
    if (!ok) return;
    try { await api(`/api/entries/${id}`, { method: "DELETE" }); loadEntries(); } catch (e) { toast(e.message, true); }
    return;
  }
  if (["update", "check", "pull"].includes(act)) {
    if (act === "update" && entry.containers_info?.some((c) => c.is_self)) {
      const ok = await confirmBox("Esta entrada incluye a PullPanel. Si hay versión nueva, la app se va a reiniciar sola. ¿Seguir?", "Actualizar");
      if (!ok) return;
    }
    try {
      b.disabled = true;
      await api(`/api/entries/${id}/run`, { method: "POST", body: { action: act } });
      state.openLogs.add(id);
      loadEntries();
    } catch (e) { toast(e.message, true); b.disabled = false; }
  }
});

$("#check-all").addEventListener("click", async () => {
  try {
    const r = await api("/api/check-all", { method: "POST" });
    toast(r.started ? `Chequeando ${r.started} imagen(es)…` : "No hay nada para chequear");
    loadEntries();
  } catch (e) { toast(e.message, true); }
});

// ---------- modal entrada ----------
$("#add-entry").addEventListener("click", () => openEntryDialog(null));

async function openEntryDialog(entry) {
  state.editing = entry;
  $("#entry-title").textContent = entry ? "Editar imagen" : "Agregar imagen";
  $("#entry-error").hidden = true;
  $("#f-image").value = entry?.image || "";
  $("#f-name").value = entry?.name || "";
  $("#f-recreate").checked = entry ? entry.recreate !== false : true;
  $("#f-auto").checked = !!entry?.auto?.enabled;
  $("#f-every").value = entry?.auto?.every || 6;
  $("#f-unit").value = entry?.auto?.unit || "h";
  syncAuto();
  $("#container-picker").innerHTML = `<p class="muted small" style="padding:8px">Cargando contenedores…</p>`;
  $("#entry-dialog").showModal();
  try {
    state.containers = await api("/api/containers");
  } catch (e) {
    state.containers = [];
    $("#container-picker").innerHTML = `<p class="error small" style="padding:8px">${esc(e.message)}</p>`;
    return;
  }
  const selected = new Set(entry?.containers || []);
  const names = new Set(state.containers.map((c) => c.name));
  const missing = [...selected].filter((n) => !names.has(n));
  const rows = state.containers.map((c) => `
    <label>
      <input type="checkbox" value="${esc(c.name)}" data-image="${esc(c.image)}" ${selected.has(c.name) ? "checked" : ""}>
      <span><span class="p-name">${esc(c.name)}${c.is_self ? " <span class='muted'>(esta app)</span>" : ""}</span>
      <span class="p-img">${esc(c.image)} · ${esc(c.state)}</span></span>
    </label>`);
  missing.forEach((n) => rows.push(`<label><input type="checkbox" value="${esc(n)}" checked><span><span class="p-name">${esc(n)}</span><span class="p-img">no existe en este equipo</span></span></label>`));
  $("#container-picker").innerHTML = rows.join("") || `<p class="muted small" style="padding:8px">No hay contenedores.</p>`;
}

$("#container-picker").addEventListener("change", (ev) => {
  const cb = ev.target;
  if (cb.type !== "checkbox" || !cb.checked || !cb.dataset.image) return;
  if (!$("#f-image").value.trim()) $("#f-image").value = cb.dataset.image;
  if (!$("#f-name").value.trim()) $("#f-name").value = cb.value;
});

function syncAuto() {
  const on = $("#f-auto").checked;
  $("#f-every").disabled = !on;
  $("#f-unit").disabled = !on;
}
$("#f-auto").addEventListener("change", syncAuto);

$("#entry-form").addEventListener("submit", async (ev) => {
  if (ev.submitter?.value !== "save") return; // cancelar cierra el diálogo
  ev.preventDefault();
  const body = {
    name: $("#f-name").value.trim(),
    image: $("#f-image").value.trim(),
    containers: $$("#container-picker input:checked").map((c) => c.value),
    recreate: $("#f-recreate").checked,
    auto: { enabled: $("#f-auto").checked, every: Number($("#f-every").value) || 1, unit: $("#f-unit").value },
  };
  // advertir si los contenedores elegidos usan otra imagen
  const other = $$("#container-picker input:checked")
    .filter((c) => c.dataset.image && normRef(c.dataset.image) !== normRef(body.image))
    .map((c) => c.value);
  if (other.length) {
    const ok = await confirmBox(`${other.join(", ")} hoy usa(n) otra imagen. Al actualizar se va(n) a recrear con ${body.image}. ¿Seguro?`, "Sí, guardar");
    if (!ok) return;
  }
  try {
    if (state.editing) await api(`/api/entries/${state.editing.id}`, { method: "PUT", body });
    else await api("/api/entries", { method: "POST", body });
    $("#entry-dialog").close();
    toast("Guardado");
    loadEntries();
  } catch (e) {
    $("#entry-error").textContent = e.message;
    $("#entry-error").hidden = false;
  }
});
function normRef(r) {
  r = (r || "").trim();
  const last = r.split("/").pop();
  return last.includes(":") ? r : r + ":latest";
}

// ---------- limpieza ----------
async function loadImages() {
  $("#images-body").innerHTML = `<tr><td colspan="6" class="muted">Cargando…</td></tr>`;
  try {
    state.images = await api("/api/images");
    renderImages();
  } catch (e) {
    $("#images-body").innerHTML = `<tr><td colspan="6" class="error">${esc(e.message)}</td></tr>`;
  }
}
function renderImages() {
  const onlyUnused = $("#only-unused").checked;
  const list = state.images.filter((i) => !onlyUnused || !i.containers.length);
  const unused = state.images.filter((i) => !i.containers.length);
  $("#images-summary").textContent =
    `${state.images.length} imágenes en total · ${unused.length} sin uso (${bytes(unused.reduce((a, i) => a + i.size, 0))})`;
  $("#images-body").innerHTML = list.map((i) => {
    const name = i.tags.length
      ? i.tags.map((t) => `<div class="img-name">${esc(t)}</div>`).join("")
      : `<div class="img-name">${esc(i.repos[0] || "&lt;sin nombre&gt;")}</div><div class="tag-old">sin tag · versión anterior</div>`;
    const used = i.containers.length
      ? `En uso: ${esc(i.containers.join(", "))}`
      : `<span class="muted">Sin uso</span>`;
    return `<tr>
      <td class="col-check"><input type="checkbox" class="img-sel" value="${esc(i.id)}" ${i.containers.length ? "disabled" : ""} aria-label="Seleccionar"></td>
      <td>${name}<div class="img-id">${esc(i.short_id)}</div></td>
      <td>${used}</td>
      <td class="num">${bytes(i.size)}</td>
      <td>${dateStr(i.created)}</td>
      <td><button class="btn small danger" data-del="${esc(i.id)}" ${i.containers.length ? "disabled" : ""}>Borrar</button></td>
    </tr>`;
  }).join("") || `<tr><td colspan="6" class="muted">No hay imágenes ${onlyUnused ? "sin uso" : ""}.</td></tr>`;
  $("#select-all").checked = false;
  syncDeleteBtn();
}
function syncDeleteBtn() {
  const n = $$(".img-sel:checked").length;
  $("#delete-selected").disabled = !n;
  $("#delete-selected").textContent = n ? `Borrar seleccionadas (${n})` : "Borrar seleccionadas";
}
$("#only-unused").addEventListener("change", renderImages);
$("#select-all").addEventListener("change", (ev) => {
  $$(".img-sel:not(:disabled)").forEach((c) => (c.checked = ev.target.checked));
  syncDeleteBtn();
});
$("#images-body").addEventListener("change", (ev) => { if (ev.target.classList.contains("img-sel")) syncDeleteBtn(); });
$("#images-body").addEventListener("click", (ev) => {
  const b = ev.target.closest("[data-del]");
  if (b) deleteImages([b.dataset.del]);
});
$("#delete-selected").addEventListener("click", () => deleteImages($$(".img-sel:checked").map((c) => c.value)));

async function deleteImages(ids) {
  const ok = await confirmBox(ids.length === 1 ? "¿Borrar esta imagen?" : `¿Borrar ${ids.length} imágenes?`, "Borrar");
  if (!ok) return;
  try {
    const r = await api("/api/images/delete", { method: "POST", body: { ids } });
    const bad = r.results.filter((x) => !x.ok);
    if (bad.length) toast(`${r.results.length - bad.length} borrada(s). Error: ${bad[0].error}`, true);
    else toast(`${r.results.length} imagen(es) borrada(s)`);
    loadImages();
  } catch (e) { toast(e.message, true); }
}

// ---------- ajustes ----------
async function loadSettings() {
  try {
    const s = await api("/api/settings");
    state.registries = s.registries.map((r) => ({ ...r, token: "" }));
    renderRegistries();
    const i = s.info;
    $("#sys-info").innerHTML = `
      <dt>PullPanel</dt><dd>${esc(i.version)}</dd>
      <dt>Docker</dt><dd>${esc(i.docker_version || i.docker_error || "—")}</dd>
      <dt>Contenedor propio</dt><dd>${esc(i.self_container || "—")}</dd>`;
  } catch (e) { toast(e.message, true); }
}
function renderRegistries() {
  const rows = state.registries.map((r, idx) => `
    <div class="reg-row" data-idx="${idx}">
      <input data-k="host" placeholder="ghcr.io" value="${esc(r.host)}" aria-label="Registro">
      <input data-k="username" placeholder="usuario" value="${esc(r.username)}" aria-label="Usuario">
      <input data-k="token" type="password" placeholder="${r.has_token ? "•••••• (guardado, dejá vacío para conservar)" : "token"}" value="${esc(r.token)}" aria-label="Token" autocomplete="new-password">
      <button class="btn ghost small danger" data-rm="${idx}">Quitar</button>
    </div>`);
  $("#registries").innerHTML = rows.join("") || `<p class="muted">No hay registros configurados. Las imágenes públicas no lo necesitan.</p>`;
}
$("#registries").addEventListener("input", (ev) => {
  const row = ev.target.closest(".reg-row");
  if (row) state.registries[row.dataset.idx][ev.target.dataset.k] = ev.target.value;
});
$("#registries").addEventListener("click", (ev) => {
  const b = ev.target.closest("[data-rm]");
  if (!b) return;
  state.registries.splice(Number(b.dataset.rm), 1);
  renderRegistries();
});
$("#add-registry").addEventListener("click", () => {
  state.registries.push({ host: state.registries.length ? "" : "ghcr.io", username: "", token: "", has_token: false });
  renderRegistries();
});
$("#save-settings").addEventListener("click", async () => {
  try {
    const s = await api("/api/settings", { method: "PUT", body: { registries: state.registries } });
    state.registries = s.registries.map((r) => ({ ...r, token: "" }));
    renderRegistries();
    const m = $("#settings-saved");
    m.hidden = false;
    setTimeout(() => (m.hidden = true), 2500);
  } catch (e) { toast(e.message, true); }
});

boot().catch((e) => showLogin("No se pudo conectar: " + e.message));
