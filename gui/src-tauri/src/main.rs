// IST Screen Cast: GUI untuk cast.sh / gcast.py.
//
// GUI berjalan sebagai user biasa. Semua yang butuh root dikerjakan oleh `cast.sh --gui`
// yang dijalankan lewat pkexec (satu kali minta izin per sesi). Komunikasi:
//   - stdout/stderr helper -> event "helper-line" (baris log, termasuk baris status "@@...")
//   - perintah GUI -> stdin helper (rescan | cast ... | stop)
//   - helper selesai -> event "helper-exit" (kode keluar)
// GUI tidak bisa mengirim sinyal ke proses root, jadi berhenti = kirim "stop" / tutup stdin.

use std::io::{BufRead, BufReader, Read, Write};
use std::path::PathBuf;
use std::process::{ChildStdin, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter, Manager, State, WindowEvent};

#[derive(Default)]
struct Helper {
    stdin: Option<ChildStdin>,
    running: bool,
    quit_after_exit: bool,
}

type Shared = Arc<Mutex<Helper>>;

const INSTALLED_DIR: &str = "/usr/lib/ist-screen-cast";

/// Folder berisi cast.sh, wfd_source.py, gcast.py, ...
/// IST_SCREENCAST_DIR -> repo (build debug / `npm run dev`) -> /usr/lib/ist-screen-cast (paket .deb)
fn script_dir() -> PathBuf {
    if let Ok(d) = std::env::var("IST_SCREENCAST_DIR") {
        return PathBuf::from(d);
    }
    if cfg!(debug_assertions) {
        // saat pengembangan selalu pakai script di repo, walaupun paket .deb juga terpasang
        return PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..");
    }
    let sys = PathBuf::from(INSTALLED_DIR);
    if sys.join("cast.sh").exists() {
        return sys;
    }
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..")
}

#[derive(Deserialize)]
struct StartOpts {
    iface: Option<String>,
    capture: Option<String>,
}

#[derive(Serialize, Clone)]
struct LineEvent {
    stream: &'static str,
    line: String,
}

fn pipe_lines<R: Read + Send + 'static>(app: AppHandle, stream: &'static str, r: R) {
    thread::spawn(move || {
        let reader = BufReader::new(r);
        for line in reader.split(b'\n') {
            let Ok(bytes) = line else { break };
            let line = String::from_utf8_lossy(&bytes).trim_end_matches('\r').to_string();
            let _ = app.emit("helper-line", LineEvent { stream, line });
        }
    });
}

fn valid_iface(s: &str) -> bool {
    !s.is_empty() && s.len() <= 15 && s.chars().all(|c| c.is_ascii_alphanumeric() || "-_.".contains(c))
}

#[tauri::command]
fn start_session(app: AppHandle, state: State<'_, Shared>, opts: StartOpts) -> Result<String, String> {
    let mut h = state.lock().unwrap();
    if h.running {
        return Err("Sesi sudah berjalan".into());
    }
    let dir = script_dir()
        .canonicalize()
        .map_err(|e| format!("folder script tidak ditemukan: {e}"))?;
    let script = dir.join("cast.sh");
    if !script.exists() {
        return Err(format!("{} tidak ada", script.display()));
    }
    let mut cmd = Command::new("pkexec");
    if dir == std::path::Path::new(INSTALLED_DIR) {
        // Paket .deb: aturan polkit app.ist.screencast.cast cocok dengan path program ini,
        // jadi user yang login di depan laptop tidak ditanya password.
        cmd.arg(&script);
    } else {
        // Pengembangan: tidak ada aturan polkit, pkexec meminta password administrator.
        cmd.arg("/bin/bash").arg(&script);
    }
    cmd.arg("--gui");
    if let Some(i) = opts.iface.filter(|s| !s.is_empty()) {
        if !valid_iface(&i) {
            return Err(format!("nama interface tidak valid: {i}"));
        }
        cmd.arg("--iface").arg(i);
    }
    match opts.capture.as_deref() {
        Some("mutter") | Some("portal") => {
            cmd.arg("--capture").arg(opts.capture.unwrap());
        }
        _ => {}
    }
    cmd.stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped());
    let mut child = cmd.spawn().map_err(|e| format!("gagal menjalankan pkexec: {e}"))?;
    h.stdin = child.stdin.take();
    h.running = true;
    h.quit_after_exit = false;
    pipe_lines(app.clone(), "out", child.stdout.take().unwrap());
    pipe_lines(app.clone(), "err", child.stderr.take().unwrap());

    let shared = state.inner().clone();
    thread::spawn(move || {
        let code = child.wait().ok().and_then(|s| s.code()).unwrap_or(-1);
        let quit = {
            let mut h = shared.lock().unwrap();
            h.running = false;
            h.stdin = None;
            h.quit_after_exit
        };
        let _ = app.emit("helper-exit", code);
        if quit {
            app.exit(0);
        }
    });
    Ok(dir.display().to_string())
}

fn write_line(h: &mut Helper, line: &str) -> Result<(), String> {
    let stdin = h.stdin.as_mut().ok_or("Sesi tidak berjalan")?;
    stdin
        .write_all(format!("{line}\n").as_bytes())
        .and_then(|_| stdin.flush())
        .map_err(|e| format!("gagal mengirim perintah: {e}"))
}

/// Perintah ke helper. Hanya karakter aman; validasi isinya dilakukan lagi di cast.sh.
#[tauri::command]
fn send_command(state: State<'_, Shared>, line: String) -> Result<(), String> {
    if line.is_empty()
        || line.len() > 300
        || !line.chars().all(|c| c.is_ascii_alphanumeric() || " .:=_-".contains(c))
    {
        return Err(format!("perintah tidak valid: {line}"));
    }
    write_line(&mut state.lock().unwrap(), &line)
}

fn request_stop(h: &mut Helper) {
    let _ = write_line(h, "stop");
    h.stdin = None; // tutup stdin: helper tetap berhenti walau "stop" tidak terbaca
}

#[tauri::command]
fn stop_session(state: State<'_, Shared>) {
    request_stop(&mut state.lock().unwrap());
}

#[tauri::command]
fn session_running(state: State<'_, Shared>) -> bool {
    state.lock().unwrap().running
}

/// Interface WiFi di laptop (untuk opsi Advanced), tanpa root.
#[tauri::command]
fn wifi_interfaces() -> Vec<String> {
    let mut v: Vec<String> = std::fs::read_dir("/sys/class/net")
        .map(|rd| {
            rd.filter_map(|e| e.ok())
                .filter(|e| e.path().join("wireless").is_dir())
                .map(|e| e.file_name().to_string_lossy().to_string())
                .collect()
        })
        .unwrap_or_default();
    v.sort();
    v
}

fn main() {
    let shared: Shared = Arc::new(Mutex::new(Helper::default()));
    tauri::Builder::default()
        .manage(shared)
        .invoke_handler(tauri::generate_handler![
            start_session,
            send_command,
            stop_session,
            session_running,
            wifi_interfaces
        ])
        .on_window_event(|window, event| {
            // Jendela ditutup saat sesi berjalan: hentikan + bersihkan dulu, baru keluar.
            if let WindowEvent::CloseRequested { api, .. } = event {
                let st = window.state::<Shared>();
                let mut h = st.lock().unwrap();
                if h.running {
                    api.prevent_close();
                    if !h.quit_after_exit {
                        h.quit_after_exit = true;
                        request_stop(&mut h);
                        let _ = window.emit("closing", ());
                    }
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("gagal menjalankan IST Screen Cast");
}
