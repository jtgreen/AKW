# Wiki Stack Monorepo (Wiki.js + Ask + Ingestion)

This repo is a template monorepo for deploying a Wiki.js instance plus an “Ask” (RAG) API/UI backed by an OpenAI vector store, and for running a laptop-side PDF → Markdown ingestion pipeline.

Repo layout:

```
.
├── stack/                 # docker-compose + Makefile + example Caddyfile
├── indexer/               # Ask API, vector-store indexer, and Ask UI assets
├── scripts/               # ops helpers (server bootstrap)
└── README_updated.md      # this file
```

---

## 1) What gets deployed where

On the server/droplet (created by `scripts/setup_wiki_interactive.py`):

- Wiki stack repo clone: `/opt/<repo-dir>` (contains `stack/` + `indexer/`)
- Wiki.js persistent data (bind mounted): `/opt/<wiki-name>-data`
  - Wiki content git repo (Wiki.js Git Storage): `/opt/<wiki-name>-data/repo`
  - Wiki uploads (served at `/uploads/*`): `/opt/<wiki-name>-data/uploads`
  - Ask UI (served at `/ask/*`): `/opt/<wiki-name>-data/ask`
- Ingest-managed PDFs (served at `/pdfs/*`): `/opt/<wiki-name>-pdfs`

Public URLs (via Caddy):

- Wiki.js: `https://<domain>/`
- Ask UI: `https://<domain>/ask`
- Ask API: `https://<domain>/ask/api/ask`
- Wiki.js uploads: `https://<domain>/uploads/...`
- Ingest-managed PDFs: `https://<domain>/pdfs/...`

---

## 2) Server bootstrap (fresh Ubuntu droplet)

Prereqs:

- Ubuntu 22.04/24.04 droplet
- DNS `A` record for `<domain>` pointing at the droplet
- You can SSH as root (or a sudo user)

Run (as root):

```bash
sudo -i
python3 scripts/setup_wiki_interactive.py
```

The script will:

- Install Docker, docker-compose, Caddy, UFW
- Clone/pull this monorepo into `/opt/<repo-dir>`
- Create `/opt/<wiki-name>-data` and `/opt/<wiki-name>-pdfs`
- Write `stack/.env` (container names, data dir, OpenAI key, DB password, base URL). If you leave the Postgres password blank, it auto-generates one.
- Update `indexer/config.yaml` with the wiki base URL + paths
- Write `/etc/caddy/Caddyfile` and start the stack

After it finishes:

1. Visit `https://<domain>` and complete the initial Wiki.js setup (admin user, etc.).
2. In Wiki.js → Storage, enable **Git** storage and point it at:
   - Container path: `/wiki/data/repo`
   - Host path: `/opt/<wiki-name>-data/repo`
3. Confirm the extra routes work:
   - `https://<domain>/ask`
   - `https://<domain>/pdfs/` (directory listing may be off; try a known file)

---

## 3) Stack config (docker-compose + env)

The stack is defined in `stack/docker-compose.yml` and expects a `stack/.env` file.

Start/rebuild:

```bash
cd /opt/<repo-dir>/stack
docker-compose up -d --build
```

Or use Make targets:

```bash
cd /opt/<repo-dir>/stack
make deploy DOMAIN=<domain>
```

Notes:

- `stack/.env.example` is the committed template; copy it to `stack/.env`.
- `stack/.env` is ignored by git (don’t commit secrets).

---

## 4) Ingest-managed PDFs (`/pdfs/*`)

Wiki.js may purge unknown files under `/uploads`. To keep PDFs stable and aligned with generated Markdown summaries, ingestion targets a dedicated directory per wiki:

- Host directory: `/opt/<wiki-name>-pdfs`
- Served at: `https://<domain>/pdfs/...`

When rebuilding embeddings with `indexer/indexer_stub.py`, set `pdf_assets.local_dir` in `indexer/config.yaml` to the same directory so the indexer can also ingest the uploaded extracted-PDF `.txt` companions.

---

## 5) Vector store + indexing (server-side)

`indexer/config.yaml` controls:

- `wiki_repo_root`: where the wiki content repo lives (usually `/opt/<wiki-name>-data/repo`)
- `wiki_base_url`: your canonical wiki base URL (usually `https://<domain>`)
- `openai.vector_store_id`: which vector store Ask queries
- `pdf_assets.local_dir`: where ingest uploads place PDFs + extracted text

Typical flow:

1. Create a vector store (example script):
   - `cd /opt/<repo-dir>/indexer`
   - `python3 create_vector_store.py`
   - Copy the returned ID into `indexer/config.yaml` as `openai.vector_store_id`
2. Upload wiki pages + PDF text into the vector store:
   - `python3 indexer_stub.py`

---

## 6) Laptop-side ingestion (PDF → Markdown)

Ingestion scripts live in `indexer/client_ingest/` and are meant to run on your laptop (not on the droplet).

Prereqs:

- Python 3.10+
- `OPENAI_API_KEY` in your environment
- OCR deps (needed when PDFs have no embedded text):

```bash
# macOS (Homebrew)
brew install ghostscript tesseract

# Ubuntu/Debian
sudo apt install ghostscript tesseract-ocr
```

Install Python deps:

```bash
cd indexer/client_ingest
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Single PDF ingest:

```bash
python ingest_paper.py path/to/paper.pdf \
  --wiki-root /absolute/path/to/your/wiki-content-repo \
  --base-dir research/topic/subtopic
```

Batch ingest + remote PDF upload:

```bash
python batch_ingest.py "/path/to/papers" \
  --pdf-upload-target root@<domain>:/opt/<wiki-name>-pdfs \
  --pdf-url-base https://<domain>/pdfs \
  -- \
  --wiki-root /absolute/path/to/your/wiki-content-repo \
  --base-dir research/topic
```

Incremental “auto-filer” ingest:

```bash
python client_ingest/auto_filer/auto_ingest.py \
  --watch-dir "/path/to/incoming/PDFs" \
  --wiki-root /absolute/path/to/your/wiki-content-repo \
  --pdf-upload root@<domain>:/opt/<wiki-name>-pdfs \
  --pdf-url-base https://<domain>/pdfs
```

Notable flags (selected):

- `ingest_paper.py`: `--base-dir`, `--wiki-root`, `--wiki-path-prefix`, `--doc-id`, `--model`, `--max-chars`, `--dry-run`, `--print-json`, `--pdf-upload`, `--pdf-url-base`, `--ignore-dir`, `--log-file`
- `batch_ingest.py`: `--pdf-upload-target`, `--pdf-url-base`, `--rebuild-log`, plus any `ingest_paper.py` flags after `--`
- `auto_ingest.py`: `--deduplicate`, `--wiki-base-url`, `--dry-run`, `--git-commit`

Customization note:

- The ingestion and auto-filer prompts are currently tuned for a specific research domain; for a brand-new wiki topic you’ll want to edit prompt templates in `indexer/client_ingest/ingest_paper.py` (and `indexer/client_ingest/auto_filer/auto_ingest.py`) to match your domain.

---

## 7) Security & ops notes

- Rotate any OpenAI keys that were ever committed anywhere.
- Keep secrets in `stack/.env` (ignored by git). Use `stack/.env.example` as a template.
- The stack assumes Elasticsearch is **not** exposed publicly (no `ports:` mapping).
- If you embed PDFs or other assets in Wiki.js pages, you may need to allow iframes in Wiki.js admin settings depending on your theme/customizations.
