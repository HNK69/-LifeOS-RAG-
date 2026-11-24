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

---

## 11. Hybrid Local-Cloud Routing & Privacy Gatekeeper (Milestone 7)

### Architecture Overview
LifeOS Milestone 7 implements an intelligent, privacy-preserving LLM routing architecture that balances local privacy, deterministic security, and cloud reasoning capacity.

```
+-------------------------------------------------------------------------------+
|                             Caller / LLM Request                              |
|                          (generate_response / API)                            |
+-------------------------------------------------------------------------------+
                                        |
                                        v
+-------------------------------------------------------------------------------+
|                            5-Step Pre-Egress Gating                           |
|                                                                               |
|  1. Workspace Check     --> Is workspace local_only?   ---[Yes]---> Local LLM |
|  2. Consent Check       --> Has cloud_reasoning consent? -[No]----> Local LLM |
|  3. Budget Check        --> Daily budget exceeded?     ---[Yes]---> Local LLM |
|  4. Privacy Gatekeeper  --> Critical secrets detected? ---[Yes]---> Local LLM |
|                             Moderate/High PII detected?---[Yes]---> Scrub PII |
|                                                                     (Vault)   |
|  5. Cloud Dispatch      --> Send scrubbed prompt to cloud                     |
|                             Cascade on timeout/error   -----------> Local LLM |
+-------------------------------------------------------------------------------+
                                        |
                 +----------------------+----------------------+
                 |                                             |
                 v                                             v
+----------------------------------+         +----------------------------------+
|           Cloud LLM              |         |         Local Provider           |
| (OpenRouter / CloudOpenAIProvider|         | (LocalOllamaProvider / Fallback) |
+----------------------------------+         +----------------------------------+
                 |                                             |
                 v                                             v
      Scrubbed Cloud Response                       Local Offline Response
                 |                                             |
                 v                                             |
+----------------------------------+                           |
|    Surrogate Re-Identification   |                           |
|      (Restores placeholders)     |                           |
+----------------------------------+                           |
                 |                                             |
                 +----------------------+----------------------+
                                        |
                                        v
                          Clean Final Text Response
```

### 5-Step Pre-Egress Gating Order
Cloud network egress is strictly forbidden until the following five checks evaluate in exact sequence:
1. **Workspace Boundary Enforcement**:
   - Queries originating from workspaces with `local_only=True`, `profile_type="confidential"`, or `profile_type="local_only"` are routed directly to `LocalProvider`. Zero cloud network calls are made.
2. **Granular Consent Verification**:
   - Checks whether active consent exists for purpose `"cloud_reasoning"` and resource `"cloud_llm"` / `"ai_inference"`.
   - In accordance with Milestone 4 consent principles, if no active consent grant is present or if consent was revoked, the request immediately flips to local execution.
3. **Daily Cloud Budget Enforcement**:
   - Compares cumulative daily expenditure against `LIFEOS_DAILY_CLOUD_BUDGET_USD` ($1.00 by default).
   - If the budget cap has been met or exceeded, the router automatically flips to local execution without interrupting user workflow.
4. **Deterministic Privacy Gatekeeper & Surrogate Vaulting**:
   - The gatekeeper inspects text for PII entities across 7 distinct categories:
     - `CREDENTIALS`: API keys (`sk-`, `ghp_`, `xoxb-`), private keys (PEM/SSH), bearer tokens $\rightarrow$ **CRITICAL** sensitivity. Hard-blocks cloud egress and forces local execution.
     - `FINANCIAL`: Payment card numbers validated with the Luhn checksum algorithm $\rightarrow$ **HIGH** sensitivity.
     - `GOV_ID`: US Social Security Numbers (SSN) $\rightarrow$ **HIGH** sensitivity.
     - `EMAIL`: Email addresses $\rightarrow$ **MODERATE** sensitivity.
     - `PHONE`: North American and international E.164 phone numbers $\rightarrow$ **MODERATE** sensitivity.
     - `IP_ADDRESS`: IPv4 and IPv6 network addresses $\rightarrow$ **MODERATE** sensitivity.
     - `SENSITIVE_NAME`: Configured private terms/code names $\rightarrow$ **MODERATE** sensitivity.
   - When moderate/high PII is detected and `LIFEOS_PII_REDACTION_ENABLED=True`, the request-scoped `SurrogateVault` replaces sensitive strings with placeholders (e.g. `<EMAIL_1>`, `<PHONE_1>`).
5. **Cloud Dispatch with Graceful Fallback Cascade**:
   - The cloud provider sends exclusively scrubbed text containing placeholders.
   - If the cloud provider encounters network drops, rate limits (429), gateway timeouts (504), or service downtime, the router catches the exception and immediately cascades to the local fallback provider.

### SurrogateVault Lifecycle & Leak Prevention Guarantees
- **Request-Scoped & Ephemeral**: Vault instances are initialized per generation request and strictly cleared in a `finally` block or context manager (`with SurrogateVault() as vault:`).
- **Zero Disk / Database Persistence**: Surrogate mappings are maintained exclusively in volatile memory; they are never written to disk, database, or cache.
- **Masked Representations**: `__repr__` and `__str__` outputs explicitly mask dictionary contents (`<SurrogateVault vault_id='...' entries=N>`) preventing inadvertent leakage into log streams.
- **Reversible Re-identification**: Once the cloud provider generates a response, the vault maps surrogate placeholders back to original values before returning the string to the caller.

### Provider Architecture
- `CloudOpenAIProvider`: Wraps OpenRouter / OpenAI client. Dynamically accesses `app.llm.generator.client` to preserve 100% backward compatibility with test monkeypatches.
- `LocalOllamaProvider`: Connects to local endpoint (`LIFEOS_LOCAL_LLM_URL`, default `http://localhost:11434/v1`). If the Ollama daemon is unreachable, automatically cascades to `DeterministicFallbackProvider`.
- `DeterministicFallbackProvider`: Offline, zero-dependency extractive/heuristic generator that guarantees structured, deterministic output even on isolated air-gapped systems.
- `MockLLMProvider`: Fully deterministic provider recording all calls and prompts for testing and verification.

### Daily Budget Tracking & Cryptographic Audit Logging
- **Persistent Ledger (`data/routing_budget.json`)**: Tracks daily input tokens, output tokens, estimated spend (USD), and request count with automatic midnight UTC rollover.
- **Cryptographic Audit Event (`cloud_llm_egress`)**: Emits audit logs with token counts and estimated costs. Details strictly contain numeric values and model identifiers; ZERO raw prompt or PII data is written to audit logs.

### Routing REST API Endpoints (`/api/routing`)
- `GET /api/routing/policy`: Retrieve current routing mode, local model URL, budget limits, and redaction flags (requires authentication).
- `PUT /api/routing/policy`: Update routing mode (`local_only`, `local_first`, `cloud_with_gatekeeper`, `hybrid_cascade`), daily budget USD, and redaction flags (requires administrative authorization).
- `POST /api/routing/gatekeeper/inspect`: Dry-run PII inspection endpoint returning detected entity spans, sensitivity level, scrubbed preview, and egress eligibility.
- `GET /api/routing/metrics`: Retrieve real-time daily token counts, expenditure, and budget saturation state.

### Configuration Variables
| Variable | Default | Allowed Values | Description |
| :--- | :--- | :--- | :--- |
| `LIFEOS_ROUTING_MODE` | `hybrid_cascade` | `local_only`, `local_first`, `cloud_with_gatekeeper`, `hybrid_cascade` | System-wide LLM routing strategy |
| `LIFEOS_LOCAL_LLM_URL` | `http://localhost:11434/v1` | URL | Base URL for local Ollama/OpenAI-compatible inference server |
| `LIFEOS_LOCAL_LLM_MODEL` | `llama3.2:1b` | String | Model identifier for local LLM inference |
| `LIFEOS_DAILY_CLOUD_BUDGET_USD` | `1.0` | Float $\ge 0.01$ | Daily cloud expenditure limit in USD |
| `LIFEOS_PII_REDACTION_ENABLED` | `true` | Boolean | Whether gatekeeper scrubs PII before cloud dispatch |
| `LIFEOS_ROUTING_BUDGET_FILE` | `data/routing_budget.json` | Path | Persistent ledger for daily cloud usage accumulators |

---

## 12. Cross-Device Synchronization & Conflict Resolution (Milestone 8)

### Architecture Overview
LifeOS Milestone 8 introduces peer-to-peer and offline-first cross-device synchronization with end-to-end encryption (E2EE), vector clock causality tracking, and non-destructive conflict resolution.

```
+-------------------------------------------------------------------------------+
|                                Local Device A                                 |
|               (Context Store, Workspace Profiles, Documents)                  |
+-------------------------------------------------------------------------------+
                                        │
                                        ▼
+-------------------------------------------------------------------------------+
|                                 Sync Engine                                   |
|  1. Peer Trust Check     ──► Is peer paired in identity.db?  ──[No]─► Abort   |
|  2. Workspace Isolation  ──► Filter out local_only/confidential workspaces    |
|  3. Change Packaging     ──► Vector clocks, Lamport counters, tombstones      |
|  4. Asymmetric Sealing   ──► X25519-HKDF-AES256GCM + Ed25519 signature        |
+-------------------------------------------------------------------------------+
                                        │
                                        ▼
                         Cryptographic Envelope (E2EE)
                          (Zero Relay / Network Leak)
                                        │
                                        ▼
+-------------------------------------------------------------------------------+
|                                Trusted Device B                               |
|  1. Open & Verify        ──► Verify Ed25519 signature & decrypt AEAD payload  |
|  2. Causality Check      ──► Compare vector clocks (ANCESTOR / DESCENDANT)    |
|  3. Conflict Handling    ──► Concurrent? Route to ConflictResolver            |
|                              - Context: Deterministic LWW + conflict log      |
|                              - Documents: Non-destructive conflict forking    |
|  4. Store Convergence    ──► Apply updates & deletion tombstones              |
+-------------------------------------------------------------------------------+
```

### Core Security & Privacy Guarantees
1. **Peer Trust Store Gating**:
   - Synchronization is strictly restricted to devices registered in `trusted_devices` (`data/identity.db`) with status `'paired'`.
   - Unknown devices or peers with status `'revoked'` are immediately rejected at the cryptographic boundary.
2. **Cryptographic Envelope Protection (E2EE)**:
   - All synchronization packages are wrapped inside `CryptographicEnvelope` using ephemeral X25519 key exchange, HKDF-SHA256 key derivation (`LifeOS-Envelope-v1`), AES-256-GCM AEAD encryption, and sender Ed25519 digital signatures.
   - Intermediate relays or local transport layers possess zero ability to decrypt or tamper with sync batches.
3. **Workspace Air-Gap Enforcement**:
   - Workspaces marked with `is_local_only=True` or `profile_type in ('confidential', 'local_only')` are strictly excluded from sync package generation.
   - Incoming sync payloads attempting to mutate a local-only workspace are safely skipped.
4. **Offline Deletion Tombstones**:
   - Offline deletions record explicit deletion tombstones (`is_tombstone=1`, `SyncChangeType.DELETE`) in `sync_change_log` (`data/sync.db`), ensuring deleted entities propagate rather than being resurrected.
5. **Vector Clocks & Causality Ordering**:
   - Every change records a vector clock `dict[device_id, counter]` and Lamport sequence counter.
   - Causality comparisons identify:
     - `ANCESTOR`: Incoming change is strictly newer $\rightarrow$ applied immediately.
     - `DESCENDANT`: Incoming change is strictly older $\rightarrow$ discarded as stale.
     - `EQUAL`: Identical causal state $\rightarrow$ no-op.
     - `CONCURRENT`: Divergent concurrent edits $\rightarrow$ routed to deterministic conflict resolution.

### Non-Destructive Conflict Resolution
- **Context Items (LWW with Tie-Breaker)**:
  - Divergent context updates resolve via Last-Write-Wins (LWW) using ISO timestamps, breaking ties with deterministic lexicographical device IDs.
  - The losing state is preserved in `sync_conflicts` (`data/sync.db`), enabling user inspection and rollback.
- **Documents (Branching / Forking)**:
  - If content hashes differ for the same relative document path, the incoming remote version is preserved non-destructively as `{filename}.conflict-{peer_device_id[:6]}-{timestamp}.{ext}`.
  - Both files remain accessible to the user; zero content is overwritten.

### Sync REST API Endpoints (`/api/sync`)
- `GET /api/sync/peers`: List paired peers, last synchronization timestamps, and unresolved conflict counts.
- `POST /api/sync/export`: Assemble and seal an encrypted synchronization envelope for a trusted peer.
- `POST /api/sync/import`: Decrypt, verify, and apply an incoming encrypted sync envelope.
- `GET /api/sync/conflicts`: Query detected synchronization conflicts with status filtering.
- `POST /api/sync/conflicts/{conflict_id}/resolve`: Manually resolve a conflict with an explicit payload.

### Configuration Variables
| Variable | Default | Allowed Values | Description |
| :--- | :--- | :--- | :--- |
| `LIFEOS_SYNC_DB` | `data/sync.db` | Path | Persistent SQLite store for sync ledgers, vector clocks, and conflicts |
| `LIFEOS_SYNC_TOMBSTONE_TTL_DAYS` | `30` | Integer $\ge 1$ | Retention threshold before pruning deletion tombstones |
| `LIFEOS_SYNC_AUTO_RESOLVE_CONFLICTS` | `true` | Boolean | Whether to apply automatic deterministic conflict resolution |

---

## 13. Plugin & Sidecar Extensibility Sandbox (Milestone 9)

### Architecture Overview
LifeOS Milestone 9 introduces an isolated sidecar execution sandbox for untrusted or third-party extensions. Sidecars execute as child processes communicating with the host exclusively via JSON-RPC 2.0 over standard I/O pipes.

```
+-------------------------------------------------------------------------------+
│                                LifeOS Host                                    │
│   ┌──────────────────┐   ┌──────────────────────┐   ┌─────────────────────┐   │
│   │   ToolRegistry   │   │  Workspace & Consent │   │ Cryptographic Audit │   │
│   │   & Executor     │   │  Security Broker     │   │ & Privacy Log       │   │
│   └────────▲─────────┘   └──────────▲───────────┘   └─────────▲───────────┘   │
│            │                        │                         │               │
│   ┌────────┴────────────────────────┴─────────────────────────┴───────────┐   │
│   │                         HostBroker                                    │   │
│   │  - Authoritative Security Boundary                                    │   │
│   │  - Re-evaluates capabilities, consent, and workspace RLS on each call │   │
│   │  - Enforces local_only air-gaps (strictly denies network:egress)       │   │
│   │  - Token-authenticated JSON-RPC 2.0 (1MB limit, 5s execution timeout) │   │
│   └──────────────────────────────────┬────────────────────────────────────┘   │
+──────────────────────────────────────┼────────────────────────────────────────+
                                       │ Sanitized Environment (No host secrets)
                                       │ Authenticated stdin/stdout IPC
+──────────────────────────────────────▼────────────────────────────────────────+
│                      Isolated Plugin Sidecar Subprocess                       │
│   ┌───────────────────────────────────────────────────────────────────────┐   │
│   │                       Sidecar Tool / Capability                       │   │
│   │  - Sandboxed working directory (data/plugins/{plugin_id}/sandbox)     │   │
│   │  - Exported tools wrapped into SidecarTool(AgentTool) adapters        │   │
│   │  - Monitored by PluginManager (crash backoff & circuit breaker)       │   │
│   └───────────────────────────────────────────────────────────────────────┘   │
+───────────────────────────────────────────────────────────────────────────────+
```

### Authoritative Security & Privacy Boundary
1. **HostBroker Gating**:
   - The plugin subprocess has zero direct access to the host filesystem, SQLite databases, or network sockets.
   - Every privileged IPC method (`context:read`, `context:write`, `documents:read`, `events:publish`, `network:egress`) is mediated by `HostBroker`.
   - Permissions are never granted permanently or delegated to the child process.
2. **Real-Time Consent & Immediate Revocation**:
   - Capabilities require active `ConsentGrant` records (`scope_type='plugin'`, `resource_path=plugin_id`, `purpose=capability`) in `data/privacy.db`.
   - If consent is missing, expired, or revoked, access is immediately blocked with JSON-RPC error code `-32002` (`CONSENT_DENIED`).
3. **Workspace Air-Gap Enforcement**:
   - If a plugin is bound to a workspace where `is_local_only=True` or `profile_type in ('confidential', 'local_only')`, `network:egress` requests are strictly blocked with error code `-32003` (`AIRGAP_VIOLATION`).
4. **Environment Hygiene**:
   - Host secrets (`OPENROUTER_API_KEY`, device private keys, credentials) are stripped before spawning sidecars.
   - Child processes receive only allowlisted variables (`PATH`, `SYSTEMROOT`, `PYTHONPATH`, `LIFEOS_PLUGIN_ID`, `LIFEOS_IPC_TOKEN`, `LIFEOS_WORKSPACE_ID`).
5. **Circuit Breaker & Crash Recovery**:
   - Crash count is tracked within a sliding restart window (`PLUGIN_RESTART_WINDOW_SECONDS=60`).
   - If crashes exceed `PLUGIN_MAX_RESTART_ATTEMPTS=3`, the circuit breaker trips, transitioning the plugin state to `DISABLED`.
   - Repeated restarts enforce exponential backoff ($1\text{s}, 2\text{s}, 4\text{s}$).

### Plugin REST API Endpoints (`/api/plugins`)
- `GET /api/plugins`: List all registered plugins, lifecycle states, and active capabilities.
- `POST /api/plugins/register`: Register a new plugin manifest and prepare its sandboxed directory.
- `GET /api/plugins/{plugin_id}/status`: Query detailed plugin status, PID, crash counter, and circuit breaker metrics.
- `POST /api/plugins/{plugin_id}/start`: Start a registered plugin sidecar subprocess.
- `POST /api/plugins/{plugin_id}/stop`: Gracefully terminate a running plugin sidecar.
- `POST /api/plugins/{plugin_id}/restart`: Restart a plugin sidecar with exponential backoff.
- `POST /api/plugins/{plugin_id}/invoke`: Directly invoke an exported sidecar tool.
- `DELETE /api/plugins/{plugin_id}`: Unregister a plugin, stop its process, and remove its tool registrations.

### Configuration Variables
| Variable | Default | Allowed Values | Description |
| :--- | :--- | :--- | :--- |
| `LIFEOS_PLUGINS_DIR` | `data/plugins` | Path | Root directory for plugin manifests and sandboxed workspaces |
| `LIFEOS_PLUGIN_IPC_TIMEOUT_SECONDS` | `5.0` | Float $[0.5, 60.0]$ | Per-call timeout for sidecar JSON-RPC execution |
| `LIFEOS_PLUGIN_MAX_PAYLOAD_BYTES` | `1048576` | Integer $[1024, 10485760]$ | Maximum allowed size (bytes) for a single IPC frame (1 MB) |
| `LIFEOS_PLUGIN_MAX_RESTART_ATTEMPTS` | `3` | Integer $[1, 20]$ | Maximum crash restart budget before circuit breaker trips |
| `LIFEOS_PLUGIN_RESTART_WINDOW_SECONDS` | `60` | Integer $[5, 3600]$ | Sliding window threshold for tracking crash frequency |
| `LIFEOS_PLUGIN_AUTO_START` | `false` | Boolean | Whether to auto-start all registered plugins at application launch |

---

## 14. Production Hardening, Packaging & Release (Milestone 10)

### Architecture Overview
LifeOS Milestone 10 establishes production-grade packaging, standardized CLI operations, pre-flight diagnostics, comprehensive disaster recovery, secret log masking, and graceful process lifecycle management.

```
+-------------------------------------------------------------------------------+
│                          LifeOS Unified Production CLI                        │
│                                (app/cli.py)                                   │
│   ┌──────────────────┐   ┌──────────────────────┐   ┌─────────────────────┐   │
│   │   lifeos serve   │   │     lifeos doctor    │   │lifeos backup/restore│   │
│   └────────┬─────────┘   └──────────┬───────────┘   └─────────┬───────────┘   │
+────────────┼────────────────────────┼─────────────────────────┼───────────────+
             ▼                        ▼                         ▼
+-------------------------+ +-------------------------+ +-----------------------+
│ FastASGI Server         │ │ Pre-Flight Diagnostics  │ │ Disaster Recovery     │
│ - Single-process        │ │ - 8 SQLite DB checks:   │ │ - All 8 SQLite DBs    │
│ - Secret-masked logging │ │   PRAGMA integrity_check│ │ - Documents & Vectors │
│ - /ready & /live probes │ │   foreign_key_check     │ │ - Plugins manifests   │
│ - Sidecar shutdown      │ │ - Device keypair check  │ │ - SHA-256 verification│
│ - WAL truncation        │ │ - Storage permissions   │ │ - Retention pruning   │
+-------------------------+ +-------------------------+ +-----------------------+
```

### 1. Pre-Flight Diagnostics (`lifeos doctor`)
The diagnostic engine inspects all subsystems prior to production deployment:
- **8 SQLite Databases**:
  1. `document_registry.db`: Registry metadata and file hashes.
  2. `entities.db`: Extracted entities and relationships.
  3. `people_registry.db`: Face vectors and dynamic identity.
  4. `context.db`: Episodic context and key-value memories.
  5. `privacy.db`: Consent grants and SHA-256 hash-chained audit records.
  6. `identity.db`: Device identity keypairs and paired peers.
  7. `workspace.db`: Workspace boundaries and memberships.
  8. `sync.db`: Vector clocks, Lamport counters, and conflict logs.
  - Executes `PRAGMA integrity_check`, `PRAGMA foreign_key_check`, and verifies WAL mode.
- **Filesystem Permissions**: Validates write permissions for `data/`, `data/documents/`, `data/backups/`, and `data/plugins/`.
- **Security & Cryptography**: Confirms local device Ed25519/X25519 keypair presence.
- **Vector Database**: Connects to ChromaDB and lists collections without running embedding models.
- **Network Ports**: Validates API port bindability.

### 2. Comprehensive Disaster Recovery & Backup
- **Backup Generation (`lifeos backup create [--label TAG]`)**:
  - Atomically backs up all 8 SQLite databases using the online SQLite backup API.
  - Backs up the documents directory, ChromaDB persistent vectors, and plugin manifests.
  - Writes a `manifest.json` containing SHA-256 checksums and file sizes for every database.
  - Compresses into a portable `lifeos_backup_*.tar.gz` archive.
- **Disaster Recovery Restore (`lifeos backup restore <ARCHIVE>`)**:
  - Extracts to a staging directory and recalculates SHA-256 checksums against `manifest.json`.
  - Runs `PRAGMA integrity_check` on every extracted database before overwriting active state.
  - Creates an automatic pre-restore safety snapshot to prevent catastrophic data loss.
- **Retention Pruning (`lifeos backup prune [--days N]`)**:
  - Automatically deletes backup archives older than `LIFEOS_BACKUP_RETENTION_DAYS` (default 30 days).

### 3. Secret-Masked Observability
- All text and JSON log formatters incorporate `SecretMaskingFilter`.
- Automatically redacts API keys (`sk-...`, `api_key=...`), bearer tokens (`Bearer ...`), passwords, and session tokens to `[REDACTED]`.

### 4. CLI Quick Reference
```bash
# Start production API server
lifeos serve --host 0.0.0.0 --port 8000

# Run pre-flight system diagnostics
lifeos doctor
lifeos doctor --strict --json

# Create point-in-time disaster recovery snapshot
lifeos backup create --label pre_upgrade

# List existing backups
lifeos backup list

# Restore from backup archive
lifeos backup restore data/backups/lifeos_backup_20261124_170000.tar.gz

# Prune archives older than 30 days
lifeos backup prune --days 30

# Inspect system status and active databases
lifeos status
```

### Configuration Variables
| Variable | Default | Allowed Values | Description |
| :--- | :--- | :--- | :--- |
| `LIFEOS_BACKUP_RETENTION_DAYS` | `30` | Integer $\ge 1$ | Retention threshold (days) for automated backup archive pruning |
| `LIFEOS_LOG_MASK_SECRETS` | `true` | Boolean | Whether logging filters scrub sensitive tokens and credentials |
| `LIFEOS_DOCTOR_STRICT_MODE` | `false` | Boolean | Whether pre-flight diagnostics fail on warnings as well as errors |

