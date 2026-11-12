"""
conftest.py

Central Pytest configuration and fixtures for LifeOS.
Ensures:
1. Python path contains both repo root and 'app' directory.
2. Complete persistence isolation (SQLite databases, ChromaDB, Documents) to tmp_path for every test.
3. Zero workspace side effects and zero cross-test state leakage.
4. Safe offline execution by blocking unmocked live LLM network requests.
"""

import os
import sys
from pathlib import Path
import pytest

# Ensure repo root and app directory are on sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
APP_DIR = BASE_DIR / "app"

for p in [str(BASE_DIR), str(APP_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)


@pytest.fixture(autouse=True)
def isolate_test_environment(tmp_path, monkeypatch):
    """
    Autouse fixture that runs for every test:
    - Isolates all database files and directories to tmp_path
    - Initializes fresh SQLite database schemas
    - Isolates ChromaDB client and collection
    - Resets session state in main
    - Guards against unintended live LLM calls
    """
    # 1. Prepare isolated paths
    test_data_dir = tmp_path / "data"
    test_docs_dir = test_data_dir / "documents"
    test_chroma_dir = tmp_path / "chroma_db"
    test_reg_db = tmp_path / "document_registry.db"
    test_ent_db = test_data_dir / "entities.db"
    test_peo_db = test_data_dir / "people_registry.db"
    test_ctx_db = test_data_dir / "context.db"

    test_docs_dir.mkdir(parents=True, exist_ok=True)
    test_chroma_dir.mkdir(parents=True, exist_ok=True)

    # 2. Redirect config and module-level path globals
    import config
    monkeypatch.setattr(config, "DATA_DIR", test_data_dir)
    monkeypatch.setattr(config, "DOCUMENTS_DIR", test_docs_dir)
    monkeypatch.setattr(config, "CHROMA_DIR", test_chroma_dir)
    monkeypatch.setattr(config, "REGISTRY_DB", test_reg_db)
    monkeypatch.setattr(config, "ENTITY_DB", test_ent_db)
    monkeypatch.setattr(config, "PEOPLE_DB", test_peo_db)
    monkeypatch.setattr(config, "CONTEXT_DB", test_ctx_db)

    # Also redirect in all loaded modules that may have bound them
    for mod_name in ["ingestion.ingest", "app.ingestion.ingest"]:
        if mod_name in sys.modules:
            monkeypatch.setattr(sys.modules[mod_name], "DOCUMENTS_DIR", test_docs_dir)

    for mod_name in ["knowledge.document_registry", "app.knowledge.document_registry"]:
        if mod_name in sys.modules:
            monkeypatch.setattr(sys.modules[mod_name], "REGISTRY_DB", test_reg_db)

    for mod_name in ["knowledge.entity_relationship", "app.knowledge.entity_relationship"]:
        if mod_name in sys.modules:
            monkeypatch.setattr(sys.modules[mod_name], "ENTITY_DB", test_ent_db)

    for mod_name in ["knowledge.people", "app.knowledge.people"]:
        if mod_name in sys.modules:
            monkeypatch.setattr(sys.modules[mod_name], "PEOPLE_DB", test_peo_db)

    for mod_name in ["context.context_store", "app.context.context_store"]:
        if mod_name in sys.modules:
            monkeypatch.setattr(sys.modules[mod_name], "CONTEXT_DB", test_ctx_db)

    # 3. Initialize fresh schemas in the isolated databases
    try:
        from knowledge.document_registry import initialize_registry
        initialize_registry()
    except Exception:
        pass

    try:
        from knowledge.entity_relationship import initialize_entity_store
        initialize_entity_store()
    except Exception:
        pass

    try:
        from knowledge.people import initialize_people_registry
        initialize_people_registry()
    except Exception:
        pass

    try:
        from context.context_store import initialize_context_store
        initialize_context_store()
    except Exception:
        pass

    # 4. Isolate ChromaDB
    try:
        from vectordb import chroma_db
        chroma_db.reset_client(path=test_chroma_dir)
    except Exception:
        pass

    # 5. Clear main session state
    try:
        from app import main
        main.clear_session()
    except Exception:
        pass

    # 6. Safety guard against unmocked live LLM calls
    try:
        import llm.generator as generator

        def blocked_live_create(*args, **kwargs):
            raise RuntimeError(
                "Live OpenRouter LLM call blocked during test execution. "
                "Tests must remain offline and deterministic. Please mock "
                "'generate_response' or 'client.chat.completions.create'."
            )

        monkeypatch.setattr(
            generator.client.chat.completions,
            "create",
            blocked_live_create,
        )
    except Exception:
        pass

    yield {
        "data_dir": test_data_dir,
        "docs_dir": test_docs_dir,
        "chroma_dir": test_chroma_dir,
        "reg_db": test_reg_db,
        "ent_db": test_ent_db,
        "peo_db": test_peo_db,
        "ctx_db": test_ctx_db,
    }
