/* LuluJar browser client - vanilla JS, no build step. */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const api = async (path, options = {}) => {
    const response = await fetch(path, options);
    let payload = {};
    try { payload = await response.json(); } catch (_) { /* empty body */ }
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    return payload;
  };

  const state = {
    source: null,        // { name, path, size, kind, label }
    outputPath: "",
    fsMode: "destination",
    fsCurrent: "",
    polling: null,
  };

  const human = (bytes) => {
    if (!bytes && bytes !== 0) return "-";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let value = bytes, i = 0;
    while (value >= 1024 && i < units.length - 1) { value /= 1024; i++; }
    return i === 0 ? `${value} B` : `${value.toFixed(1)} ${units[i]}`;
  };

  const toast = (message, ms = 2600) => {
    const el = $("toast");
    el.textContent = message;
    el.classList.remove("hidden");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => el.classList.add("hidden"), ms);
  };

  /* ---------------------------------------------------------- health */
  async function loadHealth() {
    try {
      const health = await api("/api/health");
      const names = (health.engines || []).map((e) => e.name);
      const hasRar = names.includes("7zip") || names.includes("unrar") ||
                    names.includes("unar") || names.includes("unrar-cffi") ||
                    names.includes("rarfile") || names.includes("bsdtar");
      const chip = $("engineChip");
      chip.textContent = hasRar
        ? `engines: ${names.join(", ")}`
        : `engines: zip, tar (install 7-Zip or unrar for RAR)`;
      chip.className = "chip " + (hasRar ? "ok" : "warn");
      if (!state.outputPath) {
        $("destination").value = health.output_dir || "";
        state.outputPath = health.output_dir || "";
        browse(state.outputPath);
      }
    } catch (error) {
      $("engineChip").textContent = "server not reachable";
      $("engineChip").className = "chip warn";
    }
  }

  /* ---------------------------------------------------------- uploads */
  async function uploadFile(file) {
    $("errorBox").classList.add("hidden");
    $("sourceInfo").classList.remove("hidden");
    $("sourceName").textContent = file.name;
    $("sourceSize").textContent = human(file.size);
    $("sourceKind").textContent = "…";
    $("uploadProgress").classList.remove("hidden");
    setUploadProgress(0, "starting upload…");

    try {
      const started = await api(`/api/upload/start?name=${encodeURIComponent(file.name)}`, { method: "POST" });
      const chunkSize = 8 * 1024 * 1024;
      let offset = 0;
      while (offset < file.size) {
        const slice = file.slice(offset, Math.min(offset + chunkSize, file.size));
        const written = await api(
          `/api/upload/chunk?id=${started.id}&offset=${offset}`,
          { method: "POST", body: slice }
        );
        offset += written.written;
        setUploadProgress(file.size ? (offset / file.size) * 100 : 100,
                         `uploading ${human(offset)} of ${human(file.size)}`);
      }
      const done = await api(`/api/upload/complete?id=${started.id}`, { method: "POST" });
      state.source = done;
      $("sourceKind").textContent = done.label || done.kind;
      $("extractBtn").disabled = false;
      setUploadProgress(100, "upload complete");
      setTimeout(() => $("uploadProgress").classList.add("hidden"), 900);
      refreshEntries();
      refreshApk();
    } catch (error) {
      fail(error.message);
      $("uploadProgress").classList.add("hidden");
    }
  }

  const setUploadProgress = (percent, text) => {
    $("uploadBar").style.width = `${percent}%`;
    $("uploadStatus").textContent = text;
  };

  /* ---------------------------------------------------------- extract */
  async function startExtraction() {
    if (!state.source) return;
    $("errorBox").classList.add("hidden");
    $("resultBox").classList.add("hidden");
    $("extractBtn").disabled = true;

    let destination = $("destination").value.trim() || state.outputPath;
    if ($("newFolder").checked) {
      const base = state.source.name.replace(/\.[^.]+$/, "");
      destination = `${destination.replace(/\/+$/, "")}/${base}`;
    }

    try {
      const job = await api("/api/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          source_path: state.source.path,
          source_name: state.source.name,
          destination,
          kind: state.source.kind,
        }),
      });
      $("jobBox").classList.remove("hidden");
      $("jobStatus").dataset.job = job.id;
      state.outputPath = job.destination;
      pollJob(job.id);
    } catch (error) {
      fail(error.message);
      $("extractBtn").disabled = false;
    }
  }

  function pollJob(jobId) {
    clearInterval(state.polling);
    state.polling = setInterval(async () => {
      try {
        const job = await api(`/api/jobs/${jobId}`);
        $("jobBar").style.width = `${job.percent || 0}%`;
        $("jobStatus").textContent =
          job.status === "running" ? `extracting — ${job.percent || 0}%`
          : job.status === "queued" ? "queued…"
          : job.status;
        $("jobCurrent").textContent = job.current || job.phase || "";
        if (["done", "failed", "cancelled"].includes(job.status)) {
          clearInterval(state.polling);
          $("extractBtn").disabled = false;
          if (job.status === "done") {
            const result = job.result || {};
            $("jobStatus").textContent = "extraction complete";
            $("resultBox").innerHTML =
              `<strong>Done.</strong> ${result.files || 0} files, ${human(result.bytes_written || 0)}` +
              ` written to <span class="mono">${escapeHtml(job.destination)}</span>` +
              `<ul><li>engine: ${escapeHtml(job.engine || result.engine || "?")}</li>` +
              (job.warnings && job.warnings.length
                 ? `<li>${job.warnings.length} warning(s): ${escapeHtml(job.warnings.join("; "))}</li>` : "") +
              `</ul>`;
            $("resultBox").classList.remove("hidden");
            browse(job.destination);
            refreshApk();
          } else if (job.status === "cancelled") {
            $("jobStatus").textContent = "cancelled";
          } else {
            $("jobStatus").textContent = "failed";
            fail(job.error || "extraction failed");
          }
        }
      } catch (error) {
        clearInterval(state.polling);
        $("extractBtn").disabled = false;
        fail(error.message);
      }
    }, 450);
  }

  const fail = (message) => {
    $("errorBox").textContent = message;
    $("errorBox").classList.remove("hidden");
  };

  const escapeHtml = (value) =>
    String(value).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  /* ---------------------------------------------------------- browsing */
  async function browse(path) {
    const target = path || state.outputPath;
    if (!target) return;
    try {
      const data = await api(`/api/fs/list?path=${encodeURIComponent(target)}`);
      state.outputPath = data.path;
      renderCrumbs(data.breadcrumbs || [], data.path);
      renderTree(data.entries || [], data.path);
    } catch (error) {
      $("tree").innerHTML = `<div class="tree-empty">${escapeHtml(error.message)}</div>`;
    }
  }

  function renderCrumbs(crumbs, fallback) {
    const host = $("crumbs");
    if (!crumbs.length) { host.textContent = fallback || ""; return; }
    host.innerHTML = crumbs.map((crumb, index) =>
      `${index ? '<span class="sep">/</span>' : ""}<a data-path="${escapeHtml(crumb.path)}">${escapeHtml(crumb.name)}</a>`
    ).join("");
    host.querySelectorAll("a").forEach((link) =>
      link.addEventListener("click", () => browse(link.dataset.path)));
  }

  function renderTree(entries, basePath) {
    const host = $("tree");
    if (!entries.length) {
      host.innerHTML = `<div class="tree-empty">Nothing here yet — extract an archive to fill this view.</div>`;
      return;
    }
    host.innerHTML = entries.map((entry) => `
      <div class="tree-row" data-path="${escapeHtml(entry.path)}" data-dir="${entry.is_dir}">
        <span class="ic">${entry.is_dir ? "📁" : iconFor(entry.name)}</span>
        <span class="nm">${escapeHtml(entry.name)}</span>
        <span class="sz">${entry.is_dir ? "" : human(entry.size)}</span>
        ${entry.is_dir ? "" : `<a class="dl" href="/api/download?path=${encodeURIComponent(entry.path)}" download title="download">⬇</a>`}
      </div>`).join("");

    host.querySelectorAll(".tree-row").forEach((row) => {
      row.addEventListener("click", (event) => {
        if (event.target.tagName === "A") return;
        if (row.dataset.dir === "true") browse(row.dataset.path);
        else preview(row.dataset.path);
      });
    });
  }

  const iconFor = (name) => {
    const lower = name.toLowerCase();
    if (lower.endsWith(".apk")) return "🤖";
    if (lower.endsWith(".dex")) return "⚙️";
    if (lower.endsWith(".so")) return "🔌";
    if (lower.endsWith(".png") || lower.endsWith(".jpg") || lower.endsWith(".webp")) return "🖼️";
    if (lower.endsWith(".json") || lower.endsWith(".xml") || lower.endsWith(".txt")) return "📄";
    if (lower.endsWith(".jar")) return "☕";
    return "📄";
  };

  async function preview(path) {
    try {
      const data = await api(`/api/preview?path=${encodeURIComponent(path)}`);
      $("previewName").textContent = data.name;
      $("downloadLink").href = `/api/download?path=${encodeURIComponent(path)}`;
      $("previewBody").textContent = data.binary
        ? "[binary file — use Download to save it]"
        : (data.content || "[empty file]");
      $("previewPane").classList.remove("hidden");
    } catch (error) {
      toast(error.message);
    }
  }

  /* ---------------------------------------------------------- archive */
  async function refreshEntries() {
    if (!state.source) { $("entryList").innerHTML = ""; return; }
    try {
      const data = await api(`/api/entries?path=${encodeURIComponent(state.source.path)}`);
      $("entryList").innerHTML =
        `<p class="muted small">${data.count} entries · ${human(data.total_size)} uncompressed · ${escapeHtml(data.label)}</p>` +
        data.entries.map((entry) => `
          <div class="entry"><span>${entry.is_dir ? "📁" : "📄"}</span>
          <span>${escapeHtml(entry.path)}</span>
          <span class="sz">${entry.is_dir ? "" : human(entry.size)}</span></div>`).join("");
    } catch (error) {
      $("entryList").innerHTML = `<p class="muted small">${escapeHtml(error.message)}</p>`;
    }
  }

  async function refreshApk() {
    const host = $("apkInfo");
    if (!state.source || !/\.apk$/i.test(state.source.name)) {
      host.innerHTML = `<p class="muted">Select an <code>.apk</code> to inspect its manifest.</p>`;
      return;
    }
    try {
      const data = await api(`/api/apk?path=${encodeURIComponent(state.source.path)}`);
      const kv = (rows) => `<dl class="kv">${rows.filter(Boolean).map(([k, v]) => `<dt>${k}</dt><dd>${escapeHtml(v)}</dd>`).join("")}</dl>`;
      host.innerHTML = `
        <div class="apk-card">
          <h4>Application</h4>
          ${kv([
            ["package", data.package],
            ["version", data.version_name ? `${data.version_name} (code ${data.version_code ?? "?"})` : null],
            ["min SDK", data.min_sdk],
            ["target SDK", data.target_sdk],
            ["ABIs", (data.abis || []).join(", ")],
            ["signed", data.signed ? "yes" : "no"],
          ])}
        </div>
        <div class="apk-card">
          <h4>Permissions (${(data.permissions || []).length})</h4>
          <div class="chips">${(data.permissions || []).map((p) => `<span class="chip-sm">${escapeHtml(p)}</span>`).join("") || "<span class='muted small'>none</span>"}</div>
        </div>
        <div class="apk-card">
          <h4>Components</h4>
          ${kv([
            ["activities", (data.activities || []).join(", ")],
            ["services", (data.services || []).join(", ")],
            ["receivers", (data.receivers || []).join(", ")],
            ["providers", (data.providers || []).join(", ")],
          ])}
        </div>
        ${(data.warnings || []).length ? `<div class="apk-card"><h4>Warnings</h4>${data.warnings.map((w) => `<p class="muted small">${escapeHtml(w)}</p>`).join("")}</div>` : ""}`;
    } catch (error) {
      host.innerHTML = `<p class="muted small">${escapeHtml(error.message)}</p>`;
    }
  }

  /* ---------------------------------------------------------- fs modal */
  async function openFsModal(mode) {
    state.fsMode = mode;
    $("fsTitle").textContent = mode === "destination" ? "Choose a destination folder" : "Choose a file on this machine";
    $("fsModal").classList.remove("hidden");
    const start = mode === "destination" ? ($("destination").value || state.outputPath) : "";
    await fsBrowse(start);
  }

  async function fsBrowse(path) {
    const host = $("fsList");
    host.innerHTML = `<div class="tree-empty">loading…</div>`;
    try {
      const data = await api(`/api/fs/list?path=${encodeURIComponent(path || "")}`);
      state.fsCurrent = data.path;
      $("fsManual").value = data.path;
      renderCrumbsInto($("fsCrumbs"), data.breadcrumbs || [], data.path);
      const rows = (data.entries || []).map((entry) => `
        <div class="fs-row" data-path="${escapeHtml(entry.path)}" data-dir="${entry.is_dir}">
          <span>${entry.is_dir ? "📁" : iconFor(entry.name)}</span>
          <span class="nm">${escapeHtml(entry.name)}</span>
          <span class="sz">${entry.is_dir ? "" : human(entry.size)}</span>
        </div>`).join("");
      host.innerHTML = rows || `<div class="tree-empty">empty</div>`;
      host.querySelectorAll(".fs-row").forEach((row) => {
        row.addEventListener("click", () => {
          if (row.dataset.dir === "true") fsBrowse(row.dataset.path);
          else if (state.fsMode === "source") {
            $("fsModal").classList.add("hidden");
            useServerFile(row.dataset.path);
          }
        });
      });
    } catch (error) {
      host.innerHTML = `<div class="tree-empty">${escapeHtml(error.message)}</div>`;
    }
  }

  function renderCrumbsInto(host, crumbs, fallback) {
    if (!crumbs.length) { host.textContent = fallback || ""; return; }
    host.innerHTML = crumbs.map((crumb, index) =>
      `${index ? '<span class="sep">/</span>' : ""}<a data-path="${escapeHtml(crumb.path)}">${escapeHtml(crumb.name)}</a>`
    ).join("");
    host.querySelectorAll("a").forEach((link) =>
      link.addEventListener("click", () => fsBrowse(link.dataset.path)));
  }

  async function useServerFile(path) {
    try {
      const data = await api(`/api/entries?path=${encodeURIComponent(path)}`);
      const name = path.split("/").pop();
      state.source = { name, path, size: 0, kind: data.kind, label: data.label };
      $("sourceInfo").classList.remove("hidden");
      $("sourceName").textContent = name;
      $("sourceSize").textContent = "on this machine";
      $("sourceKind").textContent = data.label;
      $("extractBtn").disabled = false;
      refreshEntries();
      refreshApk();
    } catch (error) {
      toast(error.message);
    }
  }

  /* ---------------------------------------------------------- events */
  function wire() {
    $("pickFile").addEventListener("click", () => $("fileInput").click());
    $("fileInput").addEventListener("change", (event) => {
      const file = event.target.files[0];
      if (file) uploadFile(file);
      event.target.value = "";
    });

    const zone = $("dropzone");
    ["dragenter", "dragover"].forEach((name) =>
      zone.addEventListener(name, (event) => { event.preventDefault(); zone.classList.add("drag"); }));
    ["dragleave", "drop"].forEach((name) =>
      zone.addEventListener(name, (event) => { event.preventDefault(); zone.classList.remove("drag"); }));
    zone.addEventListener("drop", (event) => {
      const file = event.dataTransfer.files[0];
      if (file) uploadFile(file);
    });

    $("clearSource").addEventListener("click", () => {
      state.source = null;
      $("sourceInfo").classList.add("hidden");
      $("extractBtn").disabled = true;
    });

    $("extractBtn").addEventListener("click", startExtraction);
    $("cancelBtn").addEventListener("click", async () => {
      const jobId = $("jobStatus").dataset.job;
      if (jobId) await api(`/api/jobs/${jobId}/cancel`, { method: "POST" });
    });

    $("browseDest").addEventListener("click", () => openFsModal("destination"));
    $("pickServerFile").addEventListener("click", () => openFsModal("source"));

    $("refreshTree").addEventListener("click", () => browse(state.outputPath));
    $("filter").addEventListener("input", (event) => filterTree(event.target.value));

    $("deleteOut").addEventListener("click", async () => {
      if (!state.outputPath) return;
      if (!confirm(`Delete everything in ${state.outputPath}?`)) return;
      try {
        await api("/api/delete", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ path: state.outputPath }),
        });
        toast("output deleted");
        browse(state.outputPath);
      } catch (error) { toast(error.message); }
    });

    $("closePreview").addEventListener("click", () => $("previewPane").classList.add("hidden"));

    document.querySelectorAll(".tab").forEach((tab) => {
      tab.addEventListener("click", () => {
        document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
        document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
        tab.classList.add("active");
        $(`tab-${tab.dataset.tab}`).classList.add("active");
      });
    });

    $("fsClose").addEventListener("click", () => $("fsModal").classList.add("hidden"));
    $("fsUse").addEventListener("click", () => {
      const value = $("fsManual").value.trim();
      if (!value) return;
      if (state.fsMode === "destination") {
        $("destination").value = value;
        state.outputPath = value;
        browse(value);
      } else {
        $("fsModal").classList.add("hidden");
        useServerFile(value);
      }
      $("fsModal").classList.add("hidden");
    });

    $("themeToggle").addEventListener("click", () => {
      const root = document.documentElement;
      const next = root.dataset.theme === "dark" ? "light" : "dark";
      root.dataset.theme = next;
      $("themeToggle").textContent = next === "dark" ? "🌙" : "☀️";
      localStorage.setItem("lulujar-theme", next);
    });
    const saved = localStorage.getItem("lulujar-theme");
    if (saved) {
      document.documentElement.dataset.theme = saved;
      $("themeToggle").textContent = saved === "dark" ? "🌙" : "☀️";
    }
  }

  const filterTree = (term) => {
    const needle = term.trim().toLowerCase();
    document.querySelectorAll("#tree .tree-row").forEach((row) => {
      row.style.display = !needle || row.textContent.toLowerCase().includes(needle) ? "" : "none";
    });
  };

  wire();
  loadHealth();
})();
