#!/usr/bin/env python3
import argparse
import json
import hashlib
import os
from pathlib import Path
import re
import yaml
from dataclasses import dataclass
from typing import List, Tuple, Optional
from urllib.parse import urlparse
from openai import OpenAI

from dotenv import load_dotenv

# ------------------- Config & setup -------------------
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY)

if not OPENAI_API_KEY:
    raise SystemExit("OPENAI_API_KEY not set in environment/.env")

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.yaml"
CONFIG: dict = {}
if CONFIG_PATH.exists():
    CONFIG = yaml.safe_load(CONFIG_PATH.read_text()) or {}

WIKI_NAME = (os.getenv("WIKI_NAME") or (CONFIG.get("wiki_name") if isinstance(CONFIG, dict) else None) or "").strip()
if not WIKI_NAME and not CONFIG:
    raise SystemExit(
        "WIKI_NAME is not set and config.yaml is missing. "
        "Run setup or set WIKI_NAME/WIKI_REPO_ROOT in your environment."
    )
if not WIKI_NAME:
    WIKI_NAME = "my-wiki"

WIKI_REPO_ROOT_ENV = (os.getenv("WIKI_REPO_ROOT") or "").strip()
WIKI_DATA_DIR_ENV = (os.getenv("WIKI_DATA_DIR") or "").strip()
default_repo_root = Path(f"/opt/{WIKI_NAME}-data/repo")
REPO_ROOT = Path(
    WIKI_REPO_ROOT_ENV
    or (CONFIG.get("wiki_repo_root") if isinstance(CONFIG, dict) else None)
    or (Path(WIKI_DATA_DIR_ENV) / "repo" if WIKI_DATA_DIR_ENV else default_repo_root)
).expanduser()

domain_from_env = (os.getenv("DOMAIN") or "").strip()
WIKI_BASE_URL = str(
    os.getenv("WIKI_BASE_URL")
    or (CONFIG.get("wiki_base_url") if isinstance(CONFIG, dict) else None)
    or (f"https://{domain_from_env}" if domain_from_env else "https://example.com")
).rstrip("/")

OPENAI_CFG = (CONFIG.get("openai") or {}) if isinstance(CONFIG, dict) else {}
VECTOR_STORE_ID = (os.getenv("OPENAI_VECTOR_STORE_ID") or OPENAI_CFG.get("vector_store_id") or "vs_TBD").strip()
STATE_PATH = ROOT / ".indexer_state.json"
PDF_ASSETS_CFG = (CONFIG.get("pdf_assets") or {}) if isinstance(CONFIG, dict) else {}
pdf_dir_env = (os.getenv("WIKI_PDFS_DIR") or "").strip()
if pdf_dir_env and not PDF_ASSETS_CFG.get("local_dir"):
    PDF_ASSETS_CFG["local_dir"] = pdf_dir_env
elif not PDF_ASSETS_CFG.get("local_dir"):
    PDF_ASSETS_CFG["local_dir"] = f"/opt/{WIKI_NAME}-pdfs"
PDF_TEXT_ROOT: Optional[Path] = None
if PDF_ASSETS_CFG.get("local_dir"):
    candidate = Path(PDF_ASSETS_CFG["local_dir"]).expanduser()
    if candidate.exists():
        PDF_TEXT_ROOT = candidate.resolve()
    else:
        print(f"Warning: pdf_assets.local_dir '{candidate}' not found; PDF text files will be skipped.")

FRONT_MATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)

@dataclass
class WikiDocument:
    """Logical document ready to be sent to OpenAI vector store."""
    path: str          # wiki path or derived key (e.g. "...::pdf")
    url: str           # primary citation URL (wiki page or pdf)
    title: str
    tags: List[str]
    year: str | None
    source_type: str | None
    content: str       # text to index (title + body)
    wiki_url: Optional[str] = None
    pdf_url: Optional[str] = None
    pdf_text_url: Optional[str] = None


# ------------------- Helpers -------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Upload Markdown summaries and PDF text into the configured OpenAI vector store."
    )
    parser.add_argument(
        "--force-clear",
        action="store_true",
        help="Skip the confirmation prompt when clearing an existing vector store before re-indexing.",
    )
    return parser.parse_args()


def list_vector_store_file_ids() -> list[str]:
    file_ids: list[str] = []
    cursor = None
    while True:
        resp = client.vector_stores.files.list(
            vector_store_id=VECTOR_STORE_ID,
            limit=100,
            after=cursor,
        )
        if not resp.data:
            break
        for f in resp.data:
            file_ids.append(f.id)
        if getattr(resp, "has_more", False):
            cursor = resp.data[-1].id
        else:
            break
    return file_ids


def clear_vector_store(file_ids: list[str]) -> None:
    if not file_ids:
        return
    print(f"Deleting {len(file_ids)} file(s) from vector store {VECTOR_STORE_ID}...")
    for file_id in file_ids:
        client.vector_stores.files.delete(vector_store_id=VECTOR_STORE_ID, file_id=file_id)
    print("Vector store cleared.")

def upsert_document_to_vector_store(doc: WikiDocument):
    """
    Upload a wiki document to the OpenAI vector store.
    Prefers the new single-call API, but falls back to the legacy
    upload + batch attach flow when running on older SDK versions.
    """
    if VECTOR_STORE_ID == "vs_TBD":
        raise RuntimeError("Vector store ID not set in config.yaml")

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

    # Preferred path: new vector store upload helper
    try:
        print(f"Uploading via vector store API: {file_name}")
        result = client.vector_stores.files.upload(
            vector_store_id=VECTOR_STORE_ID,
            file={
                "name": file_name,
                "contents": file_bytes,
            },
            metadata=metadata,
        )
        print("Uploaded:", result.id)
        return
    except (AttributeError, TypeError):
        print("vector_stores.files.upload unavailable; falling back to legacy upload.")

    # Fallback: upload file, then batch-attach
    upload_result = client.files.create(
        file=(file_name, file_bytes),
        purpose="assistants",
    )
    file_id = upload_result.id
    print(f"Uploaded raw file to OpenAI: {file_name} (file_id={file_id})")

    batch_kwargs = {
        "vector_store_id": VECTOR_STORE_ID,
        "file_ids": [file_id],
    }

    try:
        batch = client.vector_stores.file_batches.create(
            **batch_kwargs,
            metadata=metadata,
        )
    except TypeError:
        print("File batch metadata unsupported; attaching without metadata.")
        batch = client.vector_stores.file_batches.create(**batch_kwargs)

    print("Batch created:", batch.id)


def compute_document_hash(doc: WikiDocument) -> str:
    """Return a stable hash of the document content for change detection."""
    hasher = hashlib.sha256()
    hasher.update(doc.content.encode("utf-8"))
    return hasher.hexdigest()


def load_index_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text())
    except json.JSONDecodeError:
        print("State file is corrupted; starting fresh.")
        return {}


def save_index_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True))

def parse_front_matter(md_text: str) -> Tuple[dict, str]:
    """
    Return (front_matter_dict, body_text).
    If no front matter, returns ({}, original_text).
    """
    m = FRONT_MATTER_RE.match(md_text)
    if not m:
        return {}, md_text
    fm_raw = m.group(1)
    body = md_text[m.end():]
    fm = yaml.safe_load(fm_raw) or {}
    return fm, body


def wiki_path_from_file(md_path: Path, fm: dict) -> str:
    """
    Determine the logical wiki path used in URLs, e.g. "shock/hemorrhage/cardiac/foo-bar".
    Prefer explicit `path` in front matter; fall back to filesystem path.
    """
    rel = md_path.relative_to(REPO_ROOT).with_suffix("")  # remove .md
    # skip hubs/tags if you want only content pages
    rel_posix = rel.as_posix()

    explicit = fm.get("path")
    if explicit:
        # strip leading slash if present
        return explicit.lstrip("/")
    return rel_posix


def normalize_tags(raw) -> List[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(t).strip().lower() for t in raw if str(t).strip()]
    if isinstance(raw, str):
        return [t.strip().lower() for t in raw.split(",") if t.strip()]
    return []


def find_pdf_text_candidate(md_path: Path, pdf_text_url: Optional[str]) -> Optional[Path]:
    """Return a Path to the extracted PDF text file if we can find one."""
    sibling = md_path.with_suffix(".txt")
    if sibling.exists():
        return sibling

    if pdf_text_url and PDF_TEXT_ROOT:
        parsed = urlparse(pdf_text_url)
        name = Path(parsed.path).name
        if name:
            candidate = PDF_TEXT_ROOT / name
            if candidate.exists():
                return candidate
    return None


def build_documents(md_path: Path) -> List[WikiDocument]:
    text = md_path.read_text(encoding="utf-8")
    fm, body = parse_front_matter(text)

    # You may choose to ignore unpublished pages
    if fm.get("published") is False:
        return []

    wiki_path = wiki_path_from_file(md_path, fm)
    title = fm.get("title") or md_path.stem
    tags = normalize_tags(fm.get("tags"))
    year = str(fm["year"]) if "year" in fm else None
    source_type = fm.get("source_type")
    wiki_url = f"{WIKI_BASE_URL}/{wiki_path.lstrip('/')}"
    pdf_url = fm.get("pdf_url")
    pdf_text_url = fm.get("pdf_text_url")

    source_meta_lines = ["Sources:"]
    source_meta_lines.append(f"- Wiki: {wiki_url}")
    if pdf_url:
        source_meta_lines.append(f"- PDF: {pdf_url}")
    if pdf_text_url:
        source_meta_lines.append(f"- PDF Text: {pdf_text_url}")
    source_meta = "\n".join(source_meta_lines)

    docs: List[WikiDocument] = []
    md_content = f"# {title}\n\n{source_meta}\n\n{body.strip()}\n"
    docs.append(
        WikiDocument(
            path=wiki_path,
            url=wiki_url,
            title=title,
            tags=tags,
            year=year,
            source_type=source_type or "wiki",
            content=md_content,
            wiki_url=wiki_url,
            pdf_url=pdf_url,
            pdf_text_url=pdf_text_url,
        )
    )

    text_path = find_pdf_text_candidate(md_path, pdf_text_url)
    if text_path and text_path.exists():
        pdf_text = text_path.read_text(encoding="utf-8", errors="ignore").strip()
        if pdf_text:
            pdf_doc = WikiDocument(
                path=f"{wiki_path}::pdf",
                url=pdf_url or wiki_url,
                title=f"{title} (Full PDF Text)",
                tags=tags,
                year=year,
                source_type="pdf_text",
                content=f"# {title} — Full PDF Text\n\n{source_meta}\n\n{pdf_text}\n",
                wiki_url=wiki_url,
                pdf_url=pdf_url,
                pdf_text_url=pdf_text_url,
            )
            docs.append(pdf_doc)

    return docs


def walk_markdown_files():
    for path in REPO_ROOT.rglob("*.md"):
        rel = path.relative_to(REPO_ROOT)
        # Skip non-content directories if you want
        if rel.parts[0] in ("hubs", "tags"):
            continue
        yield path


# ------------------- Main (for now: just inspect) -------------------

def main():
    args = parse_args()
    print(f"Using wiki repo at: {REPO_ROOT}")
    print(f"Vector store: {VECTOR_STORE_ID}\n")

    if not REPO_ROOT.exists():
        raise SystemExit("Repo root does not exist!")
    existing_file_ids = list_vector_store_file_ids()
    if existing_file_ids:
        proceed = args.force_clear
        if not proceed:
            answer = input(
                f"Vector store {VECTOR_STORE_ID} already contains {len(existing_file_ids)} files. "
                "Delete them and re-index? [y/N]: "
            ).strip().lower()
            proceed = answer == "y"
        if not proceed:
            print("Aborting without changes.")
            return
        clear_vector_store(existing_file_ids)
        if STATE_PATH.exists():
            STATE_PATH.unlink()
        state = {}
    else:
        state = load_index_state()
    total = 0
    uploaded = 0
    for md_path in walk_markdown_files():
        docs = build_documents(md_path)
        for doc in docs:
            doc_hash = compute_document_hash(doc)
            if state.get(doc.path) == doc_hash:
                print(f"Skipping unchanged: {doc.title}")
                total += 1
                continue

            upsert_document_to_vector_store(doc)
            state[doc.path] = doc_hash
            uploaded += 1
            total += 1

    if uploaded:
        save_index_state(state)

    print(f"\nCompleted run. Uploaded {uploaded} files (processed {total} total).")


if __name__ == "__main__":
    main()
