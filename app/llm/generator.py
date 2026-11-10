import json
import os

from dotenv import load_dotenv
from openai import OpenAI
import base64
import mimetypes
from pathlib import Path

try:
    from ingestion.guardrails import validate_document_path, check_file_size
except ImportError:
    from app.ingestion.guardrails import validate_document_path, check_file_size

try:
    from config import (
        get_max_file_size_bytes,
        LLM_TIMEOUT_SECONDS,
        LLM_MAX_RETRIES,
        OPENROUTER_BASE_URL,
    )
except ImportError:
    from app.config import (
        get_max_file_size_bytes,
        LLM_TIMEOUT_SECONDS,
        LLM_MAX_RETRIES,
        OPENROUTER_BASE_URL,
    )


VISION_MODEL = "google/gemma-4-31b-it"


load_dotenv()


client = OpenAI(
    api_key=os.getenv("OPENROUTER_API_KEY"),
    base_url=os.getenv("OPENROUTER_BASE_URL", OPENROUTER_BASE_URL),
    timeout=float(LLM_TIMEOUT_SECONDS),
    max_retries=int(LLM_MAX_RETRIES),
)


MODEL = "openai/gpt-oss-120b"


def generate_response(prompt):
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {
                "role": "user",
                "content": prompt,
            }
        ],
    )

    return response.choices[0].message.content


def classify_sources(query, current_context, candidates):
    prompt = f"""
You are a document relevance classifier.

User query:
{query}

Current context:
{json.dumps(current_context, ensure_ascii=False)}

Candidate documents:
{json.dumps(candidates, ensure_ascii=False)}

For each candidate, assign:
- relevance: integer from 0 to 100
- reason: short explanation

Judge ONLY from the document content.
Do not use filename similarity as evidence.

Return ONLY valid JSON:

{{
  "candidates": [
    {{
      "index": 0,
      "relevance": 95,
      "reason": "..."
    }}
  ]
}}

Include every candidate exactly once.
"""

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "user", "content": prompt}
        ],
        temperature=0,
    )

    return response.choices[0].message.content


def analyze_image(file_path, prompt=None):
    """
    Analyze a local image using an OpenRouter free vision model.
    """

    path = validate_document_path(file_path, must_exist=True)

    max_bytes = get_max_file_size_bytes(path)
    check_file_size(path, max_bytes=max_bytes)

    mime_type, _ = mimetypes.guess_type(
        path.name
    )

    if mime_type not in {
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/gif",
    }:
        raise ValueError(
            f"Unsupported image type: {mime_type}"
        )

    with open(path, "rb") as f:
        raw_bytes = f.read(max_bytes + 1)
        if len(raw_bytes) > max_bytes:
            raise ValueError(
                f"Image exceeds maximum allowed size ({max_bytes} bytes)"
            )

    image_data = base64.b64encode(
        raw_bytes
    ).decode("utf-8")

    if prompt is None:
        prompt = """
Analyze this image for LifeOS memory.

Return a concise description containing:
- what is visible
- important objects
- people if clearly identifiable
- text/OCR if readable
- useful context such as location or activity
- distinctive details that could help retrieve this image later

Do not invent details that are not visible.
"""

    response = client.chat.completions.create(
        model=VISION_MODEL,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": prompt,
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": (
                                f"data:{mime_type};base64,"
                                f"{image_data}"
                            )
                        },
                    },
                ],
            }
        ],
        temperature=0,
        max_tokens=1200,
    )

    return response.choices[0].message.content

def analyze_image_metadata(file_path):
    """
    Extract structured, retrieval-oriented visual metadata.

    The vision model determines the entities/context; LifeOS does not
    hardcode object, location, or activity categories.
    """
    prompt = """
Analyze this image for LifeOS memory.

Return ONLY valid JSON with this schema:

{
  "objects": [],
  "locations": [],
  "activities": [],
  "context": "",
  "ocr": "",
  "entities": []
}

Rules:
- objects: visually identifiable objects or items.
- locations: locations or environmental settings only when supported.
- activities: visible activities/actions only when supported.
- context: concise useful contextual description.
- ocr: readable text only; use "" when none is readable.
- entities: distinctive identifiable entities relevant for retrieval.
- Use empty arrays/strings when information is unavailable.
- Never invent details.
"""

    raw = analyze_image(
        file_path,
        prompt=prompt,
    )

    try:
        metadata = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {
            "objects": [],
            "locations": [],
            "activities": [],
            "context": "",
            "ocr": "",
            "entities": [],
        }

    return {
        "objects": metadata.get("objects", []),
        "locations": metadata.get("locations", []),
        "activities": metadata.get("activities", []),
        "context": metadata.get("context", ""),
        "ocr": metadata.get("ocr", ""),
        "entities": metadata.get("entities", []),
    }