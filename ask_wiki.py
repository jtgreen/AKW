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

def ask_wiki(query: str):
    """
    Ask a question against the bsos-wiki vector store using File Search.
    Uses the new Responses API-style pattern.
    """
    print(f"Query: {query}\n")

    # NOTE: signatures may vary slightly by openai version,
    # but conceptually this is:
    #  - model: LLM
    #  - input: your query
    #  - tools: file_search
    #  - file_search: vector store(s) to search
    resp = client.responses.create(
        model="gpt-4.1-mini",  # or gpt-4.1 / gpt-5.1 depending on access
        input=[{"role": "user", "content": query}],
        tools=[{"type": "file_search"}],
        tool_resources={
            "file_search": {"vector_store_ids": [VECTOR_STORE_ID]}
        },
    )

    # Print the answer text
    outputs = resp.output  # depending on version, this may be resp.output[0].content...
    print("Raw response object:\n", resp, "\n")

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python ask_wiki.py 'Your question here'")
        raise SystemExit(1)
    q = " ".join(sys.argv[1:])
    ask_wiki(q)
