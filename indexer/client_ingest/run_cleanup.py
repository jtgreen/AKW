#!/usr/bin/env python3
"""
Move batch ingest artifacts into a timestamped stale_batches subdirectory so you can start fresh.
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
STALE_ROOT = SCRIPT_DIR / "stale_batches"

TARGETS = [
    SCRIPT_DIR / "batch_ingested.log",
    SCRIPT_DIR / "failed_pdfs.log",
    SCRIPT_DIR / "ingest.log",
    SCRIPT_DIR / "ingest_std_out.log",
    SCRIPT_DIR / "uploaded_pdf_text",
    SCRIPT_DIR / "uploaded_pdf_raw_renamed",
]


def move_path(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    print(f"Moved {src} -> {dst}")


def main() -> int:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    batch_dir = STALE_ROOT / timestamp
    batch_dir.mkdir(parents=True, exist_ok=True)

    moved_any = False
    for src in TARGETS:
        if src.exists():
            move_path(src, batch_dir / src.name)
            moved_any = True
        else:
            print(f"Skipping missing {src}")

    if not moved_any:
        print("Nothing to move; no cleanup necessary.")
    else:
        print(f"Cleanup complete. Artifacts stashed in {batch_dir}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
