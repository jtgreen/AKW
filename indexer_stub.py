#!/usr/bin/env python3
import os
from pathlib import Path
import re
import yaml
from dataclasses import dataclass, asdict
from typing import List, Tuple
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
CONFIG = yaml.safe_load(CONFIG_PATH.read_text())

REPO_ROOT = Path(CONFIG["wiki_repo_root"])
OPENAI_CFG = CONFIG.get("openai", {})
VECTOR_STORE_ID = OPENAI_CFG.get("vector_store_id", "vs_TBD")

FRONT_MATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)

@dataclass
class WikiDocument:
    """Logical document ready to be sent to OpenAI vector store."""
    path: str          # wiki path, e.g. "shock/hemorrhage/cardiac/..."
    url: str           # https://bsos.wiki/<path>
    title: str
    tags: List[str]
    year: str | None
    source_type: str | None
    content: str       # text to index (title + body)


# ------------------- Helpers -------------------

def upsert_document_to_vector_store(doc: WikiDocument):
    """
    Upload a wiki document to the OpenAI vector store.
    """
    if VECTOR_STORE_ID == "vs_TBD":
        raise RuntimeError("Vector store ID not set in config.yaml")

    contents = doc.content
    metadata = {
        "kind": "wiki",
        "wiki_path": doc.path,
        "wiki_url": doc.url,
        "title": doc.title,
        "tags": doc.tags,
        "year": doc.year,
        "source_type": doc.source_type,
    }

    print(f"Uploading: {doc.title} -> vector store {VECTOR_STORE_ID}")

    result = client.vector_stores.files.upload(
        vector_store_id=VECTOR_STORE_ID,
        file={
            "name": f"{doc.path.replace('/', '_')}.md",
            "contents": contents.encode("utf-8"),
        },
        metadata=metadata,
    )

    print("Uploaded:", result.id)

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


def build_document(md_path: Path) -> WikiDocument | None:
    text = md_path.read_text(encoding="utf-8")
    fm, body = parse_front_matter(text)

    # You may choose to ignore unpublished pages
    if fm.get("published") is False:
        return None

    wiki_path = wiki_path_from_file(md_path, fm)
    title = fm.get("title") or md_path.stem
    tags = normalize_tags(fm.get("tags"))
    year = str(fm["year"]) if "year" in fm else None
    source_type = fm.get("source_type")

    # Content: title as H1 + body
    content = f"# {title}\n\n{body.strip()}\n"

    url = f"https://bsos.wiki/{wiki_path}"

    return WikiDocument(
        path=wiki_path,
        url=url,
        title=title,
        tags=tags,
        year=year,
        source_type=source_type,
        content=content,
    )


def walk_markdown_files():
    for path in REPO_ROOT.rglob("*.md"):
        rel = path.relative_to(REPO_ROOT)
        # Skip non-content directories if you want
        if rel.parts[0] in ("hubs", "tags"):
            continue
        yield path


# ------------------- Main (for now: just inspect) -------------------

def main():
    print(f"Using wiki repo at: {REPO_ROOT}")
    print(f"Vector store: {VECTOR_STORE_ID}\n")

    if not REPO_ROOT.exists():
        raise SystemExit("Repo root does not exist!")

    count = 0
    for md_path in walk_markdown_files():
        doc = build_document(md_path)
        if doc is None:
            continue

        upsert_document_to_vector_store(doc)
        count += 1

    print(f"\nCompleted uploading {count} files into vector store.")


if __name__ == "__main__":
    main()
