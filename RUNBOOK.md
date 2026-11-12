# LifeOS Production Operational Runbook

This runbook provides step-by-step procedures and commands for deploying, monitoring, operating, and recovering LifeOS in production environments.

---

## 1. System Overview & Architecture Constraints

LifeOS is an intelligent personal assistant that processes natural language queries against local documents, knowledge graphs, people registries, and temporal context.

### Architectural Constraints
- **Execution Model**: Single-node, single-process ASGI (`LIFEOS_API_WORKERS=1`).
- **Persistence**: SQLite (WAL mode, busy timeout 10s) and ChromaDB persistent vector collection.
- **Process Boundaries**: The API process handles HTTP requests, conversational sessions in memory, and triggers reindexing or background watchers.
- **Horizontal Scaling**: **NOT SUPPORTED**. Running multiple Uvicorn workers or multi-node processes against the same filesystem leads to uncoordinated watcher activity, split-brain rate limits, and SQLite lock contention.

---

## 2. Process Operations

### START
#### Bare Metal / Systemd:
```bash
# Activate virtual environment
source .venv/bin/activate  # On Linux
# or: .venv\Scripts\activate.ps1  # On Windows

# Start Uvicorn production server
uvicorn app.api.server:app --host 0.0.0.0 --port 8000 --workers 1
```

#### Docker Container:
```bash
docker run -d \
  --name lifeos-prod \
  --restart unless-stopped \
  -p 8000:8000 \
  --env-file .env \
  -v /var/lifeos/data:/app/data \
  -v /var/lifeos/chroma_db:/app/chroma_db \
  lifeos:latest
```

---

### STOP (Graceful Shutdown)
Graceful shutdown stops the background watcher, drains in-flight requests, and cleanly shuts down the agent thread pool.
#### Bare Metal:
```bash
kill -TERM $(pgrep -f "uvicorn app.api.server:app")
```
#### Docker:
```bash
docker stop --time 15 lifeos-prod
```

---

### RESTART
#### Bare Metal:
```bash
kill -TERM $(pgrep -f "uvicorn app.api.server:app")
sleep 2
uvicorn app.api.server:app --host 0.0.0.0 --port 8000 --workers 1
```
#### Docker:
```bash
docker restart lifeos-prod
```

---

## 3. Health & Readiness Monitoring

### HEALTH CHECK (Liveness Probe)
Verifies the HTTP process is alive and responsive. Does not perform I/O or model calls.
```bash
curl -f http://127.0.0.1:8000/health
# Expected: {"status":"healthy","timestamp":"2026-..."} (HTTP 200)
```
Alias for orchestrators:
```bash
curl -f http://127.0.0.1:8000/live
# Expected: {"status":"live","timestamp":"2026-..."} (HTTP 200)
```

### READINESS CHECK (Subsystem Probe)
Verifies filesystem directories exist and SQLite databases are accessible.
```bash
curl -f http://127.0.0.1:8000/ready
# Expected: {"status":"ready","checks":{"data_directory":true,"documents_directory":true,"sqlite_storage":true,"chroma_directory":true},"timestamp":"..."} (HTTP 200)
```
If any subsystem is inaccessible, returns HTTP 503 Service Unavailable.

### METRICS SCRAPING (Admin Only)
Scrapes operational metrics (request counts, status distributions, latencies, worker states):
```bash
curl -H "Authorization: Bearer <LIFEOS_API_ADMIN_KEY>" http://127.0.0.1:8000/metrics
```

---

## 4. Backup & Disaster Recovery

### CREATE BACKUP
Creates a point-in-time consistent `.tar.gz` archive containing transactionally consistent SQLite backups, documents, Chroma vector indices, and an integrity manifest:
```bash
python -c "from app.storage.backup import create_backup; archive = create_backup(label='daily_snapshot'); print(f'Backup archive created: {archive}')"
```
The archive is saved in `LIFEOS_BACKUP_DIR` (default: `data/backups/`).

### RESTORE BACKUP
Restores databases, documents, and Chroma vectors from a verified archive:
```bash
# 1. Stop API process first
docker stop lifeos-prod  # or kill -TERM

# 2. Run restore procedure
python -c "from app.storage.backup import restore_backup; result = restore_backup('/path/to/lifeos_backup_<timestamp>.tar.gz'); print(result)"

# 3. Restart API process
docker start lifeos-prod
```
The restore procedure automatically runs SQLite `PRAGMA integrity_check` on all extracted databases before placing them in production.

---

## 5. Administrative Maintenance

### TRIGGER FULL REINDEX
Executes an atomic reindex across all documents with automated snapshot and rollback protection:
```bash
curl -X POST \
  -H "Authorization: Bearer <LIFEOS_API_ADMIN_KEY>" \
  http://127.0.0.1:8000/api/admin/reindex
```
If an administrative reindex or mutation is already running, the endpoint returns `409 Conflict`.

### CHECK REINDEX STATUS
```bash
curl -H "Authorization: Bearer <LIFEOS_API_ADMIN_KEY>" \
  http://127.0.0.1:8000/api/admin/reindex/status
# Expected: {"is_active": false, "status": "idle"}
```

---

## 6. Incident Response & Troubleshooting

### Incident A: LLM Provider Outage (OpenRouter 402/500/Timeout)
- **Symptom**: `POST /api/query` returns HTTP 500 or fallback response "I couldn't find...".
- **Diagnosis**: Check logs: `docker logs --tail 100 lifeos-prod | grep "LLM"`
- **Remediation**:
  1. Verify OpenRouter credits at https://openrouter.ai/settings/credits.
  2. Verify API key validity: `curl -H "Authorization: Bearer $OPENROUTER_API_KEY" https://openrouter.ai/api/v1/models`
  3. Update `OPENROUTER_API_KEY` in `.env` and restart service.

### Incident B: SQLite Lock Timeout / Database Busy
- **Symptom**: Logs show `sqlite3.OperationalError: database is locked`.
- **Diagnosis**: Check if an active reindex operation or stuck process holds a file lock:
  `ls -l data/reindex.lock`
- **Remediation**:
  1. Check reindex status via `GET /api/admin/reindex/status`.
  2. If process crashed, OS-level FileLock releases automatically on process exit. Restart process: `docker restart lifeos-prod`.

### Incident C: Storage Full / Disk Exhaustion
- **Symptom**: `POST /api/documents` returns HTTP 500 or `/ready` returns HTTP 503.
- **Diagnosis**: `df -h /app/data`
- **Remediation**:
  1. Prune old backups in `data/backups/`.
  2. Remove obsolete documents via `DELETE /api/documents/{filename}`.
  3. Expand volume capacity.

### Incident D: Compromised API Key / Key Rotation
- **Procedure**:
  1. Generate new 32-character cryptographically secure keys:
     ```bash
     python -c "import secrets; print('KEY:', secrets.token_hex(24)); print('ADMIN:', secrets.token_hex(24))"
     ```
  2. Update `.env` with new `LIFEOS_API_KEY` and `LIFEOS_API_ADMIN_KEY`.
  3. Restart LifeOS server: `docker restart lifeos-prod`.
  4. Verify access with new keys and confirm old keys return HTTP 401.

### Incident E: Emergency Shutdown
If malicious activity, runaway processes, or data corruption is detected:
```bash
# Immediate hard kill
docker kill lifeos-prod
# or: kill -9 $(pgrep -f "uvicorn app.api.server:app")
```
Re-verify disk integrity with `python -c "from app.storage.sqlite import connect_sqlite; [connect_sqlite(p).execute('PRAGMA integrity_check') for p in ['data/document_registry.db', 'data/context.db', 'data/entities.db', 'data/people_registry.db']]"` before restarting.
