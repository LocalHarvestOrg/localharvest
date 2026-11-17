//! The FastAPI sidecar (table_extract/server): started with the app, stopped with it.
//!
//! - A fresh random token per launch goes in TABLE_EXTRACT_SIDECAR_TOKEN; the
//!   window gets it, with the URL, from the `sidecar_info` command.
//! - The sidecar picks a free port (`--port 0`) and prints one JSON line on
//!   stdout once it listens: {"event": "listening", "port": N, "url": ...}.
//! - Its stdin is a pipe that only this process holds. With
//!   `--stop-when-stdin-closes` the sidecar exits when the pipe closes, so it
//!   also stops if the app crashes or is killed. On a normal exit the pipe is
//!   closed first, then the sidecar is killed if it has not stopped in time.
//!
//! Which program to start (see `SidecarCommand::resolve`): the
//! TABLE_EXTRACT_SIDECAR_COMMAND environment variable if set, else in
//! development the repository's `.venv_extract` Python, else the frozen
//! sidecar bundled with the app (step 5, packaging).

use std::io::{BufRead, BufReader, Read};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::{Arc, Condvar, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};

pub const COMMAND_ENV: &str = "TABLE_EXTRACT_SIDECAR_COMMAND";
const TOKEN_ENV: &str = "TABLE_EXTRACT_SIDECAR_TOKEN";
/// The first start imports FastAPI and the pipeline; a cold disk can be slow.
const START_TIMEOUT: Duration = Duration::from_secs(90);
/// Time to stop a run cleanly and exit once stdin closes (the sidecar needs about 6 s at most).
const STOP_TIMEOUT: Duration = Duration::from_secs(8);

#[derive(Debug, Clone)]
pub struct SidecarCommand {
    pub program: PathBuf,
    pub args: Vec<String>,
    pub cwd: Option<PathBuf>,
}

impl SidecarCommand {
    /// The command that runs `serve`; `--port 0 --stop-when-stdin-closes` are added by `start`.
    pub fn resolve(resource_dir: Option<PathBuf>) -> Result<Self, String> {
        if let Ok(value) = std::env::var(COMMAND_ENV) {
            return Self::from_env(&value);
        }
        if cfg!(debug_assertions) {
            return Self::development();
        }
        Self::bundled(resource_dir)
    }

    /// A JSON array, e.g. ["/path/to/python", "-m", "table_extract", "serve"].
    fn from_env(value: &str) -> Result<Self, String> {
        let parts: Vec<String> = serde_json::from_str(value)
            .map_err(|e| format!("{COMMAND_ENV} must be a JSON array of strings: {e}"))?;
        let (program, args) = parts
            .split_first()
            .ok_or_else(|| format!("{COMMAND_ENV} is empty"))?;
        Ok(Self { program: PathBuf::from(program), args: args.to_vec(), cwd: None })
    }

    /// `python -m table_extract serve` from the repository's virtual environment.
    fn development() -> Result<Self, String> {
        // app/src-tauri -> the repository
        let repo = Path::new(env!("CARGO_MANIFEST_DIR"))
            .ancestors()
            .nth(2)
            .ok_or("cannot find the repository folder")?
            .to_path_buf();
        let python = if cfg!(windows) {
            repo.join(".venv_extract").join("Scripts").join("python.exe")
        } else {
            repo.join(".venv_extract").join("bin").join("python")
        };
        if !python.exists() {
            return Err(format!(
                "{} not found. Create the virtual environment (see the README), or set {COMMAND_ENV}.",
                python.display()
            ));
        }
        Ok(Self {
            program: python,
            args: ["-m", "table_extract", "serve"].map(String::from).to_vec(),
            cwd: Some(repo),
        })
    }

    /// The frozen sidecar shipped with the app: `<app> serve` (packaging is step 5).
    fn bundled(resource_dir: Option<PathBuf>) -> Result<Self, String> {
        let name = if cfg!(windows) { "table-extract.exe" } else { "table-extract" };
        let program = resource_dir
            .map(|dir| dir.join("sidecar").join(name))
            .filter(|path| path.exists())
            .ok_or_else(|| format!("The bundled sidecar was not found. Set {COMMAND_ENV} to start one."))?;
        Ok(Self { program, args: vec!["serve".into()], cwd: None })
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct SidecarInfo {
    pub url: String,
    pub token: String,
}

#[derive(Debug, Clone)]
enum State {
    Starting,
    Ready(SidecarInfo),
    Failed(String),
}

#[derive(Deserialize)]
struct Listening {
    event: String,
    port: u16,
}

struct Process {
    child: Child,
    stdin: Option<ChildStdin>,
}

/// The running sidecar, shared between the setup hook, the commands and the exit handler.
#[derive(Clone)]
pub struct Sidecar {
    state: Arc<(Mutex<State>, Condvar)>,
    process: Arc<Mutex<Option<Process>>>,
}

fn random_token() -> Result<String, String> {
    let mut bytes = [0u8; 32];
    getrandom::fill(&mut bytes).map_err(|e| format!("no random source: {e}"))?;
    Ok(bytes.iter().map(|b| format!("{b:02x}")).collect())
}

impl Sidecar {
    pub fn new() -> Self {
        Self {
            state: Arc::new((Mutex::new(State::Starting), Condvar::new())),
            process: Arc::new(Mutex::new(None)),
        }
    }

    fn set(&self, state: State) {
        let (lock, ready) = &*self.state;
        *lock.lock().unwrap() = state;
        ready.notify_all();
    }

    /// Starts the sidecar in the background; `info` waits for the result.
    pub fn start(&self, command: Result<SidecarCommand, String>) {
        let command = match command {
            Ok(command) => command,
            Err(message) => return self.set(State::Failed(message)),
        };
        let token = match random_token() {
            Ok(token) => token,
            Err(message) => return self.set(State::Failed(message)),
        };
        let mut process = Command::new(&command.program);
        process
            .args(&command.args)
            .args(["--port", "0", "--stop-when-stdin-closes"])
            .env(TOKEN_ENV, &token)
            .env("PYTHONUNBUFFERED", "1")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit()); // the sidecar's warnings, in the terminal during development
        if let Some(cwd) = &command.cwd {
            process.current_dir(cwd);
        }
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            const CREATE_NO_WINDOW: u32 = 0x0800_0000;
            process.creation_flags(CREATE_NO_WINDOW);
        }
        let mut child = match process.spawn() {
            Ok(child) => child,
            Err(e) => {
                return self.set(State::Failed(format!(
                    "Could not start the background service ({}): {e}",
                    command.program.display()
                )))
            }
        };
        let stdout = child.stdout.take();
        let stdin = child.stdin.take();
        *self.process.lock().unwrap() = Some(Process { child, stdin });

        let sidecar = self.clone();
        thread::spawn(move || sidecar.read_announcement(stdout, token));
        let sidecar = self.clone();
        thread::spawn(move || sidecar.watch_exit());
    }

    fn read_announcement(&self, stdout: Option<impl Read>, token: String) {
        let Some(stdout) = stdout else {
            return self.set(State::Failed("The background service has no output.".into()));
        };
        let mut lines = BufReader::new(stdout).lines();
        match lines.next() {
            Some(Ok(line)) => match serde_json::from_str::<Listening>(&line) {
                Ok(listening) if listening.event == "listening" => self.set(State::Ready(SidecarInfo {
                    // The sidecar only accepts Host: 127.0.0.1:<port> (or localhost)
                    url: format!("http://127.0.0.1:{}", listening.port),
                    token,
                })),
                _ => self.set(State::Failed(format!("Unexpected output from the background service: {line}"))),
            },
            _ => {
                // Exited before listening: watch_exit reports the exit code
                return;
            }
        }
        // Nothing else is printed on stdout; keep reading so the pipe never fills
        for _ in lines {}
    }

    fn watch_exit(&self) {
        loop {
            thread::sleep(Duration::from_millis(500));
            let mut guard = self.process.lock().unwrap();
            let Some(process) = guard.as_mut() else { return };
            match process.child.try_wait() {
                Ok(Some(status)) => {
                    *guard = None;
                    drop(guard);
                    self.set(State::Failed(format!("The background service stopped ({status}).")));
                    return;
                }
                Ok(None) => {}
                Err(_) => return,
            }
        }
    }

    /// Waits until the sidecar listens (or failed to start).
    pub fn info(&self) -> Result<SidecarInfo, String> {
        let (lock, ready) = &*self.state;
        let deadline = Instant::now() + START_TIMEOUT;
        let mut state = lock.lock().unwrap();
        loop {
            match &*state {
                State::Ready(info) => return Ok(info.clone()),
                State::Failed(message) => return Err(message.clone()),
                State::Starting => {
                    let left = deadline.saturating_duration_since(Instant::now());
                    if left.is_zero() {
                        return Err("The background service did not start in time.".into());
                    }
                    state = ready.wait_timeout(state, left).unwrap().0;
                }
            }
        }
    }

    /// Closes the sidecar's stdin so it stops its run and exits; kills it if it does not.
    pub fn stop(&self) {
        let Some(mut process) = self.process.lock().unwrap().take() else { return };
        drop(process.stdin.take());
        let deadline = Instant::now() + STOP_TIMEOUT;
        while Instant::now() < deadline {
            if let Ok(Some(_)) = process.child.try_wait() {
                return;
            }
            thread::sleep(Duration::from_millis(100));
        }
        let _ = process.child.kill();
        let _ = process.child.wait();
    }
}
