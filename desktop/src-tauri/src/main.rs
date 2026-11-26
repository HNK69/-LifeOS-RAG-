// main.rs
// LifeOS Desktop Tauri Application Entry Point.

mod sidecar;

use sidecar::{SidecarInfo, SidecarManager};
use std::sync::Mutex;
use tauri::{Manager, RunEvent};

struct AppState {
    sidecar: Mutex<SidecarManager>,
}

#[tauri::command]
fn get_sidecar_status(state: tauri::State<AppState>) -> Result<SidecarInfo, String> {
    let mgr = state.sidecar.lock().unwrap();
    Ok(mgr.get_info())
}

#[tauri::command]
fn stop_sidecar(state: tauri::State<AppState>) -> Result<(), String> {
    let mgr = state.sidecar.lock().unwrap();
    mgr.stop()
}

fn main() {
    let sidecar_mgr = SidecarManager::new();

    // Start sidecar using system python
    let python_bin = if cfg!(windows) { "python" } else { "python3" };
    if let Err(e) = sidecar_mgr.start(python_bin) {
        eprintln!("Warning: Failed to start sidecar automatically: {}", e);
    } else {
        let _ = sidecar_mgr.wait_for_ready(10);
    }

    let state = AppState {
        sidecar: Mutex::new(sidecar_mgr),
    };

    let app = tauri::Builder::default()
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(state)
        .invoke_handler(tauri::generate_handler![get_sidecar_status, stop_sidecar])
        .build(tauri::generate_context!())
        .expect("Error building LifeOS Desktop application");

    app.run(|app_handle, event| {
        if let RunEvent::Exit = event {
            // Ensure sidecar process is gracefully shut down when app exits
            let state = app_handle.state::<AppState>();
            if let Ok(mgr) = state.sidecar.lock() {
                let _ = mgr.stop();
            }
        }
    });
}
