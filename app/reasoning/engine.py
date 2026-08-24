import json

from llm.generator import generate_response


def build_reasoning_prompt(query, evidence):
    """Build a grounded reasoning prompt from deterministic evidence."""
    return f"""
You are the reasoning layer of LifeOS.

Answer the user's question using ONLY the evidence supplied below.

Rules:
- Do not invent facts.
- Do not use outside knowledge.
- Distinguish evidence from inference.
- If the evidence is insufficient, say so explicitly.
- Prefer precise answers over speculation.
- Do not mention internal implementation details unless asked.

USER QUERY:
{query}

EVIDENCE:
{json.dumps(evidence, ensure_ascii=False, default=str)}

ANSWER:
"""


def reason(query, evidence):
    """Generate a grounded answer from deterministic LifeOS evidence."""
    if not evidence:
        return "I don't have enough information to answer that."

    prompt = build_reasoning_prompt(
        query,
        evidence,
    )

    response = generate_response(prompt)

    if not response:
        return "I don't have enough information to answer that."

    return response.strip()