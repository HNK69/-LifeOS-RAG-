// sidecar.rs
// Rust sidecar lifecycle manager for LifeOS backend.

use serde::{Deserialize, Serialize};
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

#[derive(Debug, Clone)]
pub struct SidecarLaunchConfig {
    pub python_path: PathBuf,
    pub working_dir: PathBuf,
    pub pythonpath: Option<PathBuf>,
}

/// Resolves the optimal Python executable and working directory for the sidecar backend.
/// Resolution priority:
/// 1. Explicit `custom_python` override (if provided)
/// 2. `LIFEOS_PYTHON` environment variable
/// 3. `VIRTUAL_ENV` environment variable
/// 4. Bundled Python runtime relative to current executable (packaged distribution)
/// 5. Repository `.venv` / `venv` relative to app root or current directory
/// 6. System `python` (Windows) or `python3` (Unix) fallback
pub fn resolve_sidecar_config(custom_python: Option<&str>) -> SidecarLaunchConfig {
    let current_dir = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    let exe_dir = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|p| p.to_path_buf()));

    // 1. Resolve working directory (containing app/cli.py)
    let mut working_dir = current_dir.clone();
    if let Ok(env_root) = std::env::var("LIFEOS_APP_ROOT").or_else(|_| std::env::var("LIFEOS_ROOT")) {
        let p = PathBuf::from(env_root);
        if p.join("app").join("cli.py").exists() {
            working_dir = p;
        }
    }

    if !working_dir.join("app").join("cli.py").exists() {
        let mut check_dir = Some(current_dir.as_path());
        while let Some(dir) = check_dir {
            if dir.join("app").join("cli.py").exists() {
                working_dir = dir.to_path_buf();
                break;
            }
            check_dir = dir.parent();
        }
    }

    if !working_dir.join("app").join("cli.py").exists() {
        if let Some(ref dir) = exe_dir {
            let mut check_dir = Some(dir.as_path());
            while let Some(d) = check_dir {
                if d.join("app").join("cli.py").exists() {
                    working_dir = d.to_path_buf();
                    break;
                }
                check_dir = d.parent();
            }
        }
    }

    // 2. Resolve Python binary
    let python_path = if let Some(custom) = custom_python {
        PathBuf::from(custom)
    } else if let Ok(env_py) = std::env::var("LIFEOS_PYTHON") {
        PathBuf::from(env_py)
    } else {
        find_python_binary(&working_dir, exe_dir.as_deref(), &current_dir)
    };

    SidecarLaunchConfig {
        python_path,
        working_dir: working_dir.clone(),
        pythonpath: Some(working_dir),
    }
}

fn find_python_binary(app_root: &Path, exe_dir: Option<&Path>, current_dir: &Path) -> PathBuf {
    // Check VIRTUAL_ENV
    if let Ok(venv) = std::env::var("VIRTUAL_ENV") {
        let venv_path = PathBuf::from(venv);
        let py = if cfg!(windows) {
            venv_path.join("Scripts").join("python.exe")
        } else {
            venv_path.join("bin").join("python3")
        };
        if py.exists() {
            return py;
        }
    }

    // Check bundled runtimes relative to current_exe
    if let Some(exe_d) = exe_dir {
        let bundled_candidates = if cfg!(windows) {
            vec![
                exe_d.join("python").join("python.exe"),
                exe_d.join("python.exe"),
                exe_d.join("resources").join("python").join("python.exe"),
                exe_d.join("sidecar").join("python.exe"),
            ]
        } else {
            vec![
                exe_d.join("python").join("bin").join("python3"),
                exe_d.join("resources").join("python").join("bin").join("python3"),
                exe_d.join("sidecar").join("python3"),
            ]
        };

        for candidate in bundled_candidates {
            if candidate.exists() {
                return candidate;
            }
        }
    }

    // Check virtual environments in app root
    let venv_names = [".venv", "venv", "env"];
    for name in &venv_names {
        let py = if cfg!(windows) {
            app_root.join(name).join("Scripts").join("python.exe")
        } else {
            app_root.join(name).join("bin").join("python3")
        };
        if py.exists() {
            return py;
        }
    }

    // Check virtual environments in current directory
    for name in &venv_names {
        let py = if cfg!(windows) {
            current_dir.join(name).join("Scripts").join("python.exe")
        } else {
            current_dir.join(name).join("bin").join("python3")
        };
        if py.exists() {
            return py;
        }
    }

    // Fallback to system command
    if cfg!(windows) {
        PathBuf::from("python")
    } else {
        PathBuf::from("python3")
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SidecarHandshake {
    pub event: String,
    pub status: String,
    pub host: String,
    pub port: u16,
    pub token: String,
    pub pid: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SidecarInfo {
    pub status: String,
    pub host: String,
    pub port: u16,
    pub token: String,
    pub pid: Option<u32>,
    pub error: Option<String>,
}

pub struct SidecarManager {
    child: Arc<Mutex<Option<Child>>>,
    info: Arc<Mutex<SidecarInfo>>,
}

impl SidecarManager {
    pub fn new() -> Self {
        Self {
            child: Arc::new(Mutex::new(None)),
            info: Arc::new(Mutex::new(SidecarInfo {
                status: "stopped".to_string(),
                host: "127.0.0.1".to_string(),
                port: 8000,
                token: "".to_string(),
                pid: None,
                error: None,
            })),
        }
    }

    pub fn get_info(&self) -> SidecarInfo {
        self.info.lock().unwrap().clone()
    }

    #[allow(dead_code)]
    pub fn start(&self, python_cmd: &str) -> Result<SidecarInfo, String> {
        let config = if python_cmd.is_empty() || python_cmd == "python" || python_cmd == "python3" {
            resolve_sidecar_config(None)
        } else {
            resolve_sidecar_config(Some(python_cmd))
        };
        self.start_with_config(&config)
    }

    pub fn start_with_config(&self, config: &SidecarLaunchConfig) -> Result<SidecarInfo, String> {
        let mut child_guard = self.child.lock().unwrap();
        if child_guard.is_some() {
            return Ok(self.get_info());
        }

        // Spawn python sidecar process in sidecar mode
        let mut cmd = Command::new(&config.python_path);
        cmd.args(["-m", "app.cli", "serve", "--sidecar-mode", "--port", "0"]);
        cmd.current_dir(&config.working_dir);
        if let Some(ref py_path) = config.pythonpath {
            cmd.env("PYTHONPATH", py_path);
        } else {
            cmd.env("PYTHONPATH", &config.working_dir);
        }
        cmd.stdout(Stdio::piped());
        cmd.stderr(Stdio::inherit());

        let mut spawned = cmd.spawn().map_err(|e| {
            format!(
                "Failed to spawn Python sidecar (path: {:?}, cwd: {:?}): {}",
                config.python_path, config.working_dir, e
            )
        })?;
        let stdout = spawned.stdout.take().ok_or_else(|| "Failed to capture sidecar stdout".to_string())?;

        let mut reader = BufReader::new(stdout);
        let mut first_line = String::new();

        // Read the first line which must be the structured JSON handshake
        reader.read_line(&mut first_line).map_err(|e| format!("Failed reading handshake: {}", e))?;

        let handshake: SidecarHandshake = serde_json::from_str(&first_line)
            .map_err(|e| format!("Invalid handshake JSON ('{}'): {}", first_line.trim(), e))?;

        if handshake.status != "ready" || handshake.host != "127.0.0.1" {
            let _ = spawned.kill();
            return Err("Sidecar handshake rejected: invalid host or status".to_string());
        }

        let pid = spawned.id();
        *child_guard = Some(spawned);

        let sidecar_info = SidecarInfo {
            status: "ready".to_string(),
            host: handshake.host,
            port: handshake.port,
            token: handshake.token,
            pid: Some(pid),
            error: None,
        };

        *self.info.lock().unwrap() = sidecar_info.clone();
        Ok(sidecar_info)
    }

    pub fn wait_for_ready(&self, timeout_secs: u64) -> Result<(), String> {
        let info = self.get_info();
        let ready_url = format!("http://{}:{}/ready", info.host, info.port);
        let client = reqwest::blocking::Client::builder()
            .timeout(Duration::from_millis(500))
            .build()
            .map_err(|e| e.to_string())?;

        let start = Instant::now();
        while start.elapsed() < Duration::from_secs(timeout_secs) {
            let resp = client
                .get(&ready_url)
                .header("Authorization", format!("Bearer {}", info.token))
                .send();

            if let Ok(res) = resp {
                if res.status().is_success() {
                    return Ok(());
                }
            }
            std::thread::sleep(Duration::from_millis(100));
        }

        Err(format!("Sidecar failed to report ready within {} seconds", timeout_secs))
    }

    pub fn stop(&self) -> Result<(), String> {
        let mut child_guard = self.child.lock().unwrap();
        if let Some(mut child) = child_guard.take() {
            // Send kill / terminate
            let _ = child.kill();
            let _ = child.wait();
        }

        let mut info_guard = self.info.lock().unwrap();
        info_guard.status = "stopped".to_string();
        info_guard.pid = None;
        Ok(())
    }
}
