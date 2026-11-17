//! Keeps the computer awake while a run is in progress (docs/gui-design.md, "Long runs").
//!
//! Only idle sleep is prevented: the screen can still turn off and lock, and
//! closing a laptop still puts it to sleep. Each way is released when the app
//! exits, even if it crashes:
//!
//! - macOS: `caffeinate -i -w <app pid>`, which stops with the app.
//! - Windows: a power request (PowerSetRequest, PowerRequestSystemRequired);
//!   Windows closes its handle with the process.
//! - Linux: `systemd-inhibit --what=idle:sleep cat`, with cat reading a pipe
//!   only the app holds. When the pipe closes cat exits, and systemd-inhibit
//!   releases its lock. Needs systemd's logind; without it the computer is not
//!   kept awake (the window ignores the error).

use std::sync::Mutex;

#[derive(Default)]
pub struct KeepAwake {
    guard: Mutex<Option<Guard>>,
}

impl KeepAwake {
    /// Whether the computer is being kept awake right now.
    #[cfg_attr(not(debug_assertions), allow(dead_code))]
    pub fn active(&self) -> bool {
        self.guard.lock().unwrap().as_mut().is_some_and(Guard::alive)
    }

    pub fn set(&self, on: bool) -> Result<(), String> {
        let mut guard = self.guard.lock().unwrap();
        if !on {
            *guard = None; // dropping the guard releases it
            return Ok(());
        }
        if guard.as_mut().is_some_and(Guard::alive) {
            return Ok(()); // already on
        }
        *guard = Some(Guard::start()?);
        Ok(())
    }
}

#[cfg(unix)]
use std::process::{Child, Command, Stdio};

/// The helper process that keeps the computer awake until it exits.
#[cfg(unix)]
struct Guard(Child);

#[cfg(target_os = "macos")]
impl Guard {
    fn start() -> Result<Self, String> {
        Self::spawn(
            Command::new("/usr/bin/caffeinate")
                .args(["-i", "-w", &std::process::id().to_string()])
                .stdin(Stdio::null()),
        )
    }
}

#[cfg(target_os = "linux")]
impl Guard {
    fn start() -> Result<Self, String> {
        Self::spawn(
            Command::new("systemd-inhibit")
                .args([
                    "--what=idle:sleep",
                    "--mode=block",
                    "--who=LocalHarvest",
                    "--why=A run is in progress",
                    "cat",
                ])
                .stdin(Stdio::piped()),
        )
    }
}

#[cfg(all(unix, not(any(target_os = "macos", target_os = "linux"))))]
impl Guard {
    fn start() -> Result<Self, String> {
        Err("Keeping the computer awake is not supported on this system.".into())
    }
}

#[cfg(unix)]
impl Guard {
    #[cfg_attr(not(any(target_os = "macos", target_os = "linux")), allow(dead_code))]
    fn spawn(command: &mut Command) -> Result<Self, String> {
        command
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .map(Self)
            .map_err(|e| format!("Could not keep the computer awake: {e}"))
    }

    fn alive(&mut self) -> bool {
        matches!(self.0.try_wait(), Ok(None))
    }
}

#[cfg(unix)]
impl Drop for Guard {
    fn drop(&mut self) {
        drop(self.0.stdin.take()); // Linux: cat sees the end of its input and exits
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

/// An active power request, cleared and closed on drop.
#[cfg(windows)]
struct Guard {
    handle: windows_sys::Win32::Foundation::HANDLE,
    /// The reason shown by `powercfg /requests`, kept alive with the request
    _reason: Vec<u16>,
}

// The handle is only passed to the power request functions, which any thread may call
#[cfg(windows)]
unsafe impl Send for Guard {}

#[cfg(windows)]
impl Guard {
    fn start() -> Result<Self, String> {
        use windows_sys::Win32::Foundation::{CloseHandle, GetLastError, INVALID_HANDLE_VALUE};
        use windows_sys::Win32::System::Power::{PowerCreateRequest, PowerRequestSystemRequired, PowerSetRequest};
        use windows_sys::Win32::System::SystemServices::POWER_REQUEST_CONTEXT_VERSION;
        use windows_sys::Win32::System::Threading::{
            POWER_REQUEST_CONTEXT_SIMPLE_STRING, REASON_CONTEXT, REASON_CONTEXT_0,
        };

        let mut reason: Vec<u16> = "A LocalHarvest run is in progress".encode_utf16().chain([0]).collect();
        let context = REASON_CONTEXT {
            Version: POWER_REQUEST_CONTEXT_VERSION,
            Flags: POWER_REQUEST_CONTEXT_SIMPLE_STRING,
            Reason: REASON_CONTEXT_0 { SimpleReasonString: reason.as_mut_ptr() },
        };
        unsafe {
            let handle = PowerCreateRequest(&context);
            if handle == INVALID_HANDLE_VALUE {
                return Err(format!("Could not keep the computer awake (error {}).", GetLastError()));
            }
            if PowerSetRequest(handle, PowerRequestSystemRequired) == 0 {
                let error = GetLastError();
                CloseHandle(handle);
                return Err(format!("Could not keep the computer awake (error {error})."));
            }
            Ok(Self { handle, _reason: reason })
        }
    }

    fn alive(&mut self) -> bool {
        true // a power request holds until it is cleared
    }
}

#[cfg(windows)]
impl Drop for Guard {
    fn drop(&mut self) {
        use windows_sys::Win32::Foundation::CloseHandle;
        use windows_sys::Win32::System::Power::{PowerClearRequest, PowerRequestSystemRequired};
        unsafe {
            PowerClearRequest(self.handle, PowerRequestSystemRequired);
            CloseHandle(self.handle);
        }
    }
}
