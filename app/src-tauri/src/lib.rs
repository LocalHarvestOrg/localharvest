mod awake;
#[cfg(debug_assertions)]
mod dev;
mod sidecar;

use std::path::PathBuf;

use tauri::{Manager, RunEvent};
use tauri_plugin_opener::OpenerExt;

use awake::KeepAwake;
use sidecar::{Sidecar, SidecarCommand, SidecarInfo};

/// The sidecar's URL and token, once it listens. The window calls the sidecar
/// through tauri-plugin-http with these (it sends no CORS headers on purpose).
#[tauri::command]
async fn sidecar_info(sidecar: tauri::State<'_, Sidecar>) -> Result<SidecarInfo, String> {
    let sidecar = sidecar.inner().clone();
    tauri::async_runtime::spawn_blocking(move || sidecar.info())
        .await
        .map_err(|e| e.to_string())?
}

/// Opens a results folder in the file manager. Only folders: never runs a file.
#[tauri::command]
fn open_folder(app: tauri::AppHandle, path: String) -> Result<(), String> {
    let folder = PathBuf::from(&path);
    if !folder.is_absolute() || !folder.is_dir() {
        return Err(format!("{path} is not a folder."));
    }
    app.opener().open_path(path, None::<&str>).map_err(|e| e.to_string())
}

#[tauri::command]
fn keep_awake(state: tauri::State<'_, KeepAwake>, on: bool) -> Result<(), String> {
    state.set(on)
}

#[cfg(not(debug_assertions))]
fn handlers() -> impl Fn(tauri::ipc::Invoke) -> bool {
    tauri::generate_handler![sidecar_info, open_folder, keep_awake]
}

#[cfg(debug_assertions)]
fn handlers() -> impl Fn(tauri::ipc::Invoke) -> bool {
    tauri::generate_handler![
        sidecar_info,
        open_folder,
        keep_awake,
        dev::dev_smoke_config,
        dev::dev_keep_awake_active,
        dev::dev_smoke_report
    ]
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_http::init())
        .plugin(tauri_plugin_opener::init())
        .manage(Sidecar::new())
        .manage(KeepAwake::default())
        .setup(|app| {
            let resource_dir = app.path().resource_dir().ok();
            app.state::<Sidecar>().start(SidecarCommand::resolve(resource_dir));
            Ok(())
        })
        .invoke_handler(handlers())
        .build(tauri::generate_context!())
        .expect("error while building the app");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            let _ = handle.state::<KeepAwake>().set(false);
            handle.state::<Sidecar>().stop();
        }
    });
}
