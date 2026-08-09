import json
import os

from dotenv import load_dotenv
from openai import OpenAI


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