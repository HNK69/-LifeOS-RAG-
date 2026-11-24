// sidecar.rs
// Rust sidecar lifecycle manager for LifeOS backend.

use serde::{Deserialize, Serialize};
use std::io::{BufRead, BufReader};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

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

    pub fn start(&self, python_cmd: &str) -> Result<SidecarInfo, String> {
        let mut child_guard = self.child.lock().unwrap();
        if child_guard.is_some() {
            return Ok(self.get_info());
        }

        // Spawn python sidecar process in sidecar mode
        let mut cmd = Command::new(python_cmd);
        cmd.args(["-m", "app.cli", "serve", "--sidecar-mode", "--port", "0"]);
        cmd.stdout(Stdio::piped());
        cmd.stderr(Stdio::inherit());

        let mut spawned = cmd.spawn().map_err(|e| format!("Failed to spawn Python sidecar: {}", e))?;
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
