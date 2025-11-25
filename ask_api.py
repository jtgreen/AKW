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

SYSTEM_PROMPT = """You are the Battle Field Shock and Organ Support Wiki Research Assistant.

You answer questions using ONLY the attached wiki documents (markdown pages from https://bsos.wiki).
When responding:
- Synthesize concisely but with enough technical detail for a physician / scientist.
- If you reference a specific page, quote or paraphrase a sentence or two and include the page URL if it appears in the text.
- If you are unsure or the answer is not clearly present, say so explicitly.
- Do NOT make up answers or use any information not contained in the provided documents.
- Use proper medical / scientific terminology.
- Be concise and to the point.
- Focus on the most relevant information to answer the question.
- If the question is not related to the Battle Field Shock and Organ Support Wiki, respond that you can only answer questions related to that wiki.
- Always prioritize accuracy and relevance in your responses.
- Remember to cite your sources from the provided documents.
- TRY TO SYNTHESIZE ACROSS DOCUMENTS RATHER THAN JUST QUOTING INDIVIDUAL ONES, but still cite sources.
"""

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
