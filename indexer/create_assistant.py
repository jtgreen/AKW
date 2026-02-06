#!/usr/bin/env python3
import os
from pathlib import Path
import textwrap

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

instructions = textwrap.dedent(
    f"""
    You are the research assistant for the "{WIKI_NAME}" wiki.
    You always ground answers in the retrieved documents (Markdown summaries + full PDF text).
    Reason over both sources, but favor the richer PDF text when resolving facts.

    Retrieved documents:
    - Each retrieved document begins with YAML front-matter like:
      doc_id, kind, title, year, authors, slug, pdf_url, pdf_text_url.

    Every response must:
    - Synthesize multiple sources when possible.
    - Include inline citations referencing BOTH the wiki page and the PDF link for each claim.
    - Never rely on outside knowledge.
    - If the vector store does not contain an answer, say so explicitly and STOP.

    Citations & Sources rules (mandatory):
    1) Every factual claim must be supported by citations to retrieved text.
    2) You MUST cite using the document's doc_id and also include the URLs from front-matter:
       - Wiki URL: {WIKI_BASE_URL}/en/{{slug}}
       - PDF URL: {{pdf_url}}
    3) Inline citations must be compact and human-readable, like:
       (Mullins & Bondarenko 2020; Wiki: /en/<slug>; PDF: <pdf_url>; doc_id=<doc_id>)
    4) End every response with a "Sources" section listing each distinct doc used exactly once in this format:
       - <title> (<year>) — <authors> • [Wiki]({WIKI_BASE_URL}/en/<slug>) • [PDF](<pdf_url>) • doc_id=<doc_id>

    Failure modes:
    - If you cannot find the needed front-matter fields in the retrieved text, say "Missing front-matter for citation formatting"
      and still cite what you have.
    - If the vector store does not contain an answer, say so explicitly and STOP.
    """
).strip()

assistant = client.assistants.create(
    name=ASSISTANT_NAME,
    instructions=instructions,
    model="gpt-5.1",
    tools=[{"type": "file_search", "vector_store_ids": [VECTOR_STORE_ID]}],
)

print("Assistant created with ID:", assistant.id)
