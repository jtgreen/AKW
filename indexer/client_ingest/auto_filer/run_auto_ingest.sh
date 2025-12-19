#!/usr/bin/env bash
# Helper wrapper for cron/systemd timers.
# Adjust the environment variables below to match your paths.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

cd "$REPO_ROOT/indexer"

WIKI_NAME=${WIKI_NAME:-"my-wiki"}
DOMAIN=${DOMAIN:-"example.com"}

WATCH_DIR=${WATCH_DIR:-"$HOME/Dropbox/Papers/$WIKI_NAME"}
WIKI_ROOT=${WIKI_ROOT:-"$HOME/Dev/$WIKI_NAME"}
PDF_UPLOAD=${PDF_UPLOAD:-"root@$DOMAIN:/opt/$WIKI_NAME-pdfs"}
PDF_URL_BASE=${PDF_URL_BASE:-"https://$DOMAIN/pdfs"}
PDF_TEXT_DIR=${PDF_TEXT_DIR:-"$REPO_ROOT/indexer/client_ingest/uploaded_pdf_text"}
PDF_RAW_DIR=${PDF_RAW_DIR:-"$REPO_ROOT/indexer/client_ingest/uploaded_pdf_raw_renamed"}

python3 client_ingest/auto_filer/auto_ingest.py \
  --watch-dir "$WATCH_DIR" \
  --wiki-root "$WIKI_ROOT" \
  --pdf-upload "$PDF_UPLOAD" \
  --pdf-url-base "$PDF_URL_BASE" \
  --pdf-text-dir "$PDF_TEXT_DIR" \
  --pdf-raw-dir "$PDF_RAW_DIR" \
  --git-commit
