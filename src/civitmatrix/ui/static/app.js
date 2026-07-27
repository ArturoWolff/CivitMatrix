/* CivitMatrix Win95 UI */
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

let models = [];
let eventCursor = 0;
let lastPct = 0;
let uiRunAlive = false;
const THEME_KEY = "civitmatrix.theme";
const DIR_KEYS = [
  "LORA",
  "LoCon",
  "DoRA",
  "Checkpoint",
  "TextualInversion",
  "Embedding",
  "VAE",
  "Workflows",
  "Controlnet",
  "Upscaler",
  "Hypernetwork",
  "AestheticGradient",
  "MotionModule",
  "Poses",
  "Wildcards",
  "Detection",
  "TextEncoder",
  "UNet",
  "LLM",
  "Other",
];

const SM_SUBDIRS = {
  LORA: "Lora",
  LoCon: "Lora",
  DoRA: "Lora",
  Checkpoint: "StableDiffusion",
  TextualInversion: "Embeddings",
  Embedding: "Embeddings",
  VAE: "VAE",
  Workflows: "Workflows",
  Controlnet: "ControlNet",
  Upscaler: "ESRGAN",
  Hypernetwork: "Hypernetworks",
  AestheticGradient: "AestheticGradients",
  MotionModule: "Motion",
  Poses: "Poses",
  Wildcards: "Wildcards",
  Detection: "Detection",
  TextEncoder: "TextEncoders",
  UNet: "UNet",
  LLM: "VLM",
  Other: "Other",
};

let uiToken = "";

function setStatus(msg) {
  $("#statusMsg").textContent = msg;
}

async function ensureToken() {
  if (uiToken) return uiToken;
  const res = await fetch("/api/session", { cache: "no-store" });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || !data.token) throw new Error(data.error || "UI session unavailable");
  uiToken = data.token;
  return uiToken;
}

async function api(path, opts = {}) {
  const token = await ensureToken();
  const headers = {
    "Content-Type": "application/json",
    "X-CivitMatrix-Token": token,
    ...(opts.headers || {}),
  };
  const res = await fetch(path, { ...opts, headers });
  const data = await res.json().catch(() => ({}));
  if (res.status === 401) {
    uiToken = "";
    throw new Error(data.error || "UI session expired — reload the page");
  }
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

function filterPayload() {
  const maxRaw = Number($("#fMax").value);
  return {
    type: $("#fType").value || "All",
    baseModel: $("#fBase").value || "All",
    sort: $("#fSort").value,
    nsfw: $("#fNsfw").checked,
    format: $("#fFormat").value || "All",
    checkpointType: $("#fCheckpoint").value || "All",
    updatedFrom: $("#fUpdatedFrom").value || "",
    updatedTo: $("#fUpdatedTo").value || "",
    category: $("#fCategory").value || "All",
    users: $("#fUsers").value.trim(),
    tagInclude: $("#fTagInc").value.trim(),
    tagExclude: $("#fTagExc").value.trim(),
    maxResults: Number.isFinite(maxRaw) ? maxRaw : 500,
    downloadAll: $("#fDownloadAll").checked,
  };
}

function fmtSize(kb) {
  if (kb == null || kb === "") return "—";
  const mib = Number(kb) / 1024;
  if (mib >= 1) return `${mib.toFixed(1)} MiB`;
  return `${Number(kb).toFixed(0)} KiB`;
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function versionCellHtml(m) {
  const vers = m.versions || [];
  let opts = "";
  for (const v of vers) {
    opts += `<option value="${v.id}" data-size="${v.sizeKB != null ? v.sizeKB : ""}">${escapeHtml(
      v.name || String(v.id)
    )} (${v.id})</option>`;
  }
  const id = m.id;
  return `<div class="ver-cell" data-id="${id}">
    <div class="ver-mode">
      <label><input type="radio" name="vm-${id}" value="latest" checked /> latest</label>
      <label><input type="radio" name="vm-${id}" value="pick" ${vers.length ? "" : "disabled"} /> pick…</label>
    </div>
    <select class="ver" data-id="${id}" multiple size="${Math.min(4, Math.max(2, vers.length || 2))}" disabled title="Ctrl/Cmd-click for multiple versions">${opts || "<option disabled>(no versions)</option>"}</select>
  </div>`;
}

function rowSizeLabel(m, versionIds) {
  const vers = m.versions || [];
  if (!vers.length) return "—";
  if (!versionIds || versionIds.length === 0 || (versionIds.length === 1 && versionIds[0] === "latest")) {
    return fmtSize(vers[0].sizeKB);
  }
  let total = 0;
  let n = 0;
  for (const id of versionIds) {
    const v = vers.find((x) => String(x.id) === String(id));
    if (v && v.sizeKB != null) {
      total += Number(v.sizeKB);
      n += 1;
    }
  }
  if (!n) return "—";
  if (n === 1) return fmtSize(total);
  return `${fmtSize(total)} (${n})`;
}

function versionIdsForRow(id) {
  const cell = document.querySelector(`.ver-cell[data-id="${id}"]`);
  if (!cell) return ["latest"];
  const mode = cell.querySelector('input[type="radio"]:checked');
  if (!mode || mode.value === "latest") return ["latest"];
  const sel = cell.querySelector("select.ver");
  const vals = sel ? [...sel.selectedOptions].map((o) => o.value) : [];
  const ids = vals.map((v) => Number(v)).filter((n) => Number.isFinite(n));
  return ids.length ? ids : ["latest"];
}

function syncVerMode(cell) {
  const mode = cell.querySelector('input[type="radio"]:checked');
  const sel = cell.querySelector("select.ver");
  if (!sel) return;
  const pick = mode && mode.value === "pick";
  sel.disabled = !pick;
  if (pick && sel.selectedOptions.length === 0 && sel.options.length) {
    sel.options[0].selected = true;
  }
}

function updateRowSize(id) {
  const m = models.find((x) => String(x.id) === String(id));
  const td = document.querySelector(`td.size-cell[data-id="${id}"]`);
  if (!m || !td) return;
  td.textContent = rowSizeLabel(m, versionIdsForRow(id));
}

function renderModels() {
  const body = $("#modelBody");
  body.innerHTML = "";
  for (const m of models) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><input type="checkbox" class="rowchk" data-id="${m.id}" checked /></td>
      <td class="name" title="${escapeHtml(m.name || "")}">${escapeHtml(m.name || "")}</td>
      <td>${escapeHtml(m.creator || "")}</td>
      <td>${versionCellHtml(m)}</td>
      <td class="size-cell" data-id="${m.id}">${rowSizeLabel(m, ["latest"])}</td>`;
    body.appendChild(tr);
  }
  updateSelectedLabel();
}

function updateSelectedLabel() {
  const checks = $$(".rowchk");
  const on = checks.filter((c) => c.checked).length;
  const all = checks.length;
  const label = all && on === all ? "all" : String(on);
  const trunc = models._truncated ? " (preview truncated — Start still downloads ALL if checked)" : "";
  $("#matchInfo").textContent = `preview: ${all}${trunc} · selected: ${label}`;
}

function collectSelection() {
  const out = [];
  for (const chk of $$(".rowchk")) {
    if (!chk.checked) continue;
    const id = Number(chk.dataset.id);
    out.push({ modelId: id, versionIds: versionIdsForRow(id) });
  }
  return out;
}

function applyTheme(name) {
  const theme = name === "modern" ? "modern" : "win95";
  document.documentElement.setAttribute("data-theme", theme);
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch (_) {
    /* ignore */
  }
  const sel = $("#fTheme");
  if (sel) sel.value = theme;
}

async function populate() {
  setStatus("Populating…");
  $("#btnPopulate").disabled = true;
  try {
    const data = await api("/api/populate", {
      method: "POST",
      body: JSON.stringify(filterPayload()),
    });
    if (data.error) throw new Error(data.error);
    models = data.items || [];
    models._truncated = !!data.truncated;
    renderModels();
    setStatus(`Populated ${models.length} model(s).`);
  } catch (e) {
    setStatus(`Populate failed: ${e.message}`);
  } finally {
    $("#btnPopulate").disabled = false;
  }
}

function resetLogTail() {
  eventCursor = 0;
  lastPct = 0;
  const box = $("#logBox");
  if (box) box.textContent = "";
}

async function startRun() {
  if (uiRunAlive) {
    setStatus("A run is already active.");
    return;
  }
  const downloadAll = $("#fDownloadAll").checked;
  const selection = downloadAll ? [] : collectSelection();
  if (!downloadAll && !selection.length) {
    setStatus("Nothing selected. Check rows or enable Download all matching filters.");
    return;
  }
  const body = {
    ...filterPayload(),
    selection,
    downloadAll,
    concurrency: 2,
  };
  resetLogTail();
  setStatus(
    downloadAll
      ? "Starting full-catalog run (all matching filters)…"
      : `Starting run for ${selection.length} selected model(s)…`
  );
  try {
    const data = await api("/api/run", { method: "POST", body: JSON.stringify(body) });
    if (data.error || data.ok === false) throw new Error(data.error || "spawn failed");
    uiRunAlive = true;
    $("#btnStart").disabled = true;
    setStatus(`Run started (pid ${data.pid}). Watch Status / Console.`);
  } catch (e) {
    uiRunAlive = false;
    $("#btnStart").disabled = false;
    setStatus(`Start failed: ${e.message}`);
  }
}

async function controlAction(path, label) {
  setStatus(`${label}…`);
  try {
    const data = await api(path, { method: "POST" });
    if (data.error) throw new Error(data.error);
    const code = data.exitCode != null ? ` exit=${data.exitCode}` : "";
    const pid = data.pid != null ? ` pid=${data.pid}` : "";
    const msg = data.message ? ` ${data.message}` : "";
    setStatus(`${label} ok.${code}${pid}${msg}`);
  } catch (e) {
    setStatus(`${label} failed: ${e.message}`);
  }
}

async function retryFailedResume() {
  setStatus("Retrying failed (resume partials)…");
  try {
    const data = await api("/api/retry-failed", { method: "POST" });
    if (data.error || data.ok === false) throw new Error(data.error || "spawn failed");
    uiRunAlive = true;
    $("#btnStart").disabled = true;
    resetLogTail();
    setStatus(`Retry+resume started (pid ${data.pid}). Watch Status / Console.`);
  } catch (e) {
    uiRunAlive = false;
    $("#btnStart").disabled = false;
    setStatus(`Retry failed: ${e.message}`);
  }
}

async function browseDir(start) {
  const data = await api("/api/browse-dir", {
    method: "POST",
    body: JSON.stringify({ start: start || "" }),
  });
  if (data.error) throw new Error(data.error);
  if (data.cancelled) return null;
  return data.path || null;
}

async function loadDirectories() {
  const data = await api("/api/directories");
  if (data.apiKey) {
    // Should never happen — strip client-side if leaked
    delete data.apiKey;
  }
  const grid = $("#dirGrid");
  grid.innerHTML = "";
  const paths = data.paths || {};
  for (const key of DIR_KEYS) {
    if (!(key in paths) && key !== "Embedding") continue;
    const val = paths[key] || paths.TextualInversion || "";
    grid.insertAdjacentHTML(
      "beforeend",
      `<span>${escapeHtml(key)}</span>
       <input type="text" data-dir="${key}" value="${escapeHtml(val)}" />
       <button type="button" class="btn-browse" data-dir="${key}">Browse…</button>`
    );
  }
  $("#dModelsRoot").value = data.modelsRoot || "";
  $("#dBaseUrl").value = data.baseUrl || "";
  $("#dFloor").value = data.diskFloorGib ?? 2;
  $("#dApiKey").placeholder = data.apiKeySet ? "•••••••• (set)" : "(not set)";
  $("#dApiKey").value = "";
}

function applyRootToPaths() {
  const root = $("#dModelsRoot").value.trim().replace(/[/\\]+$/, "");
  if (!root) {
    $("#dirMsg").textContent = "Set Models root first.";
    return;
  }
  for (const input of $$("#dirGrid input[data-dir]")) {
    const sub = SM_SUBDIRS[input.dataset.dir] || "Other";
    input.value = `${root}/${sub}`;
  }
  $("#dirMsg").textContent = "Paths filled from models root (Save to persist).";
}

async function saveDirectories() {
  const paths = {};
  for (const input of $$("#dirGrid input[data-dir]")) {
    paths[input.dataset.dir] = input.value.trim();
  }
  const body = {
    modelsRoot: $("#dModelsRoot").value.trim(),
    paths,
    baseUrl: $("#dBaseUrl").value.trim(),
    diskFloorGib: Number($("#dFloor").value) || 0,
  };
  const key = $("#dApiKey").value.trim();
  if (key) body.apiKey = key;
  const data = await api("/api/directories", { method: "POST", body: JSON.stringify(body) });
  if (data.apiKey) delete data.apiKey;
  $("#dirMsg").textContent = "Saved (.env updated atomically; LORA_DIR synced).";
  $("#dApiKey").value = "";
  $("#dApiKey").placeholder = data.apiKeySet ? "•••••••• (set)" : "(not set)";
  if (data.modelsRoot) $("#dModelsRoot").value = data.modelsRoot;
}

async function loadFailures() {
  try {
    const data = await api("/api/failures");
    const rows = data.retryable || [];
    const body = $("#failBody");
    body.innerHTML = "";
    if (!rows.length) {
      body.innerHTML = `<tr><td colspan="4" class="muted">(no retryable failures)</td></tr>`;
    } else {
      for (const row of rows.slice(-50).reverse()) {
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td>${escapeHtml(row.modelId)}</td>
          <td title="${escapeHtml(row.reason || "")}">${escapeHtml(row.reason || "")}</td>
          <td>${row.retryable === false ? "no" : "yes"}</td>
          <td>${escapeHtml(row.eventId != null ? row.eventId : "—")}</td>`;
        body.appendChild(tr);
      }
    }
    $("#logFails").textContent = `Failures (retryable): ${rows.length}`;
  } catch (e) {
    $("#logFails").textContent = `Failures: error (${e.message})`;
  }
}

async function pollStatus() {
  try {
    const st = await api("/api/status");
    const job = st.job;
    const phase = job ? job.phase || "—" : "IDLE";
    const phaseUp = String(phase).toUpperCase();
    $("#runPhase").textContent = `Status: ${phaseUp}`;
    const counts = (job && job.counts) || {};
    $("#runCounts").textContent = Object.keys(counts)
      .map((k) => `${k}=${counts[k]}`)
      .join("  ");
    const cur = job && job.current;
    if (cur && (cur.modelName || cur.modelId != null)) {
      const name = cur.modelName || `model ${cur.modelId}`;
      $("#runCurrent").textContent = name;
    } else if (phaseUp === "DONE" || phaseUp === "CANCELLED" || phaseUp === "ERROR") {
      $("#runCurrent").textContent = "";
    }
    let pct = cur && cur.pct != null ? Number(cur.pct) : null;
    if (pct == null) {
      if (phaseUp === "DONE") pct = 100;
      else if (phaseUp === "RUNNING" || phaseUp === "PAUSED") pct = lastPct;
      else pct = lastPct;
    } else {
      lastPct = pct;
    }
    if (phaseUp === "DONE") lastPct = 100;
    $("#progBar").style.width = `${Math.max(0, Math.min(100, pct || 0))}%`;
    $("#logJob").textContent = job
      ? `Job: phase=${phase} listed=${counts.listed || 0} ok=${counts.ok || 0} skip_hash=${counts.skip_hash || 0} forbidden=${counts.forbidden || 0}`
      : "Job: —";
    uiRunAlive = !!st.uiRunAlive;
    $("#btnStart").disabled = uiRunAlive;
    if (st.retryableFailures != null) {
      $("#logFails").textContent = `Failures (retryable): ${st.retryableFailures}`;
    }
  } catch (_) {
    /* ignore */
  }
}

async function pollEvents() {
  try {
    const data = await api(`/api/events?after=${eventCursor}`);
    const box = $("#logBox");
    for (const line of data.lines || []) {
      const ev = line.event || line.type || "event";
      const mid = line.modelId != null ? ` model=${line.modelId}` : "";
      const name = line.modelName ? ` ${line.modelName}` : "";
      const extra = line.reason
        ? ` ${line.reason}`
        : line.localStem
          ? ` ${line.localStem}`
          : line.pct != null
            ? ` ${line.pct}%`
            : "";
      box.textContent += `[${line.ts || ""}] ${ev}${mid}${name}${extra}\n`;
    }
    if ((data.lines || []).length) {
      box.scrollTop = box.scrollHeight;
      // Cap console growth
      const lines = box.textContent.split("\n");
      if (lines.length > 2000) {
        box.textContent = lines.slice(-1500).join("\n");
      }
    }
    eventCursor = data.next || eventCursor;
  } catch (_) {
    /* ignore */
  }
}

function wireNav() {
  $$('input[name="view"]').forEach((r) => {
    r.addEventListener("change", () => {
      $$(".view").forEach((v) => v.classList.remove("active"));
      $(`#view-${r.value}`).classList.add("active");
      if (r.value === "directories") loadDirectories().catch((e) => setStatus(e.message));
      if (r.value === "logs") loadFailures().catch((e) => setStatus(e.message));
    });
  });
}

async function loadBaseModels() {
  const sel = $("#fBase");
  const prev = sel.value || "Anima";
  try {
    const data = await api("/api/enums");
    const list = Array.isArray(data.BaseModel) ? data.BaseModel : [];
    if (!list.length) return;
    sel.innerHTML = "";
    const all = document.createElement("option");
    all.value = "All";
    all.textContent = "All";
    sel.appendChild(all);
    for (const name of list) {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name;
      sel.appendChild(opt);
    }
    if (prev === "All" || [...sel.options].some((o) => o.value === prev)) {
      sel.value = prev;
    } else if (list.includes("Anima")) {
      sel.value = "Anima";
    } else {
      sel.value = "All";
    }
  } catch (e) {
    setStatus(`Base model list: ${e.message}`);
  }
}

function wire() {
  wireNav();
  try {
    applyTheme(localStorage.getItem(THEME_KEY) || "win95");
  } catch (_) {
    applyTheme("win95");
  }
  $("#fTheme").addEventListener("change", (e) => applyTheme(e.target.value));
  loadBaseModels().catch(() => {});
  $("#btnPopulate").addEventListener("click", populate);
  $("#btnStart").addEventListener("click", startRun);
  $("#btnPause").addEventListener("click", () => controlAction("/api/pause", "Pause"));
  $("#btnResume").addEventListener("click", () => controlAction("/api/resume", "Resume"));
  $("#btnCancel").addEventListener("click", () => controlAction("/api/cancel", "Cancel"));
  $("#btnRetry").addEventListener("click", retryFailedResume);
  $("#btnRetryResume").addEventListener("click", retryFailedResume);
  $("#btnClearLog").addEventListener("click", () => {
    resetLogTail();
    setStatus("Console cleared.");
  });
  $("#btnRefreshFails").addEventListener("click", () =>
    loadFailures().then(() => setStatus("Failures refreshed.")).catch((e) => setStatus(e.message))
  );
  $("#btnSaveDirs").addEventListener("click", () =>
    saveDirectories().catch((e) => {
      $("#dirMsg").textContent = e.message;
    })
  );
  $("#btnBrowseRoot").addEventListener("click", async () => {
    try {
      const path = await browseDir($("#dModelsRoot").value.trim());
      if (path) {
        $("#dModelsRoot").value = path;
        $("#dirMsg").textContent = "Models root updated (Apply root → paths, then Save).";
      }
    } catch (e) {
      $("#dirMsg").textContent = e.message;
    }
  });
  $("#btnApplyRoot").addEventListener("click", applyRootToPaths);
  $("#dirGrid").addEventListener("click", async (e) => {
    const btn = e.target.closest(".btn-browse");
    if (!btn) return;
    const key = btn.dataset.dir;
    const input = $(`#dirGrid input[data-dir="${key}"]`);
    try {
      const path = await browseDir(input ? input.value : "");
      if (path && input) {
        input.value = path;
        $("#dirMsg").textContent = `${key} path updated (Save to persist).`;
      }
    } catch (err) {
      $("#dirMsg").textContent = err.message;
    }
  });
  $("#chkAll").addEventListener("change", (e) => {
    $$(".rowchk").forEach((c) => {
      c.checked = e.target.checked;
    });
    if (!e.target.checked) $("#fDownloadAll").checked = false;
    updateSelectedLabel();
  });
  $("#modelBody").addEventListener("change", (e) => {
    const t = e.target;
    if (t.classList.contains("rowchk")) {
      if (!t.checked) $("#fDownloadAll").checked = false;
      updateSelectedLabel();
      return;
    }
    const cell = t.closest(".ver-cell");
    if (!cell) return;
    if (t.type === "radio") {
      syncVerMode(cell);
      $("#fDownloadAll").checked = false;
    }
    if (t.classList.contains("ver")) {
      $("#fDownloadAll").checked = false;
    }
    updateRowSize(cell.dataset.id);
  });
  setInterval(pollStatus, 1500);
  setInterval(pollEvents, 2000);
  pollStatus();
  pollEvents();
}

wire();
