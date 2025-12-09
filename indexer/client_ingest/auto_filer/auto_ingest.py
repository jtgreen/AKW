#!/usr/bin/env python3
"""
Incremental ingest helper:
- Watches a directory tree for new PDFs not recorded in batch_ingested.log.
- Summarizes each PDF (once) to classify it into existing hubs/tags using the BSOS vector store.
- Files the Markdown into the chosen directory (never creating new folders or tags), uploads PDF/TXT, and
  immediately upserts the new content into the vector store so Ask can cite it without a full rebuild.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Set, Tuple

import yaml
from dotenv import load_dotenv
from openai import OpenAI

# Paths
SCRIPT_PATH = Path(__file__).resolve()
CLIENT_INGEST_DIR = SCRIPT_PATH.parents[1]
INDEXER_DIR = SCRIPT_PATH.parents[2]
REPO_ROOT = INDEXER_DIR.parent

# Defaults
DEFAULT_INGEST_SCRIPT = CLIENT_INGEST_DIR / "ingest_paper.py"
DEFAULT_STATE_FILE = CLIENT_INGEST_DIR / "batch_ingested.log"
DEFAULT_LOG_FILE = CLIENT_INGEST_DIR / "incremental_ingest.log"
DEFAULT_PDF_TEXT_DIR = CLIENT_INGEST_DIR / "uploaded_pdf_text"
DEFAULT_PDF_RAW_DIR = CLIENT_INGEST_DIR / "uploaded_pdf_raw_renamed"
DEDUP_THRESHOLD = 0.9

# Regex
FRONT_MATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
WROTE_RE = re.compile(r"Wrote (.+\.md)")

# Load OpenAI key
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise SystemExit("OPENAI_API_KEY not set")
client = OpenAI(api_key=OPENAI_API_KEY)

# Config / vector store ID
CONFIG = yaml.safe_load((INDEXER_DIR / "config.yaml").read_text())
VECTOR_STORE_ID = CONFIG["openai"]["vector_store_id"]
if not VECTOR_STORE_ID or VECTOR_STORE_ID == "vs_TBD":
    raise SystemExit("Vector store ID missing in indexer/config.yaml")


@dataclass
class WikiDocument:
    path: str
    url: str
    title: str
    tags: List[str]
    year: Optional[str]
    source_type: str | None
    content: str
    wiki_url: Optional[str] = None
    pdf_url: Optional[str] = None
    pdf_text_url: Optional[str] = None


class Logger:
    def __init__(self, log_path: Path):
        self.log_path = log_path
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.duplicate_log = self.log_path.with_name("duplicates.log")

    def log(self, message: str) -> None:
        timestamp = dt.datetime.utcnow().isoformat(timespec="seconds")
        line = f"[{timestamp}] {message}"
        print(line)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def log_duplicate(self, pdf_path: Path, matches: list[tuple[str, float]]) -> None:
        timestamp = dt.datetime.utcnow().isoformat(timespec="seconds")
        with self.duplicate_log.open("a", encoding="utf-8") as handle:
            handle.write(f"[{timestamp}] {pdf_path} considered duplicate (similarity ≥ 0.9)\n")
            for path, score in matches:
                handle.write(f"    {path} ({score:.3f})\n")
            handle.write("\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Incrementally ingest new PDFs into existing BSOS wiki hubs/tags."
    )
    parser.add_argument("--watch-dir", type=Path, required=True, help="Directory tree to scan for PDFs.")
    parser.add_argument("--wiki-root", type=Path, required=True, help="Local bsos-wiki repo root.")
    parser.add_argument(
        "--ingest-script",
        type=Path,
        default=DEFAULT_INGEST_SCRIPT,
        help="Path to ingest_paper.py",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=DEFAULT_STATE_FILE,
        help="batch_ingested.log compatible file used to skip previously ingested PDFs.",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=DEFAULT_LOG_FILE,
        help="Where to tee stdout/stderr for this helper.",
    )
    parser.add_argument("--pdf-upload", type=str, help="Value to pass to ingest_paper --pdf-upload.")
    parser.add_argument("--pdf-url-base", type=str, help="Value to pass to ingest_paper --pdf-url-base.")
    parser.add_argument(
        "--pdf-text-dir",
        type=Path,
        default=DEFAULT_PDF_TEXT_DIR,
        help="Directory where ingest_paper writes extracted PDF text.",
    )
    parser.add_argument(
        "--pdf-raw-dir",
        type=Path,
        default=DEFAULT_PDF_RAW_DIR,
        help="Directory where ingest_paper stores renamed PDFs before upload.",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="gpt-5.1",
        help="OpenAI model to use for directory/tag classification.",
    )
    parser.add_argument(
        "--max-tags",
        type=int,
        default=5,
        help="Maximum number of tags to assign per article.",
    )
    parser.add_argument(
        "--wiki-base-url",
        type=str,
        default="https://bsos.wiki",
        help="Base URL used to build wiki links in vector store metadata.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview classification and logging without writing files or uploading.",
    )
    parser.add_argument(
        "--git-commit",
        action="store_true",
        help="After successful ingestion, git pull/add/commit/push in --wiki-root.",
    )
    parser.add_argument(
        "--deduplicate",
        action="store_true",
        help="Skip ingestion if vector store similarity > 0.9 (logged to duplicates.log).",
    )
    return parser.parse_args()


def load_history(path: Path) -> Set[str]:
    if not path.exists():
        return set()
    entries = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        cleaned = line.strip()
        if cleaned and not cleaned.startswith("#"):
            entries.add(cleaned)
    return entries


def append_history_with_note(path: Path, pdf_path: Path, logger: Logger) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.utcnow().isoformat(timespec="seconds")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"# incremental {timestamp}\n")
        handle.write(str(pdf_path.resolve()) + "\n")
    logger.log(f"Updated state log: {path}")


def discover_pdfs(root: Path) -> List[Path]:
    return sorted(p for p in root.rglob("*.pdf") if p.is_file())


def parse_front_matter(text: str) -> Tuple[dict, str]:
    match = FRONT_MATTER_RE.match(text)
    if not match:
        return {}, text
    fm = yaml.safe_load(match.group(1)) or {}
    body = text[match.end() :]
    return fm, body


def dump_front_matter(front_matter: dict, body: str) -> str:
    fm_yaml = yaml.safe_dump(front_matter, sort_keys=False).strip()
    return f"---\n{fm_yaml}\n---\n\n{body.lstrip()}"


def gather_directories(wiki_root: Path) -> List[str]:
    directories: Set[str] = set()
    for md_path in wiki_root.rglob("*.md"):
        rel = md_path.relative_to(wiki_root).with_suffix("")
        parent = rel.parent.as_posix()
        if parent:
            directories.add(parent)
    return sorted(directories)


def gather_tags(wiki_root: Path) -> List[str]:
    tags: Set[str] = set()
    for md_path in wiki_root.rglob("*.md"):
        fm, _ = parse_front_matter(md_path.read_text(encoding="utf-8"))
        raw = fm.get("tags")
        if isinstance(raw, str):
            entries = [t.strip() for t in raw.split(",")]
        elif isinstance(raw, list):
            entries = [str(t).strip() for t in raw]
        else:
            entries = []
        tags.update(t for t in entries if t)
    return sorted(tags)


def run_ingest_preview(
    pdf_path: Path,
    ingest_script: Path,
    wiki_root: Path,
    logger: Logger,
) -> Tuple[dict, str]:
    preview_dir = wiki_root / "__preview__"
    preview_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable or "python3",
        str(ingest_script),
        str(pdf_path),
        "--dry-run",
        "--print-json",
        "--base-dir",
        str(preview_dir),
        "--wiki-root",
        str(wiki_root),
        "--log-file",
        "-",
        "--stdout-log-file",
        "-",
    ]
    logger.log(f"Previewing summary for {pdf_path}")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.stdout:
        logger.log(proc.stdout.strip())
    if proc.stderr:
        logger.log(proc.stderr.strip())
    if proc.returncode != 0:
        raise RuntimeError(f"Preview ingest failed for {pdf_path} (exit {proc.returncode})")

    summary = extract_json_block(proc.stdout)
    if not summary:
        raise RuntimeError("Unable to parse summary JSON from ingest preview output.")
    summary_text = json.dumps(summary, indent=2)
    return summary, summary_text


def extract_json_block(text: str) -> Optional[dict]:
    decoder = json.JSONDecoder()
    idx = text.find("{")
    while idx != -1:
        try:
            obj, end = decoder.raw_decode(text[idx:])
            return obj
        except json.JSONDecodeError:
            idx = text.find("{", idx + 1)
    return None


def classify_document(
    summary: dict,
    directories: List[str],
    tags: List[str],
    model: str,
    max_tags: int,
    logger: Logger,
) -> Tuple[str, List[str]]:
    dir_blob = "\n".join(f"- {d}" for d in directories)
    tag_blob = ", ".join(tags)
    key_points = summary.get("key_points") or []
    if key_points:
        key_points_blob = "\n".join(f"- {kp}" for kp in key_points)
    else:
        key_points_blob = "- n/a"
    user_prompt = f"""Allowed directories (choose exactly one):
{dir_blob}

Allowed tags (choose up to {max_tags}):
{tag_blob}

Paper summary:
Title: {summary.get('title')}
One sentence takeaway: {summary.get('one_sentence_takeaway')}
Existing tags: {', '.join(summary.get('tags', []))}
Key points:
{key_points_blob}
"""

    resp = client.responses.create(
        model=model,
        input=[
            {
                "role": "system",
                "content": (
                    "You assign new BSOS wiki papers to existing hubs."
                    " Respond with STRICT JSON: {\"directory\": \"path\", \"tags\": [\"tag1\", ...]}"
                    " Use only the provided directories/tags. Never invent new values."
                ),
            },
            {"role": "user", "content": user_prompt},
        ],
        tools=[
            {
                "type": "file_search",
                "vector_store_ids": [VECTOR_STORE_ID],
            }
        ],
        response_format={"type": "json_object"},
    )
    raw = resp.output[0].content[0].text
    logger.log(f"Classifier raw response: {raw}")
    data = json.loads(raw)
    directory = normalize_directory(data.get("directory", ""))
    if directory not in directories:
        raise ValueError(f"Classifier chose unknown directory '{directory}'")
    chosen_tags: List[str] = []
    for tag in data.get("tags", []):
        tag = str(tag).strip()
        if tag and tag in tags and tag not in chosen_tags:
            chosen_tags.append(tag)
        if len(chosen_tags) >= max_tags:
            break
    return directory, chosen_tags


def normalize_directory(value: str) -> str:
    return value.strip().strip("/").replace("\\", "/")


def run_actual_ingest(
    pdf_path: Path,
    ingest_script: Path,
    wiki_root: Path,
    directory: str,
    pdf_upload: Optional[str],
    pdf_url_base: Optional[str],
    pdf_text_dir: Path,
    pdf_raw_dir: Path,
    logger: Logger,
) -> Path:
    target_dir = wiki_root / directory
    if not target_dir.exists():
        raise FileNotFoundError(f"Target directory '{directory}' does not exist.")

    cmd = [
        sys.executable or "python3",
        str(ingest_script),
        str(pdf_path),
        "--base-dir",
        str(target_dir),
        "--wiki-root",
        str(wiki_root),
        "--pdf-text-dir",
        str(pdf_text_dir),
        "--pdf-raw-renamed-dir",
        str(pdf_raw_dir),
    ]
    if pdf_upload:
        cmd.extend(["--pdf-upload", pdf_upload])
    if pdf_url_base:
        cmd.extend(["--pdf-url-base", pdf_url_base])

    logger.log(f"Running ingest_paper for {pdf_path}")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.stdout:
        logger.log(proc.stdout.strip())
    if proc.stderr:
        logger.log(proc.stderr.strip())
    if proc.returncode != 0:
        raise RuntimeError(f"ingest_paper failed for {pdf_path} (exit {proc.returncode})")

    match = WROTE_RE.search(proc.stdout)
    if not match:
        raise RuntimeError("Could not determine Markdown path from ingest output.")
    md_path = Path(match.group(1)).expanduser().resolve()
    return md_path


def update_front_matter(
    md_path: Path,
    directory: str,
    tags: List[str],
) -> dict:
    text = md_path.read_text(encoding="utf-8")
    fm, body = parse_front_matter(text)
    slug = fm.get("slug") or md_path.stem
    wiki_path = f"{directory}/{slug}"
    fm["slug"] = slug
    fm["path"] = wiki_path
    if tags:
        fm["tags"] = ", ".join(tags)
    new_text = dump_front_matter(fm, body)
    md_path.write_text(new_text, encoding="utf-8")
    fm["path"] = wiki_path
    return fm


def build_wiki_document(
    md_path: Path,
    front_matter: dict,
    base_url: str,
    wiki_root: Path,
) -> WikiDocument:
    tags_raw = front_matter.get("tags")
    if isinstance(tags_raw, str):
        tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
    elif isinstance(tags_raw, list):
        tags = [str(t).strip() for t in tags_raw if str(t).strip()]
    else:
        tags = []

    body = md_path.read_text(encoding="utf-8")
    fm, body_text = parse_front_matter(body)
    body_content = body_text.strip()
    content = f"# {title}\n\n{body_content}"

    path = fm.get("path")
    if not path:
        path = md_path.relative_to(wiki_root).with_suffix("").as_posix()
    title = fm.get("title") or md_path.stem
    wiki_url = f"{base_url.rstrip('/')}/{path}"

    doc = WikiDocument(
        path=path,
        url=wiki_url,
        title=title,
        tags=tags,
        year=str(fm.get("year")) if fm.get("year") else None,
        source_type=fm.get("kind") or "wiki",
        content=content,
        wiki_url=wiki_url,
        pdf_url=fm.get("pdf_url"),
        pdf_text_url=fm.get("pdf_text_url"),
    )
    return doc


def build_pdf_text_document(
    wiki_doc: WikiDocument,
    pdf_text_path: Path,
) -> Optional[WikiDocument]:
    if not pdf_text_path.exists():
        return None
    text = pdf_text_path.read_text(encoding="utf-8", errors="ignore").strip()
    if not text:
        return None
    pdf_doc = WikiDocument(
        path=f"{wiki_doc.path}::pdf",
        url=wiki_doc.pdf_url or wiki_doc.url,
        title=f"{wiki_doc.title} (Full PDF Text)",
        tags=wiki_doc.tags,
        year=wiki_doc.year,
        source_type="pdf_text",
        content=text,
        wiki_url=wiki_doc.wiki_url,
        pdf_url=wiki_doc.pdf_url,
        pdf_text_url=wiki_doc.pdf_text_url,
    )
    return pdf_doc


def upsert_document_to_vector_store(doc: WikiDocument, logger: Logger) -> None:
    metadata = {
        "kind": doc.source_type or "wiki",
        "wiki_path": doc.path,
        "wiki_url": doc.wiki_url or doc.url,
        "primary_url": doc.url,
        "pdf_url": doc.pdf_url,
        "pdf_text_url": doc.pdf_text_url,
        "title": doc.title,
        "tags": doc.tags,
        "year": doc.year,
        "source_type": doc.source_type,
    }

    safe_path = re.sub(r"[^A-Za-z0-9._-]+", "_", doc.path)
    extension = ".txt" if (doc.source_type == "pdf_text") else ".md"
    file_name = f"{safe_path}{extension}"
    file_bytes = doc.content.encode("utf-8")

    try:
        logger.log(f"Uploading {file_name} via vector store API")
        client.vector_stores.files.upload(
            vector_store_id=VECTOR_STORE_ID,
            file={"name": file_name, "contents": file_bytes},
            metadata=metadata,
        )
    except (AttributeError, TypeError):
        logger.log("vector_stores.files.upload unavailable; falling back to legacy upload.")
        upload_result = client.files.create(file=(file_name, file_bytes), purpose="assistants")
        file_id = upload_result.id
        logger.log(f"Uploaded raw file to OpenAI: {file_name} ({file_id})")
        try:
            client.vector_stores.file_batches.create(
                vector_store_id=VECTOR_STORE_ID,
                file_ids=[file_id],
                metadata=metadata,
            )
        except TypeError:
            client.vector_stores.file_batches.create(
                vector_store_id=VECTOR_STORE_ID,
                file_ids=[file_id],
            )


def upload_to_vector_store(
    md_path: Path,
    front_matter: dict,
    pdf_text_dir: Path,
    base_url: str,
    wiki_root: Path,
    logger: Logger,
) -> None:
    wiki_doc = build_wiki_document(md_path, front_matter, base_url, wiki_root)
    upsert_document_to_vector_store(wiki_doc, logger)

    pdf_text_url = front_matter.get("pdf_text_url")
    if pdf_text_url:
        pdf_text_filename = Path(pdf_text_url).name
        pdf_text_path = pdf_text_dir / pdf_text_filename
        pdf_doc = build_pdf_text_document(wiki_doc, pdf_text_path)
        if pdf_doc:
            upsert_document_to_vector_store(pdf_doc, logger)


def git_sync(wiki_root: Path, logger: Logger) -> None:
    try:
        subprocess.run(["git", "-C", str(wiki_root), "pull", "--ff-only"], check=True)
        status = subprocess.run(
            ["git", "-C", str(wiki_root), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
        )
        if not status.stdout.strip():
            logger.log("No wiki changes to commit.")
            return
        subprocess.run(["git", "-C", str(wiki_root), "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", str(wiki_root), "commit", "-m", "Incremental ingest"],
            check=True,
        )
        subprocess.run(["git", "-C", str(wiki_root), "push"], check=True)
        logger.log("Git push complete.")
    except subprocess.CalledProcessError as exc:
        logger.log(f"Git command failed: {exc}")


def process_pdf(
    pdf_path: Path,
    args: argparse.Namespace,
    directories: List[str],
    tags: List[str],
    state_file: Path,
    logger: Logger,
) -> None:
    summary, summary_text = run_ingest_preview(pdf_path, args.ingest_script, args.wiki_root, logger)
    if args.deduplicate:
        matches = find_similar_documents(summary_text, args.model)
        if matches and matches[0][1] >= DEDUP_THRESHOLD:
            logger.log(f"Duplicate detected for {pdf_path}; similarity {matches[0][1]:.3f}. Skipping.")
            logger.log_duplicate(pdf_path, matches)
            return
    directory, chosen_tags = classify_document(summary, directories, tags, args.model, args.max_tags, logger)
    logger.log(f"Chosen directory: {directory}")
    logger.log(f"Chosen tags: {chosen_tags}")

    if args.dry_run:
        logger.log("Dry run enabled; skipping ingest/move/upload.")
        return

    md_path = run_actual_ingest(
        pdf_path,
        args.ingest_script,
        args.wiki_root,
        directory,
        args.pdf_upload,
        args.pdf_url_base,
        args.pdf_text_dir,
        args.pdf_raw_dir,
        logger,
    )
    front_matter = update_front_matter(md_path, directory, chosen_tags)
    upload_to_vector_store(md_path, front_matter, args.pdf_text_dir, args.wiki_base_url, args.wiki_root, logger)
    append_history_with_note(state_file, pdf_path, logger)


def main() -> None:
    args = parse_args()
    logger = Logger(args.log_file.expanduser())

    args.watch_dir = args.watch_dir.expanduser()
    args.wiki_root = args.wiki_root.expanduser()
    args.ingest_script = args.ingest_script.expanduser()
    args.state_file = args.state_file.expanduser()
    args.pdf_text_dir = args.pdf_text_dir.expanduser()
    args.pdf_raw_dir = args.pdf_raw_dir.expanduser()
    args.pdf_text_dir.mkdir(parents=True, exist_ok=True)
    args.pdf_raw_dir.mkdir(parents=True, exist_ok=True)

    for path in [args.watch_dir, args.wiki_root, args.ingest_script]:
        if not path.exists():
            raise SystemExit(f"Path not found: {path}")

    processed = load_history(args.state_file)
    all_pdfs = discover_pdfs(args.watch_dir)
    pending = [p for p in all_pdfs if str(p.resolve()) not in processed]
    if not pending:
        logger.log("No new PDFs to process.")
        return

    logger.log(f"Found {len(pending)} new PDF(s). Gathering wiki structure...")
    directories = gather_directories(args.wiki_root)
    tags = gather_tags(args.wiki_root)
    if not directories:
        raise SystemExit("No directories discovered in wiki root.")
    if not tags:
        raise SystemExit("No tags found in wiki front matter.")

    for pdf_path in pending:
        try:
            process_pdf(pdf_path, args, directories, tags, args.state_file, logger)
        except Exception as exc:
            logger.log(f"Error processing {pdf_path}: {exc}")

    if args.git_commit and not args.dry_run:
        git_sync(args.wiki_root, logger)


if __name__ == "__main__":
    main()
def find_similar_documents(
    summary_text: str,
    model: str,
    max_results: int = 3,
) -> List[Tuple[str, float]]:
    if not summary_text.strip():
        return []
    resp = client.responses.create(
        model=model,
        input=[
            {
                "role": "system",
                "content": (
                    "You check for duplicates in the BSOS wiki. "
                    "Use the file_search tool and then reply with STRICT JSON as "
                    '{"matches": [{"path": "...", "score": 0.0-1.0}]} using the wiki_path metadata.'
                ),
            },
            {
                "role": "user",
                "content": "Check whether this paper already exists:\n" + summary_text,
            },
        ],
        tools=[
            {
                "type": "file_search",
                "vector_store_ids": [VECTOR_STORE_ID],
            }
        ],
        response_format={"type": "json_object"},
    )
    try:
        raw = resp.output[0].content[0].text
        data = json.loads(raw)
    except Exception:
        return []

    raw_matches = data.get("matches") or data.get("similar_docs") or []
    matches: List[Tuple[str, float]] = []
    for item in raw_matches:
        path = item.get("path") or item.get("wiki_path") or item.get("id")
        score = item.get("score")
        if path and score is not None:
            matches.append((path, float(score)))
    matches.sort(key=lambda x: x[1], reverse=True)
    return matches[:max_results]
