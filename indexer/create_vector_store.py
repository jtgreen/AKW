#!/usr/bin/env python3
import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Create the vector store once
resp = client.vector_stores.create(name="bsos-wiki-store")

print("VECTOR STORE CREATED:")
print("Name: bsos-wiki-store")
print("ID: ", resp.id)