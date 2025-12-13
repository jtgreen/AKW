#!/usr/bin/env python3
import os
from pathlib import Path

import yaml
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

ROOT = Path(__file__).resolve().parent
CONFIG = yaml.safe_load((ROOT / "config.yaml").read_text())
store_name = (CONFIG.get("openai", {}) or {}).get("vector_store_name") or "wiki-store"

resp = client.vector_stores.create(name=store_name)

print("VECTOR STORE CREATED:")
print(f"Name: {store_name}")
print("ID: ", resp.id)
