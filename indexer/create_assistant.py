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
        "You are the BSOS Wiki Assistant.\n"
        "Your knowledge comes from the attached vector store containing wiki Markdown pages.\n"
        "When answering:\n"
        "- Prefer wiki knowledge over speculation.\n"
        "- Cite page URLs when possible.\n"
        "- Synthesize multiple sources.\n"
        "- Focus on hemorrhagic shock, cardiac collapse, physiology, microcirculation.\n"
        "- If you do not know something from the store, say so.\n"
    ),
    model="gpt-5.1",
    tools=[
        {"type": "file_search", "vector_store_ids": [VECTOR_STORE_ID]}
    ]
)

print("Assistant created with ID:", assistant.id)
