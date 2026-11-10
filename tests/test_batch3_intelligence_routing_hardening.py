import concurrent.futures
import json
import sys
import time
from unittest.mock import MagicMock, Mock, patch

import pytest

from app import main
from app.agent.decision import AgentDecision, AgentPlanner
from app.agent.executor import AgentExecutor
from app.agent.runner import AgentRunner
from app.agent.state import AgentState, ToolCall
from app.agent.tools import ToolRegistry
from app.intelligence.models import IntentPlan, StructuredArguments
from app.intelligence import planner
from app.intelligence.router import route_intelligent, execute_plan
from app.knowledge import entity_extractor as extractor
from knowledge.document_registry import (
    get_document,
    initialize_registry,
)
from app.query.router import QueryResult


# =====================================================================
# 1. HR-01 — PLANNER LLM OUTPUT RESILIENCE
# =====================================================================

def test_planner_valid_json(monkeypatch):
    raw = json.dumps({
        "intent": "document_search",
        "confidence": 0.95,
        "arguments": {"dataset_query": "research"},
    })
    monkeypatch.setattr(planner, "generate_response", lambda _: raw)

    plan = planner.plan_query("Tell me about research")
    assert plan.intent == "document_search"
    assert plan.confidence == 0.95
    assert plan.arguments.dataset_query == "research"


def test_planner_markdown_fenced_json(monkeypatch):
    raw = """```json
{
  "intent": "file_discovery",
  "confidence": 0.9,
  "arguments": {}
}
```"""
    monkeypatch.setattr(planner, "generate_response", lambda _: raw)

    plan = planner.plan_query("Locate my thesis")
    assert plan.intent == "file_discovery"
    assert plan.confidence == 0.9


def test_planner_markdown_fenced_json_without_lang(monkeypatch):
    raw = """```
{
  "intent": "current_time",
  "confidence": 0.99,
  "arguments": {}
}
```"""
    monkeypatch.setattr(planner, "generate_response", lambda _: raw)

    plan = planner.plan_query("What time is it?")
    assert plan.intent == "current_time"
    assert plan.confidence == 0.99


def test_planner_empty_response(monkeypatch):
    for empty_val in ["", "   ", "\n\t\n"]:
        monkeypatch.setattr(planner, "generate_response", lambda _, val=empty_val: val)
        plan = planner.plan_query("Something vague")
        assert plan.intent == "unknown"
        assert plan.confidence == 0.0


def test_planner_plain_text_response(monkeypatch):
    raw = "I am a helpful assistant and I think you want to search files."
    monkeypatch.setattr(planner, "generate_response", lambda _: raw)

    plan = planner.plan_query("Something vague")
    assert plan.intent == "unknown"
    assert plan.confidence == 0.0


def test_planner_malformed_partial_json(monkeypatch):
    raw = '{"intent": "document_sea'
    monkeypatch.setattr(planner, "generate_response", lambda _: raw)

    plan = planner.plan_query("Something vague")
    assert plan.intent == "unknown"
    assert plan.confidence == 0.0


def test_planner_json_null(monkeypatch):
    raw = "null"
    monkeypatch.setattr(planner, "generate_response", lambda _: raw)

    plan = planner.plan_query("Something vague")
    assert plan.intent == "unknown"
    assert plan.confidence == 0.0


def test_planner_structurally_invalid_json(monkeypatch):
    for raw in ["[1, 2, 3]", "12345", '"just a string"']:
        monkeypatch.setattr(planner, "generate_response", lambda _, val=raw: val)
        plan = planner.plan_query("Something vague")
        assert plan.intent == "unknown"
        assert plan.confidence == 0.0


def test_planner_valid_json_with_unsupported_fields(monkeypatch):
    # Unsupported intent name
    raw = json.dumps({"intent": "invalid_intent_xyz", "confidence": 0.8})
    monkeypatch.setattr(planner, "generate_response", lambda _: raw)

    plan = planner.plan_query("Something vague")
    assert plan.intent == "unknown"
    assert plan.confidence == 0.0


def test_planner_valid_json_with_missing_confidence(monkeypatch):
    raw = json.dumps({"intent": "document_search"})
    monkeypatch.setattr(planner, "generate_response", lambda _: raw)

    plan = planner.plan_query("Something vague")
    assert plan.intent == "unknown"
    assert plan.confidence == 0.0


def test_planner_valid_json_with_out_of_bounds_confidence(monkeypatch):
    raw = json.dumps({"intent": "document_search", "confidence": 1.5})
    monkeypatch.setattr(planner, "generate_response", lambda _: raw)

    plan = planner.plan_query("Something vague")
    assert plan.intent == "unknown"
    assert plan.confidence == 0.0


def test_planner_surrounding_whitespace(monkeypatch):
    raw = '  \n\t  {"intent": "current_time", "confidence": 0.99, "arguments": {}} \r\n '
    monkeypatch.setattr(planner, "generate_response", lambda _: raw)

    plan = planner.plan_query("What time is it?")
    assert plan.intent == "current_time"
    assert plan.confidence == 0.99


def test_planner_deterministic_rescue_on_malformed_llm_output(monkeypatch):
    # LLM returns malformed output, but query contains clear structured signal
    monkeypatch.setattr(planner, "generate_response", lambda _: "NOT JSON AT ALL")

    plan = planner.plan_query("How many customers churned?")
    assert plan.intent == "structured_query"
    assert plan.arguments.operation == "count"


def test_planner_does_not_catch_broad_exceptions(monkeypatch):
    def buggy_generate(_):
        raise ConnectionError("Network unreachable")

    monkeypatch.setattr(planner, "generate_response", buggy_generate)

    with pytest.raises(ConnectionError, match="Network unreachable"):
        planner.plan_query("Any query")


# =====================================================================
# 2. HR-02 — DUPLICATE / WASTED LLM PLANNING
# =====================================================================

def test_routing_one_planning_call_for_supported_agent_intent():
    doc_plan = IntentPlan(intent="document_search", confidence=0.95)
    agent_result = QueryResult(
        query="what is quantum computing?",
        intent=doc_plan,
        answer_type="documents",
        data=[{"document": "Quantum notes"}],
    )

    with patch("app.main.plan_query", return_value=doc_plan) as mock_plan, \
         patch("app.main._try_agent_query", return_value=agent_result) as mock_try, \
         patch("app.main._format_document_result", return_value="Result answer"):

        response = main.handle_query("what is quantum computing?")
        assert response == "Result answer"
        assert mock_plan.call_count == 1
        mock_try.assert_called_once_with("what is quantum computing?", plan=doc_plan)


def test_routing_one_planning_call_for_non_agent_intent():
    sched_plan = IntentPlan(intent="schedule_query", confidence=0.9)
    sched_result = QueryResult(
        query="when is my class?",
        intent=sched_plan,
        answer_type="schedule_context",
        data={"documents": [{"document": "Class at 10 AM"}]},
    )

    with patch("app.main.plan_query", return_value=sched_plan) as mock_plan, \
         patch("app.main._try_agent_query") as mock_try, \
         patch("app.main.route_intelligent", return_value=sched_result) as mock_route, \
         patch("app.main._format_reasoning_result", return_value="You have class at 10 AM"):

        response = main.handle_query("when is my class?")
        assert response == "You have class at 10 AM"
        # Must be planned exactly once
        assert mock_plan.call_count == 1
        # Non-agent intent must NOT enter agent path
        mock_try.assert_not_called()
        # Direct dispatch to route_intelligent with reused plan
        mock_route.assert_called_once_with("when is my class?", plan=sched_plan)


def test_routing_unsupported_agent_intent_no_duplicate_planning():
    main.clear_session()
    unknown_plan = IntentPlan(intent="unknown", confidence=0.0)
    unknown_result = QueryResult(
        query="tell me a joke",
        intent=unknown_plan,
        answer_type="unknown",
        data=None,
    )

    with patch("app.main.plan_query", return_value=unknown_plan) as mock_plan, \
         patch("app.main._try_agent_query") as mock_try, \
         patch("app.main.route_intelligent", return_value=unknown_result) as mock_route:

        response = main.handle_query("tell me a joke")
        assert "don't have a tool for it yet" in response
        assert mock_plan.call_count == 1
        mock_try.assert_not_called()
        mock_route.assert_called_once_with("tell me a joke", plan=unknown_plan)


def test_routing_agent_failure_fallback_no_duplicate_planning():
    doc_plan = IntentPlan(intent="document_search", confidence=0.95)
    fallback_result = QueryResult(
        query="search query",
        intent=doc_plan,
        answer_type="documents",
        data=[{"document": "Recovered document"}],
    )

    with patch("app.main.plan_query", return_value=doc_plan) as mock_plan, \
         patch("app.main._try_agent_query", return_value=None) as mock_try, \
         patch("app.main.route_intelligent", return_value=fallback_result) as mock_route, \
         patch("app.main._format_document_result", return_value="Recovered answer"):

        response = main.handle_query("search query")
        assert response == "Recovered answer"
        assert mock_plan.call_count == 1
        mock_try.assert_called_once_with("search query", plan=doc_plan)
        mock_route.assert_called_once_with("search query", plan=doc_plan)


def test_agent_planner_reuses_provided_plan():
    registry = Mock()
    registry.get.return_value = Mock()
    planner_instance = AgentPlanner(registry)
    plan = IntentPlan(intent="current_time", confidence=1.0)

    with patch("app.agent.decision.plan_query") as mock_plan_query:
        decision = planner_instance.decide("What time is it?", plan=plan)
        assert decision.tool_name == "current_time"
        mock_plan_query.assert_not_called()


def test_agent_planner_supports_intent_method():
    assert AgentPlanner.supports_intent("document_search") is True
    assert AgentPlanner.supports_intent("current_time") is True
    assert AgentPlanner.supports_intent("file_discovery") is True
    assert AgentPlanner.supports_intent("relationship_search") is True
    assert AgentPlanner.supports_intent("schedule_query") is False
    assert AgentPlanner.supports_intent("structured_query") is False
    assert AgentPlanner.supports_intent("unknown") is False
    assert AgentPlanner.supports_intent(None) is False


# =====================================================================
# 3. MR-P3-01 — ENTITY EXTRACTOR MALFORMED JSON
# =====================================================================

def test_entity_extractor_valid_json(monkeypatch):
    raw = json.dumps({
        "entities": [
            {"name": "Alice Smith", "type": "person", "attributes": {"title": "Dr."}}
        ]
    })
    monkeypatch.setattr(extractor, "generate_response", lambda _: raw)

    results = extractor.extract_entities("Dr. Alice Smith is here.")
    assert len(results) == 1
    assert results[0]["name"] == "Alice Smith"
    assert results[0]["type"] == "person"
    assert results[0]["attributes"]["title"] == "Dr."


def test_entity_extractor_markdown_fenced_json(monkeypatch):
    raw = """```json
{
  "entities": [
    {"name": "Bob Jones", "type": "person", "attributes": {}}
  ]
}
```"""
    monkeypatch.setattr(extractor, "generate_response", lambda _: raw)

    results = extractor.extract_entities("Bob Jones arrived.")
    assert len(results) == 1
    assert results[0]["name"] == "Bob Jones"


def test_entity_extractor_empty_response(monkeypatch):
    for empty_val in ["", "  ", None]:
        monkeypatch.setattr(extractor, "generate_response", lambda _, v=empty_val: v)
        assert extractor.extract_entities("Some text") == []


def test_entity_extractor_malformed_json(monkeypatch):
    raw = '{"entities": [{"name": "Broken'
    monkeypatch.setattr(extractor, "generate_response", lambda _: raw)

    assert extractor.extract_entities("Some text") == []


def test_entity_extractor_structurally_invalid_json(monkeypatch):
    for raw in ["null", "[1, 2, 3]", "123", '"just string"']:
        monkeypatch.setattr(extractor, "generate_response", lambda _, v=raw: v)
        assert extractor.extract_entities("Some text") == []


def test_relationship_extractor_fenced_and_malformed(monkeypatch):
    entities = [{"name": "Alice", "type": "person"}, {"name": "Bob", "type": "person"}]

    fenced_raw = """```json
{
  "relationships": [
    {"source": "Alice", "target": "Bob", "type": "knows", "confidence": 0.9}
  ]
}
```"""
    monkeypatch.setattr(extractor, "generate_response", lambda _: fenced_raw)
    rels = extractor.extract_relationships("Alice knows Bob", entities=entities)
    assert len(rels) == 1
    assert rels[0]["source"] == "Alice"
    assert rels[0]["target"] == "Bob"

    # Malformed JSON fallback
    monkeypatch.setattr(extractor, "generate_response", lambda _: "not json")
    assert extractor.extract_relationships("Alice knows Bob", entities=entities) == []


# =====================================================================
# 4. MR-P4-03 — AGENT WALL-CLOCK TIMEOUT / CANCELLATION
# =====================================================================

def test_agent_runner_normal_execution_under_timeout():
    planner_mock = Mock()
    planner_mock.decide.return_value = AgentDecision(
        action="tool",
        tool_name="current_time",
        has_more_steps=False,
    )
    executor_mock = Mock()
    executor_mock.execute_tool.return_value = AgentState(
        query="time",
        status="running",
        step_count=1,
    )

    runner = AgentRunner(planner_mock, executor_mock, timeout=5.0)
    state = runner.run("time")
    assert state.status == "completed"
    assert state.step_count == 1


def test_agent_runner_timeout_exceeded_stalled_planner():
    def hanging_decide(*args, **kwargs):
        time.sleep(0.5)
        return AgentDecision(action="finish")

    planner_mock = Mock()
    planner_mock.decide.side_effect = hanging_decide
    executor_mock = Mock()

    # Configure short timeout (50ms)
    runner = AgentRunner(planner_mock, executor_mock, timeout=0.05)
    t0 = time.monotonic()
    state = runner.run("college notes")
    duration = time.monotonic() - t0

    assert state.status == "failed"
    assert "timed out" in state.error.lower()
    # Ensure control path unblocked quickly without waiting for full sleep
    assert duration < 0.3


def test_agent_runner_timeout_exceeded_stalled_tool():
    planner_mock = Mock()
    planner_mock.decide.return_value = AgentDecision(
        action="tool",
        tool_name="hanging_tool",
        arguments={},
        has_more_steps=False,
    )

    def hanging_tool(*args, **kwargs):
        time.sleep(0.5)
        return AgentState(query="test", status="completed")

    executor_mock = Mock()
    executor_mock.execute_tool.side_effect = hanging_tool

    runner = AgentRunner(planner_mock, executor_mock, timeout=0.05)
    t0 = time.monotonic()
    state = runner.run("test query")
    duration = time.monotonic() - t0

    assert state.status == "failed"
    assert "timed out" in state.error.lower()
    assert duration < 0.3


def test_agent_runner_zero_or_negative_timeout():
    planner_mock = Mock()
    executor_mock = Mock()

    runner = AgentRunner(planner_mock, executor_mock, timeout=0.0)
    state = runner.run("test query")
    assert state.status == "failed"
    assert "timed out" in state.error.lower()
    planner_mock.decide.assert_not_called()


def test_agent_runner_repeated_stalls_do_not_create_unbounded_workers():
    """Prove repeated stalled operations do not spawn 1 worker per request."""
    import concurrent.futures
    import threading

    custom_pool = concurrent.futures.ThreadPoolExecutor(
        max_workers=2,
        thread_name_prefix="b3_bounded_worker",
    )

    def hanging_decide(*args, **kwargs):
        time.sleep(0.4)
        return AgentDecision(action="finish")

    planner_mock = Mock()
    planner_mock.decide.side_effect = hanging_decide
    executor_mock = Mock()

    runner = AgentRunner(
        planner_mock,
        executor_mock,
        timeout=0.03,
        executor_pool=custom_pool,
    )

    # Trigger 6 consecutive stalled operations
    for i in range(6):
        t0 = time.monotonic()
        state = runner.run(f"query {i}")
        duration = time.monotonic() - t0
        assert state.status == "failed"
        assert "timed out" in state.error.lower()
        assert duration < 0.25

    # Check live workers created by this pool
    workers = [t for t in threading.enumerate() if t.name.startswith("b3_bounded_worker")]
    assert len(workers) <= 2, f"Expected at most 2 bounded workers, got {len(workers)}"

    custom_pool.shutdown(wait=False, cancel_futures=True)


def test_agent_runner_queued_future_cancellation_under_saturation():
    """Prove that tasks queued when pool is saturated get cancelled on timeout."""
    import concurrent.futures

    custom_pool = concurrent.futures.ThreadPoolExecutor(
        max_workers=1,
        thread_name_prefix="b3_single_worker",
    )

    def slow_decide(*args, **kwargs):
        time.sleep(0.4)
        return AgentDecision(action="finish")

    planner_mock = Mock()
    planner_mock.decide.side_effect = slow_decide
    executor_mock = Mock()

    runner = AgentRunner(
        planner_mock,
        executor_mock,
        timeout=0.03,
        executor_pool=custom_pool,
    )

    # First stalled task: occupies the 1 worker
    state1 = runner.run("stalled 1")
    assert state1.status == "failed"
    assert "timed out" in state1.error.lower()

    # Second stalled task while worker is still occupied: queued and cancelled
    t0 = time.monotonic()
    state2 = runner.run("stalled 2")
    duration = time.monotonic() - t0
    assert state2.status == "failed"
    assert "timed out" in state2.error.lower()
    assert duration < 0.25

    custom_pool.shutdown(wait=False, cancel_futures=True)


def test_agent_runner_shared_executor_lifecycle_and_shutdown():
    """Verify shared pool initialization, usage, and clean shutdown lifecycle."""
    from app.agent.runner import get_agent_executor, shutdown_agent_executor

    pool1 = get_agent_executor()
    assert pool1 is not None
    # Pool is idempotent
    assert get_agent_executor() is pool1

    # Shutdown resets singleton
    shutdown_agent_executor(wait=False, cancel_futures=True)

    # Next call instantiates fresh pool
    pool2 = get_agent_executor()
    assert pool2 is not None
    assert pool2 is not pool1

    shutdown_agent_executor(wait=False, cancel_futures=True)


def test_llm_generator_socket_timeout_configured():
    """Verify lowest-layer LLM client socket timeout and retry limits."""
    from llm.generator import client
    import config

    assert client.timeout == float(config.LLM_TIMEOUT_SECONDS)
    assert client.max_retries == int(config.LLM_MAX_RETRIES)


# =====================================================================
# 5. BUG-P3-05 — INGESTION METADATA LOSS
# =====================================================================

def test_ingest_file_persists_extracted_metadata_to_registry(tmp_path, monkeypatch):
    import config
    from ingestion import ingest
    import knowledge.document_registry as doc_reg

    test_db = tmp_path / "test_reg.db"
    monkeypatch.setattr(config, "DOCUMENTS_DIR", tmp_path)
    monkeypatch.setattr(ingest, "DOCUMENTS_DIR", tmp_path)
    monkeypatch.setattr(config, "REGISTRY_DB", test_db)
    monkeypatch.setattr(doc_reg, "REGISTRY_DB", test_db)
    if "app.knowledge.document_registry" in sys.modules:
        monkeypatch.setattr(sys.modules["app.knowledge.document_registry"], "REGISTRY_DB", test_db)
    initialize_registry()

    test_file = tmp_path / "research_notes.txt"
    test_file.write_text("Alice works on LifeOS architecture with Bob.", encoding="utf-8")

    # Mock chunking, embedding, and chroma storage
    monkeypatch.setattr(ingest, "file_needs_processing", lambda _: True)
    monkeypatch.setattr(ingest, "is_unchanged", lambda *_: False)
    monkeypatch.setattr(ingest, "classify_file", lambda _: {"type": "text_document", "strategy": "text", "size_mb": 0.01})
    monkeypatch.setattr(ingest, "calculate_file_hash", lambda _: "hash_b3_test")
    monkeypatch.setattr(ingest, "generate_embeddings", lambda chunks: [[0.1] * 10 for _ in chunks])
    monkeypatch.setattr(ingest, "store_embeddings", lambda chunks, embs, path: None)

    # Mock analyzer to return rich metadata
    expected_metadata = {
        "document_type": "notes",
        "category": "computer_science",
        "topics": ["LifeOS", "architecture"],
        "entities": [{"name": "Alice", "type": "person"}, {"name": "Bob", "type": "person"}],
        "dates": [],
        "summary": "Notes on LifeOS distributed architecture.",
        "folder": tmp_path.name,
        "parent_folder": tmp_path.parent.name,
    }

    import knowledge.analyzer as analyzer_mod
    monkeypatch.setattr(analyzer_mod, "analyze_document", lambda *args, **kwargs: expected_metadata)

    success = ingest.ingest_file(test_file)
    assert success is True

    # Verify the document was saved in the registry WITH metadata_json
    record = get_document(test_file)
    assert record is not None
    assert record["ingestion_status"] == "success"
    assert record["metadata_json"] is not None

    stored_metadata = json.loads(record["metadata_json"])
    assert stored_metadata["summary"] == "Notes on LifeOS distributed architecture."
    assert len(stored_metadata["entities"]) == 2
    assert stored_metadata["category"] == "computer_science"


def test_ingestion_failure_does_not_persist_success_metadata(tmp_path, monkeypatch):
    import config
    from ingestion import ingest
    import knowledge.document_registry as doc_reg

    test_db = tmp_path / "test_reg_fail.db"
    monkeypatch.setattr(config, "DOCUMENTS_DIR", tmp_path)
    monkeypatch.setattr(ingest, "DOCUMENTS_DIR", tmp_path)
    monkeypatch.setattr(config, "REGISTRY_DB", test_db)
    monkeypatch.setattr(doc_reg, "REGISTRY_DB", test_db)
    if "app.knowledge.document_registry" in sys.modules:
        monkeypatch.setattr(sys.modules["app.knowledge.document_registry"], "REGISTRY_DB", test_db)
    initialize_registry()

    test_file = tmp_path / "corrupt.txt"
    test_file.write_text("corrupted content", encoding="utf-8")

    monkeypatch.setattr(ingest, "file_needs_processing", lambda _: True)
    monkeypatch.setattr(ingest, "is_unchanged", lambda *_: False)
    monkeypatch.setattr(ingest, "classify_file", lambda _: {"type": "text_document", "strategy": "text", "size_mb": 0.01})
    monkeypatch.setattr(ingest, "calculate_file_hash", lambda _: "corrupt_hash")

    # Reading or chunking fails
    def fail_read(*args, **kwargs):
        raise ValueError("Corrupt file encoding")

    monkeypatch.setattr(ingest, "read_documents", fail_read)

    success = ingest.ingest_file(test_file)
    assert success is False

    record = get_document(test_file)
    assert record is not None
    assert record["ingestion_status"] == "failed"
    assert record["metadata_json"] is None


# =====================================================================
# 6. CROSS-CHECK BATCH 3 INTERACTIONS
# =====================================================================

def test_cross_check_malformed_planner_output_with_agent_routing():
    """
    Interaction A: Malformed planner output must fall back safely to unknown
    without crashing, without entering agent execution, and without duplicate planning.
    """
    main.clear_session()
    with patch("llm.generator.generate_response", return_value="INVALID MODEL OUTPUT {{"), \
         patch("intelligence.planner.generate_response", return_value="INVALID MODEL OUTPUT {{"), \
         patch("app.agent.runner.AgentRunner.run") as mock_runner_run:

        response = main.handle_query("arbitrary malformed trigger query")
        assert "don't have a tool for it yet" in response
        # Should NOT enter agent execution on unknown/malformed plan
        mock_runner_run.assert_not_called()


def test_cross_check_planner_stall_with_agent_timeout():
    """
    Interaction B: Planner LLM stall must not create an unbounded agent execution path.
    """
    def stalling_decide(query, state=None, plan=None):
        time.sleep(0.3)
        return AgentDecision(action="finish")

    planner_mock = Mock()
    planner_mock.decide.side_effect = stalling_decide
    executor_mock = Mock()

    runner = AgentRunner(planner_mock, executor_mock, timeout=0.05)
    t0 = time.monotonic()
    state = runner.run("stalling query")
    duration = time.monotonic() - t0

    assert state.status == "failed"
    assert "timed out" in state.error.lower()
    assert duration < 0.2


def test_cross_check_entity_extraction_malformed_does_not_fail_ingestion(tmp_path, monkeypatch):
    """
    Interaction C: Malformed extraction output must not fail otherwise valid ingestion.
    """
    import config
    from ingestion import ingest
    import knowledge.document_registry as doc_reg

    test_db = tmp_path / "test_reg_cross.db"
    monkeypatch.setattr(config, "DOCUMENTS_DIR", tmp_path)
    monkeypatch.setattr(ingest, "DOCUMENTS_DIR", tmp_path)
    monkeypatch.setattr(config, "REGISTRY_DB", test_db)
    monkeypatch.setattr(doc_reg, "REGISTRY_DB", test_db)
    if "app.knowledge.document_registry" in sys.modules:
        monkeypatch.setattr(sys.modules["app.knowledge.document_registry"], "REGISTRY_DB", test_db)
    initialize_registry()

    test_file = tmp_path / "valid_document.txt"
    test_file.write_text("Valid text document content for indexing.", encoding="utf-8")

    monkeypatch.setattr(ingest, "file_needs_processing", lambda _: True)
    monkeypatch.setattr(ingest, "is_unchanged", lambda *_: False)
    monkeypatch.setattr(ingest, "classify_file", lambda _: {"type": "text_document", "strategy": "text", "size_mb": 0.01})
    monkeypatch.setattr(ingest, "calculate_file_hash", lambda _: "hash_cross_test")
    monkeypatch.setattr(ingest, "generate_embeddings", lambda chunks: [[0.1] * 10 for _ in chunks])
    monkeypatch.setattr(ingest, "store_embeddings", lambda chunks, embs, path: None)

    # Malformed LLM output for entity/relationship extraction
    import knowledge.entity_extractor as extractor_mod
    monkeypatch.setattr(extractor_mod, "generate_response", lambda _: "MALFORMED JSON {{{")

    success = ingest.ingest_file(test_file)
    # Ingestion must still succeed despite entity extractor failure
    assert success is True

    record = get_document(test_file)
    assert record is not None
    assert record["ingestion_status"] == "success"
    # Metadata is persisted with empty entities list rather than crashing
    assert record["metadata_json"] is not None
    parsed_meta = json.loads(record["metadata_json"])
    assert parsed_meta["entities"] == []

