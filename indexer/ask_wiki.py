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
CONFIG_PATH = ROOT / "config.yaml"
if not CONFIG_PATH.exists():
    raise SystemExit(
        "Missing indexer config.yaml. Run setup or copy examples/indexer/config.yaml.example "
        "to indexer/config.yaml."
    )
CONFIG = yaml.safe_load(CONFIG_PATH.read_text()) or {}
OPENAI_CFG = (CONFIG.get("openai") or {}) if isinstance(CONFIG, dict) else {}
VECTOR_STORE_ID = (os.getenv("OPENAI_VECTOR_STORE_ID") or OPENAI_CFG.get("vector_store_id") or "").strip()
if not VECTOR_STORE_ID or VECTOR_STORE_ID == "vs_TBD":
    raise SystemExit("Vector store ID missing; set openai.vector_store_id in config.yaml.")

WIKI_NAME = (os.getenv("WIKI_NAME") or (CONFIG.get("wiki_name") if isinstance(CONFIG, dict) else None) or "my-wiki").strip()
WIKI_BASE_URL = (os.getenv("WIKI_BASE_URL") or (CONFIG.get("wiki_base_url") if isinstance(CONFIG, dict) else None) or "https://example.com").rstrip("/")

SYSTEM_PROMPT = f"""You are the research assistant for the \"{WIKI_NAME}\" wiki.

You answer questions using ONLY the attached wiki documents (markdown pages from {WIKI_BASE_URL}).
When responding:
- Synthesize concisely but with enough technical detail for a physician / scientist.
- If you reference a specific page, quote or paraphrase a sentence or two and include the page URL if it appears in the text.
- If you are unsure or the answer is not clearly present, say so explicitly.
- Do NOT make up answers or use any information not contained in the provided documents.
- Use proper medical / scientific terminology.
- Be concise and to the point.
- Focus on the most relevant information to answer the question.
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
