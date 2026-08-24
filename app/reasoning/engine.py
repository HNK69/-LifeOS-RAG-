from llm.generator import generate_response

from .evidence import build_evidence


def build_reasoning_prompt(query, evidence):
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

USER QUERY:
{query}

EVIDENCE:
{evidence}

ANSWER:
"""


def reason(query, result):
    """Generate a grounded answer from a QueryResult or evidence dict."""

    if isinstance(result, dict):
        evidence = result
    else:
        evidence = build_evidence(result)

    if not evidence:
        return "I don't have enough information to answer that."

    response = generate_response(
        build_reasoning_prompt(
            query,
            evidence,
        )
    )

    if not response:
        return "I don't have enough information to answer that."

    return response.strip()