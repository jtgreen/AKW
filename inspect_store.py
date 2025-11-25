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

def inspect_store():
    print("Listing files for vector store:", VECTOR_STORE_ID)

    total = 0
    cursor = None

    while True:
        resp = client.vector_stores.files.list(
            vector_store_id=VECTOR_STORE_ID,
            limit=100,
            after=cursor,
        )

        if not resp.data:
            break

        for f in resp.data:
            total += 1
            print(f"- file_id={f.id}, name={getattr(f, 'filename', 'n/a')}")

        # pagination: if there are more, set cursor to last id and continue
        if getattr(resp, "has_more", False):
            cursor = resp.data[-1].id
        else:
            break

    print(f"\nTotal files attached: {total}")

if __name__ == "__main__":
    inspect_store()