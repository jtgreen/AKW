#!/usr/bin/env python3
import os
from pathlib import Path
import yaml
from typing import List
from fastapi import FastAPI
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

SYSTEM_PROMPT = """You are the Battle Field Shock and Organ Support (BSOS) Wiki assistant.

All knowledge comes from the attached vector store, which contains BOTH the Markdown summaries
and the extracted full-text of the underlying PDFs. Favor PDF evidence when it is available, but
use the Markdown summaries to orient and cross-check claims.

For every response:
1. Provide a concise, technically rigorous answer that synthesizes across the retrieved sources.
2. Include inline citations that reference BOTH the wiki page and the PDF link (e.g., "[1]") for every major claim.
3. End with a "Sources" section where each bullet follows this format:
   [Title (Wiki)](https://bsos.wiki/...) • [PDF](https://bsos.wiki/uploads/...)
4. If the store lacks the answer, state that clearly instead of speculating.

Never use knowledge outside the provided documents. Cite precisely and prefer the richest evidence."""

app = FastAPI()

class AskRequest(BaseModel):
    query: str

class Source(BaseModel):
    # You can extend this later (title, url, etc.)
    snippet: str | None = None

class AskResponse(BaseModel):
    answer: str
    sources: List[Source] = []


@app.post("/ask", response_model=AskResponse)
async def ask(req: AskRequest):
    # Call Responses API with file_search
    resp = client.responses.create(
        model="gpt-5.1",
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

    # For now, we don't parse sources from tool output; we just return answer.
    # Later, we can inspect tool calls for citations and include them here.
    return AskResponse(answer=answer_text, sources=[])
