#!/usr/bin/env python3
"""
Helpers for checksum-based ingest tracking.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Dict

CHECKSUM_LOG_PATH = Path(__file__).with_name("checksum.log")
CHUNK_SIZE = 1024 * 1024


def compute_checksum(path: Path) -> str:
    """Return a hex sha256 checksum for the file at `path`."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(CHUNK_SIZE)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def load_checksum_log(log_path: Path = CHECKSUM_LOG_PATH) -> Dict[str, str]:
    """
    Load the checksum log into a {absolute_path: checksum} mapping.
    Log format: JSON Lines with keys {"path": "...", "checksum": "..."}.
    Last occurrence of a path wins.
    """
    if not log_path.exists():
        return {}
    entries: Dict[str, str] = {}
    with log_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            cleaned = line.strip()
            if not cleaned or cleaned.startswith("#"):
                continue
            try:
                record = json.loads(cleaned)
                path = record.get("path")
                checksum = record.get("checksum")
                if path and checksum:
                    entries[path] = checksum
            except json.JSONDecodeError:
                print(f"Skipping invalid checksum log line: {cleaned}", file=sys.stderr)
    return entries


def write_checksum_log(entries: Dict[str, str], log_path: Path = CHECKSUM_LOG_PATH) -> None:
    """Persist the mapping to disk as JSON Lines."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as handle:
        for path in sorted(entries.keys()):
            handle.write(json.dumps({"path": path, "checksum": entries[path]}) + "\n")
