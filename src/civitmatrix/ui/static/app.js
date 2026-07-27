/* CivitMatrix Win95 UI */
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

let models = [];
let eventCursor = 0;
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
  "Other",
];

function setStatus(msg) {
  $("#statusMsg").textContent = msg;
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

function filterPayload() {
  const maxRaw = Number($("#fMax").value);
  return {
    type: $("#fType").value,
    baseModel: $("#fBase").value.trim() || "Anima",
    sort: $("#fSort").value,
    nsfw: $("#fNsfw").checked,
    format: $("#fFormat").value,
    category: $("#fCategory").value.trim() || "any",
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

function versionSelectHtml(m) {
  const vers = m.versions || [];
  let opts = `<option value="latest" selected>latest</option>`;
  for (const v of vers) {
    opts += `<option value="${v.id}">${escapeHtml(v.name || String(v.id))} (${v.id})</option>`;
  }
  // multi: use select multiple small
  return `<select class="ver" data-id="${m.id}" multiple size="1" title="Ctrl+click for multiple versions; 'latest' alone = newest only">${opts}</select>`;
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function renderModels() {
  const body = $("#modelBody");
  body.innerHTML = "";
  for (const m of models) {
    const tr = document.createElement("tr");
    const size =
      (m.versions && m.versions[0] && m.versions[0].sizeKB) != null
        ? fmtSize(m.versions[0].sizeKB)
        : "—";
    tr.innerHTML = `
      <td><input type="checkbox" class="rowchk" data-id="${m.id}" checked /></td>
      <td class="name" title="${escapeHtml(m.name || "")}">${escapeHtml(m.name || "")}</td>
      <td>${escapeHtml(m.creator || "")}</td>
      <td>${versionSelectHtml(m)}</td>
      <td>${size}</td>`;
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
    const sel = document.querySelector(`select.ver[data-id="${id}"]`);
    let versionIds = ["latest"];
    if (sel) {
      const vals = [...sel.selectedOptions].map((o) => o.value);
      if (vals.length && !(vals.length === 1 && vals[0] === "latest")) {
        versionIds = vals.filter((v) => v !== "latest").map((v) => Number(v));
        if (!versionIds.length) versionIds = ["latest"];
      }
    }
    out.push({ modelId: id, versionIds });
  }
  return out;
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

async function startRun() {
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
  setStatus(
    downloadAll
      ? "Starting full-catalog run (all matching filters)…"
      : `Starting run for ${selection.length} selected model(s)…`
  );
  try {
    const data = await api("/api/run", { method: "POST", body: JSON.stringify(body) });
    if (data.error) throw new Error(data.error);
    setStatus(`Run started (pid ${data.pid}).`);
  } catch (e) {
    setStatus(`Start failed: ${e.message}`);
  }
}

async function retryFailedResume() {
  setStatus("Clearing pause + retrying failed (resume partials)…");
  try {
    await api("/api/resume", { method: "POST" });
    const data = await api("/api/retry-failed", { method: "POST" });
    if (data.error) throw new Error(data.error);
    setStatus(`Retry+resume started (pid ${data.pid}).`);
  } catch (e) {
    setStatus(`Retry failed: ${e.message}`);
  }
}

async function loadDirectories() {
  const data = await api("/api/directories");
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
       <span class="muted"></span>`
    );
  }
  $("#dBaseUrl").value = data.baseUrl || "";
  $("#dFloor").value = data.diskFloorGib ?? 2;
  $("#dApiKey").placeholder = data.apiKeySet ? "•••••••• (set)" : "(not set)";
  $("#dApiKey").value = "";
}

async function saveDirectories() {
  const paths = {};
  for (const input of $$("#dirGrid input[data-dir]")) {
    paths[input.dataset.dir] = input.value.trim();
  }
  const body = {
    paths,
    baseUrl: $("#dBaseUrl").value.trim(),
    diskFloorGib: Number($("#dFloor").value) || 0,
  };
  const key = $("#dApiKey").value.trim();
  if (key) body.apiKey = key;
  const data = await api("/api/directories", { method: "POST", body: JSON.stringify(body) });
  $("#dirMsg").textContent = "Saved.";
  $("#dApiKey").value = "";
  $("#dApiKey").placeholder = data.apiKeySet ? "•••••••• (set)" : "(not set)";
}

async function pollStatus() {
  try {
    const st = await api("/api/status");
    const job = st.job;
    const phase = job ? job.phase || "—" : "IDLE";
    $("#runPhase").textContent = `Status: ${String(phase).toUpperCase()}`;
    const counts = (job && job.counts) || {};
    $("#runCounts").textContent = Object.keys(counts)
      .map((k) => `${k}=${counts[k]}`)
      .join("  ");
    const cur = job && job.current;
    const pct = cur && cur.pct != null ? Number(cur.pct) : 0;
    $("#progBar").style.width = `${Math.max(0, Math.min(100, pct))}%`;
    $("#logJob").textContent = job
      ? `Job: phase=${phase} listed=${counts.listed || 0} ok=${counts.ok || 0} skip_hash=${counts.skip_hash || 0} forbidden=${counts.forbidden || 0}`
      : "Job: —";
    $("#logFails").textContent = `Failures (retryable): ${st.retryableFailures || 0}`;
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
      const extra = line.reason ? ` ${line.reason}` : line.localStem ? ` ${line.localStem}` : "";
      box.textContent += `[${line.ts || ""}] ${ev}${mid}${extra}\n`;
    }
    if ((data.lines || []).length) {
      box.scrollTop = box.scrollHeight;
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
    });
  });
}

function wire() {
  wireNav();
  $("#btnPopulate").addEventListener("click", populate);
  $("#btnStart").addEventListener("click", startRun);
  $("#btnPause").addEventListener("click", () => api("/api/pause", { method: "POST" }));
  $("#btnResume").addEventListener("click", () => api("/api/resume", { method: "POST" }));
  $("#btnCancel").addEventListener("click", () => api("/api/cancel", { method: "POST" }));
  $("#btnRetry").addEventListener("click", retryFailedResume);
  $("#btnRetryResume").addEventListener("click", retryFailedResume);
  $("#btnSaveDirs").addEventListener("click", () =>
    saveDirectories().catch((e) => {
      $("#dirMsg").textContent = e.message;
    })
  );
  $("#chkAll").addEventListener("change", (e) => {
    $$(".rowchk").forEach((c) => {
      c.checked = e.target.checked;
    });
    updateSelectedLabel();
  });
  $("#modelBody").addEventListener("change", (e) => {
    if (e.target.classList.contains("rowchk")) updateSelectedLabel();
  });
  setInterval(pollStatus, 1500);
  setInterval(pollEvents, 2000);
  pollStatus();
  pollEvents();
}

wire();
