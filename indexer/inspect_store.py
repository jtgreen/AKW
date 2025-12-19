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

def inspect_store():
    print(f"Listing files for vector store ({WIKI_NAME}): {VECTOR_STORE_ID}")

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
