# Client Ingestion Scripts (Laptop‑Side)

This directory mirrors the original `knowledge-wiki` tooling. Run it on your laptop to turn PDFs into Markdown pages (plus optional remote PDF uploads) before committing the results to the server-side wiki repo. Nothing in here should run on the droplet—keep it decoupled from the `/opt/bsos-wiki-*` stack.

---

## 1. Requirements

- Python 3.10+  
- `pip install -r requirements.txt`  
- `OPENAI_API_KEY` present in your environment (or `.env` if you prefer direnv)  
- OCR dependencies for `ocrmypdf` (Ghostscript, Tesseract, leptonica, etc.) installed locally

Optional helpers:
- `ocrmypdf` (automatic fallback when a PDF has no embedded text)  
- `scp` + SSH keys for hands-free uploads to `/opt/bsos-wiki-pdfs`

---

## 2. Single-PDF Ingest (`ingest_paper.py`)

Basic run:

```bash
python ingest_paper.py path/to/paper.pdf \
  --base-dir /Users/you/Dev/bsos-wiki/research/cardiovascular/hemorrhagic_shock
```

Notable flags (all forward-compatible with the server indexer):

| Flag | Purpose |
| --- | --- |
| `--base-dir` | Directory (inside your local wiki clone) where the Markdown is written. Defaults to `research/cardiovascular/hemorrhagic_shock`. |
| `--wiki-root` | Absolute path to the wiki repo root. Lets the script infer the correct front-matter `path` when `--base-dir` is absolute. |
| `--wiki-path-prefix` | Manual POSIX path override (skips inference). |
| `--doc-id` | Optional override for the `doc_id` stored in front matter. By default, the script derives one from the slug + year so the server-side indexer sees a stable identity. |
| `--model` / `--max-chars` | Control which OpenAI model to call and how much text to send (default ≈900k chars). |
| `--print-json` / `--dry-run` | Inspect the structured JSON or skip writing Markdown altogether. |
| `--pdf-upload` / `--pdf-url-base` | `scp` the source PDF to the dedicated ingest directory (e.g., `root@bsos.wiki:/opt/bsos-wiki-pdfs`) and embed a download link such as `https://bsos.wiki/pdfs/...`. |
| `--pdf-text-dir` | Local directory where the extracted PDF plaintext files are stored before upload (defaults to `uploaded_pdf_text/` next to the script). |
| `--pdf-raw-renamed-dir` | Local directory where the PDF is copied/renamed to the canonical filename before SCP (defaults to `uploaded_pdf_raw_renamed/`). |
| `--ignore-dir` | Repeatable; skip these directory names when checking for duplicate `doc_id`s (e.g., `.git`). |
| `--input-cost-per-1k`, `--output-cost-per-1k` | Override USD pricing if you’re on negotiated OpenAI rates. |
| `--log-file` | Defaults to `ingest.log`; pass `-` to disable file logging. |

Every generated Markdown page now includes:

- `doc_id` and `kind: "wiki"` so the droplet’s indexer (and future citation tooling) can distinguish wiki summaries from raw PDFs.  
- `source_type: "paper"` plus the normalized `path`, `slug`, tags, etc.  
- A standard body layout with sections for key points, methods, findings, implications, figures, and Related Work.

If the LLM ever returns placeholder text, the script raises `llm_placeholder` and saves the attempted Markdown in the failure payload so you can inspect what went wrong.

---

## 3. Batch Ingest (`batch_ingest.py`)

Process entire folders:

```bash
python batch_ingest.py "/path/to/papers" \
  --pdf-upload-target root@bsos.wiki:/opt/bsos-wiki-pdfs \
  --pdf-url-base https://bsos.wiki/pdfs \
  -- \
  --base-dir /Users/you/Dev/bsos-wiki/research/cardiovascular \
  --wiki-path-prefix "shock/hemorrhage/cardiac"
```

Behavior:

- Recurses through the directory, passing every `.pdf` to `ingest_paper.py`.  
- Remembers successes via `batch_ingested.log` so reruns skip already-processed files.  
- Logs failures (with reason + payload) to `failed_pdfs.log`, then keeps going.  
- `--pdf-upload-target` / `--pdf-url-base` make it easy to default uploads to `/opt/<wiki>-pdfs` and links to `https://<domain>/pdfs/...` without repeating the flags in every run.  
- `--rebuild-log` scans the directory and recreates `batch_ingested.log` without running ingest (handy if the PDFs are already uploaded but the log was lost).  
- Any options placed after `--` are forwarded verbatim to `ingest_paper.py` (so you can tweak `--doc-id`, `--wiki-root`, etc.).

---

## 4. Auto-Filer (incremental ingest)

`client_ingest/auto_filer/auto_ingest.py` handles “bee-line” ingests: it classifies each new PDF against the current wiki structure, writes the Markdown directly into the chosen hub, uploads the PDF/TXT, and upserts just that article into the vector store so Ask can cite it immediately—no full re-index required.

```bash
python client_ingest/auto_filer/auto_ingest.py \
  --watch-dir "/path/to/incoming/PDFs" \
  --wiki-root /Users/you/Dev/bsos-wiki \
  --pdf-upload root@bsos.wiki:/opt/bsos-wiki-pdfs \
  --pdf-url-base https://bsos.wiki/pdfs \
  --git-commit
```

  python3 client_ingest/auto_filer/auto_ingest.py \
  --watch-dir "~/Dropbox/Papers/BSOS-Wiki" \
  --wiki-root ~/Dev/bsos-wiki \
  --pdf-upload root@bsos.wiki:/opt/bsos-wiki-pdfs \
  --pdf-url-base https://bsos.wiki/pdfs \
  --deduplicate \
  
  
Workflow:

1. Scans the watch directory for PDFs missing from `batch_ingested.log` (comments are ignored).  
2. Runs a dry-run ingest to get the LLM summary, optionally checks the vector store for near-duplicates (`--deduplicate`), then asks GPT‑5.1 (with file_search over the live vector store) to pick the best existing directory and up to 5 existing tags.  
3. Re-runs ingest_paper into that directory, rewrites front matter to match the chosen `path/slug/tags`, uploads the PDF/TXT, and immediately upserts the Markdown + PDF text into the configured vector store.  
4. Appends a timestamped comment + path entry to `batch_ingested.log` so future batch jobs still skip the file.  
5. Optional `--git-commit` pulls the repo, commits the new Markdown, and pushes it upstream.

Flags:

| Flag | Purpose |
| --- | --- |
| `--watch-dir` | Directory of PDFs to monitor recursively. |
| `--wiki-root` | Local bsos-wiki repo (used to read directories/tags and commit changes). |
| `--state-file` | Which log to use for “already ingested” tracking (default `batch_ingested.log`). |
| `--pdf-upload`, `--pdf-url-base` | Passed to ingest_paper so uploads/links match your deployment. |
| `--pdf-text-dir`, `--pdf-raw-dir` | Local folders where ingest_paper stores extracted text / renamed PDFs. |
| `--model` / `--max-tags` | Control the classifier LLM and how many tags it can keep. |
| `--wiki-base-url` | Used when writing vector-store metadata (default `https://bsos.wiki`). |
| `--deduplicate` | Query the vector store first and skip anything ≥90% similar (logged to `duplicates.log`). |
| `--dry-run` | Shows classification suggestions without writing files or touching the vector store. |
| `--git-commit` | Pull/add/commit/push inside `--wiki-root` after successful ingests. |

Automate it with `client_ingest/auto_filer/run_auto_ingest.sh` (set `WATCH_DIR`, `WIKI_ROOT`, etc.) and drop it into cron/systemd.

---

## 5. What the pipeline produces

- OCR (via `pypdf` or `ocrmypdf`) to pull text out of crusty scans.  
- Structured JSON from OpenAI (title, tags, key points, etc.) → Markdown with Wiki.js-ready front matter.  
- Figure caption detection + summaries (`## Key Figures`).  
- Clean PDF filenames (title + primary author + year + journal) to keep uploads tidy.  
- Token accounting + cost estimate per ingest run.  
- Lower-case, hyphenated slugs/paths so Git + Wiki.js stay perfectly aligned.

---

## 6. Generated files

The `.gitignore` in this folder already excludes:

- `batch_ingested.log` and `failed_pdfs.log` (state)  
- Local logs (`ingest.log`, etc.)  
- `__pycache__/`, `tmp.fix`, and other build debris

Keep everything else checked in so future laptop environments stay reproducible.
