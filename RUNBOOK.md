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
Re-verify disk integrity with `python -c "from app.storage.sqlite import connect_sqlite; [connect_sqlite(p).execute('PRAGMA integrity_check') for p in ['data/document_registry.db', 'data/context.db', 'data/entities.db', 'data/people_registry.db', 'data/queue.db']]"` before restarting.

---

## 5. Ingestion Queue Operations

LifeOS includes a persistent, durable SQLite-backed task queue (`data/queue.db`) for asynchronous document processing.

### Configuration
- `LIFEOS_QUEUE_WORKER_ENABLED`: Set to `true` in production `.env` to enable the in-process background worker daemon. Defaults to `false` in development/testing to prevent uncoordinated background thread spawning.
- `LIFEOS_QUEUE_POLL_INTERVAL`: Polling interval in seconds (default: `0.5`).
- `LIFEOS_QUEUE_DB`: Path to persistent queue database (default: `data/queue.db`).

### Operational Guarantees
- **No Stranded Jobs**: When `LIFEOS_QUEUE_WORKER_ENABLED=true`, the server lifespan initializes the background worker thread. When enqueuing via `POST /api/documents?async=true` or `POST /api/documents/jobs`, the client receives `worker_running: true/false`.
- **Atomic Lease Claims**: Jobs are claimed with an atomic lease expiry (`lease_until`), preventing duplicate processing.
- **Poison-Pill Quarantine**: Files causing recurring crashes are quarantined after 3 attempts with full error messages logged.
- **Reindexing Exclusivity**: Queue execution pauses automatically while `reindex.lock` is held.

### Ingestion Job Monitoring
```bash
# Check job status
curl -H "Authorization: Bearer <LIFEOS_API_KEY>" http://127.0.0.1:8000/api/documents/jobs/<job_id>

# Readiness check includes queue DB and worker health
curl http://127.0.0.1:8000/ready
```

---

## 6. Extensible Connectors & Media Format Operations

LifeOS includes an extensible connector architecture and native format support for spreadsheets (`.xlsx`, `.xls`) and audio recordings (`.wav`, `.mp3`).

### Extensible Connector Architecture
Connectors implement `BaseConnector` (`app.connectors.base`) and register with `ConnectorRegistry` (`app.connectors.registry`).
- **REST Endpoints**:
  - `GET /api/connectors`: List all registered connectors, source types, and synchronization statuses.
  - `GET /api/connectors/{name}`: Get detailed connector status and configuration metadata.
  - `POST /api/connectors/{name}/sync`: Trigger an on-demand connector sync.

### Authorization & Security Boundary
To enforce privacy-first boundaries prior to full consent management:
- Connectors (`FilesystemConnector`, `SpreadsheetConnector`, `AudioConnector`) only scan filesystem directories residing within `config.AUTHORIZED_CONNECTOR_ROOTS` (default: `DOCUMENTS_DIR`) or explicitly authorized in-process via `authorize_connector_root(path)`.
- Any attempt to scan unauthorized host filesystem locations raises `PermissionError` and is rejected immediately.
- Authorization can be revoked at any time via `revoke_connector_root(path)`.

### Spreadsheet Ingestion (.xlsx, .xls)
- Multi-sheet workbooks are inspected without loading entire files into memory.
- Metadata (sheet names, row counts, column counts) is stored in the document registry (`metadata_json`).
- Tabular text is extracted, cleaned, chunked, and embedded into ChromaDB.
- Dual-mode retrieval is supported: structured queries (`retrieve_structured_files`) and semantic queries (`retrieve_chunks`).
- Corrupted workbooks fail safely with `ingestion_status='failed'` without crashing the process.

### Audio Ingestion & Whisper STT (.wav, .mp3)
- Waveforms are decoded via `soundfile` with native support for WAV and MP3 (including 16kHz mono resampling).
- Offline STT is performed by `openai/whisper-tiny` running locally on CPU.
- Transcriptions are normalized, chunked, embedded, and stored in ChromaDB and document registry.
- Fallback/mock mode can be enabled for headless unit test suites via `LIFEOS_MOCK_STT=true` or in Python via `set_mock_transcriber()`.

---

## 7. Device Identity, Key Management & Cryptographic Envelope

LifeOS uses asymmetric public key cryptography and authenticated envelope encryption to establish verifiable device identities, secure peer-to-peer pairing, and tamper-proof data exchange.

### Cryptographic Architecture
- **Asymmetric Identity Keypair (Ed25519)**: Verifiable digital signatures for device authentication and non-repudiation.
- **Asymmetric Key-Exchange Keypair (X25519)**: Ephemeral Diffie-Hellman key exchange providing forward secrecy.
- **Envelope Encryption**: `X25519-HKDF-AES256GCM` with 12-byte random IVs and 16-byte authentication tags. Envelopes are digitally signed by the sender.
- **Symmetric Envelope**: `PBKDF2HMAC-SHA256` (100,000 iterations) + `AES-256-GCM` for local backups and credential vaults.
- **Key Storage**: Local device private keys are persisted with restricted permissions (0600) at `data/device_keys.json`.

### Persistent Trust Store (`data/identity.db`)
- Manages local device public credentials, paired peer devices, and pairing challenges.
- **Pairing Handshake**:
  1. Local device issues a 6-digit numeric pairing challenge with a 300s TTL via `POST /api/device/pairing/initiate`.
  2. Peer device signs `(challenge_id + challenge_code)` using its Ed25519 private key.
  3. Local device verifies challenge code and peer signature via `POST /api/device/pairing/verify`, registering peer in `trusted_devices`.

### Device REST API Endpoints
- `GET /api/device/identity`: Retrieve local device public identity and public keys.
- `GET /api/device/peers`: List all registered peer devices.
- `GET /api/device/peers/{device_id}`: Retrieve specific peer metadata.
- `DELETE /api/device/peers/{device_id}`: Revoke a peer device's trust.
- `POST /api/device/envelope/seal`: Encrypt and sign a payload for a trusted recipient peer.
- `POST /api/device/envelope/open`: Decrypt and verify an authenticated envelope addressed to this device.

---

## 8. Privacy Dashboard, Granular Consent & Cryptographic Audit Trail

LifeOS enforces a zero-trust, local-first privacy boundary with granular user consent and an immutable, tamper-evident audit trail backed by SHA-256 cryptographic hash chains.

### Granular Consent Engine (`data/privacy.db`)
- **Explicit Scopes**: Authorizations are granted per filesystem path, data category (`document`, `spreadsheet`, `audio`, `image`, `structured_data`), or connector source.
- **Purpose Limitation**: Grants enforce purpose restrictions (`indexing`, `retrieval`, `reasoning`, `local_embeddings`).
- **Dynamic Real-Time Evaluation**: Before any connector scan or retrieval operation, `is_consent_granted()` verifies that the target resource has an active, non-expired grant matching the requested purpose and category.
- **Immediate Revocation**: Revoking consent via `DELETE /api/privacy/consent/{id}` immediately prohibits subsequent scans and indexing without restarting the application.

### Tamper-Evident Cryptographic Audit Trail
- **SHA-256 Hash Chaining**: Every security-sensitive event (consent grants, consent revocations, connector scans, document ingestions, vector queries, GDPR exports, data purges) is linked to the previous record's hash (`prev_hash` -> `record_hash`), starting from a 64-character zero genesis hash.
- **Tamper Detection**: `verify_audit_log_integrity()` recalculates the entire hash chain from genesis to head. If any record, actor, resource, or hash pointer is modified, verification fails immediately, reporting the exact compromised row and error.

### Privacy API Endpoints
- `GET /api/privacy/summary`: High-level privacy posture, active consented sources, total audit entries, and real-time audit chain validation status.
- `GET /api/privacy/consent`: List consent grants, with optional status filtering (`active`, `revoked`).
- `POST /api/privacy/consent`: Grant explicit granular consent for a folder, file, or connector.
- `DELETE /api/privacy/consent/{id}`: Revoke an active consent grant.
- `GET /api/privacy/audit`: Query paginated audit log entries with verified SHA-256 record hashes.
- `GET /api/privacy/audit/verify`: Verify complete cryptographic hash-chain integrity across the entire audit log.
- `POST /api/privacy/export`: Self-service GDPR user data export returning an archive bundle of consents, documents metadata, context memories, people entities, and audit events.
- `POST /api/privacy/forget`: Cascading "Right to be Forgotten" deletion (requires `admin` role) allowing targeted or complete purging of documents, vectors, people, or context records with an immutable audit entry.

---

## 9. Event-Driven Client API (SSE/WebSockets) & Desktop Client Companion

LifeOS provides a real-time reactive event architecture allowing desktop companions, background tray daemons, and client SDKs to receive instantaneous updates without polling.

### In-Memory Event Bus (`app/events/bus.py`)
- **Pub/Sub Engine**: High-throughput, thread-safe event bus connecting asynchronous request loops and background worker threads (`publish_sync()`).
- **Privacy Enforcement**: Intercepts outgoing event payloads and evaluates `is_consent_granted()`. If consent is revoked or missing for a referenced file, sensitive paths are automatically redacted to `[REDACTED_BY_CONSENT]`.
- **Reconnection Replay Buffer**: Rolling buffer (last 100 events) allowing clients to resume missed events via `Last-Event-ID` or `?last_event_id=`.
- **Slow-Consumer Protection**: Bounded subscriber queues drop oldest messages on overflow to preserve system stability and prevent memory leaks.

### Real-Time Streaming Endpoints
- `GET /api/events/sse`: Server-Sent Events stream delivering typed system, ingestion, and security events. Supports:
  - Header `Last-Event-ID` or query param `?last_event_id=`.
  - Topic filtering via `?topics=ingestion.*,device.*`.
  - Finite stream bounds via `?max_events=N`.
  - Automatic 15s keep-alive heartbeat comments (`: keep-alive\n\n`).
  - Browser query authentication via `?token=<key>`.
- `WebSocket /ws/events`: Full-duplex WebSocket endpoint supporting bidirectional heartbeats, dynamic topic subscription updates, and low-latency event delivery.
- `POST /api/events/broadcast`: Administrative endpoint to broadcast synthetic or system-wide events.

### Desktop Client Companion & SDK
- **Desktop Companion Daemon (`app/client/companion.py`)**:
  - `LifeOSCompanion`: Listens to live events and dispatches desktop user notifications (`DesktopNotification`) for ingestion completions, pipeline failures, pairing requests, and consent modifications.
- **Python Client SDK (`app/client/sdk.py`)**:
  - `LifeOSClient`: Programmatic client library providing typed access to queries, document uploads, queue tracking, and privacy dashboard controls.

---

## 10. Multi-Profile Workspace Boundaries & Row-Level Authorization

LifeOS provides multi-profile workspace boundaries and storage-level row-level authorization (RLS) to support strict data isolation across personal, work, financial, and research contexts.

### Architecture & Storage Engine (`app/workspace/store.py`)
- **Workspace Database (`data/workspace.db`)**:
  - `workspaces`: Stores workspace metadata (`workspace_id`, `name`, `profile_type`, `description`, `is_active`, `settings_json`, timestamps).
  - `workspace_memberships`: Tracks role assignments (`user_id`, `workspace_id`, `role`, `joined_at`) with composite primary keys and foreign key cascade deletion.
- **Canonical Default Workspace**:
  - Automatically seeded on first startup with ID `"default"`, name `"Default Workspace"`, and profile type `"personal"`.
  - Canonical fallback: All unparameterized requests, queries, and document uploads default to `"default"`, ensuring 100% backward compatibility.
  - Protection: The canonical default workspace is immutable to deletion (`DELETE /api/workspaces/default` returns HTTP 400 Bad Request).

### Role Hierarchy & Authorization
- **Role Hierarchy**: `owner` (Level 4) > `admin` (Level 3) > `member` (Level 2) > `viewer` (Level 1).
  - `viewer`: Read documents, personal context, and workspace metadata.
  - `member`: Ingest files, queue background ingestion jobs, upsert personal context entries.
  - `admin`: Update workspace metadata, invite/add members, and modify member roles.
  - `owner`: All administrative actions plus deletion of the workspace profile.
- **System Admin Bypass**: Authenticated system administrators (`role="admin"`) maintain emergency visibility and administrative authority across all workspaces.

### Storage & Retrieval Layer Row-Level Isolation
- **Document Registry (`data/document_registry.db`)**:
  - Documents table includes `workspace_id` and `owner_id` columns with an index on `idx_documents_workspace`.
  - All registry queries (`get_all_documents`, `get_documents_by_status`, `get_document_summary`, `search_documents`) enforce `workspace_id = ?` at the SQL engine level.
- **Vector Database (`data/chroma_db`)**:
  - Chunk ingestion records `workspace_id` in chunk metadata.
  - Retrieval queries (`retrieve_chunks`, `retrieve`) apply strict metadata filters `where={"workspace_id": workspace_id}` to prevent cross-workspace vector similarity leakage.
- **Context Store (`data/context.db`)**:
  - Context table partitioned by `workspace_id` column with index `idx_context_workspace`.
  - Transparent key namespacing (`f"{workspace_id}::{key}"` for custom profiles) guarantees zero key collision across workspaces.
- **Task Queue (`data/queue.db`)**:
  - Ingestion jobs persist `workspace_id` through queuing, leasing, processing, and completion phases.

### Workspace API Endpoints (`/api/workspaces`)
- `GET /api/workspaces`: List workspaces accessible to the caller with real-time document and context entry counts.
- `POST /api/workspaces`: Create a new isolated workspace profile; assigns the creator as `owner`.
- `GET /api/workspaces/{workspace_id}`: Retrieve workspace profile metadata (RLS verified).
- `PUT /api/workspaces/{workspace_id}`: Update workspace metadata (requires workspace admin/owner).
- `DELETE /api/workspaces/{workspace_id}`: Delete an isolated workspace and cascade all memberships (requires workspace owner).
- `GET /api/workspaces/{workspace_id}/members`: List member user IDs and roles.
- `POST /api/workspaces/{workspace_id}/members`: Add or update a workspace member.
- `DELETE /api/workspaces/{workspace_id}/members/{user_id}`: Remove a member from the workspace.

### Request Resolution (`X-Workspace-ID`)
- Requests resolve target workspace via header `X-Workspace-ID` or query parameter `?workspace_id=`.
- `get_request_context` validates workspace existence and verifies user membership; unauthorized access is immediately rejected with HTTP 403 Forbidden.
