# Indexer & Ask API

This directory is self-contained:

- `indexer_stub.py`, `config.yaml`, `create_vector_store.py`, `create_assistant.py`, `inspect_store.py`
- FastAPI entrypoints `ask_api.py` and CLI helper `ask_wiki.py`
- Docker assets (`Dockerfile`, `requirements.txt`)
- Static Ask UI under `ask/`
- Laptop-only ingestion scripts in `client_ingest/`

Run everything from here so relative paths resolve and state files (`.indexer_state.json`, `file_index.yaml`) stay beside the scripts.
