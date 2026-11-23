"""
test_milestone7_hybrid_routing_and_gatekeeper.py

Deterministic isolation and verification test suite for Milestone 7:
- Deterministic PII detection across all categories (email, phone, IP, credit card with Luhn, SSN, API secrets)
- Request-scoped SurrogateVault lifecycle, zero persistence, and leak prevention
- 5-step pre-egress gating pipeline (workspace local_only -> consent -> budget -> gatekeeper -> cloud)
- Critical credentials hard block
- Surrogate de-identification and re-identification restoration
- Resilient cascade to local fallback on cloud failure
- Compatibility with existing generate_response monkeypatching
- REST API authorization and endpoints (/api/routing/policy, inspect, metrics)
"""

import json
import os
import sys
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from starlette.testclient import TestClient

import app.config as config
from app.api.server import app
from app.routing.models import (
    PIICategory,
    SensitivityLevel,
    RoutingMode,
    ProviderType,
)
from app.routing.gatekeeper import SurrogateVault, PrivacyGatekeeper, _luhn_checksum
from app.routing.budget import BudgetManager
from app.routing.providers.base import BaseLLMProvider
from app.routing.providers.cloud import CloudOpenAIProvider
from app.routing.providers.fallback import DeterministicFallbackProvider
from app.routing.providers.local import LocalOllamaProvider
from app.routing.providers.mock import MockLLMProvider
from app.routing.hybrid_router import HybridRouter, get_hybrid_router, reset_hybrid_router
from app.workspace.models import WorkspaceProfile
from app.workspace.store import create_workspace, init_workspace_store
from app.security.consent import grant_consent, revoke_consent
from app.security.privacy_store import initialize_privacy_store


# ============================================================
# 1. PII DETECTION & SENSITIVITY LEVEL TESTS
# ============================================================

def test_luhn_algorithm_validation():
    """Verify Luhn checksum accurately accepts valid card numbers and rejects invalid ones."""
    # Valid Visa test number
    assert _luhn_checksum("4532015112830366") is True
    # Invalid card number (checksum fails)
    assert _luhn_checksum("4532015112830367") is False
    # Numbers outside 13-19 digit range
    assert _luhn_checksum("12345") is False


def test_gatekeeper_pii_detection():
    """Verify privacy gatekeeper detects emails, phones, IPs, cards, SSNs, and secrets."""
    gk = PrivacyGatekeeper(sensitive_names=["ProjectTitan", "SecretVault"])

    text = (
        "Contact me at alice@example.com or +1-555-867-5309. "
        "Server IP is 192.168.1.100. "
        "SSN is 123-45-6789. "
        "Card is 4532-0151-1283-0366. "
        "API key is sk-1234567890abcdef1234567890abcdef. "
        "Code name ProjectTitan."
    )

    entities = gk.detect_pii(text)
    categories = {e.category for e in entities}

    assert PIICategory.EMAIL in categories
    assert PIICategory.PHONE in categories
    assert PIICategory.IP_ADDRESS in categories
    assert PIICategory.GOV_ID in categories
    assert PIICategory.FINANCIAL in categories
    assert PIICategory.CREDENTIALS in categories
    assert PIICategory.SENSITIVE_NAME in categories


def test_gatekeeper_sensitivity_assessment():
    """Verify sensitivity levels: CRITICAL for credentials, HIGH for financial/gov_id, MODERATE for contact/IP, LOW for benign."""
    gk = PrivacyGatekeeper()

    # Benign text
    benign_entities = gk.detect_pii("The weather is nice today.")
    assert gk.assess_sensitivity(benign_entities) == SensitivityLevel.LOW

    # Contact info
    contact_entities = gk.detect_pii("Send email to bob@example.com")
    assert gk.assess_sensitivity(contact_entities) == SensitivityLevel.MODERATE

    # Financial / Gov ID
    high_entities = gk.detect_pii("My SSN is 123-45-6789")
    assert gk.assess_sensitivity(high_entities) == SensitivityLevel.HIGH

    # Critical secrets
    crit_entities = gk.detect_pii("My token is ghp_123456789012345678901234567890123456")
    assert gk.assess_sensitivity(crit_entities) == SensitivityLevel.CRITICAL


# ============================================================
# 2. SURROGATE VAULT LIFECYCLE & LEAK PREVENTION TESTS
# ============================================================

def test_surrogate_vault_lifecycle_and_masking():
    """Verify SurrogateVault replaces entities, restores text, clears state, and never prints raw values."""
    vault = SurrogateVault()
    gk = PrivacyGatekeeper()

    original = "Hello alice@example.com, call 555-234-5678"
    entities = gk.detect_pii(original)

    scrubbed = vault.deidentify(original, entities)
    assert "alice@example.com" not in scrubbed
    assert "555-234-5678" not in scrubbed
    assert "<EMAIL_1>" in scrubbed
    assert "<PHONE_1>" in scrubbed

    # Re-identify
    restored = vault.reidentify(scrubbed)
    assert restored == original

    # Masking in repr / str
    rep = repr(vault)
    assert "alice@example.com" not in rep
    assert "555-234-5678" not in rep
    assert "entries=2" in rep

    # Clear
    vault.clear()
    assert len(vault._forward) == 0
    assert len(vault._reverse) == 0


def test_surrogate_vault_context_manager():
    """Verify context manager usage automatically wipes surrogate mapping upon exit."""
    gk = PrivacyGatekeeper()
    with SurrogateVault() as vault:
        prompt = "Email is test@example.com"
        ents = gk.detect_pii(prompt)
        scrubbed = vault.deidentify(prompt, ents)
        assert "<EMAIL_1>" in scrubbed
        assert len(vault._forward) == 1

    # After exit, vault should be cleared
    assert len(vault._forward) == 0


# ============================================================
# 3. 5-STEP PRE-EGRESS GATING PIPELINE TESTS
# ============================================================

def test_gate1_workspace_local_only_enforcement(tmp_path):
    """Gate 1: If workspace has local_only=True, immediately routes to LOCAL with ZERO cloud calls."""
    db_file = tmp_path / "ws.db"
    init_workspace_store(db_file)

    # Create local_only workspace
    ws = create_workspace(
        name="Confidential Medical",
        profile_type="confidential",
        settings={"local_only": True},
        db_path=db_file,
    )

    cloud_mock = MockLLMProvider(provider_type=ProviderType.CLOUD)
    local_mock = MockLLMProvider(provider_type=ProviderType.LOCAL, default_response="Local response")

    router = HybridRouter(
        cloud_provider=cloud_mock,
        local_provider=local_mock,
    )

    with patch("app.routing.hybrid_router.get_workspace", return_value=ws):
        res = router.route_and_generate("Analyze patient notes", workspace_id=ws.workspace_id)

    assert res == "Local response"
    assert len(cloud_mock.calls) == 0, "Cloud provider MUST NOT be called when workspace is local_only"
    assert len(local_mock.calls) == 1


def test_gate2_consent_check_enforcement(tmp_path):
    """Gate 2: Without cloud_reasoning consent, routes locally. When consent granted, proceeds."""
    privacy_db = tmp_path / "privacy.db"
    initialize_privacy_store(privacy_db)

    cloud_mock = MockLLMProvider(provider_type=ProviderType.CLOUD, default_response="Cloud response")
    local_mock = MockLLMProvider(provider_type=ProviderType.LOCAL, default_response="Local response")

    router = HybridRouter(
        cloud_provider=cloud_mock,
        local_provider=local_mock,
    )

    # 1. No consent granted -> Must route locally
    with patch("app.security.consent._get_db_path", return_value=privacy_db):
        res = router.route_and_generate("General question", workspace_id="default")
        assert res == "Local response"
        assert len(cloud_mock.calls) == 0

    # 2. Grant explicit consent for cloud reasoning
    grant = grant_consent(
        resource_path="cloud_llm",
        scope_type="connector",
        purposes=["cloud_reasoning"],
        allow_cloud_sync=True,
        db_path=privacy_db,
    )

    with patch("app.security.consent._get_db_path", return_value=privacy_db):
        res = router.route_and_generate("General question", workspace_id="default")
        assert res == "Cloud response"
        assert len(cloud_mock.calls) == 1

    # 3. Revoke consent -> Must immediately flip back to local
    revoke_consent(grant.consent_id, reason="User revoked cloud access", db_path=privacy_db)

    with patch("app.security.consent._get_db_path", return_value=privacy_db):
        res = router.route_and_generate("General question", workspace_id="default")
        assert res == "Local response"
        assert len(cloud_mock.calls) == 1  # No additional cloud calls


def test_gate3_daily_budget_cap_enforcement(tmp_path):
    """Gate 3: When daily budget is exceeded, auto-routes locally without hitting cloud."""
    budget_file = tmp_path / "budget.json"
    bm = BudgetManager(daily_budget_usd=0.05, budget_file=budget_file)

    cloud_mock = MockLLMProvider(provider_type=ProviderType.CLOUD, default_response="Cloud response")
    local_mock = MockLLMProvider(provider_type=ProviderType.LOCAL, default_response="Local response")

    router = HybridRouter(
        cloud_provider=cloud_mock,
        local_provider=local_mock,
        budget_manager=bm,
    )

    # Bypass gate 2 by mocking consent as granted
    with patch("app.security.consent.is_consent_granted", return_value=True):
        # 1. Under budget: cloud call succeeds
        res = router.route_and_generate("Query 1")
        assert res == "Cloud response"
        assert len(cloud_mock.calls) == 1

        # Record high spend to exceed budget
        bm.record_usage(tokens_in=50000, tokens_out=50000, cost_usd=0.10)
        assert bm.is_budget_exceeded() is True

        # 2. Over budget: automatically routes locally
        res2 = router.route_and_generate("Query 2")
        assert res2 == "Local response"
        assert len(cloud_mock.calls) == 1  # No new cloud call


def test_gate4_critical_secrets_hard_block():
    """Gate 4: Prompts containing critical secrets/API tokens are hard-blocked from cloud egress."""
    cloud_mock = MockLLMProvider(provider_type=ProviderType.CLOUD)
    local_mock = MockLLMProvider(provider_type=ProviderType.LOCAL, default_response="Handled locally")

    router = HybridRouter(
        cloud_provider=cloud_mock,
        local_provider=local_mock,
    )

    prompt = "Here is my private token sk-abcdef1234567890abcdef1234567890 for debugging."

    with patch("app.security.consent.is_consent_granted", return_value=True):
        decision = router.evaluate_route(prompt)
        assert decision.target_provider == ProviderType.LOCAL
        assert decision.sensitivity_level == SensitivityLevel.CRITICAL
        assert "Critical secrets" in decision.reason

        res = router.route_and_generate(prompt)
        assert res == "Handled locally"
        assert len(cloud_mock.calls) == 0


def test_gate5_surrogate_deidentification_before_cloud_egress():
    """Gate 5: Moderate PII is scrubbed before reaching cloud provider, and restored on return."""
    # Custom cloud mock that verifies the prompt it receives contains placeholders, not raw PII
    def mock_cloud_handler(prompt_text: str) -> str:
        assert "john.doe@example.com" not in prompt_text
        assert "+1-555-123-4567" not in prompt_text
        assert "<EMAIL_1>" in prompt_text
        assert "<PHONE_1>" in prompt_text
        return "I will email <EMAIL_1> and call <PHONE_1> tomorrow."

    cloud_mock = MockLLMProvider(
        provider_type=ProviderType.CLOUD,
        custom_handler=mock_cloud_handler,
    )
    local_mock = MockLLMProvider(provider_type=ProviderType.LOCAL)

    router = HybridRouter(
        cloud_provider=cloud_mock,
        local_provider=local_mock,
    )

    user_prompt = "Please send invoice to john.doe@example.com or call +1-555-123-4567."

    with patch("app.security.consent.is_consent_granted", return_value=True):
        final_answer = router.route_and_generate(user_prompt)

    # Caller receives re-identified text!
    assert "john.doe@example.com" in final_answer
    assert "+1-555-123-4567" in final_answer
    assert "<EMAIL_1>" not in final_answer


# ============================================================
# 4. RESILIENT CASCADE & FALLBACK TESTS
# ============================================================

def test_cascade_to_local_on_cloud_failure():
    """If cloud provider throws an error (e.g. 503 or timeout), router cascades to local provider."""
    cloud_failing = MockLLMProvider(
        provider_type=ProviderType.CLOUD,
        should_fail=True,
        failure_exception=TimeoutError("Cloud LLM gateway timeout"),
    )
    local_mock = MockLLMProvider(
        provider_type=ProviderType.LOCAL,
        default_response="Local cascade fallback response",
    )

    router = HybridRouter(
        cloud_provider=cloud_failing,
        local_provider=local_mock,
    )

    with patch("app.security.consent.is_consent_granted", return_value=True):
        res = router.route_and_generate("Summarize report")

    assert res == "Local cascade fallback response"
    assert len(cloud_failing.calls) == 1
    assert len(local_mock.calls) == 1


def test_deterministic_fallback_provider():
    """DeterministicFallbackProvider produces valid response without external dependencies."""
    fb = DeterministicFallbackProvider()

    # Plain query
    res = fb.generate("What is quantum computing?")
    assert res.provider_type == ProviderType.FALLBACK
    assert "Local Fallback Response" in res.text

    # JSON query
    res_json = fb.generate("Extract entities. Return only valid JSON.")
    parsed = json.loads(res_json.text)
    assert parsed["status"] == "success"


# ============================================================
# 5. MONKEYPATCH COMPATIBILITY TESTS
# ============================================================

def test_generate_response_monkeypatch_planner_compatibility(monkeypatch):
    """Verify that existing monkeypatching of generate_response continues to intercept cleanly."""
    import app.llm.generator as gen

    # 1. Monkeypatch at app.llm.generator
    monkeypatch.setattr(gen, "generate_response", lambda prompt: "Intercepted Plan")
    assert gen.generate_response("Plan task") == "Intercepted Plan"

    # 2. Integration through get_hybrid_router
    reset_hybrid_router()
    # Unpatched generator calls hybrid router
    # When no consent, routes to fallback
    with patch("app.security.consent.is_consent_granted", return_value=False):
        from app.llm.generator import generate_response
        out = generate_response("Hello LifeOS")
        assert isinstance(out, str)
        assert len(out) > 0


# ============================================================
# 6. REST API ENDPOINTS TESTS
# ============================================================

TEST_API_KEY = "test-secret-user-key-12345"
TEST_ADMIN_KEY = "test-secret-admin-key-67890"


@pytest.fixture
def client(monkeypatch):
    for mod_name in ("config", "app.config"):
        if mod_name in sys.modules:
            mod = sys.modules[mod_name]
            monkeypatch.setattr(mod, "API_AUTH_ENABLED", True)
            monkeypatch.setattr(mod, "API_KEY", TEST_API_KEY)
            monkeypatch.setattr(mod, "API_ADMIN_KEY", TEST_ADMIN_KEY)
    return TestClient(app)


def test_routing_api_endpoints(client):
    """Verify /api/routing/policy, /api/routing/gatekeeper/inspect, and /api/routing/metrics."""
    headers = {"X-API-Key": TEST_API_KEY}
    admin_headers = {"X-API-Key": TEST_ADMIN_KEY}

    # 1. GET /api/routing/policy
    resp = client.get("/api/routing/policy", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "routing_mode" in data
    assert "daily_budget_usd" in data

    # 2. PUT /api/routing/policy (Requires Admin)
    # Non-admin forbidden
    resp_forbidden = client.put(
        "/api/routing/policy",
        json={"routing_mode": "local_only"},
        headers=headers,
    )
    assert resp_forbidden.status_code in (401, 403)

    # Admin update allowed
    resp_admin = client.put(
        "/api/routing/policy",
        json={"routing_mode": "local_only", "daily_budget_usd": 2.50},
        headers=admin_headers,
    )
    assert resp_admin.status_code == 200
    assert resp_admin.json()["routing_mode"] == "local_only"
    assert resp_admin.json()["daily_budget_usd"] == 2.50

    # 3. POST /api/routing/gatekeeper/inspect (Dry-run inspection)
    inspect_body = {
        "text": "My email is test@company.org and key is sk-12345678901234567890abcdef",
        "dry_run": True,
    }
    resp_inspect = client.post(
        "/api/routing/gatekeeper/inspect",
        json=inspect_body,
        headers=headers,
    )
    assert resp_inspect.status_code == 200
    inspect_data = resp_inspect.json()
    assert inspect_data["sensitivity_level"] == "critical"
    assert inspect_data["cloud_egress_allowed"] is False
    assert len(inspect_data["entities"]) >= 2

    # 4. GET /api/routing/metrics
    resp_metrics = client.get("/api/routing/metrics", headers=headers)
    assert resp_metrics.status_code == 200
    metrics_data = resp_metrics.json()
    assert "date" in metrics_data
    assert "total_tokens_in" in metrics_data
    assert "total_cost_usd" in metrics_data

    # Restore default routing mode
    client.put(
        "/api/routing/policy",
        json={"routing_mode": "hybrid_cascade", "daily_budget_usd": 1.00},
        headers=admin_headers,
    )

