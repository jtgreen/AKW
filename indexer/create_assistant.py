#!/usr/bin/env python3
import os
from pathlib import Path

import yaml
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

CONFIG = yaml.safe_load(Path("config.yaml").read_text())
VECTOR_STORE_ID = CONFIG["openai"]["vector_store_id"]

assistant = client.assistants.create(
    name="BSOS-Wiki-Assistant",
    instructions=(
        "You are the Battle Field Shock & Organ Support (BSOS) research assistant.\n"
        "You always ground answers in the retrieved documents (Markdown summaries + full PDF text).\n"
        "Reason over both sources, but favor the richer PDF text when resolving facts.\n"
        "Every response must:\n"
        "- Synthesize multiple sources when possible.\n"
        "- Include inline citations referencing BOTH the wiki page and the PDF link for each claim.\n"
        "- End with a \"Sources\" section listing items as "
        "[Title (Wiki)](https://...) • [PDF](https://...).\n"
        "- If the vector store does not contain an answer, say so explicitly.\n"
        "Never rely on outside knowledge.\n"
    ),
    model="gpt-5.1",
    tools=[{"type": "file_search", "vector_store_ids": [VECTOR_STORE_ID]}],
)

print("Assistant created with ID:", assistant.id)
