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
VECTOR_STORE_ID = (os.getenv("OPENAI_VECTOR_STORE_ID") or OPENAI_CFG.get("vector_store_id") or "vs_TBD").strip()
ASSISTANT_NAME = (OPENAI_CFG.get("assistant_name") or "").strip() or f"{WIKI_NAME}-assistant"

domain_from_env = (os.getenv("DOMAIN") or "").strip()
WIKI_BASE_URL = str(
    os.getenv("WIKI_BASE_URL")
    or (CONFIG.get("wiki_base_url") if isinstance(CONFIG, dict) else None)
    or (f"https://{domain_from_env}" if domain_from_env else "https://example.com")
).rstrip("/")

if VECTOR_STORE_ID == "vs_TBD":
    raise SystemExit("Set openai.vector_store_id in config.yaml first.")

assistant = client.assistants.create(
    name=ASSISTANT_NAME,
    instructions=(
        f"You are the research assistant for the \"{WIKI_NAME}\" wiki.\n"
        "You always ground answers in the retrieved documents (Markdown summaries + full PDF text).\n"
        "Reason over both sources, but favor the richer PDF text when resolving facts.\n"
        "Every response must:\n"
        "- Synthesize multiple sources when possible.\n"
        "- Include inline citations referencing BOTH the wiki page and the PDF link for each claim.\n"
        f"- End with a \"Sources\" section listing items as "
        f"[Title (Wiki)]({WIKI_BASE_URL}/...) • [PDF]({WIKI_BASE_URL}/pdfs/...).\n"
        "- If the vector store does not contain an answer, say so explicitly.\n"
        "Never rely on outside knowledge.\n"
    ),
    model="gpt-5.1",
    tools=[{"type": "file_search", "vector_store_ids": [VECTOR_STORE_ID]}],
)

print("Assistant created with ID:", assistant.id)
