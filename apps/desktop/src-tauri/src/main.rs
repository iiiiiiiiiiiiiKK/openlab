// OPENLAB desktop shell.
//
// Responsibilities:
//   1. start the bundled/local FastAPI backend on 127.0.0.1:3210,
//   2. wait until /api/health answers before showing the window,
//   3. forward backend logs to the Tauri log stream,
//   4. terminate the backend when the app exits.
//
// The webview loads the same URL the browser mode uses, so desktop and local web
// share one database and one data directory.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use tauri::{Manager, RunEvent, State};

const DEFAULT_PORT: u16 = 3210;
const HEALTH_PATH: &str = "/api/health";

struct BackendProcess(Mutex<Option<Child>>);

/// Locate the backend entry point.
///
/// Order of preference:
///   1. `OPENLAB_BACKEND_CMD` environment variable (explicit override),
///   2. the frozen sidecar shipped in `resources/backend/openlab-backend[.exe]`,
///   3. the repository's Python module (`python -m openlab`) for development.
fn backend_command(app: &tauri::AppHandle) -> (String, Vec<String>) {
    if let Ok(custom) = std::env::var("OPENLAB_BACKEND_CMD") {
        let mut parts = custom.split_whitespace().map(|s| s.to_string());
        let program = parts.next().unwrap_or_else(|| "python".to_string());
        return (program, parts.collect());
    }

    let sidecar = app
        .path()
        .resource_dir()
        .ok()
        .map(|dir| dir.join("backend").join(sidecar_name()));

    if let Some(path) = sidecar {
        if path.exists() {
            return (path.to_string_lossy().to_string(), Vec::new());
        }
    }

    let python = if cfg!(windows) { "python" } else { "python3" };
    (
        python.to_string(),
        vec![
            "-m".to_string(),
            "openlab".to_string(),
            "--port".to_string(),
            DEFAULT_PORT.to_string(),
        ],
    )
}

fn sidecar_name() -> &'static str {
    if cfg!(windows) {
        "openlab-backend.exe"
    } else {
        "openlab-backend"
    }
}

fn spawn_backend(app: &tauri::AppHandle) -> Option<Child> {
    let (program, args) = backend_command(app);
    let mut command = Command::new(&program);
    command.args(&args);
    command.env("OPENLAB_DESKTOP", "1");
    command.stdout(Stdio::null()).stderr(Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        command.creation_flags(CREATE_NO_WINDOW);
    }
    match command.spawn() {
        Ok(child) => Some(child),
        Err(error) => {
            eprintln!("OPENLAB: could not start the backend ({program}): {error}");
            None
        }
    }
}

fn wait_for_backend(timeout: Duration) -> bool {
    let url = format!("http://127.0.0.1:{DEFAULT_PORT}{HEALTH_PATH}");
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if tcp_reachable() {
            return true;
        }
        let _ = url.len();
        thread::sleep(Duration::from_millis(300));
    }
    false
}

fn tcp_reachable() -> bool {
    use std::net::TcpStream;
    TcpStream::connect(("127.0.0.1", DEFAULT_PORT)).is_ok()
}

#[tauri::command]
fn backend_status(state: State<'_, BackendProcess>) -> serde_json::Value {
    let mut guard = state.0.lock().unwrap();
    let running = match guard.as_mut() {
        Some(child) => matches!(child.try_wait(), Ok(None)),
        None => false,
    };
    serde_json::json!({
        "url": format!("http://127.0.0.1:{DEFAULT_PORT}"),
        "running": running,
        "port": DEFAULT_PORT,
    })
}

#[tauri::command]
fn restart_backend(app: tauri::AppHandle, state: State<'_, BackendProcess>) -> bool {
    {
        let mut guard = state.0.lock().unwrap();
        if let Some(child) = guard.as_mut() {
            let _ = child.kill();
            let _ = child.wait();
        }
        *guard = spawn_backend(&app);
    }
    wait_for_backend(Duration::from_secs(45))
}

fn main() {
    tauri::Builder::default()
        .manage(BackendProcess(Mutex::new(None)))
        .invoke_handler(tauri::generate_handler![backend_status, restart_backend])
        .setup(|app| {
            let handle = app.handle().clone();
            let child = spawn_backend(&handle);
            app.state::<BackendProcess>().0.lock().unwrap().replace(child.unwrap_or_else(|| {
                Command::new(if cfg!(windows) { "cmd" } else { "true" })
                    .arg("/c")
                    .arg("exit")
                    .spawn()
                    .expect("noop process")
            }));

            if !wait_for_backend(Duration::from_secs(60)) {
                eprintln!(
                    "OPENLAB: backend did not answer on port {DEFAULT_PORT}; \
                     the window will show a connection error. Start it manually with `python -m openlab`."
                );
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build the OPENLAB desktop shell")
        .run(|app_handle, event| {
            if let RunEvent::ExitRequested { .. } = event {
                let state = app_handle.state::<BackendProcess>();
                let mut guard = state.0.lock().unwrap();
                if let Some(child) = guard.as_mut() {
                    let _ = child.kill();
                    let _ = child.wait();
                }
                *guard = None;
            }
        });
}
