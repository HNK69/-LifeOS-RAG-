"""
engine.py

Grounded reasoning engine for LifeOS.
Separates deterministic evidence gathering from LLM synthesis,
verifying facts against evidence and producing structured reasoning output.
"""
from __future__ import annotations

import re
from typing import Any

from llm.generator import generate_response

from .evidence import build_evidence
from .models import ReasoningResult


def build_reasoning_prompt(query: str, evidence: dict[str, Any]) -> str:
    return f"""
You are the reasoning layer of LifeOS.

Answer the user's question using ONLY the evidence supplied below.

Rules:
- Do not invent facts.
- Do not use outside knowledge.
- Distinguish evidence from inference.
- If evidence sources disagree, acknowledge the conflict.
- If evidence is insufficient, say so explicitly.
- Do not treat filenames or metadata as factual evidence unless relevant.
- Prefer precise answers over speculation.
- Treat temporal metadata as evidence.
- Prefer context that is valid at the current query time.
- Distinguish current context from historical context.
- Do not treat expired context as current.
- If temporal evidence conflicts, acknowledge the conflict.
- Never infer a time relationship that is not supported by the evidence.
- Synthesize information across all evidence types when multiple types are present.
- Cross-check personal context against documents and structured data.
- Cross-check relationships against supporting document or multimodal evidence when available.
- Prefer evidence that is directly relevant to the user's question.
- Do not assume that the presence of multiple sources makes a claim true.
- If evidence sources support the same conclusion, explain the conclusion clearly.
- If evidence sources conflict, explicitly identify the conflict instead of choosing arbitrarily.
- Use the confidence score as a signal of evidence quality, not as a fact.

USER QUERY:
{query}

EVIDENCE:
{evidence}

ANSWER:
"""


def _extract_evidence_used(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    """Deterministically assemble evidence references directly from the evidence package."""
    used: list[dict[str, Any]] = []

    for s in evidence.get("sources", []):
        if isinstance(s, dict) and s.get("content"):
            used.append({
                "type": "document",
                "source": s.get("source"),
                "file_path": s.get("file_path"),
                "content": str(s.get("content", ""))[:200],
            })

    for r in evidence.get("relationships", []):
        if isinstance(r, dict):
            src = (
                r.get("source_name")
                or (r.get("source", {}).get("canonical_name") if isinstance(r.get("source"), dict) else None)
                or (r.get("source", {}).get("name") if isinstance(r.get("source"), dict) else str(r.get("source", "")))
            )
            tgt = (
                r.get("target_name")
                or (r.get("target", {}).get("canonical_name") if isinstance(r.get("target"), dict) else None)
                or (r.get("target", {}).get("name") if isinstance(r.get("target"), dict) else str(r.get("target", "")))
            )
            used.append({
                "type": "relationship",
                "source": src,
                "target": tgt,
                "relationship_type": r.get("relationship_type") or r.get("type"),
                "confidence": r.get("confidence"),
            })

    for t in evidence.get("temporal_context", []):
        if isinstance(t, dict):
            used.append({
                "type": "context",
                "key": t.get("key"),
                "value": t.get("value"),
                "context_type": t.get("context_type"),
            })

    for sd in evidence.get("structured_data", []):
        if isinstance(sd, dict):
            dataset_name = (
                sd.get("dataset", {}).get("filename")
                if isinstance(sd.get("dataset"), dict)
                else str(sd.get("dataset", ""))
            )
            used.append({
                "type": "structured",
                "dataset": dataset_name,
                "result": sd.get("result"),
            })

    for mm in evidence.get("multimodal", []):
        if isinstance(mm, dict):
            used.append({
                "type": "multimodal",
                "source": mm.get("source") or mm.get("file_path"),
                "caption": mm.get("caption"),
            })

    return used


def _build_grounded_facts(evidence: dict[str, Any]) -> list[str]:
    """Deterministically derive grounded factual statements directly from evidence items."""
    facts: list[str] = []

    for r in evidence.get("relationships", []):
        if isinstance(r, dict):
            src = (
                r.get("source_name")
                or (r.get("source", {}).get("canonical_name") if isinstance(r.get("source"), dict) else None)
                or (r.get("source", {}).get("name") if isinstance(r.get("source"), dict) else str(r.get("source", "")))
            )
            tgt = (
                r.get("target_name")
                or (r.get("target", {}).get("canonical_name") if isinstance(r.get("target"), dict) else None)
                or (r.get("target", {}).get("name") if isinstance(r.get("target"), dict) else str(r.get("target", "")))
            )
            rel = r.get("relationship_type") or r.get("type")
            if src and tgt and rel:
                facts.append(f"{src} {rel} {tgt}.")

    for t in evidence.get("temporal_context", []):
        if isinstance(t, dict) and t.get("key") and t.get("value") is not None:
            facts.append(f"{t['key']} is {t['value']}.")

    for s in evidence.get("sources", []):
        if isinstance(s, dict) and s.get("content"):
            content_snippet = str(s["content"]).strip().replace("\n", " ")
            if len(content_snippet) > 100:
                content_snippet = content_snippet[:97] + "..."
            facts.append(f"From {s.get('source', 'document')}: {content_snippet}")

    for sd in evidence.get("structured_data", []):
        if isinstance(sd, dict) and sd.get("result") is not None:
            facts.append(f"Structured result: {sd.get('result')}.")

    return facts


def _parse_llm_response(
    raw_response: str,
    evidence: dict[str, Any],
) -> tuple[str, list[str], list[str]]:
    """
    Parse LLM response into answer, facts, and inferences.
    Validates that claims classified as facts have direct lexical grounding in evidence.
    """
    answer_text = raw_response.strip()
    candidate_facts: list[str] = []
    candidate_inferences: list[str] = []

    # Check for structured section headers
    if "ANSWER:" in raw_response:
        parts = re.split(r"(?:ANSWER:|FACTS:|INFERENCES:)", raw_response)
        if len(parts) >= 2:
            answer_text = parts[1].strip()
        if len(parts) >= 3 and "FACTS:" in raw_response:
            candidate_facts = [
                line.strip(" -*")
                for line in parts[2].splitlines()
                if line.strip(" -*")
            ]
        if len(parts) >= 4 and "INFERENCES:" in raw_response:
            candidate_inferences = [
                line.strip(" -*")
                for line in parts[3].splitlines()
                if line.strip(" -*")
            ]

    # Combine deterministic evidence facts with LLM-proposed facts
    grounded_evidence_facts = _build_grounded_facts(evidence)

    # Validate candidate facts against evidence tokens
    evidence_corpus = str(evidence).lower()
    verified_facts: list[str] = list(grounded_evidence_facts)

    for fact in candidate_facts:
        words = [w.lower() for w in fact.split() if len(w) > 3]
        match_count = sum(1 for w in words if w in evidence_corpus)
        if words and (match_count / len(words)) >= 0.75:
            if fact not in verified_facts:
                verified_facts.append(fact)
        else:
            # Demote ungrounded statements to inference
            if fact not in candidate_inferences:
                candidate_inferences.append(fact)

    # If no inferences exist and answer differs from simple fact restatement, record answer inference
    if not candidate_inferences and answer_text:
        # Check if answer contains terms not present in verified facts
        fact_tokens = {w.lower() for f in verified_facts for w in f.split()}
        ans_tokens = {w.lower() for w in answer_text.split() if len(w) > 3}
        if ans_tokens - fact_tokens:
            candidate_inferences.append("Answer synthesized across available evidence.")

    return answer_text, verified_facts, candidate_inferences


def reason(query: str, result: Any) -> ReasoningResult:
    """
    Generate a grounded, structured answer from a QueryResult or evidence dict.

    Returns ReasoningResult (subclass of str) containing:
    - answer (str representation)
    - confidence (calibrated float)
    - evidence_used (list of deterministic references)
    - facts (direct factual assertions supported by evidence)
    - inferences (derived conclusions)
    - conflicts_detected (deterministic conflict records)
    """
    if isinstance(result, dict):
        evidence = dict(result)
        if "confidence" not in evidence:
            from .confidence import calculate_calibrated_confidence
            from .conflicts import detect_all_conflicts

            if "conflicts" not in evidence:
                evidence["conflicts"] = detect_all_conflicts(evidence)
            evidence["confidence"] = calculate_calibrated_confidence(evidence)
    else:
        evidence = build_evidence(result)

    if not evidence or not any(
        evidence.get(k)
        for k in (
            "sources",
            "structured_data",
            "relationships",
            "personal_context",
            "temporal_context",
            "multimodal",
        )
    ):
        return ReasoningResult(
            "I don't have enough information to answer that.",
            confidence=0.0,
            evidence_used=[],
            facts=[],
            inferences=[],
            conflicts_detected=[],
        )

    response = generate_response(
        build_reasoning_prompt(query, evidence)
    )

    if not response or not response.strip():
        return ReasoningResult(
            "I don't have enough information to answer that.",
            confidence=0.0,
            evidence_used=_extract_evidence_used(evidence),
            facts=[],
            inferences=[],
            conflicts_detected=evidence.get("conflicts", []),
        )

    answer, facts, inferences = _parse_llm_response(response, evidence)

    return ReasoningResult(
        answer=answer,
        confidence=evidence.get("confidence", 0.0),
        evidence_used=_extract_evidence_used(evidence),
        facts=facts,
        inferences=inferences,
        conflicts_detected=evidence.get("conflicts", []),
    )


def reason_structured(query: str, result: Any) -> ReasoningResult:
    """Explicit alias for structured reasoning output."""
    return reason(query, result)
