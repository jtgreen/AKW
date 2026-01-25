#!/usr/bin/env python3
"""
Compute checksums for a directory of PDFs and upsert them into checksum.log without ingesting.
Useful for backfilling the log when PDFs were already ingested outside batch_ingest.py.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict

from checksum_utils import CHECKSUM_LOG_PATH, compute_checksum, load_checksum_log, write_checksum_log


def discover_pdfs(root: Path) -> list[Path]:
    return sorted(
        path for path in root.rglob("*") if path.is_file() and path.suffix.lower() == ".pdf"
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill checksum.log for a directory of PDFs (no ingestion).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("pdf_dir", type=Path, help="Directory containing PDFs to hash.")
    parser.add_argument(
        "--checksum-log",
        "--state-file",
        dest="checksum_log",
        type=Path,
        default=CHECKSUM_LOG_PATH,
        help="Checksum log to update (JSONL with path + sha256).",
    )
    args = parser.parse_args(argv)

    pdf_root = args.pdf_dir.expanduser()
    checksum_log = args.checksum_log.expanduser()

    if not pdf_root.exists():
        print(f"PDF directory not found: {pdf_root}", file=sys.stderr)
        return 1
    if not pdf_root.is_dir():
        print(f"PDF path is not a directory: {pdf_root}", file=sys.stderr)
        return 1

    pdfs = discover_pdfs(pdf_root)
    if not pdfs:
        print("No PDFs found; nothing to backfill.")
        return 0

    known = load_checksum_log(checksum_log)
    added = updated = 0

    for path in pdfs:
        resolved = str(path.resolve())
        checksum = compute_checksum(path)
        if resolved not in known:
            added += 1
        elif known[resolved] != checksum:
            updated += 1
        known[resolved] = checksum

    write_checksum_log(known, checksum_log)
    print(
        f"Backfill complete. Added {added}, updated {updated}, now tracking {len(known)} PDFs in {checksum_log}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
