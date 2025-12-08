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

## 4. What the pipeline produces

- OCR (via `pypdf` or `ocrmypdf`) to pull text out of crusty scans.  
- Structured JSON from OpenAI (title, tags, key points, etc.) → Markdown with Wiki.js-ready front matter.  
- Figure caption detection + summaries (`## Key Figures`).  
- Clean PDF filenames (title + primary author + year + journal) to keep uploads tidy.  
- Token accounting + cost estimate per ingest run.  
- Lower-case, hyphenated slugs/paths so Git + Wiki.js stay perfectly aligned.

---

## 5. Generated files

The `.gitignore` in this folder already excludes:

- `batch_ingested.log` and `failed_pdfs.log` (state)  
- Local logs (`ingest.log`, etc.)  
- `__pycache__/`, `tmp.fix`, and other build debris

Keep everything else checked in so future laptop environments stay reproducible.
