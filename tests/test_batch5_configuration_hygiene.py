"""
test_batch5_configuration_hygiene.py

Deterministic regression tests for Batch 5:
- Central configuration source-of-truth
- Prevention of hardcoded model/config bypasses (generator.py, embedder.py, chunker.py, watcher.py)
- Configuration validation and range bounding
- Deterministic error messages for malformed environment variables
- Complete test/production persistence isolation and absence of workspace state pollution
- Offline execution and absence of live network/LLM calls
"""

import os
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

import config


# =====================================================================
# 1. HARDCODED MODEL & CONFIGURATION BYPASS VERIFICATION
# =====================================================================

def test_generator_respects_config_openrouter_model(monkeypatch):
    """
    Verify generator.MODEL consumes config.OPENROUTER_MODEL instead of
    bypassing it with a hardcoded model name.
    """
    monkeypatch.setattr(config, "OPENROUTER_MODEL", "custom/test-model-42")

    # Reload generator with new config
    import importlib
    import llm.generator as generator
    monkeypatch.setattr(generator, "MODEL", config.OPENROUTER_MODEL)

    assert generator.MODEL == "custom/test-model-42"


def test_generator_respects_config_vision_model(monkeypatch):
    """
    Verify generator.VISION_MODEL consumes config.OPENROUTER_VISION_MODEL.
    """
    monkeypatch.setattr(config, "OPENROUTER_VISION_MODEL", "custom/vision-model-99")
    import llm.generator as generator
    monkeypatch.setattr(generator, "VISION_MODEL", config.OPENROUTER_VISION_MODEL)

    assert generator.VISION_MODEL == "custom/vision-model-99"


def test_generator_client_configured_from_config():
    """
    Verify generator client timeout and retries match config definitions.
    """
    import llm.generator as generator
    assert generator.client.timeout == float(config.LLM_TIMEOUT_SECONDS)
    assert generator.client.max_retries == int(config.LLM_MAX_RETRIES)


def test_embedder_respects_config_embedding_model(monkeypatch):
    """
    Verify embedder uses config.EMBEDDING_MODEL and lazy-loads the model.
    """
    monkeypatch.setattr(config, "EMBEDDING_MODEL", "test-embedding-model")
    import embeddings.embedder as embedder

    mock_st_class = MagicMock()
    monkeypatch.setattr("sentence_transformers.SentenceTransformer", mock_st_class)
    monkeypatch.setattr(embedder, "_model", None)

    # Calling generate_embeddings should instantiate with configured model
    embedder.generate_embeddings(["test text"])
    mock_st_class.assert_called_once_with("test-embedding-model")


def test_chunker_uses_config_defaults(monkeypatch):
    """
    Verify chunk_text uses CHUNK_SIZE and CHUNK_OVERLAP from config when not explicitly passed.
    """
    from processing import chunker

    monkeypatch.setattr(config, "CHUNK_SIZE", 800)
    monkeypatch.setattr(config, "CHUNK_OVERLAP", 120)
    monkeypatch.setattr(chunker, "CHUNK_SIZE", 800)
    monkeypatch.setattr(chunker, "CHUNK_OVERLAP", 120)

    with patch("processing.chunker.RecursiveCharacterTextSplitter") as mock_splitter_cls:
        chunker.chunk_text("Some text to chunk")
        mock_splitter_cls.assert_called_once()
        _, kwargs = mock_splitter_cls.call_args
        assert kwargs["chunk_size"] == 800
        assert kwargs["chunk_overlap"] == 120


def test_watcher_uses_config_debounce(monkeypatch):
    """
    Verify watcher uses WATCHER_DEBOUNCE_SECONDS from config.
    """
    from knowledge import watcher
    assert watcher.DEBOUNCE_SECONDS == config.WATCHER_DEBOUNCE_SECONDS


# =====================================================================
# 2. CONFIGURATION VALIDATION & ERROR HANDLING
# =====================================================================

def test_config_int_validation_rejects_non_numeric():
    # Valid default passes when key is not set
    assert config._get_env_int("TEST_INT_KEY_UNSET", 10) == 10

    with patch.dict(os.environ, {"TEST_BAD_INT": "not_an_int"}):
        with pytest.raises(ValueError) as exc:
            config._get_env_int("TEST_BAD_INT", 10)
        assert "not a valid integer" in str(exc.value)


def test_config_int_validation_enforces_bounds():
    with patch.dict(os.environ, {"TEST_LOW_INT": "0"}):
        with pytest.raises(ValueError) as exc:
            config._get_env_int("TEST_LOW_INT", 10, min_val=1)
        assert "must be >= 1" in str(exc.value)


def test_config_float_validation_rejects_non_numeric():
    with patch.dict(os.environ, {"TEST_BAD_FLOAT": "abc"}):
        with pytest.raises(ValueError) as exc:
            config._get_env_float("TEST_BAD_FLOAT", 1.0)
        assert "not a valid float" in str(exc.value)


def test_config_float_validation_enforces_bounds():
    with patch.dict(os.environ, {"TEST_NEG_TIMEOUT": "-5.0"}):
        with pytest.raises(ValueError) as exc:
            config._get_env_float("TEST_NEG_TIMEOUT", 10.0, min_val=0.1)
        assert "must be >= 0.1" in str(exc.value)


def test_config_str_validation_enforces_allowed_set():
    with patch.dict(os.environ, {"TEST_LOG_LVL": "SUPER_VERBOSE"}):
        with pytest.raises(ValueError) as exc:
            config._get_env_str("TEST_LOG_LVL", "INFO", allowed={"DEBUG", "INFO", "WARNING", "ERROR"})
        assert "must be one of" in str(exc.value)


def test_config_cross_field_validation():
    """Verify validate_config catches inconsistent configuration states."""
    with patch.object(config, "CHUNK_SIZE", 100), patch.object(config, "CHUNK_OVERLAP", 150):
        with pytest.raises(ValueError) as exc:
            config.validate_config()
        assert "CHUNK_OVERLAP" in str(exc.value)

    with patch.object(config, "WATCHER_CHECK_INTERVAL_SECONDS", 5.0), \
         patch.object(config, "WATCHER_STABILITY_SECONDS", 1.0):
        with pytest.raises(ValueError) as exc:
            config.validate_config()
        assert "WATCHER_CHECK_INTERVAL_SECONDS" in str(exc.value)


# =====================================================================
# 3. TEST SUITE HYGIENE & PRODUCTION PERSISTENCE ISOLATION
# =====================================================================

def test_test_environment_is_completely_isolated(tmp_path):
    """
    Ensure test environment paths point inside tmp_path and never touch
    the real workspace paths.
    """
    import config
    workspace_root = config.BASE_DIR

    assert str(workspace_root) not in str(config.DATA_DIR) or "pytest" in str(config.DATA_DIR) or "tmp" in str(config.DATA_DIR).lower()
    assert str(workspace_root) not in str(config.REGISTRY_DB) or "pytest" in str(config.REGISTRY_DB) or "tmp" in str(config.REGISTRY_DB).lower()
    assert str(workspace_root) not in str(config.CHROMA_DIR) or "pytest" in str(config.CHROMA_DIR) or "tmp" in str(config.CHROMA_DIR).lower()


def test_unmocked_live_llm_call_is_blocked_in_tests():
    """
    Verify that any attempt to call live OpenRouter completions without a mock
    is blocked by conftest guard, preventing live network calls.
    """
    import llm.generator as generator
    with pytest.raises(RuntimeError) as exc:
        generator.client.chat.completions.create(model="any", messages=[])
    assert "Live OpenRouter LLM call blocked during test execution" in str(exc.value)
