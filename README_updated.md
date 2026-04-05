# Wiki Stack Monorepo (Wiki.js + Ask + Ingestion)

This repo is a template monorepo for deploying a Wiki.js instance plus an "Ask" (RAG) API/UI backed by an OpenAI vector store, and for running a laptop-side PDF → Markdown ingestion pipeline.

Repo layout:

```
.
├── examples/              # *.example templates (Makefile/yaml/html/etc)
├── indexer/               # Ask API + vector-store indexer + organizer
│   ├── client_ingest/     # Laptop-side PDF → Markdown pipeline
│   │   ├── auto_filer/    # Incremental watch-dir auto-ingest
│   │   ├── batch_ingest.py
│   │   ├── ingest_paper.py
│   │   ├── checksum_utils.py
│   │   ├── backfill_checksums.py
│   │   └── run_cleanup.py
│   ├── organizer/         # LLM-driven wiki reorganization & tagging
│   │   ├── build_catalog.py
│   │   ├── plan_tags.py
│   │   ├── plan_reorg.py
│   │   ├── apply_reorg.py
│   │   ├── fix_front_matter_paths.py
│   │   └── fix_titles_and_headings.py
│   ├── indexer_stub.py    # Vector store uploader
│   ├── ask_api.py         # FastAPI RAG query server
│   └── create_vector_store.py
├── scripts/               # Server bootstrap (instantiates /opt/<wiki-name>-*)
└── README_updated.md      # this file
```

---

## 1) What gets deployed where

On the server/droplet (created by `scripts/setup_wiki_interactive.py`):

- Monorepo clone (templates + setup script): `/opt/<repo-dir>`
- Per-wiki stack (docker-compose + Makefile + .env): `/opt/<wiki-name>-stack`
- Per-wiki indexer (Ask API + indexer code + config): `/opt/<wiki-name>-indexer`
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
- DNS `A` record for `<domain>` pointing at the droplet (if using Cloudflare, disable proxying initially)
- SSH access as root (or sudo user)
- An OpenAI API key
- Python 3.10+, Git installed

Run:

```bash
sudo -i
cd /opt
git clone https://github.com/jtgreen/AKW.git
cd AKW
python3 scripts/setup_wiki_interactive.py
```

The script installs Docker, docker-compose, Caddy, UFW and instantiates all per-wiki directories.

After it finishes:

1. Visit `https://<domain>` and complete Wiki.js setup.
2. In Wiki.js → Storage, enable **Git** storage pointing at `/wiki/data/repo`.
3. Confirm Ask UI at `https://<domain>/ask`.

---

## 3) Stack config (docker-compose + env)

```bash
cd /opt/<wiki-name>-stack
docker-compose up -d --build
# or: make deploy
```

Secrets live in `/opt/<wiki-name>-stack/.env` and `/opt/<wiki-name>-indexer/.env`.

---

## 4) Ingest-managed PDFs (`/pdfs/*`)

PDFs are stored in `/opt/<wiki-name>-pdfs` and served at `https://<domain>/pdfs/...`.

---

## 5) Vector store + indexing (server-side)

Config: `/opt/<wiki-name>-indexer/config.yaml`

```yaml
wiki_repo_root: /opt/<wiki-name>-data/repo
wiki_base_url: https://<domain>
openai:
  vector_store_id: vs_...
  vector_store_name: <wiki-name>-store
pdf_assets:
  local_dir: /opt/<wiki-name>-pdfs
```

Create vector store → upload docs:

```bash
cd /opt/<wiki-name>-indexer
python3 create_vector_store.py
# copy the ID into config.yaml
python3 indexer_stub.py
```

After a full reorganization (paths change), use `--force-clear` to wipe stale entries:

```bash
python3 indexer_stub.py --force-clear
```

---

## 6) Laptop-side ingestion (PDF → Markdown)

Scripts in `indexer/client_ingest/`. Run on your laptop, not the server.

### Setup

```bash
cd indexer/client_ingest
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Create `.env`:
```
OPENAI_API_KEY=your_key_here
```

### Single PDF

```bash
python3 ingest_paper.py path/to/paper.pdf \
  --wiki-root /path/to/wiki-repo \
  --base-dir research/topic
```

### Batch ingest

```bash
python3 batch_ingest.py "/path/to/papers" \
  --pdf-upload-target root@<domain>:/opt/<wiki-name>-pdfs \
  --pdf-url-base https://<domain>/pdfs \
  -- \
  --wiki-root /path/to/wiki-repo \
  --base-dir new_ingest
```

Key flags:
- `--completed-pdfs-dir PATH` — skip PDFs whose checksums match files in this directory
- `--rebuild-log` — recompute checksums without ingesting
- Everything after `--` is passed through to `ingest_paper.py`

### Auto-filer (incremental)

```bash
python3 auto_filer/auto_ingest.py \
  --watch-dir "/path/to/incoming/PDFs" \
  --wiki-root /path/to/wiki-repo \
  --pdf-upload root@<domain>:/opt/<wiki-name>-pdfs \
  --pdf-url-base https://<domain>/pdfs
```

### Utilities

```bash
# Backfill checksums for already-ingested PDFs
python3 backfill_checksums.py "/path/to/existing/pdfs"

# Archive old batch artifacts
python3 run_cleanup.py
```

---

## 7) Wiki organizer (LLM-driven re-tagging & reorganization)

Scripts in `indexer/organizer/`. These scan the wiki, normalize tags, design a hub/directory structure, and apply it.

### Full pipeline

```bash
cd indexer/organizer

# Stage 1: Build catalog (no API calls, fast)
python3 build_catalog.py --repo-root /path/to/wiki-repo

# Stage 2a: Plan tags (LLM normalizes to ≤150 tags)
python3 plan_tags.py \
  --repo-root /path/to/wiki-repo \
  --max-tags 150 --max-tags-per-doc 5

# Stage 2b: Plan reorg (LLM designs hub structure + assigns docs)
python3 plan_reorg.py \
  --repo-root /path/to/wiki-repo \
  --max-hubs 8 --max-depth 3

# Review the plan
python3 plan_reorg.py --repo-root /path/to/wiki-repo --print-only

# Stage 3: Apply (dry-run first, then --apply)
python3 apply_reorg.py --repo-root /path/to/wiki-repo
python3 apply_reorg.py --repo-root /path/to/wiki-repo --apply --add-hub-ids

# Fix-ups
python3 fix_front_matter_paths.py --repo-root /path/to/wiki-repo --apply
find /path/to/wiki-repo -type d -empty -delete
```

### Batching for large wikis

Both `plan_tags.py` and `plan_reorg.py` support batching for catalogs that exceed LLM context/output limits:

- `--max-batch-docs N` — max documents per LLM call (default 150). This is the primary knob to prevent output truncation.
- `--max-batch-chars N` — max JSON chars per batch (default 700000, ~175k tokens). Prevents input overflow.

For a wiki with ~1400 docs, `--max-batch-docs 150` produces ~10 batches. Each batch gets a complete response.

`plan_tags.py` passes a running tag list to subsequent batches for cross-batch consistency. The final `enforce_limits()` pass deduplicates and prunes globally.

`plan_reorg.py` uses a two-phase approach:
1. **Phase 1**: Single lightweight call (path + title + tags only) to design the hub structure.
2. **Phase 2**: Batched calls to assign each doc to hubs, with the hub structure as context.

### Artifacts

| File | Description |
|------|-------------|
| `_organizer_catalog_<repo>.json` | Extracted front matter, summaries, key points |
| `_organizer_tags_<repo>.json` | Curated tag taxonomy + per-doc assignments |
| `_organizer_plan_<repo>.json` | Hub structure + file move mappings |

### Notes

- `build_catalog.py` skips directories starting with `_` or `.` (line 98). Use staging dirs like `new_ingest/` (no underscore prefix).
- `apply_reorg.py` defaults to dry-run. Use `--apply` to execute moves.
- `apply_reorg.py` will never move protected files (`home.md`, `home.html`) even if the LLM assigns them to a hub.
- `apply_reorg.py` handles duplicate destinations (same paper ingested twice under different filenames) by moving the duplicate source to `_duplicates/` in the wiki repo for manual review, rather than crashing.
- **Important:** Before batch-ingesting new PDFs, run `backfill_checksums.py` against your existing PDF directory so that checksum dedup catches papers that were ingested before checksum tracking was added. Without this, papers with different filenames but identical content will produce duplicate markdown files.
- After reorg, all wiki paths change, so `indexer_stub.py` state is stale — use `--force-clear` to rebuild the vector store.
- The `.env` file for organizer scripts is found via `dotenv` search (walks up from CWD). If you have `OPENAI_API_KEY` set in your shell environment, `dotenv` won't override it. Use `unset OPENAI_API_KEY` first if the shell value is stale.

---

## 8) End-to-end workflow: ingest + reorg + vectorize

```bash
# 1. Batch ingest new PDFs
cd indexer/client_ingest
python3 batch_ingest.py ~/Dropbox/Papers/new/ \
  --completed-pdfs-dir ~/Dropbox/Papers/already-done/ \
  -- --wiki-root /path/to/wiki-repo --base-dir new_ingest

# 2. Git checkpoint
cd /path/to/wiki-repo
git add -A && git commit -m "ingest: add new papers"

# 3. Build catalog → plan tags → plan reorg → apply
cd /path/to/monorepo/indexer/organizer
python3 build_catalog.py --repo-root /path/to/wiki-repo
python3 plan_tags.py --repo-root /path/to/wiki-repo --max-tags 150 --max-tags-per-doc 5
python3 plan_reorg.py --repo-root /path/to/wiki-repo --max-hubs 8 --max-depth 3
python3 apply_reorg.py --repo-root /path/to/wiki-repo          # dry-run
python3 apply_reorg.py --repo-root /path/to/wiki-repo --apply --add-hub-ids

# 4. Post-reorg cleanup
python3 fix_front_matter_paths.py --repo-root /path/to/wiki-repo --apply
find /path/to/wiki-repo -type d -empty -delete
cd /path/to/wiki-repo && git add -A && git commit -m "reorg: full re-tag and reorganization"

# 5. Rebuild vector store
cd /path/to/monorepo/indexer
python3 indexer_stub.py --force-clear
```

---

## 9) Security & ops notes

- Rotate any OpenAI keys that were ever committed.
- Keep secrets in `.env` files (not committed).
- Elasticsearch is not exposed publicly.
- For iframes in Wiki.js: Admin → Rendering → HTML → Security → Allow iframes.

---

## 10) Wiki.js setup notes

On the server:
```bash
ssh-keygen -t ed25519 -C "<wiki-name>-server"
cat ~/.ssh/id_ed25519.pub
# Add to GitHub repo deploy keys with write access
```

In Wiki.js Admin → Storage → Git:
- SSH private key mode: `content`
- Branch: `main`
- Repo path: `/wiki/data/repo`
- Repo URL: `git@github.com:<user>/<wiki-repo>.git`
- Enable bidirectional sync

Additional Wiki.js settings:
- Navigation: set to site tree
- Rendering → HTML → Security → allow iframes
- Utilities → Content → rerender all pages
