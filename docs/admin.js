// Admin editor for JSON files inside: bergham123/anime-news-bot (branch main by default)
//
// Features:
// - Load JSON file (array of articles)
// - List + search + select
// - Edit fields + extra fields: youtube_url, html_content, other_images[]
// - Convert & upload image as WEBP to repo (images/YYYY/MM/...) and set image URL
// - Commit updated JSON back to repo using GitHub Contents API

const OWNER = "bergham123";
const REPO  = "anime-news-bot";

let state = {
  filePath: "",
  branch: "main",
  sha: null,
  originalText: "",
  data: [],
  filteredIdxs: [],
  selectedIndex: -1, // index in state.data
};

const $ = (id) => document.getElementById(id);

function setStatus(msg, ok=true) {
  $("status").innerHTML = ok
    ? `<span class="ok">✅ ${escapeHtml(msg)}</span>`
    : `<span class="err">❌ ${escapeHtml(msg)}</span>`;
}

function escapeHtml(s) {
  return (s ?? "").toString()
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function rawUrlFor(relPath) {
  const branch = $("branch").value.trim() || "main";
  return `https://raw.githubusercontent.com/${OWNER}/${REPO}/${branch}/${relPath}`;
}

function getToken() {
  return $("token").value.trim();
}

async function ghGetFile(path, branch) {
  const token = getToken();
  if (!token) throw new Error("Token is required.");

  const url = `https://api.github.com/repos/${OWNER}/${REPO}/contents/${encodeURIComponent(path)}?ref=${encodeURIComponent(branch)}`;
  const r = await fetch(url, {
    headers: {
      "Authorization": `Bearer ${token}`,
      "Accept": "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
    },
  });

  if (!r.ok) {
    const txt = await r.text();
    throw new Error(`GET failed (${r.status}): ${txt.slice(0, 300)}`);
  }
  return await r.json(); // {content, sha, encoding...}
}

async function ghPutFile(path, branch, message, contentText, sha) {
  const token = getToken();
  if (!token) throw new Error("Token is required.");

  const url = `https://api.github.com/repos/${OWNER}/${REPO}/contents/${encodeURIComponent(path)}`;
  const body = {
    message,
    content: btoa(unescape(encodeURIComponent(contentText))), // base64 utf-8
    branch,
    sha, // required when updating existing file
  };

  const r = await fetch(url, {
    method: "PUT",
    headers: {
      "Authorization": `Bearer ${token}`,
      "Accept": "application/vnd.github+json",
      "Content-Type": "application/json",
      "X-GitHub-Api-Version": "2022-11-28",
    },
    body: JSON.stringify(body),
  });

  if (!r.ok) {
    const txt = await r.text();
    throw new Error(`PUT failed (${r.status}): ${txt.slice(0, 500)}`);
  }
  return await r.json();
}

function normalizeArticle(a) {
  // Keep your existing fields + add optional extra ones
  return {
    title: a.title ?? "",
    description_full: a.description_full ?? "",
    image: a.image ?? "",
    categories: Array.isArray(a.categories) ? a.categories : [],
    time: a.time ?? a.published_time ?? a.date ?? "",

    // extra admin fields
    youtube_url: a.youtube_url ?? "",
    html_content: a.html_content ?? "",
    other_images: Array.isArray(a.other_images) ? a.other_images : [],
  };
}

function serializeData() {
  // Keep output clean (no undefined)
  return JSON.stringify(state.data, null, 2);
}

function applySearch() {
  const q = $("search").value.trim().toLowerCase();
  const idxs = [];
  state.data.forEach((a, i) => {
    const t = (a.title || "").toLowerCase();
    const c = (a.categories || []).join(" ").toLowerCase();
    if (!q || t.includes(q) || c.includes(q)) idxs.push(i);
  });
  state.filteredIdxs = idxs;
  renderList();
}

function renderList() {
  const list = $("list");
  list.innerHTML = "";

  $("count").textContent = `Articles: ${state.data.length} | Showing: ${state.filteredIdxs.length}`;

  state.filteredIdxs.forEach((i) => {
    const a = state.data[i];
    const div = document.createElement("div");
    div.className = "item" + (i === state.selectedIndex ? " active" : "");
    div.innerHTML = `
      <div class="title">${escapeHtml(a.title || "(no title)")}</div>
      <div class="meta">${escapeHtml((a.categories||[]).join(" • "))}</div>
    `;
    div.onclick = () => selectArticle(i);
    list.appendChild(div);
  });
}

function selectArticle(i) {
  state.selectedIndex = i;
  const a = state.data[i];

  $("title").value = a.title || "";
  $("desc").value  = a.description_full || "";
  $("cats").value  = (a.categories || []).join(", ");
  $("time").value  = a.time || "";
  $("image").value = a.image || "";

  $("yt").value    = a.youtube_url || "";
  $("html").value  = a.html_content || "";
  $("others").value = (a.other_images || []).join("\n");

  updatePreview();
  renderList();
}

function updatePreview() {
  const url = $("image").value.trim();
  const img = $("imgPreview");
  img.src = url || "";
}

function saveLocalFromForm() {
  if (state.selectedIndex < 0) throw new Error("Select an article first.");

  const a = state.data[state.selectedIndex];
  a.title = $("title").value.trim();
  a.description_full = $("desc").value.trim();
  a.categories = $("cats").value
    .split(",")
    .map(s => s.trim())
    .filter(Boolean);

  a.time = $("time").value.trim();
  a.image = $("image").value.trim();

  a.youtube_url = $("yt").value.trim();
  a.html_content = $("html").value.trim();
  a.other_images = $("others").value
    .split("\n")
    .map(s => s.trim())
    .filter(Boolean);

  updatePreview();
  setStatus("Saved locally (not committed yet).");
  renderList();
}

function downloadBackup() {
  const blob = new Blob([state.originalText || ""], { type: "application/json;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  const safe = (state.filePath || "backup.json").split("/").pop();
  a.download = `backup-${safe}`;
  a.click();
  URL.revokeObjectURL(a.href);
}

function addNewArticle() {
  const a = normalizeArticle({});
  a.title = "مقال جديد";
  state.data.unshift(a);
  applySearch();
  selectArticle(0);
  setStatus("New article created (local). Edit then commit.");
}

function deleteSelected() {
  if (state.selectedIndex < 0) throw new Error("Select an article first.");
  const title = state.data[state.selectedIndex]?.title || "";
  if (!confirm(`حذف المقال؟\n\n${title}`)) return;
  state.data.splice(state.selectedIndex, 1);
  state.selectedIndex = -1;
  applySearch();
  setStatus("Deleted locally. Commit to apply on repo.");
}

// -------------------------
// WebP conversion + upload
// -------------------------
async function fileToWebPBlob(file, quality = 0.85) {
  // Convert any image file -> webp using canvas
  const img = new Image();
  img.crossOrigin = "anonymous";
  const url = URL.createObjectURL(file);

  await new Promise((res, rej) => {
    img.onload = () => res();
    img.onerror = () => rej(new Error("Failed to load image file."));
    img.src = url;
  });

  const canvas = document.createElement("canvas");
  canvas.width = img.naturalWidth || img.width;
  canvas.height = img.naturalHeight || img.height;
  const ctx = canvas.getContext("2d");
  ctx.drawImage(img, 0, 0);

  const blob = await new Promise((res) => canvas.toBlob(res, "image/webp", quality));
  URL.revokeObjectURL(url);

  if (!blob) throw new Error("WebP conversion failed.");
  return blob;
}

function sha1Hex(str) {
  // Browser subtle crypto sha-1
  const enc = new TextEncoder().encode(str);
  return crypto.subtle.digest("SHA-1", enc).then(buf =>
    Array.from(new Uint8Array(buf)).map(b => b.toString(16).padStart(2, "0")).join("")
  );
}

async function uploadWebPToRepo(webpBlob, folderBase) {
  if (state.selectedIndex < 0) throw new Error("Select an article first.");

  const a = state.data[state.selectedIndex];
  const title = a.title || "image";
  const originalImage = a.image || "";

  const h = (await sha1Hex(`${title}|${originalImage}`)).slice(0, 12);
  const safeTitle = title
    .toLowerCase()
    .replace(/\s+/g, "-")
    .replace(/[^a-z0-9\u0600-\u06FF\-]/g, "")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 60) || "image";

  const d = new Date();
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth()+1).padStart(2, "0");

  const relPath = `${folderBase}/${yyyy}/${mm}/${safeTitle}-${h}.webp`;

  // Check if file exists (GET). If exists, just set URL.
  try {
    const info = await ghGetFile(relPath, state.branch);
    const url = rawUrlFor(relPath);
    $("image").value = url;
    saveLocalFromForm();
    setStatus(`Image already exists. Using: ${relPath}`);
    return;
  } catch (e) {
    // likely 404 -> proceed upload
  }

  const arrayBuf = await webpBlob.arrayBuffer();
  const bytes = new Uint8Array(arrayBuf);
  let binary = "";
  for (let i=0;i<bytes.length;i++) binary += String.fromCharCode(bytes[i]);
  const b64 = btoa(binary);

  // Create new file (sha omitted)
  const token = getToken();
  if (!token) throw new Error("Token is required.");

  const url = `https://api.github.com/repos/${OWNER}/${REPO}/contents/${encodeURIComponent(relPath)}`;
  const body = {
    message: `Add image ${relPath}`,
    content: b64,
    branch: state.branch,
  };

  const r = await fetch(url, {
    method: "PUT",
    headers: {
      "Authorization": `Bearer ${token}`,
      "Accept": "application/vnd.github+json",
      "Content-Type": "application/json",
      "X-GitHub-Api-Version": "2022-11-28",
    },
    body: JSON.stringify(body),
  });

  if (!r.ok) {
    const txt = await r.text();
    throw new Error(`Image upload failed (${r.status}): ${txt.slice(0, 400)}`);
  }

  const publicUrl = rawUrlFor(relPath);
  $("image").value = publicUrl;
  saveLocalFromForm();
  setStatus(`Uploaded WebP + set image URL ✅ (${relPath})`);
}

// -------------------------
// Load + Commit JSON
// -------------------------
async function loadJson() {
  try {
    state.filePath = $("path").value.trim();
    state.branch = $("branch").value.trim() || "main";
    if (!state.filePath) throw new Error("Path is required.");

    setStatus("Loading JSON...", true);

    const info = await ghGetFile(state.filePath, state.branch);
    state.sha = info.sha;

    const text = decodeURIComponent(escape(atob(info.content.replace(/\n/g, ""))));
    state.originalText = text;

    const parsed = JSON.parse(text);
    if (!Array.isArray(parsed)) throw new Error("JSON file must be an array []");

    state.data = parsed.map(normalizeArticle);
    state.filteredIdxs = state.data.map((_, i) => i);
    state.selectedIndex = -1;

    applySearch();
    if (state.data.length) selectArticle(0);

    setStatus(`Loaded ✅ (${state.data.length} articles)`);
  } catch (e) {
    console.error(e);
    setStatus(e.message || "Load failed", false);
  }
}

async function commitJson() {
  try {
    saveLocalFromForm(); // ensure form -> state

    const newText = serializeData();
    const msg = `Admin update: ${state.filePath}`;

    setStatus("Committing to GitHub...", true);
    const res = await ghPutFile(state.filePath, state.branch, msg, newText, state.sha);

    // refresh sha after commit
    state.sha = res.content?.sha || state.sha;
    state.originalText = newText;

    setStatus("Committed ✅");
  } catch (e) {
    console.error(e);
    setStatus(e.message || "Commit failed", false);
  }
}

// -------------------------
// Wire UI
// -------------------------
$("loadBtn").onclick = loadJson;
$("backupBtn").onclick = downloadBackup;
$("newBtn").onclick = addNewArticle;

$("saveLocalBtn").onclick = () => {
  try { saveLocalFromForm(); }
  catch (e) { setStatus(e.message, false); }
};

$("commitBtn").onclick = commitJson;

$("deleteBtn").onclick = () => {
  try { deleteSelected(); }
  catch (e) { setStatus(e.message, false); }
};

$("search").addEventListener("input", applySearch);
$("image").addEventListener("input", updatePreview);

$("imgFile").addEventListener("change", async (ev) => {
  try {
    const file = ev.target.files?.[0];
    if (!file) return;

    const folder = $("imgFolder").value.trim() || "images";
    setStatus("Converting to WebP...", true);

    const webpBlob = await fileToWebPBlob(file, 0.85);

    setStatus("Uploading image to repo...", true);
    await uploadWebPToRepo(webpBlob, folder);
  } catch (e) {
    console.error(e);
    setStatus(e.message || "Upload failed", false);
  } finally {
    ev.target.value = "";
  }
});

// helpful default
setStatus("Ready. Put token, choose file path, click Load JSON.");
