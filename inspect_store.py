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
    files = client.vector_stores.files.list(vector_store_id=VECTOR_STORE_ID)

    for f in files.data:
        print(f"- file_id={f.id}, name={getattr(f, 'filename', 'n/a')}")

        # If metadata is exposed, it may be on the file or via batches;
        # this is somewhat API-version-dependent.
        try:
            vf = client.vector_stores.files.retrieve(
                vector_store_id=VECTOR_STORE_ID,
                file_id=f.id,
            )
            print("   metadata:", getattr(vf, "metadata", None))
        except Exception as e:
            print("   (could not fetch metadata:", e, ")")

if __name__ == "__main__":
    inspect_store()