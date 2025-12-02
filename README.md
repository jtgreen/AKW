# knowledge-wiki

Monorepo containing everything for the BSOS wiki:

```
knowledge-wiki/
  README.md           # this file (overview)
  stack/              # docker-compose + infra artifacts
  indexer/            # ingestion/indexing/Ask API code
  scripts/            # ops helpers (e.g., setup_wiki_interactive.sh)
```

- See `stack/MAIN_STACK_README.md` for server infrastructure notes.
- `indexer/README.md` covers the ingestion pipeline, Ask API, and the laptop-side `client_ingest/` scripts.
- `scripts/setup_wiki_interactive.sh` bootstraps a droplet using this layout.

## Prerequisites for Local Ingestion

If you plan to run the `client_ingest` scripts on your laptop, install the OCR dependencies before running `batch_ingest.py`:

```bash
# macOS (Homebrew)
brew install ghostscript tesseract

# Ubuntu/Debian
sudo apt install ghostscript tesseract-ocr
```

`ocrmypdf` requires `gs` (Ghostscript) on your PATH; without it, ingest runs will fail when falling back to OCR.
