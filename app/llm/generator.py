# from ollama import chat
import os
from dotenv import load_dotenv
from openai import OpenAI


load_dotenv()

client=OpenAI(
    api_key=os.getenv("OPENROUTER_API_KEY"),
    base_url="https://openrouter.ai/api/v1",
)

def generate_response(prompt):
    response=client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[
            {
                "role":"user",
                "content":prompt
            }
        ]
    )
    # return response.message.content
    # print(response)
    return response.choices[0].message.content