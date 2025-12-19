#!/usr/bin/env bash
# Helper wrapper for cron/systemd timers.
# Adjust the environment variables below to match your paths.

set -euo pipefail

WATCH_DIR=${WATCH_DIR:-"/Users/johngreen/Dropbox/Papers/BSOS-Wiki"}
WIKI_ROOT=${WIKI_ROOT:-"/Users/johngreen/Dev/bsos-wiki"}
PDF_UPLOAD=${PDF_UPLOAD:-"root@bsos.wiki:/opt/bsos-wiki-pdfs"}
PDF_URL_BASE=${PDF_URL_BASE:-"https://bsos.wiki/pdfs"}
PDF_TEXT_DIR=${PDF_TEXT_DIR:-"/Users/johngreen/Dev/knowledge-wiki-remote/indexer/client_ingest/uploaded_pdf_text"}
PDF_RAW_DIR=${PDF_RAW_DIR:-"/Users/johngreen/Dev/knowledge-wiki-remote/indexer/client_ingest/uploaded_pdf_raw_renamed"}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

cd "$REPO_ROOT/indexer"

python3 client_ingest/auto_filer/auto_ingest.py \
  --watch-dir "$WATCH_DIR" \
  --wiki-root "$WIKI_ROOT" \
  --pdf-upload "$PDF_UPLOAD" \
  --pdf-url-base "$PDF_URL_BASE" \
  --pdf-text-dir "$PDF_TEXT_DIR" \
  --pdf-raw-dir "$PDF_RAW_DIR" \
  --git-commit
