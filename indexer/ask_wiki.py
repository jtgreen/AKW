#!/usr/bin/env python3
import os
from pathlib import Path
import yaml
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise SystemExit("OPENAI_API_KEY not set")

client = OpenAI(api_key=OPENAI_API_KEY)

ROOT = Path(__file__).resolve().parent
CONFIG = yaml.safe_load((ROOT / "config.yaml").read_text())
VECTOR_STORE_ID = CONFIG["openai"]["vector_store_id"]

SYSTEM_PROMPT = """You are the Battle Field Shock and Organ Support Wiki Research Assistant.

You answer questions using ONLY the attached wiki documents (markdown pages from https://bsos.wiki).
When responding:
- Synthesize concisely but with enough technical detail for a physician / scientist.
- If you reference a specific page, quote or paraphrase a sentence or two and include the page URL if it appears in the text.
- If you are unsure or the answer is not clearly present, say so explicitly.
- Do NOT make up answers or use any information not contained in the provided documents.
- Use proper medical / scientific terminology.
- Be concise and to the point.
- Focus on the most relevant information to answer the question.
- If the question is not related to the Battle Field Shock and Organ Support Wiki, respond that you can only answer questions related to that wiki.
- Always prioritize accuracy and relevance in your responses.
- Remember to cite your sources from the provided documents.
- TRY TO SYNTHESIZE ACROSS DOCUMENTS RATHER THAN JUST QUOTING INDIVIDUAL ONES, but still cite sources.
"""

def ask_wiki(query: str):
    print(f"Query: {query}\n")

    resp = client.responses.create(
        model="gpt-5.1",
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": query},
        ],
        tools=[
            {
                "type": "file_search",
                "vector_store_ids": [VECTOR_STORE_ID],
            }
        ],
    )

    answer_text = None
    try:
        for item in resp.output:
            content = getattr(item, "content", None)
            if not content:
                continue
            for chunk in content:
                if getattr(chunk, "type", None) == "output_text":
                    answer_text = chunk.text
                    break
            if answer_text:
                break
    except Exception:
        pass

    if not answer_text:
        answer_text = str(resp)

    print("=== Answer ===\n")
    print(answer_text)
    print("\n==============\n")

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python ask_wiki.py 'Your question here'")
        raise SystemExit(1)
    q = " ".join(sys.argv[1:])
    ask_wiki(q)
