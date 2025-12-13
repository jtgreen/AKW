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
OPENAI_CFG = CONFIG.get("openai", {}) or {}
VECTOR_STORE_ID = OPENAI_CFG.get("vector_store_id", "vs_TBD")
ASSISTANT_NAME = OPENAI_CFG.get("assistant_name") or "Wiki Assistant"
WIKI_BASE_URL = str(CONFIG.get("wiki_base_url") or "https://example.com").rstrip("/")

if VECTOR_STORE_ID == "vs_TBD":
    raise SystemExit("Set openai.vector_store_id in config.yaml first.")

assistant = client.assistants.create(
    name=ASSISTANT_NAME,
    instructions=(
        "You are the wiki research assistant.\n"
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
