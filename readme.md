# ***LifeOS***
Document
    ↓
Chunk
    ↓
Embedding
    ↓
ChromaDB (+ metadata)
    ↓
Query
    ↓
Retrieved Objects
    ├── document
    ├── source
    ├── chunk_id
    └── distance
    ↓
Prompt
    ↓
LLM
    ↓
Answer


## ***overview***



## ***Goal***
LifeOS roadmap
Phase	Goal	Status
1. Core RAG	ingestion → chunks → embeddings → Chroma → retrieval → sources	✅ Done
2. Retrieval quality	ranking, filename lookup, failed-query testing, retrieval improvements	✅ Done
3. Production cleanup	config, logging, errors, clean modules, stable pipeline	✅ Done
4. Knowledge Engine	multi-file ingestion, folder watcher, document indexing, deduplication, scalable retrieval	⏳ Next
5. Intelligence layer	query classification, intent detection, routing, multi-document reasoning, temporal reasoning	⏳
6. Personal memory	preferences, recurring facts, goals, tasks, relationships between information	⏳
7. LifeOS tools	calendar, tasks, reminders, files, email, notes, etc.	⏳
8. Agent layer	decide what action/tool to use, execute workflows, verify results	⏳
9. User interface	proper chat UI, document management, source inspection, settings	⏳
10. Security & privacy	permissions, local data isolation, secrets, access control, deletion	⏳
11. Evaluation	benchmark queries, hallucination tests, retrieval metrics, regression tests	⏳
12. Deployment	packaging, database migration, backups, monitoring, updates, multi-user architecture	⏳
13. Other people	onboarding, per-user data isolation, accounts, scalable storage, configurable knowledge sources	⏳




File	Why change
app/config.py	Centralize paths, limits, supported formats, batching and OpenRouter settings.
app/knowledge/document_registry.py	Add fast metadata-based change detection so we don't SHA-hash every file on every scan.
app/ingestion/ingest.py	Make ingestion recursive and incremental; process only new/changed/deleted files.
app/knowledge/watcher.py	Stop running a full filesystem scan for every event; debounce and process only affected files.
app/retrieval/file_index.py	Remove expensive full-file rescanning during every file-discovery query.
app/ingestion/structured/query.py	Fix the broken filter API and make structured queries consistent.
app/query/router.py	Remove duplicated code, fix structured filtering, add cheap local routing before spending an LLM call.
app/llm/generator.py	Add timeout/retry/error handling and keep free OpenRouter usage controlled.
app/embeddings/embedder.py	Batch local embedding generation so large initial indexing is more efficient.
app/vectordb/chroma_db.py	Make document replacement/deletion safer and support batch storage cleanly.
app/main.py	Make application errors and routing behavior clean and predictable.
requirements.txt	Currently empty; the project cannot be reproducibly installed.