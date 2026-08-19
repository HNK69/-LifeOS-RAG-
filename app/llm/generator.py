import json
import os

from dotenv import load_dotenv
from openai import OpenAI
import base64
import mimetypes
from pathlib import Path


VISION_MODEL = "google/gemma-4-31b-it"


load_dotenv()


client = OpenAI(
    api_key=os.getenv("OPENROUTER_API_KEY"),
    base_url="https://openrouter.ai/api/v1",
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

    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(
            f"Image not found: {path}"
        )

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

    image_data = base64.b64encode(
        path.read_bytes()
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