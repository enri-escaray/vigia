"use strict";

/* Panel de Vigía: video en vivo, modalidades, alertas en tiempo real y editor de zonas. */

const $ = (sel, root = document) => root.querySelector(sel);
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const prefs = {
  get(key, fallback) {
    try {
      const value = localStorage.getItem(key);
      return value === null ? fallback : JSON.parse(value);
    } catch {
      return fallback;
    }
  },
  set(key, value) {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch {
      /* navegador sin almacenamiento: se ignora */
    }
  },
};

const RULE_LABEL = {
  intrusion: "Intrusión",
  presencia: "Presencia",
  merodeo: "Merodeo",
  cruce_linea: "Cruce de línea",
  objeto_abandonado: "Objeto abandonado",
  objeto_peligroso: "Objeto peligroso",
  aglomeracion: "Aglomeración",
  carrera: "Carrera",
  caida: "Caída",
  altercado: "Altercado",
  sabotaje: "Sabotaje",
  prueba: "Prueba",
};
const SEV_ORDER = ["critica", "alta", "media", "baja"];
const SEV_PLURAL = { critica: "críticas", alta: "altas", media: "medias", baja: "bajas" };
const PAGE = 40;

const state = {
  modes: null,
  status: null,
  tiles: new Map(),
  alerts: new Map(),
  oldestId: null,
  filters: { sev: prefs.get("vigia.sev", ""), pending: prefs.get("vigia.pending", false) },
  sound: prefs.get("vigia.sound", true),
  current: null,
  tab: "foto",
  unseen: 0,
};

/* ------------------------------------------------------------------ utilidades */
async function api(path, options = {}) {
  const init = { cache: "no-store", ...options };
  if (options.body !== undefined) {
    init.method = init.method || "POST";
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(options.body);
  }
  const res = await fetch(path, init);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    } catch {
      /* respuesta sin JSON */
    }
    throw new Error(detail || `Error ${res.status}`);
  }
  return res.status === 204 ? null : res.json();
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function relTime(ts) {
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return `hace ${Math.floor(s)} s`;
  if (s < 3600) return `hace ${Math.floor(s / 60)} min`;
  const d = new Date(ts * 1000);
  const hm = d.toLocaleTimeString("es", { hour: "2-digit", minute: "2-digit" });
  if (d.toDateString() === new Date().toDateString()) return hm;
  return `${d.toLocaleDateString("es", { day: "2-digit", month: "2-digit" })} ${hm}`;
}

function modeLabel(name) {
  const mode = state.modes?.modalidades.find((m) => m.nombre === name);
  return mode ? mode.etiqueta : name;
}

function toast({ title, text = "", sev = "", onClick = null, timeout = 6000 }) {
  const box = $("#toasts");
  const node = el("div", `toast${sev ? ` sev-${sev}` : ""}`);
  node.append(el("strong", "", title), el("small", "", text));
  node.addEventListener("click", () => {
    onClick?.();
    node.remove();
  });
  box.prepend(node);
  setTimeout(() => node.remove(), timeout);
  while (box.children.length > 4) box.lastElementChild.remove();
}

/* ------------------------------------------------------------------ sonido */
let audioCtx = null;
const BEEPS = {
  baja: [[660, 0.12]],
  media: [[880, 0.15], [0, 0.08], [880, 0.15]],
  alta: [[1000, 0.18], [0, 0.07], [1000, 0.18], [0, 0.07], [1000, 0.18]],
  critica: [[1400, 0.2], [900, 0.2], [1400, 0.2], [900, 0.2], [1400, 0.2]],
};

function ensureAudio() {
  try {
    audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    if (audioCtx.state === "suspended") audioCtx.resume();
  } catch {
    audioCtx = null;
  }
}

function beep(sev) {
  if (!state.sound) return;
  ensureAudio();
  if (!audioCtx) return;
  let t = audioCtx.currentTime + 0.02;
  for (const [freq, dur] of BEEPS[sev] || BEEPS.media) {
    if (freq) {
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      osc.type = "square";
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0.05, t);
      gain.gain.exponentialRampToValueAtTime(0.0001, t + dur);
      osc.connect(gain).connect(audioCtx.destination);
      osc.start(t);
      osc.stop(t + dur);
    }
    t += dur;
  }
}

function renderSoundButton() {
  const btn = $("#btn-sound");
  btn.setAttribute("aria-pressed", String(state.sound));
  btn.textContent = state.sound ? "🔔" : "🔕";
}

/* ------------------------------------------------------------------ modalidades */
function renderModes() {
  const info = state.modes;
  if (!info) return;
  const nav = $("#modes");
  nav.replaceChildren();
  const button = (label, color, selected, current, onClick, title) => {
    const b = el("button", `mode-btn${selected ? " selected" : ""}${current ? " current" : ""}`);
    b.type = "button";
    b.style.setProperty("--c", color);
    b.title = title || label;
    b.setAttribute("aria-pressed", String(selected));
    b.append(el("span", "dot"), document.createTextNode(label));
    b.addEventListener("click", onClick);
    return b;
  };
  nav.append(button("Automático", "#94a3b8", info.automatica, false, () => selectMode("auto", "Automático"), "Cambia de modalidad según el horario configurado"));
  for (const m of info.modalidades) {
    if (m.solo_camaras && info.actual !== m.nombre) continue; // modalidad fija de alguna cámara
    const selected = !info.automatica && info.seleccion === m.nombre;
    nav.append(button(m.etiqueta, m.color, selected, info.actual === m.nombre, () => selectMode(m.nombre, m.etiqueta), m.descripcion));
  }

  const current = info.modalidades.find((m) => m.nombre === info.actual);
  const strip = $("#mode-strip");
  strip.style.setProperty("--mode", current?.color || "");
  strip.replaceChildren();
  const head = el("span", "", "Modalidad actual: ");
  head.append(el("strong", "", current ? current.etiqueta : info.actual));
  head.append(document.createTextNode(info.automatica ? " · automática por horario" : " · elegida manualmente"));
  strip.append(head);
  if (current?.descripcion) strip.append(el("span", "muted", `— ${current.descripcion}`));
  const rules = el("span", "rules");
  for (const r of current?.reglas || []) {
    const chip = el("span", "rule-chip", r.titulo || RULE_LABEL[r.tipo] || r.tipo);
    chip.title = `${r.nombre} · severidad ${r.severidad}${r.zonas.length ? ` · zonas: ${r.zonas.join(", ")}` : ""}`;
    rules.append(chip);
  }
  if (!current?.reglas.length) rules.append(el("span", "rule-chip", "sin reglas activas"));
  strip.append(rules);
}

async function refreshModes() {
  try {
    state.modes = await api("/api/modalidades");
    renderModes();
  } catch {
    /* se reintenta con el próximo estado */
  }
}

async function selectMode(name, label) {
  if (!confirm(`¿Cambiar la modalidad a “${label}”?`)) return;
  try {
    state.modes = await api("/api/modalidad", { body: { modalidad: name } });
    renderModes();
    toast({ title: "Modalidad actualizada", text: `Ahora: ${modeLabel(state.modes.actual)}` });
  } catch (err) {
    toast({ title: "No se pudo cambiar la modalidad", text: err.message, sev: "alta" });
  }
}

/* ------------------------------------------------------------------ estado y cámaras */
function applyStatus(st) {
  state.status = st;
  $("#sys-name").textContent = st.sistema;
  const online = st.camaras.filter((c) => c.en_linea).length;
  $("#chip-cams").textContent = `${online}/${st.camaras.length} cámaras`;
  setPending(st.alertas.pendientes);
  renderStats(st.alertas.ultimas_24h);
  renderCameras(st.camaras);
  if (state.modes && (state.modes.actual !== st.modalidad || state.modes.seleccion !== st.seleccion)) refreshModes();
  updateTitle();
}

function setPending(count) {
  const chip = $("#chip-pending");
  chip.textContent = `${count} pendiente${count === 1 ? "" : "s"}`;
  chip.classList.toggle("has", count > 0);
  chip.dataset.count = count;
}

function renderStats(bySev) {
  const box = $("#stats");
  box.replaceChildren(el("span", "", "Últimas 24 h:"));
  for (const sev of SEV_ORDER) {
    const item = el("span", `sev-${sev}`);
    item.append(el("b", "", String(bySev[sev] || 0)), document.createTextNode(` ${SEV_PLURAL[sev]}`));
    box.append(item);
  }
}

function renderCameras(cams) {
  const grid = $("#cams");
  $("#cams-empty")?.remove();
  for (const cam of cams) {
    let tile = state.tiles.get(cam.id);
    if (!tile) {
      tile = createTile(cam);
      state.tiles.set(cam.id, tile);
      grid.append(tile.el);
      runFeed(tile);
    }
    updateTile(tile, cam);
  }
}

function createTile(cam) {
  const node = el("article", "cam");
  node.innerHTML = `
    <header class="cam-head"><span class="dot"></span><h3></h3><span class="cam-meta"></span></header>
    <div class="cam-view"><img alt=""></div>
    <footer class="cam-foot">
      <span class="fixed-mode" hidden></span><span class="integrity"></span><span class="rules-count"></span>
      <span class="cam-actions">
        <button class="btn small ghost" type="button" data-act="zones">Zonas y líneas</button>
        <button class="btn small ghost" type="button" data-act="full" title="Pantalla completa">⛶</button>
      </span>
    </footer>`;
  const tile = { id: cam.id, el: node, img: $("img", node), data: cam };
  $("h3", node).textContent = cam.nombre;
  tile.img.alt = `Video en vivo de ${cam.nombre}`;
  node.addEventListener("click", (ev) => {
    const act = ev.target.closest("[data-act]")?.dataset.act;
    if (act === "zones") openZoneEditor(tile.data);
    if (act === "full") {
      const view = $(".cam-view", node);
      (view.requestFullscreen || view.webkitRequestFullscreen)?.call(view);
    }
  });
  return tile;
}

function updateTile(tile, cam) {
  tile.data = cam;
  const dot = $(".dot", tile.el);
  dot.className = `dot ${cam.en_linea ? "on" : "off"}`;
  dot.title = cam.estado;
  $(".cam-meta", tile.el).textContent = cam.en_linea
    ? `${cam.fps} fps · ${cam.objetos} obj. · ${cam.detector || "—"}`
    : cam.estado;
  const integrity = $(".integrity", tile.el);
  integrity.textContent = `Integridad: ${cam.integridad_texto}`;
  integrity.classList.toggle("bad", !["ok", "calibrando"].includes(cam.integridad));
  integrity.title = cam.error || "";
  const rules = $(".rules-count", tile.el);
  rules.textContent = `· ${cam.reglas.length} regla${cam.reglas.length === 1 ? "" : "s"} activa${cam.reglas.length === 1 ? "" : "s"}`;
  rules.title = cam.reglas.join(", ");
  const fixed = $(".fixed-mode", tile.el);
  fixed.hidden = !cam.modalidad_fija;
  if (cam.modalidad_fija) {
    const mode = state.modes?.modalidades.find((m) => m.nombre === cam.modalidad);
    fixed.textContent = mode ? mode.etiqueta : cam.modalidad;
    fixed.style.setProperty("--c", mode?.color || "var(--accent)");
    fixed.title = "Modalidad fija de esta cámara (no cambia con la modalidad general)";
  }
}

async function runFeed(tile) {
  // Pide cuadro por cuadro (long-polling): no ocupa conexiones permanentes y
  // escala a muchas cámaras sin chocar con el límite de conexiones del navegador.
  let seq = 0;
  let shown = null;
  tile.img.addEventListener("load", () => {
    if (shown && shown !== tile.img.src) URL.revokeObjectURL(shown);
    shown = tile.img.src;
  });
  for (;;) {
    if (document.hidden) {
      await sleep(700);
      continue;
    }
    try {
      const res = await fetch(`/api/camaras/${encodeURIComponent(tile.id)}/cuadro?despues=${seq}`, { cache: "no-store" });
      if (res.status === 200) {
        seq = Number(res.headers.get("X-Seq")) || seq;
        tile.img.src = URL.createObjectURL(await res.blob());
      } else {
        await sleep(1000);
      }
    } catch {
      await sleep(2500);
    }
  }
}

/* ------------------------------------------------------------------ alertas */
function matchesFilters(a) {
  if (state.filters.sev && a.nivel < Number(state.filters.sev)) return false;
  if (state.filters.pending && a.reconocida) return false;
  return true;
}

function alertItem(a) {
  const li = el("li", `alert sev-${a.severidad}${a.reconocida ? " ack" : ""}`);
  li.dataset.id = a.id;
  li.innerHTML = `
    <img class="thumb" alt="" loading="lazy">
    <div class="alert-body">
      <div class="alert-top"><span class="badge"></span><time></time></div>
      <strong class="alert-title"></strong>
      <p class="alert-msg"></p>
      <span class="alert-cam"></span>
    </div>`;
  const thumb = $(".thumb", li);
  if (a.captura) thumb.src = a.captura;
  $(".badge", li).textContent = a.severidad_texto;
  const time = $("time", li);
  time.dateTime = a.fecha;
  time.dataset.ts = a.ts;
  time.textContent = relTime(a.ts);
  time.title = new Date(a.ts * 1000).toLocaleString("es");
  $(".alert-title", li).textContent = a.titulo;
  $(".alert-msg", li).textContent = a.mensaje;
  $(".alert-cam", li).textContent = `${a.camara} · ${modeLabel(a.modalidad)}`;
  li.addEventListener("click", () => openAlert(a.id));
  return li;
}

function addAlert(a, isNew) {
  state.alerts.set(a.id, a);
  if (!matchesFilters(a)) return;
  const li = alertItem(a);
  if (isNew) {
    li.classList.add("new");
    $("#alert-list").prepend(li);
  } else {
    $("#alert-list").append(li);
  }
  updateEmpty();
}

function updateEmpty() {
  $("#alerts-empty").hidden = $("#alert-list").children.length > 0;
}

async function loadAlerts(reset = true) {
  const params = new URLSearchParams({ limite: PAGE });
  if (state.filters.sev) params.set("severidad_min", state.filters.sev);
  if (state.filters.pending) params.set("pendientes", "true");
  if (!reset && state.oldestId) params.set("antes_de", state.oldestId);
  try {
    const data = await api(`/api/alertas?${params}`);
    if (reset) {
      state.alerts.clear();
      $("#alert-list").replaceChildren();
    }
    for (const a of data.alertas) addAlert(a, false);
    if (data.alertas.length) state.oldestId = data.alertas[data.alertas.length - 1].id;
    $("#btn-more").hidden = data.alertas.length < PAGE;
    setPending(data.resumen.pendientes);
    renderStats(data.resumen.ultimas_24h);
  } catch (err) {
    toast({ title: "No se pudieron cargar las alertas", text: err.message, sev: "alta" });
  }
  updateEmpty();
}

function onNewAlert(a) {
  if (state.alerts.has(a.id)) return;
  addAlert(a, true);
  setPending(Number($("#chip-pending").dataset.count || 0) + 1);
  toast({
    title: a.titulo,
    text: `${a.camara} · ${a.mensaje}`,
    sev: a.severidad,
    onClick: () => openAlert(a.id),
    timeout: a.nivel >= 4 ? 15000 : 7000,
  });
  beep(a.severidad);
  const tile = state.tiles.get(a.camara_id);
  if (tile) {
    tile.el.classList.add("flash");
    setTimeout(() => tile.el.classList.remove("flash"), 4000);
  }
  if (document.hidden) {
    state.unseen += 1;
    updateTitle();
  }
}

function onAlertUpdated(a) {
  state.alerts.set(a.id, a);
  const li = $(`#alert-list li[data-id="${a.id}"]`);
  if (li) {
    if (!matchesFilters(a)) li.remove();
    else li.classList.toggle("ack", a.reconocida);
  }
  if (state.current?.id === a.id) {
    fillAlertDialog(a);
    showTab(state.tab);
  }
  updateEmpty();
}

function updateTitle() {
  const name = state.status?.sistema || "Vigía";
  document.title = `${state.unseen ? `(${state.unseen}) ⚠ ` : ""}${name} · Centro de monitoreo`;
}

async function openAlert(id) {
  let a = state.alerts.get(id);
  try {
    a = await api(`/api/alertas/${id}`);
    state.alerts.set(id, a);
  } catch {
    /* se usa la copia local */
  }
  if (!a) return;
  fillAlertDialog(a);
  showTab("foto");
  const dlg = $("#dlg-alert");
  if (!dlg.open) dlg.showModal();
}

function fillAlertDialog(a) {
  state.current = a;
  const badge = $("#d-sev");
  badge.className = `badge sev-${a.severidad}`;
  badge.textContent = a.severidad_texto;
  $("#d-title").textContent = a.titulo;
  $("#d-msg").textContent = a.mensaje;
  $("#d-cam").textContent = a.camara;
  $("#d-date").textContent = new Date(a.ts * 1000).toLocaleString("es");
  $("#d-mode").textContent = modeLabel(a.modalidad);
  $("#d-rule").textContent = `${RULE_LABEL[a.tipo] || a.tipo} (${a.regla})`;
  $("#d-zone").textContent = a.zona || (a.extra?.linea ? `línea ${a.extra.linea}` : "—");
  $("#d-state").textContent = a.reconocida ? "Reconocida" : "Pendiente";
  $("#d-ack").disabled = a.reconocida;
  $("#d-ack").textContent = a.reconocida ? "Reconocida ✓" : "Reconocer";
  const img = $("#ev-img");
  if (a.captura && !img.src.endsWith(a.captura)) img.src = a.captura;
  const download = $("#d-download");
  download.hidden = !a.clip_listo;
  if (a.clip) download.href = a.clip;
  $('.tab[data-tab="clip"]').hidden = !a.clip;
}

function showTab(which) {
  const a = state.current;
  state.tab = which;
  document.querySelectorAll("#dlg-alert .tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === which));
  const video = $("#ev-video");
  const msg = $("#ev-clip-msg");
  const img = $("#ev-img");
  if (which === "clip" && a?.clip) {
    img.hidden = true;
    if (a.clip_listo) {
      msg.hidden = true;
      video.hidden = false;
      if (!video.src.endsWith(a.clip)) video.src = a.clip;
    } else {
      video.hidden = true;
      msg.hidden = false;
      msg.textContent =
        a.clip_estado === "no_disponible"
          ? "Clip no disponible: se borró por el límite de espacio o la retención configurada."
          : "El clip se está generando (incluye los segundos posteriores a la alerta). Vuelva a abrirla en unos segundos.";
    }
  } else {
    video.pause();
    video.hidden = true;
    msg.hidden = true;
    img.hidden = !a?.captura;
  }
}

async function acknowledge(id) {
  try {
    onAlertUpdated(await api(`/api/alertas/${id}/reconocer`, { method: "POST" }));
    const current = Number($("#chip-pending").dataset.count || 0);
    setPending(Math.max(0, current - 1));
  } catch (err) {
    toast({ title: "No se pudo reconocer la alerta", text: err.message, sev: "alta" });
  }
}

/* ------------------------------------------------------------------ editor de zonas */
const editor = { cam: null, items: [], selected: -1, drawing: null, drag: null, cursor: null };
const ZONE_COLOR = "#ffbf00";
const LINE_COLOR = "#28c8ff";

function sanitizeName(value) {
  return value
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/\s+/g, "_")
    .replace(/[^A-Za-z0-9_-]/g, "")
    .slice(0, 40);
}

async function openZoneEditor(cam) {
  editor.cam = cam;
  editor.items = [
    ...cam.zonas.map((z) => ({ kind: "zona", name: z.nombre, points: z.puntos.map((p) => [...p]) })),
    ...cam.lineas.map((l) => ({ kind: "linea", name: l.nombre, points: l.puntos.map((p) => [...p]) })),
  ];
  editor.selected = -1;
  editor.drawing = null;
  editor.drag = null;
  $("#z-cam").textContent = cam.nombre;
  $("#z-error").hidden = true;
  $("#z-finish").hidden = true;
  setHint();
  $("#dlg-zones").showModal();
  renderZoneList();
  await loadEditorImage();
}

async function loadEditorImage() {
  const img = $("#z-img");
  const stage = $("#z-stage");
  const ok = await new Promise((resolve) => {
    img.onload = () => resolve(true);
    img.onerror = () => resolve(false);
    img.src = `/api/camaras/${encodeURIComponent(editor.cam.id)}/captura?t=${Date.now()}`;
  });
  stage.style.aspectRatio = ok ? "" : "16 / 9";
  img.hidden = !ok;
  if (!ok) setHint("La cámara aún no tiene imagen; puede dibujar igual o reintentar con “↻ Imagen”.");
  resizeCanvas();
}

function resizeCanvas() {
  const canvas = $("#z-canvas");
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.round(canvas.clientWidth * dpr));
  canvas.height = Math.max(1, Math.round(canvas.clientHeight * dpr));
  drawEditor();
}

function setHint(text) {
  const d = editor.drawing;
  $("#z-hint").textContent =
    text ||
    (d?.kind === "zona"
      ? "Haga clic para agregar vértices. Termine con doble clic, clic en el primer punto, Enter o “Terminar zona”. Esc cancela."
      : d?.kind === "linea"
        ? "Haga clic en el punto inicial y luego en el final de la línea. Esc cancela."
        : "Elija “Nueva zona” o “Nueva línea” y haga clic sobre la imagen. Seleccione un elemento para moverlo o borrarlo.");
}

function toNorm(ev) {
  const canvas = $("#z-canvas");
  const rect = canvas.getBoundingClientRect();
  return [
    Math.min(1, Math.max(0, (ev.clientX - rect.left) / rect.width)),
    Math.min(1, Math.max(0, (ev.clientY - rect.top) / rect.height)),
  ];
}

function pxDist(a, b) {
  const canvas = $("#z-canvas");
  return Math.hypot((a[0] - b[0]) * canvas.clientWidth, (a[1] - b[1]) * canvas.clientHeight);
}

function findVertex(p) {
  for (let i = editor.items.length - 1; i >= 0; i -= 1) {
    const index = editor.items[i].points.findIndex((q) => pxDist(p, q) <= 10);
    if (index >= 0) return { item: i, index };
  }
  return null;
}

function pointInPolygon([x, y], poly) {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i, i += 1) {
    const [xi, yi] = poly[i];
    const [xj, yj] = poly[j];
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

function nearSegment(p, a, b) {
  const canvas = $("#z-canvas");
  const [w, h] = [canvas.clientWidth, canvas.clientHeight];
  const [px, py, ax, ay, bx, by] = [p[0] * w, p[1] * h, a[0] * w, a[1] * h, b[0] * w, b[1] * h];
  const len2 = (bx - ax) ** 2 + (by - ay) ** 2 || 1;
  const t = Math.max(0, Math.min(1, ((px - ax) * (bx - ax) + (py - ay) * (by - ay)) / len2));
  return Math.hypot(px - (ax + t * (bx - ax)), py - (ay + t * (by - ay))) <= 8;
}

function findItemAt(p) {
  for (let i = editor.items.length - 1; i >= 0; i -= 1) {
    const it = editor.items[i];
    if (it.kind === "linea" ? nearSegment(p, it.points[0], it.points[1]) : pointInPolygon(p, it.points)) return i;
  }
  return -1;
}

function uniqueName(kind) {
  const base = kind === "zona" ? "zona" : "linea";
  let n = 1;
  while (editor.items.some((it) => it.kind === kind && it.name === `${base}_${n}`)) n += 1;
  return `${base}_${n}`;
}

function startDrawing(kind) {
  editor.drawing = { kind, name: uniqueName(kind), points: [] };
  editor.selected = -1;
  $("#z-finish").hidden = true;
  setHint();
  renderZoneList();
  drawEditor();
}

function addDrawPoint(p) {
  const d = editor.drawing;
  if (d.kind === "zona") {
    if (d.points.length >= 3 && pxDist(p, d.points[0]) <= 12) {
      finishDrawing();
      return;
    }
    const last = d.points[d.points.length - 1];
    if (!last || pxDist(p, last) > 3) d.points.push(p);
    $("#z-finish").hidden = d.points.length < 3;
  } else {
    d.points.push(p);
    if (d.points.length === 2) finishDrawing();
  }
  drawEditor();
}

function finishDrawing() {
  const d = editor.drawing;
  if (!d) return;
  if ((d.kind === "zona" && d.points.length < 3) || (d.kind === "linea" && d.points.length !== 2)) return;
  editor.items.push(d);
  editor.selected = editor.items.length - 1;
  editor.drawing = null;
  $("#z-finish").hidden = true;
  setHint();
  renderZoneList();
  drawEditor();
  const inputs = document.querySelectorAll("#z-list input");
  inputs[inputs.length - 1]?.select();
}

function cancelDrawing() {
  editor.drawing = null;
  $("#z-finish").hidden = true;
  setHint();
  drawEditor();
}

function renderZoneList() {
  const list = $("#z-list");
  list.replaceChildren();
  if (!editor.items.length) {
    list.append(el("li", "hint small", "Esta cámara no tiene zonas ni líneas."));
    return;
  }
  editor.items.forEach((it, i) => {
    const li = el("li", `zone-item${i === editor.selected ? " selected" : ""}`);
    const kind = el("span", "kind", it.kind === "zona" ? "Zona" : "Línea");
    kind.style.color = it.kind === "zona" ? ZONE_COLOR : LINE_COLOR;
    const input = el("input");
    input.value = it.name;
    input.maxLength = 40;
    input.setAttribute("aria-label", "Nombre");
    input.addEventListener("input", () => {
      it.name = sanitizeName(input.value);
      if (input.value !== it.name) input.value = it.name;
      drawEditor();
    });
    input.addEventListener("focus", () => select(i, false));
    li.append(kind, input);
    if (it.kind === "linea") {
      const flip = el("button", "btn small ghost", "⇄");
      flip.type = "button";
      flip.title = "Invertir el sentido de entrada";
      flip.addEventListener("click", () => {
        it.points.reverse();
        drawEditor();
      });
      li.append(flip);
    }
    const del = el("button", "btn small danger", "✕");
    del.type = "button";
    del.title = "Eliminar";
    del.addEventListener("click", () => {
      editor.items.splice(i, 1);
      editor.selected = -1;
      renderZoneList();
      drawEditor();
    });
    li.append(del);
    li.addEventListener("click", (ev) => {
      if (ev.target === li || ev.target === kind) select(i);
    });
    list.append(li);
  });
}

function select(index, rerender = true) {
  editor.selected = index;
  document.querySelectorAll("#z-list .zone-item").forEach((node, i) => node.classList.toggle("selected", i === index));
  if (rerender) renderZoneList();
  drawEditor();
}

function withAlpha(hex, alpha) {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

function drawItem(ctx, it, selected, drawing) {
  const canvas = ctx.canvas;
  const dpr = window.devicePixelRatio || 1;
  const pts = it.points.map(([x, y]) => [x * canvas.width, y * canvas.height]);
  const color = it.kind === "zona" ? ZONE_COLOR : LINE_COLOR;
  ctx.lineWidth = (selected ? 3 : 2) * dpr;
  ctx.strokeStyle = color;
  if (it.kind === "zona" && pts.length) {
    ctx.beginPath();
    ctx.moveTo(pts[0][0], pts[0][1]);
    pts.slice(1).forEach((p) => ctx.lineTo(p[0], p[1]));
    if (drawing && editor.cursor) ctx.lineTo(editor.cursor[0] * canvas.width, editor.cursor[1] * canvas.height);
    if (!drawing) ctx.closePath();
    ctx.fillStyle = withAlpha(color, selected ? 0.3 : 0.16);
    if (!drawing || pts.length >= 2) ctx.fill();
    ctx.stroke();
  } else if (it.kind === "linea" && pts.length) {
    const end = pts[1] || (editor.cursor ? [editor.cursor[0] * canvas.width, editor.cursor[1] * canvas.height] : pts[0]);
    ctx.beginPath();
    ctx.moveTo(pts[0][0], pts[0][1]);
    ctx.lineTo(end[0], end[1]);
    ctx.stroke();
    const [dx, dy] = [end[0] - pts[0][0], end[1] - pts[0][1]];
    const len = Math.hypot(dx, dy);
    if (len > 5) {
      // La flecha apunta hacia el lado de "entrada": la derecha de A->B vista en pantalla.
      const [nx, ny] = [-dy / len, dx / len];
      const mid = [(pts[0][0] + end[0]) / 2, (pts[0][1] + end[1]) / 2];
      const size = 34 * dpr;
      const tip = [mid[0] + nx * size, mid[1] + ny * size];
      ctx.beginPath();
      ctx.moveTo(mid[0], mid[1]);
      ctx.lineTo(tip[0], tip[1]);
      ctx.stroke();
      const head = 9 * dpr;
      ctx.beginPath();
      ctx.moveTo(tip[0], tip[1]);
      ctx.lineTo(tip[0] - nx * head + ny * head * 0.7, tip[1] - ny * head - nx * head * 0.7);
      ctx.lineTo(tip[0] - nx * head - ny * head * 0.7, tip[1] - ny * head + nx * head * 0.7);
      ctx.closePath();
      ctx.fillStyle = color;
      ctx.fill();
    }
  }
  ctx.fillStyle = color;
  for (const p of pts) {
    ctx.beginPath();
    ctx.arc(p[0], p[1], (selected ? 6 : 4) * dpr, 0, Math.PI * 2);
    ctx.fill();
  }
  if (pts.length && it.name) {
    ctx.font = `600 ${12 * dpr}px system-ui, sans-serif`;
    const w = ctx.measureText(it.name).width + 10 * dpr;
    const [x, y] = [pts[0][0] + 8 * dpr, pts[0][1] - 20 * dpr];
    ctx.fillStyle = color;
    ctx.fillRect(x, y, w, 17 * dpr);
    ctx.fillStyle = "#0b1017";
    ctx.fillText(it.name, x + 5 * dpr, y + 12.5 * dpr);
  }
}

function drawEditor() {
  const canvas = $("#z-canvas");
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  editor.items.forEach((it, i) => drawItem(ctx, it, i === editor.selected, false));
  if (editor.drawing) drawItem(ctx, editor.drawing, true, true);
}

function bindEditor() {
  const canvas = $("#z-canvas");
  canvas.addEventListener("pointerdown", (ev) => {
    const p = toNorm(ev);
    if (editor.drawing) {
      addDrawPoint(p);
      return;
    }
    const vertex = findVertex(p);
    if (vertex) {
      editor.drag = vertex;
      canvas.setPointerCapture(ev.pointerId);
      select(vertex.item);
      return;
    }
    select(findItemAt(p));
  });
  canvas.addEventListener("pointermove", (ev) => {
    const p = toNorm(ev);
    editor.cursor = p;
    if (editor.drag) {
      editor.items[editor.drag.item].points[editor.drag.index] = p;
      drawEditor();
      return;
    }
    if (editor.drawing) drawEditor();
    canvas.style.cursor = !editor.drawing && findVertex(p) ? "grab" : "crosshair";
  });
  canvas.addEventListener("pointerup", () => {
    editor.drag = null;
  });
  canvas.addEventListener("dblclick", () => finishDrawing());

  const dlg = $("#dlg-zones");
  dlg.addEventListener("cancel", (ev) => {
    if (editor.drawing) {
      ev.preventDefault();
      cancelDrawing();
    }
  });
  dlg.addEventListener("keydown", (ev) => {
    if (ev.target.tagName === "INPUT") return;
    if (ev.key === "Enter" && editor.drawing) {
      ev.preventDefault();
      finishDrawing();
    }
    if ((ev.key === "Delete" || ev.key === "Backspace") && editor.selected >= 0 && !editor.drawing) {
      editor.items.splice(editor.selected, 1);
      editor.selected = -1;
      renderZoneList();
      drawEditor();
    }
  });
  $("#z-new-zone").addEventListener("click", () => startDrawing("zona"));
  $("#z-new-line").addEventListener("click", () => startDrawing("linea"));
  $("#z-finish").addEventListener("click", () => finishDrawing());
  $("#z-refresh").addEventListener("click", () => loadEditorImage());
  $("#z-cancel").addEventListener("click", () => dlg.close());
  $("#z-save").addEventListener("click", saveZones);
  window.addEventListener("resize", () => {
    if (dlg.open) resizeCanvas();
  });
}

async function saveZones() {
  const error = $("#z-error");
  error.hidden = true;
  if (editor.drawing) finishDrawing();
  for (const kind of ["zona", "linea"]) {
    const names = editor.items.filter((it) => it.kind === kind).map((it) => it.name);
    if (names.some((n) => !n)) return showZoneError("Todos los elementos necesitan un nombre.");
    const dup = names.find((n, i) => names.indexOf(n) !== i);
    if (dup) return showZoneError(`Hay dos ${kind === "zona" ? "zonas" : "líneas"} llamadas “${dup}”.`);
  }
  const shape = (it) => ({ nombre: it.name, puntos: it.points.map(([x, y]) => [+x.toFixed(4), +y.toFixed(4)]) });
  const body = {
    zonas: editor.items.filter((it) => it.kind === "zona").map(shape),
    lineas: editor.items.filter((it) => it.kind === "linea").map(shape),
  };
  const button = $("#z-save");
  button.disabled = true;
  try {
    const cam = await api(`/api/camaras/${encodeURIComponent(editor.cam.id)}/geometria`, { method: "PUT", body });
    const tile = state.tiles.get(cam.id);
    if (tile) updateTile(tile, cam);
    $("#dlg-zones").close();
    toast({ title: "Zonas guardadas", text: `${body.zonas.length} zona(s) y ${body.lineas.length} línea(s) en ${cam.nombre}` });
  } catch (err) {
    showZoneError(err.message);
  } finally {
    button.disabled = false;
  }
}

function showZoneError(message) {
  const error = $("#z-error");
  error.textContent = message;
  error.hidden = false;
}

/* ------------------------------------------------------------------ eventos en vivo */
function connectEvents() {
  const source = new EventSource("/api/eventos");
  const conn = $("#conn");
  source.onopen = () => conn.classList.add("on");
  source.onerror = () => conn.classList.remove("on"); // EventSource reintenta solo
  source.addEventListener("estado", (ev) => applyStatus(JSON.parse(ev.data).estado));
  source.addEventListener("alerta", (ev) => onNewAlert(JSON.parse(ev.data).alerta));
  source.addEventListener("alerta_actualizada", (ev) => onAlertUpdated(JSON.parse(ev.data).alerta));
  source.addEventListener("alertas_reconocidas", () => loadAlerts(true));
  source.addEventListener("modalidad", (ev) => {
    state.modes = JSON.parse(ev.data).modalidades;
    renderModes();
  });
}

/* ------------------------------------------------------------------ arranque */
function bindUI() {
  $("#f-sev").value = state.filters.sev;
  $("#f-pending").checked = state.filters.pending;
  $("#f-sev").addEventListener("change", (ev) => {
    state.filters.sev = ev.target.value;
    prefs.set("vigia.sev", state.filters.sev);
    loadAlerts(true);
  });
  $("#f-pending").addEventListener("change", (ev) => {
    state.filters.pending = ev.target.checked;
    prefs.set("vigia.pending", state.filters.pending);
    loadAlerts(true);
  });
  $("#btn-more").addEventListener("click", () => loadAlerts(false));
  $("#btn-ack-all").addEventListener("click", async () => {
    if (!confirm("¿Marcar todas las alertas pendientes como reconocidas?")) return;
    try {
      const res = await api("/api/alertas/reconocer-todas", { method: "POST" });
      toast({ title: "Alertas reconocidas", text: `${res.reconocidas} alerta(s)` });
    } catch (err) {
      toast({ title: "Error", text: err.message, sev: "alta" });
    }
  });
  $("#btn-sound").addEventListener("click", () => {
    state.sound = !state.sound;
    prefs.set("vigia.sound", state.sound);
    renderSoundButton();
    if (state.sound) beep("baja");
  });
  $("#btn-test").addEventListener("click", async () => {
    try {
      await api("/api/prueba", { method: "POST" });
      toast({ title: "Alerta de prueba enviada", text: "Revise los canales de notificación configurados." });
    } catch (err) {
      toast({ title: "No se pudo enviar la prueba", text: err.message, sev: "alta" });
    }
  });
  $("#d-ack").addEventListener("click", () => state.current && acknowledge(state.current.id));
  document.querySelectorAll("#dlg-alert .tab").forEach((tab) => tab.addEventListener("click", () => showTab(tab.dataset.tab)));
  $("#ev-video").addEventListener("error", () => {
    if ($("#ev-video").hidden) return;
    const msg = $("#ev-clip-msg");
    msg.hidden = false;
    msg.textContent = "Este navegador no puede reproducir el formato del clip. Use “Descargar clip”.";
  });
  $("#dlg-alert").addEventListener("close", () => {
    const video = $("#ev-video");
    video.pause();
    video.removeAttribute("src");
    video.load();
    state.current = null;
  });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      state.unseen = 0;
      updateTitle();
    }
  });
  document.addEventListener("pointerdown", ensureAudio, { once: true });
  bindEditor();
  renderSoundButton();
}

function tickClock() {
  $("#clock").textContent = new Date().toLocaleString("es", {
    weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
}

async function boot() {
  bindUI();
  tickClock();
  setInterval(tickClock, 1000);
  setInterval(() => document.querySelectorAll("#alert-list time").forEach((t) => (t.textContent = relTime(Number(t.dataset.ts)))), 20000);
  try {
    const [modes, status] = await Promise.all([api("/api/modalidades"), api("/api/estado")]);
    state.modes = modes;
    renderModes();
    applyStatus(status);
  } catch (err) {
    toast({ title: "No se pudo conectar con Vigía", text: err.message, sev: "critica", timeout: 15000 });
  }
  await loadAlerts(true);
  connectEvents();
}

boot();
