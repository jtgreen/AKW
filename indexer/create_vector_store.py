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

WIKI_NAME = (os.getenv("WIKI_NAME") or "my-wiki").strip()

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.yaml"
CONFIG = yaml.safe_load(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}
OPENAI_CFG = (CONFIG.get("openai") or {}) if isinstance(CONFIG, dict) else {}
store_name = (OPENAI_CFG.get("vector_store_name") or "").strip() or f"{WIKI_NAME}-store"

resp = client.vector_stores.create(name=store_name)

print("VECTOR STORE CREATED:")
print(f"Name: {store_name}")
print("ID: ", resp.id)
