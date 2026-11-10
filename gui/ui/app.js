// IST Screen Cast: UI. Backend (Rust) menjalankan `pkexec cast.sh --gui` dan meneruskan setiap baris
// output sebagai event "helper-line". Baris "@@..." adalah status terstruktur dari cast.sh; baris lain
// adalah log biasa (cast.sh, wfd_source.py, gcast.py) yang sebagian juga dipakai untuk status.
"use strict";

const { invoke } = window.__TAURI__.core;
const { listen } = window.__TAURI__.event;
const $ = (id) => document.getElementById(id);

// phase: idle | starting | checking | scanning | ready | casting | stopping
const S = {
  phase: "idle",
  devices: [],          // {kind, id, name, a, b}
  pending: [],          // perangkat yang sedang dikumpulkan saat memindai
  selected: null,       // "kind|id"
  checkErrors: [],      // baris "XX" dari pengecekan laptop
  lastError: "",
  streaming: false,
  quitting: false,
  approveTimer: null,
  log: [],
};
const DEFAULTS = { disp: "mirror", mode: "720p30", iface: "", capture: "auto", how: "auto", go: "14",
                   audio: "auto", hw: false, bitrate: "" };
const PREF_KEY = "ist-screen-cast.prefs";

// ------------------------------------------------------------------ preferensi
function loadPrefs() {
  let p = {};
  try { p = JSON.parse(localStorage.getItem(PREF_KEY) || "{}"); } catch (_) { p = {}; }
  return { ...DEFAULTS, ...p };
}
function savePrefs() {
  const p = readForm();
  try { localStorage.setItem(PREF_KEY, JSON.stringify({ ...p, lastDevice: S.selected || p.lastDevice })); } catch (_) {}
}
function readForm() {
  return {
    disp: document.querySelector('input[name="disp"]:checked').value,
    mode: $("mode").value, iface: $("iface").value, capture: $("capture").value,
    how: $("how").value, go: $("go").value, audio: $("audio").value, hw: $("hw").checked,
    bitrate: $("bitrate").value.trim(),
  };
}
function applyForm(p) {
  document.querySelector(`input[name="disp"][value="${p.disp}"]`).checked = true;
  $("mode").value = p.mode; $("capture").value = p.capture; $("how").value = p.how;
  $("go").value = p.go; $("audio").value = p.audio; $("hw").checked = !!p.hw; $("bitrate").value = p.bitrate;
  if ([...$("iface").options].some((o) => o.value === p.iface)) $("iface").value = p.iface;
  updateDispHint();
}

// ------------------------------------------------------------------ tampilan
function setPill(text, tone) { const el = $("pill"); el.textContent = text; el.dataset.tone = tone; }
function setStatus(title, detail = "") { $("st-title").textContent = title; $("st-detail").textContent = detail; }
function showError(msg) { const b = $("errbox"); b.textContent = msg; b.hidden = !msg; }
function toast(msg) {
  const t = $("toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toast.t); toast.t = setTimeout(() => (t.hidden = true), 4000);
}
function setStats(items) {
  const el = $("stats");
  el.replaceChildren(...items.map(([k, v]) => { const s = document.createElement("span"); s.append(k + " "); const b = document.createElement("b"); b.textContent = v; s.append(b); return s; }));
  el.hidden = items.length === 0;
}
const stats = new Map();
function stat(k, v) { stats.set(k, v); setStats([...stats.entries()]); }

function countdown(secs) {
  clearInterval(S.approveTimer);
  const box = $("countdown"), bar = box.querySelector(".bar");
  if (!secs) { box.hidden = true; return; }
  box.hidden = false; bar.style.transition = "none"; bar.style.width = "100%";
  const end = Date.now() + secs * 1000;
  requestAnimationFrame(() => { bar.style.transition = ""; });
  S.approveTimer = setInterval(() => {
    const left = Math.max(0, (end - Date.now()) / 1000);
    bar.style.width = (100 * left / secs) + "%";
    $("st-detail").textContent = `Setujui permintaan koneksi dengan remote TV. Sisa waktu ${Math.ceil(left)} detik.`;
    if (left <= 0) clearInterval(S.approveTimer);
  }, 1000);
}

function selectedDevice() {
  return S.devices.find((d) => `${d.kind}|${d.id}` === S.selected) || null;
}

function renderDevices() {
  const box = $("devices");
  box.classList.toggle("disabled", S.phase !== "ready");
  if (S.devices.length === 0) {
    const p = document.createElement("p"); p.className = "empty";
    p.textContent = {
      idle: "Tekan Pindai untuk mencari projektor/TV.",
      starting: "Meminta izin sistem…",
      checking: "Memeriksa kesiapan laptop…",
      scanning: "Memindai Miracast (Wi-Fi Direct) dan Google Cast (WiFi)…",
    }[S.phase] || "Tidak ada perangkat. Pastikan projektor di mode Screen Mirroring, TV menyala, atau TV Google Cast di WiFi yang sama, lalu Pindai lagi.";
    box.replaceChildren(p);
    return;
  }
  const groups = [["miracast", "Miracast (Wi-Fi Direct)"], ["gcast", "Google Cast (WiFi yang sama)"]];
  const nodes = [];
  for (const [kind, title] of groups) {
    const list = S.devices.filter((d) => d.kind === kind);
    if (!list.length) continue;
    const h = document.createElement("div"); h.className = "group-title"; h.textContent = title; nodes.push(h);
    for (const d of list) {
      const key = `${d.kind}|${d.id}`;
      const row = document.createElement("label"); row.className = "dev" + (S.selected === key ? " sel" : "");
      const r = document.createElement("input"); r.type = "radio"; r.name = "dev"; r.value = key;
      r.checked = S.selected === key; r.disabled = S.phase !== "ready";
      r.addEventListener("change", () => { S.selected = key; savePrefs(); renderDevices(); refreshControls(); });
      const main = document.createElement("div"); main.className = "dev-main";
      const n = document.createElement("div"); n.className = "dev-name"; n.textContent = d.name || d.id;
      const sub = document.createElement("div"); sub.className = "dev-sub";
      sub.textContent = kind === "miracast" ? `${d.id} · sinyal ${d.a} dBm` : `${d.id} · ${d.a}`;
      main.append(n, sub);
      const badge = document.createElement("span"); badge.className = "badge";
      badge.textContent = kind === "miracast" ? (d.b === "join" ? "join" : "negosiasi") : "Google Cast";
      row.append(r, main, badge); nodes.push(row);
    }
  }
  box.replaceChildren(...nodes);
}

function refreshControls() {
  const p = S.phase, dev = selectedDevice();
  const sessionActive = p !== "idle";
  $("btn-scan").disabled = !(p === "idle" || p === "ready");
  $("btn-scan").textContent = p === "ready" ? "Pindai ulang" : "Pindai";
  $("btn-cast").hidden = p === "casting" || p === "stopping";
  $("btn-stop").hidden = !(p === "casting" || p === "stopping");
  $("btn-stop").disabled = p === "stopping";
  $("btn-stop").textContent = p === "stopping" ? "Menghentikan…" : "Stop";
  $("btn-cast").disabled = !(p === "ready" && dev);
  // Opsi sesi hanya bisa diubah sebelum sesi dimulai; opsi lain terkunci saat cast berjalan
  const locked = p === "casting" || p === "stopping";
  for (const id of ["iface", "capture"]) $(id).disabled = sessionActive;
  $("sess-note").textContent = sessionActive ? "terkunci selama sesi ini" : "berlaku saat Pindai";
  for (const id of ["mode", "how", "go", "audio", "hw", "bitrate", "btn-reset"]) $(id).disabled = locked;
  document.querySelectorAll('input[name="disp"]').forEach((r) => (r.disabled = locked));
  $("fs-mira").disabled = locked || (dev && dev.kind === "gcast");
  $("sel-label").textContent = dev ? `${dev.name} · ${dev.kind === "gcast" ? "Google Cast" : "Miracast"}` : "Belum ada perangkat dipilih";
}

function setPhase(p) { S.phase = p; renderDevices(); refreshControls(); }

function updateDispHint() {
  const v = document.querySelector('input[name="disp"]:checked').value;
  $("disp-hint").textContent = v === "extend"
    ? "Perangkat menjadi monitor tambahan; atur posisinya di Settings → Displays."
    : "Layar laptop ditampilkan sama persis.";
}

function appendLog(line) {
  S.log.push(line);
  if (S.log.length > 3000) S.log.splice(0, S.log.length - 3000);
  const pre = $("log");
  const atBottom = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 4;
  pre.textContent = S.log.join("\n");
  if (atBottom) pre.scrollTop = pre.scrollHeight;
}

// ------------------------------------------------------------------ aksi
async function onScan() {
  showError("");
  if (S.phase === "ready") {
    S.pending = [];
    await send("rescan");
    return;
  }
  const f = readForm();
  S.devices = []; S.pending = []; S.checkErrors = []; S.lastError = ""; S.streaming = false;
  stats.clear(); setStats([]); S.log = []; $("log").textContent = "";
  setPhase("starting");
  setPill("Memulai", "busy");
  setStatus("Meminta izin sistem…", "Kalau muncul dialog password, masukkan password administrator.");
  try {
    const dir = await invoke("start_session", { opts: { iface: f.iface || null, capture: f.capture } });
    appendLog(`[GUI] folder script: ${dir}`);
  } catch (e) {
    setPhase("idle"); setPill("Gagal", "bad"); setStatus("Tidak bisa memulai sesi"); showError(String(e));
  }
}

async function send(line) {
  try { await invoke("send_command", { line }); return true; }
  catch (e) { toast(String(e)); return false; }
}

async function onCast() {
  const dev = selectedDevice(); if (!dev) return;
  const f = readForm();
  const parts = ["cast", dev.kind, dev.id, f.disp, f.mode];
  if (dev.kind === "miracast") {
    if (f.how !== "auto") parts.push(`how=${f.how}`);
    const go = parseInt(f.go, 10);
    if (Number.isInteger(go) && go >= 0 && go <= 15 && go !== 14) parts.push(`go=${go}`);
    if (f.audio !== "auto") parts.push(`audio=${f.audio}`);
    if (f.hw) parts.push("hw=1");
  }
  if (f.bitrate) {
    const b = parseInt(f.bitrate, 10);
    if (!(b >= 500 && b <= 30000)) { toast("Bitrate harus 500–30000 kbit/s"); return; }
    parts.push(`bitrate=${b}`);
  }
  savePrefs();
  showError(""); S.lastError = ""; S.streaming = false; stats.clear(); setStats([]);
  if (await send(parts.join(" "))) {
    $("adv").open = false;   // status (mis. hitung mundur persetujuan TV) harus terlihat
    document.querySelector(".card.status").scrollIntoView({ behavior: "smooth", block: "nearest" });
    setPhase("casting");
    setPill("Menyambung", "busy");
    setStatus(`Menyambung ke ${dev.name}…`, dev.kind === "gcast" ? "Membuka Google Cast di TV." : "Membuka Wi-Fi Direct.");
  }
}

async function onStop() {
  setPhase("stopping");
  setPill("Menghentikan", "busy");
  setStatus("Menghentikan dan membersihkan…", "Pengaturan jaringan sementara dikembalikan.");
  countdown(0);
  try { await invoke("stop_session"); } catch (e) { toast(String(e)); }
}

// ------------------------------------------------------------------ baris dari helper
function handleStatus(f) {
  const [kind, a = "", b = ""] = f;
  if (kind === "DEV") {
    const [, k, id, name, x, y] = f;
    S.pending.push({ kind: k, id, name, a: x, b: y });
    return;
  }
  if (kind === "ERROR") { toast(a); return; }
  if (kind !== "STATE") return;
  switch (a) {
    case "checking":
      setPhase("checking"); setPill("Memeriksa", "busy");
      setStatus("Memeriksa kesiapan laptop…", "WiFi, Wi-Fi Direct, GStreamer, tangkapan layar.");
      break;
    case "scanning":
      S.pending = [];
      setPhase("scanning"); setPill("Memindai", "busy");
      setStatus("Memindai perangkat…", "Sekitar 6 detik.");
      break;
    case "ready": {
      if (S.phase === "casting") { setPhase("ready"); break; } // perintah cast ditolak, kembali memilih
      S.devices = S.pending; S.pending = [];
      const prefs = loadPrefs();
      if (!selectedDevice()) {
        const keys = S.devices.map((d) => `${d.kind}|${d.id}`);
        S.selected = keys.includes(prefs.lastDevice) ? prefs.lastDevice : (keys.length === 1 ? keys[0] : null);
      }
      setPhase("ready");
      setPill(S.devices.length ? `${S.devices.length} perangkat` : "Tidak ada perangkat", S.devices.length ? "ok" : "warn");
      setStatus(S.devices.length ? "Pilih perangkat, lalu tekan Cast" : "Tidak ada perangkat",
                S.devices.length ? "Mode tampilan dan resolusi bisa diubah sebelum Cast." : "Pindai ulang setelah perangkat siap.");
      break;
    }
    case "connecting":
      setPill("Menyambung", "busy");
      setStatus(`Menyambung ke ${a && b ? a : (selectedDevice()?.name || a)}…`,
                b === "join" ? "Join ke grup Wi-Fi Direct perangkat." : b === "negotiate" ? "Negosiasi Wi-Fi Direct." : "");
      break;
    case "approve":
      setPill("Butuh persetujuan", "warn");
      setStatus("Setujui koneksi di TV", "");
      countdown(parseInt(b, 10) || 150);
      break;
    case "linked":
      countdown(0);
      setPill("Wi-Fi Direct tersambung", "busy");
      setStatus("Wi-Fi Direct tersambung", `Laptop sebagai ${b === "GO" ? "Group Owner" : "client"}. Menyiapkan IP…`);
      break;
    case "waiting":
      setStatus("Menunggu perangkat membuka sesi Miracast…", "Lihat layar projektor/TV.");
      break;
  }
}

const T = (s) => s.replace(/^\d\d:\d\d:\d\d /, "");
function handleText(raw) {
  const line = T(raw);
  let m;
  if ((m = line.match(/^\s*XX\s+(.*)/))) { S.checkErrors.push(m[1]); return; }
  if (/^\s*->\s/.test(line) && S.checkErrors.length) { S.checkErrors[S.checkErrors.length - 1] += "\n   " + line.trim(); return; }
  if (line.startsWith("== Membersihkan")) {
    if (S.phase !== "stopping") setPhase("stopping");
    setPill("Membersihkan", "busy"); countdown(0);
    setStatus(S.quitting ? "Membersihkan sebelum keluar…" : "Membersihkan…", "Pengaturan jaringan sementara dikembalikan.");
    return;
  }
  if (/^(Gagal tersambung|Koneksi Wi-Fi Direct ke .* putus|Tidak dapat IP|Server Miracast gagal start|Perangkat '.*' tidak ditemukan|GStreamer ERROR|TV tidak membuka|TV melaporkan|ffmpeg tidak terpasang|Koneksi Cast putus|Tidak ada perangkat Google Cast|Perangkat .* tidak ditemukan|portal .*: )/.test(line)
      || /Traceback|Error:|RuntimeError/.test(line)) {
    S.lastError = line;
  }
  if (/^Koneksi Wi-Fi Direct ke .* putus/.test(line)) S.streaming = false; // tampil sebagai error, bukan "selesai"
  if (S.phase !== "casting") return;
  if ((m = line.match(/^Wi-Fi Direct terputus sebelum perangkat tersambung: (.*)\. Menyambung ulang \((\d+\/\d+)\)/))) {
    setPill("Menyambung ulang", "warn"); countdown(0);
    setStatus(`Menyambung ulang (${m[2]})…`, m[1].charAt(0).toUpperCase() + m[1].slice(1) + ".");
    stat("Sambung ulang", m[2]);
    return;
  }
  if (/Invitation diterima/.test(line)) stat("Koneksi", "jalur cepat");
  else if (/Invitation tidak berhasil/.test(line)) stat("Koneksi", "negosiasi baru");
  else if ((m = line.match(/^IP laptop: (\S+)/))) stat("IP laptop", m[1]);
  else if (/^Portal: setujui dialog/.test(line)) setStatus("Setujui dialog berbagi layar", "Pilih layar di dialog desktop yang muncul.");
  else if ((m = line.match(/^Tangkapan layar: (.*)/))) stat("Tangkapan", m[1].replace(/ \(.*\)/, ""));
  else if ((m = line.match(/^Encoder video: (\S+)/))) stat("Encoder", m[1]);
  else if ((m = line.match(/^Sink connected from (\S+?):/))) {
    setStatus("Perangkat tersambung", "Negosiasi format (RTSP)…"); stat("Perangkat", m[1]);
  } else if ((m = line.match(/^Sink HDCP:\s+(.*)/))) stat("HDCP", m[1]);
  else if ((m = line.match(/^Chosen: (\S+), audio=([^,\s]+)/))) { stat("Format", m[1]); stat("Audio", m[2]); }
  else if (/^Streaming to /.test(line) || /^TV: PLAYING/.test(line)) {
    S.streaming = true; setPill("Sedang cast", "ok");
    setStatus("Sedang cast", "Tekan Stop untuk berhenti. Pengaturan jaringan dikembalikan otomatis.");
  } else if ((m = line.match(/^stream: ([\d.]+) MB terkirim/))) stat("Terkirim", `${m[1]} MB`);
  else if ((m = line.match(/^TV: video (\d+x\d+)/))) stat("Video di TV", m[1]);
  else if (/^Suara laptop dialihkan ke TV|^Suara ke TV aktif lagi/.test(line)) stat("Suara", "hanya di TV");
  else if (/^Suara ke TV di-mute dari laptop/.test(line)) { stat("Suara", "di-mute dari laptop"); toast("Suara ke TV di-mute dari laptop"); }
  else if (/^TV: BUFFERING/.test(line)) setStatus("TV mem-buffer stream…", "Google Cast butuh beberapa detik.");
  else if (/^Perintah LOAD terkirim/.test(line)) setStatus("TV membuka stream…", "Google Cast butuh beberapa detik.");
  else if ((m = line.match(/^Stream tersedia di (\S+)/))) stat("Stream", m[1]);
  else if (/^Session ended|^Waiting for the sink to reconnect/.test(line)) {
    S.streaming = false; setPill("Terputus", "warn"); setStatus("Perangkat memutus sesi", "Menunggu perangkat menyambung lagi…");
  } else if (/^TV menutup sesi/.test(line)) { setPill("Terputus", "warn"); setStatus("TV menutup sesi Cast"); }
}

function onLine({ line }) {
  appendLog(line);
  if (line.startsWith("@@")) handleStatus(line.slice(2).split("\t"));
  else handleText(line);
}

function onExit(code) {
  countdown(0);
  const was = S.phase;
  S.devices = []; S.pending = [];
  setPhase("idle");
  if (code === 126 || code === 127) {
    setPill("Dibatalkan", "warn");
    setStatus("Izin administrator tidak diberikan", "Tekan Pindai untuk mencoba lagi.");
    return;
  }
  if (S.checkErrors.length && (was === "checking" || code !== 0)) {
    setPill("Laptop belum siap", "bad");
    setStatus("Laptop belum siap untuk cast", "Perbaiki hal di bawah, lalu Pindai lagi.");
    showError(S.checkErrors.join("\n"));
    return;
  }
  if (S.lastError && !S.streaming) {
    setPill("Gagal", "bad");
    setStatus("Cast gagal", "Detail lengkap ada di Log.");
    showError(S.lastError);
    return;
  }
  setPill("Selesai", "idle");
  setStatus("Sesi selesai", "Pengaturan jaringan sudah dikembalikan. Tekan Pindai untuk cast lagi.");
}

// ------------------------------------------------------------------ mulai
async function init() {
  try {
    const ifs = await invoke("wifi_interfaces");
    for (const i of ifs) { const o = document.createElement("option"); o.value = i; o.textContent = i; $("iface").append(o); }
  } catch (_) {}
  applyForm(loadPrefs());
  $("btn-scan").addEventListener("click", onScan);
  $("btn-cast").addEventListener("click", onCast);
  $("btn-stop").addEventListener("click", onStop);
  $("btn-reset").addEventListener("click", () => { applyForm({ ...DEFAULTS, iface: "" }); savePrefs(); });
  $("btn-copy").addEventListener("click", async (e) => {
    e.preventDefault();
    try { await navigator.clipboard.writeText(S.log.join("\n")); toast("Log disalin"); } catch (_) { toast("Gagal menyalin log"); }
  });
  document.querySelectorAll('input[name="disp"]').forEach((r) => r.addEventListener("change", () => { updateDispHint(); savePrefs(); }));
  for (const id of ["mode", "iface", "capture", "how", "go", "audio", "hw", "bitrate"]) $(id).addEventListener("change", savePrefs);
  await listen("helper-line", (e) => onLine(e.payload));
  await listen("helper-exit", (e) => onExit(e.payload));
  await listen("closing", () => { S.quitting = true; setStatus("Membersihkan sebelum keluar…", "Jendela tertutup otomatis setelah selesai."); });
  if (await invoke("session_running")) setPhase("ready");
  else setPhase("idle");
}
init();
