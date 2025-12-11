#!/usr/bin/env python3
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import yaml
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
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
BSOS_BASE_URL = "https://bsos.wiki"

AVAILABLE_MODELS = {
    "gpt-5.1": "GPT-5.1",
    "gpt-5.1-instant": "GPT-5.1 Instant",
    "gpt-5.1-mini": "GPT-5.1 Mini",
    "gpt-4.1": "GPT-4.1",
    "gpt-4.1-mini": "GPT-4.1 Mini",
}
DEFAULT_MODEL = "gpt-5.1"

SYSTEM_PROMPT = """You are the Battle Field Shock and Organ Support (BSOS) Wiki assistant.

All knowledge comes from the attached vector store, which contains BOTH the Markdown summaries
and the extracted full-text of the underlying PDFs. Favor PDF evidence when it is available, but
use the Markdown summaries to orient and cross-check claims.

For every response:
1. Provide a concise, technically rigorous answer that synthesizes across the retrieved sources.
2. Include inline citations that reference BOTH the wiki page and the PDF link (e.g., "[1]") for every major claim.
3. End with a "Sources" section where each bullet looks like:
   [Title (Wiki)](<actual BSOS wiki URL>) • [PDF](<actual BSOS PDF URL or \"N/A\">)
   Only use URLs that start with https://bsos.wiki/. Never output external domains (journal sites, DOIs, etc.).
4. If the store lacks the answer, state that clearly instead of speculating.

Never use knowledge outside the provided documents. Cite precisely and prefer the richest evidence."""

app = FastAPI()

class AskRequest(BaseModel):
    query: str
    model: str | None = None

class Source(BaseModel):
    title: Optional[str] = None
    wiki_url: Optional[str] = None
    pdf_url: Optional[str] = None
    doc_type: Optional[str] = None

class AskResponse(BaseModel):
    answer: str
    sources: List[Source] = []


def resolve_model(requested: str | None) -> str:
    if not requested:
        return DEFAULT_MODEL
    requested = requested.strip()
    if requested not in AVAILABLE_MODELS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported model '{requested}'. Available: {', '.join(AVAILABLE_MODELS.keys())}",
        )
    return requested


def normalize_bsos_url(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    url = value.strip()
    if not url:
        return None
    if url.startswith("http://"):
        url = "https://" + url[len("http://") :]
    if url.startswith(BSOS_BASE_URL):
        return url
    if url.startswith("/"):
        return f"{BSOS_BASE_URL.rstrip('/')}{url}"
    if url.startswith("bsos.wiki"):
        return f"https://{url}"
    if not url.startswith("http"):
        return f"{BSOS_BASE_URL.rstrip('/')}/{url.lstrip('/')}"
    return None


def extract_cited_file_ids(resp_obj: Any) -> Set[str]:
    file_ids: Set[str] = set()
    try:
        payload = resp_obj.model_dump()
    except AttributeError:
        try:
            payload = resp_obj.dict()
        except AttributeError:
            payload = resp_obj

    def pull_from_content(content: Dict[str, Any]):
        annotations = content.get("annotations") or []
        for annotation in annotations:
            file_citation = annotation.get("file_citation")
            if file_citation and file_citation.get("file_id"):
                file_ids.add(file_citation["file_id"])

    for output in payload.get("output", []) or []:
        contents = output.get("content") or []
        for part in contents:
            if isinstance(part, dict):
                pull_from_content(part)
            elif hasattr(part, "model_dump"):
                pull_from_content(part.model_dump())

    usage = payload.get("usage") or {}
    citation_meta = usage.get("citation_metadata") or {}
    for citation in citation_meta.get("citations") or []:
        file_id = citation.get("file_id")
        if file_id:
            file_ids.add(file_id)

    return file_ids


def build_sources(file_ids: Set[str]) -> List[Source]:
    sources: List[Source] = []
    seen_keys: Set[tuple] = set()
    for file_id in file_ids:
        try:
            file_obj = client.files.retrieve(file_id)
        except Exception:
            continue
        metadata = getattr(file_obj, "metadata", None) or {}
        wiki_url = normalize_bsos_url(metadata.get("wiki_url") or metadata.get("primary_url"))
        if not wiki_url and metadata.get("wiki_path"):
            wiki_url = normalize_bsos_url(metadata["wiki_path"])
        pdf_url = normalize_bsos_url(metadata.get("pdf_url"))
        title = metadata.get("title") or metadata.get("wiki_path") or getattr(file_obj, "filename", None)
        doc_type = metadata.get("source_type") or metadata.get("kind")
        key = (wiki_url, pdf_url, title)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        sources.append(
            Source(
                title=title,
                wiki_url=wiki_url,
                pdf_url=pdf_url,
                doc_type=doc_type,
            )
        )
    return sources


@app.post("/ask", response_model=AskResponse)
async def ask(req: AskRequest):
    model_name = resolve_model(req.model)
    # Call Responses API with file_search
    resp = client.responses.create(
        model=model_name,
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": req.query},
        ],
        tools=[
            {
                "type": "file_search",
                "vector_store_ids": [VECTOR_STORE_ID],
            }
        ],
    )

    # Extract answer text, as in ask_wiki.py
    answer_text = None
    try:
        for item in resp.output:
            if hasattr(item, "content"):
                for c in item.content:
                    if getattr(c, "type", None) == "output_text":
                        answer_text = c.text
                        break
            if answer_text:
                break
    except Exception:
        pass

    if not answer_text:
        answer_text = str(resp)

    cited_file_ids = extract_cited_file_ids(resp)
    sources = build_sources(cited_file_ids) if cited_file_ids else []

    return AskResponse(answer=answer_text, sources=sources)
