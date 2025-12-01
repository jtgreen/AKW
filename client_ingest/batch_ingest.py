#!/usr/bin/env python3
"""
Batch driver for ingest_paper.py with failure tracking.

Given a directory of PDFs, this script:
- Tracks which files have already been processed in batch_ingested.txt.
- Walks the directory recursively to find PDF files.
- Invokes ingest_paper.py for every new file, passing through any additional CLI args.
- Records failures (e.g., too long PDF, empty text, placeholder summary) in failed_pdfs.txt.
- Streams ingest output to stdout/stderr but continues past failures automatically.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

FAILURE_LOG_PATH = Path(__file__).with_name("failed_pdfs.txt")
FAILURE_PAYLOAD_START = "===INGEST_FAILURE_PAYLOAD_START==="
FAILURE_PAYLOAD_END = "===INGEST_FAILURE_PAYLOAD_END==="

DEFAULT_STATE_FILE = Path(__file__).with_name("batch_ingested.txt")
DEFAULT_INGEST_SCRIPT = Path(__file__).with_name("ingest_paper.py")


def parse_args(argv: List[str]) -> Tuple[argparse.Namespace, List[str]]:
    parser = argparse.ArgumentParser(
        description="Batch ingest PDFs by calling ingest_paper.py for each unseen file.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("pdf_dir", type=Path, help="Directory containing PDFs to ingest.")
    parser.add_argument(
        "--state-file",
        type=Path,
        default=DEFAULT_STATE_FILE,
        help="File that tracks which PDFs have already been ingested.",
    )
    parser.add_argument(
        "--ingest-script",
        type=Path,
        default=DEFAULT_INGEST_SCRIPT,
        help="Path to ingest_paper.py (or a compatible ingest script).",
    )
    parser.add_argument(
        "--failed-log",
        type=Path,
        default=FAILURE_LOG_PATH,
        help="File to append details for PDFs that failed ingestion.",
    )
    return parser.parse_known_args(argv)


def load_history(path: Path) -> Set[str]:
    if not path.exists():
        return set()
    entries = set()
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            cleaned = line.strip()
            if cleaned:
                entries.add(cleaned)
    return entries


def append_history(path: Path, entry: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(entry + "\n")


def discover_pdfs(root: Path) -> List[Path]:
    return sorted(
        path for path in root.rglob("*") if path.is_file() and path.suffix.lower() == ".pdf"
    )


def split_failure_payload(text: str) -> Tuple[str, Optional[Dict[str, Any]]]:
    start = text.find(FAILURE_PAYLOAD_START)
    end = text.find(FAILURE_PAYLOAD_END)
    if start == -1 or end == -1 or end <= start:
        return text, None
    payload_text = text[start + len(FAILURE_PAYLOAD_START) : end].strip()
    cleaned = text[:start] + text[end + len(FAILURE_PAYLOAD_END) :]
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError:
        payload = {"detail": payload_text}
    return cleaned, payload


def append_failure_log(
    log_path: Path,
    pdf_path: Path,
    payload: Optional[Dict[str, Any]],
    stdout: str,
    stderr: str,
) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    reason = (payload or {}).get("reason") or "error"
    detail = (payload or {}).get("detail") or stdout.strip() or stderr.strip()
    header = str(pdf_path.resolve())
    if reason == "too_long":
        header = f"{header} (too long)"
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(header + "\n")
        if reason != "too_long":
            handle.write(f"Reason: {reason}\n")
        if detail:
            handle.write(detail.rstrip() + "\n")
        handle.write("\n")


def run_ingest(
    ingest_script: Path,
    pdf_path: Path,
    extra_args: List[str],
) -> Tuple[int, str, str, Optional[Dict[str, Any]]]:
    python_exe = sys.executable or "python3"
    cmd = [python_exe, str(ingest_script), str(pdf_path)]
    if extra_args:
        cmd.extend(extra_args)
    result = subprocess.run(cmd, capture_output=True, text=True)
    stdout_clean, payload = split_failure_payload(result.stdout or "")
    if stdout_clean:
        print(stdout_clean, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    return result.returncode, stdout_clean, result.stderr or "", payload


def main(argv: List[str]) -> int:
    args, passthrough_args = parse_args(argv)
    pdf_root = args.pdf_dir.expanduser()
    ingest_script = args.ingest_script.expanduser()
    state_path = args.state_file.expanduser()
    failed_log = args.failed_log.expanduser()

    if not pdf_root.exists():
        print(f"PDF directory not found: {pdf_root}", file=sys.stderr)
        return 1
    if not pdf_root.is_dir():
        print(f"PDF path is not a directory: {pdf_root}", file=sys.stderr)
        return 1
    if not ingest_script.exists():
        print(f"Ingest script not found: {ingest_script}", file=sys.stderr)
        return 1

    processed = load_history(state_path)
    all_pdfs = discover_pdfs(pdf_root)
    pending = [path for path in all_pdfs if str(path.resolve()) not in processed]

    if not pending:
        print("No new PDFs to ingest. All files are up to date.")
        return 0

    total = len(pending)
    print(f"Found {total} PDF(s) to ingest (tracking {len(processed)} previously completed).")

    for index, pdf_path in enumerate(pending, start=1):
        remaining = total - index
        resolved = str(pdf_path.resolve())
        print(f"[{index}/{total}] Ingesting {pdf_path} (remaining: {remaining})")
        code, stdout_text, stderr_text, payload = run_ingest(ingest_script, pdf_path, passthrough_args)
        if code != 0:
            append_failure_log(failed_log, pdf_path, payload, stdout_text, stderr_text)
            print(f"Recorded failure for {pdf_path} (exit code {code}). Continuing batch.", file=sys.stderr)
            continue

        append_history(state_path, resolved)
        processed.add(resolved)

    print("Batch ingest complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
