"""
gatekeeper.py

Request-scoped surrogate vaulting and deterministic privacy gatekeeper
for Milestone 7.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

from app.routing.models import PIICategory, PIIEntity, SensitivityLevel

logger = logging.getLogger("lifeos.routing.gatekeeper")


class SurrogateVault:
    """
    Strictly request-scoped, in-memory de-identification mapping.
    
    Guarantees:
    - Never persisted to disk or database.
    - Zero exposure of raw PII in repr/str/logs.
    - Reversible restoration of placeholders to original entities.
    - Explicit clearing via clear() or context manager.
    """

    def __init__(self, vault_id: Optional[str] = None):
        self.vault_id = vault_id or f"vlt_{uuid.uuid4().hex[:8]}"
        self._forward: Dict[str, str] = {}  # placeholder -> original text
        self._reverse: Dict[str, str] = {}  # original text -> placeholder
        self._category_counts: Dict[str, int] = defaultdict(int)

    def register(self, original_text: str, category: PIICategory | str) -> str:
        """Register an original sensitive string and return its unique surrogate placeholder."""
        cat_str = category.value if isinstance(category, PIICategory) else str(category)
        if original_text in self._reverse:
            return self._reverse[original_text]

        self._category_counts[cat_str] += 1
        idx = self._category_counts[cat_str]
        placeholder = f"<{cat_str.upper()}_{idx}>"

        self._forward[placeholder] = original_text
        self._reverse[original_text] = placeholder
        return placeholder

    def deidentify(self, text: str, entities: List[PIIEntity]) -> str:
        """
        Replace all detected PII entity spans with registered surrogate placeholders.
        Spans are replaced in reverse order of position to preserve indices.
        """
        if not entities or not text:
            return text

        # Sort entities by start index descending
        sorted_entities = sorted(entities, key=lambda e: e.start, reverse=True)
        result = text
        for ent in sorted_entities:
            placeholder = self.register(ent.text, ent.category)
            ent.placeholder = placeholder
            result = result[:ent.start] + placeholder + result[ent.end:]

        return result

    def reidentify(self, text: str) -> str:
        """Replace all registered surrogate placeholders in text with their original raw values."""
        if not text or not self._forward:
            return text

        result = text
        # Replace placeholders by longest placeholder first to avoid partial overlap
        for placeholder, original in sorted(self._forward.items(), key=lambda kv: len(kv[0]), reverse=True):
            result = result.replace(placeholder, original)
        return result

    def clear(self) -> None:
        """Explicitly wipe all surrogate mappings."""
        self._forward.clear()
        self._reverse.clear()
        self._category_counts.clear()

    def __enter__(self) -> SurrogateVault:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.clear()

    def __repr__(self) -> str:
        # Strictly mask mapping content to prevent accidental log leaks
        return f"<SurrogateVault vault_id='{self.vault_id}' entries={len(self._forward)}>"

    def __str__(self) -> str:
        return self.__repr__()


def _luhn_checksum(card_number_str: str) -> bool:
    """Validate a numeric string with the Luhn checksum algorithm."""
    digits = [int(c) for c in card_number_str if c.isdigit()]
    if len(digits) < 13 or len(digits) > 19:
        return False
    checksum = 0
    parity = len(digits) % 2
    for i, digit in enumerate(digits):
        if i % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


class PrivacyGatekeeper:
    """
    Deterministic rule-based privacy inspection engine.
    Detects emails, phone numbers, IPs, payment cards, SSNs, credentials,
    and sensitive identifiers.
    """

    # Precompiled regex patterns
    EMAIL_PATTERN = re.compile(
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
    )

    PHONE_PATTERNS = [
        re.compile(r"(?:\+?1[-.\s]?)?\(?[2-9]\d{2}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),
        re.compile(r"\+(?:[0-9][-.\s]?){7,14}[0-9]\b"),
    ]

    IPV4_PATTERN = re.compile(
        r"\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b"
    )

    IPV6_PATTERN = re.compile(
        r"\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b"
    )

    SSN_PATTERN = re.compile(
        r"\b(?!000|666|9\d{2})\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b"
    )

    CREDENTIAL_PATTERNS = [
        re.compile(r"\b(?:sk|pk)_(?:live|test)_[a-zA-Z0-9]{20,}\b"),
        re.compile(r"\bsk-[a-zA-Z0-9_-]{20,}\b"),
        re.compile(r"\bghp_[a-zA-Z0-9]{36}\b"),
        re.compile(r"\bxox[baprs]-[a-zA-Z0-9-]{10,}\b"),
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        re.compile(r"(?i)\b(?:api[_-]?key|secret[_-]?key|auth[_-]?token|bearer)\s*[:=]\s*['\"]?([a-zA-Z0-9_\-.~+]{16,})['\"]?"),
    ]

    CARD_CANDIDATE_PATTERN = re.compile(
        r"\b(?:\d[ -]*?){13,19}\b"
    )

    CATEGORY_PRIORITY = {
        PIICategory.CREDENTIALS: 100,
        PIICategory.FINANCIAL: 90,
        PIICategory.GOV_ID: 80,
        PIICategory.EMAIL: 70,
        PIICategory.PHONE: 60,
        PIICategory.IP_ADDRESS: 50,
        PIICategory.SENSITIVE_NAME: 40,
    }

    def __init__(self, sensitive_names: Optional[List[str]] = None):
        self.sensitive_names: Set[str] = set(sensitive_names or [])

    def add_sensitive_names(self, names: List[str]) -> None:
        self.sensitive_names.update(names)

    def detect_pii(self, text: str) -> List[PIIEntity]:
        """Inspect text and detect all PII entities with non-overlapping boundaries."""
        if not text:
            return []

        raw_entities: List[PIIEntity] = []

        # 1. Credentials (CRITICAL)
        for pattern in self.CREDENTIAL_PATTERNS:
            for match in pattern.finditer(text):
                # If pattern has a capturing group (like key=value), use captured value start/end
                if match.groups() and match.group(1):
                    start = match.start(1)
                    end = match.end(1)
                    matched_text = match.group(1)
                else:
                    start = match.start()
                    end = match.end()
                    matched_text = match.group(0)
                raw_entities.append(
                    PIIEntity(
                        category=PIICategory.CREDENTIALS,
                        text=matched_text,
                        start=start,
                        end=end,
                        confidence=1.0,
                    )
                )

        # 2. Government IDs (SSN)
        for match in self.SSN_PATTERN.finditer(text):
            raw_entities.append(
                PIIEntity(
                    category=PIICategory.GOV_ID,
                    text=match.group(0),
                    start=match.start(),
                    end=match.end(),
                    confidence=1.0,
                )
            )

        # 3. Financial / Credit Cards
        for match in self.CARD_CANDIDATE_PATTERN.finditer(text):
            cand_str = match.group(0)
            cleaned_digits = "".join(c for c in cand_str if c.isdigit())
            if _luhn_checksum(cleaned_digits):
                raw_entities.append(
                    PIIEntity(
                        category=PIICategory.FINANCIAL,
                        text=cand_str,
                        start=match.start(),
                        end=match.end(),
                        confidence=0.95,
                    )
                )

        # 4. Emails
        for match in self.EMAIL_PATTERN.finditer(text):
            raw_entities.append(
                PIIEntity(
                    category=PIICategory.EMAIL,
                    text=match.group(0),
                    start=match.start(),
                    end=match.end(),
                    confidence=1.0,
                )
            )

        # 5. Phones
        for pattern in self.PHONE_PATTERNS:
            for match in pattern.finditer(text):
                raw_entities.append(
                    PIIEntity(
                        category=PIICategory.PHONE,
                        text=match.group(0),
                        start=match.start(),
                        end=match.end(),
                        confidence=0.9,
                    )
                )

        # 6. IP Addresses
        for match in self.IPV4_PATTERN.finditer(text):
            raw_entities.append(
                PIIEntity(
                    category=PIICategory.IP_ADDRESS,
                    text=match.group(0),
                    start=match.start(),
                    end=match.end(),
                    confidence=0.95,
                )
            )
        for match in self.IPV6_PATTERN.finditer(text):
            raw_entities.append(
                PIIEntity(
                    category=PIICategory.IP_ADDRESS,
                    text=match.group(0),
                    start=match.start(),
                    end=match.end(),
                    confidence=0.95,
                )
            )

        # 7. Sensitive Names
        for name in self.sensitive_names:
            if not name.strip():
                continue
            pattern = re.compile(rf"\b{re.escape(name)}\b", re.IGNORECASE)
            for match in pattern.finditer(text):
                raw_entities.append(
                    PIIEntity(
                        category=PIICategory.SENSITIVE_NAME,
                        text=match.group(0),
                        start=match.start(),
                        end=match.end(),
                        confidence=0.85,
                    )
                )

        # Deduplicate & resolve overlapping spans:
        # Sort by: start ASC, priority DESC, length DESC
        sorted_candidates = sorted(
            raw_entities,
            key=lambda e: (
                e.start,
                -self.CATEGORY_PRIORITY.get(e.category, 0),
                -(e.end - e.start),
            ),
        )

        resolved_entities: List[PIIEntity] = []
        last_end = -1
        for candidate in sorted_candidates:
            if candidate.start >= last_end:
                resolved_entities.append(candidate)
                last_end = candidate.end
            else:
                # Overlap: if candidate has higher priority than existing overlapping entity, swap if possible
                pass

        return resolved_entities

    def assess_sensitivity(self, entities: List[PIIEntity]) -> SensitivityLevel:
        """
        Assess sensitivity level based on detected PII categories.
        - CREDENTIALS -> CRITICAL (Hard block for cloud egress)
        - FINANCIAL, GOV_ID -> HIGH
        - EMAIL, PHONE, IP_ADDRESS, SENSITIVE_NAME -> MODERATE
        - None -> LOW
        """
        if not entities:
            return SensitivityLevel.LOW

        categories = {e.category for e in entities}
        if PIICategory.CREDENTIALS in categories:
            return SensitivityLevel.CRITICAL
        if PIICategory.FINANCIAL in categories or PIICategory.GOV_ID in categories:
            return SensitivityLevel.HIGH
        if any(cat in categories for cat in (PIICategory.EMAIL, PIICategory.PHONE, PIICategory.IP_ADDRESS, PIICategory.SENSITIVE_NAME)):
            return SensitivityLevel.MODERATE
        return SensitivityLevel.LOW
