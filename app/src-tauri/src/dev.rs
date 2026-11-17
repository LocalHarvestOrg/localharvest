//! Debug builds only: a scripted check of the whole app (frontend, HTTP plugin,
//! sidecar, runs), for machines where nobody can click through it. Started by
//! TABLE_EXTRACT_SMOKE_TEST, a JSON object the window reads with `dev_smoke_config`:
//!
//!     {"inputs": ["/abs/doc.pdf"], "output_dir": "/abs/results", "server_url": "http://127.0.0.1:8000/v1/chat/completions"}
//!
//! The window runs app/src/dev/smoke.ts and reports with `dev_smoke_report`,
//! which prints the result and quits the app (exit code 0 or 1).

use tauri::{AppHandle, State};

use crate::awake::KeepAwake;

const SMOKE_ENV: &str = "TABLE_EXTRACT_SMOKE_TEST";

#[tauri::command]
pub fn dev_smoke_config() -> Option<serde_json::Value> {
    let value = std::env::var(SMOKE_ENV).ok()?;
    match serde_json::from_str(&value) {
        Ok(config) => Some(config),
        Err(e) => {
            eprintln!("{SMOKE_ENV} is not valid JSON: {e}");
            None
        }
    }
}

#[tauri::command]
pub fn dev_keep_awake_active(state: State<'_, KeepAwake>) -> bool {
    state.active()
}

#[tauri::command]
pub fn dev_smoke_report(app: AppHandle, ok: bool, log: String) {
    println!("{log}");
    println!("SMOKE TEST {}", if ok { "PASSED" } else { "FAILED" });
    app.exit(if ok { 0 } else { 1 });
}
