#!/usr/bin/env python3
"""
Quick-ingest: process a small batch of PDFs end-to-end.

For each PDF:
  1. Checksum dedup (skip already-processed)
  2. Summarize via GPT (calls ingest_paper.py --dry-run --print-json)
  3. Classify into existing hubs/tags via LLM
  4. Run full ingest (markdown + SCP upload)
  5. Update front matter with classified path/tags
  6. Upsert to vector store (immediately searchable)
  7. Record checksum

No re-org or re-tag of the whole wiki. Uses existing hub structure and tag taxonomy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import yaml
from dotenv import load_dotenv

# -- Paths --
SCRIPT_DIR = Path(__file__).resolve().parent
INDEXER_DIR = SCRIPT_DIR.parent
CLIENT_INGEST_DIR = INDEXER_DIR / "client_ingest"
ORGANIZER_DIR = INDEXER_DIR / "organizer"
CONFIG_PATH = INDEXER_DIR / "config.yaml"
INGEST_SCRIPT = CLIENT_INGEST_DIR / "ingest_paper.py"
CHECKSUM_LOG_PATH = CLIENT_INGEST_DIR / "checksum.log"
PDF_TEXT_DIR = CLIENT_INGEST_DIR / "uploaded_pdf_text"
PDF_RAW_DIR = CLIENT_INGEST_DIR / "uploaded_pdf_raw_renamed"

FRONT_MATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
WROTE_RE = re.compile(r"Wrote (.+\.md)")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config(config_path: Path) -> dict:
    if config_path.exists():
        return yaml.safe_load(config_path.read_text()) or {}
    return {}


def load_env():
    """Load .env files in priority order."""
    for env_path in [CLIENT_INGEST_DIR / ".env", INDEXER_DIR / ".env"]:
        if env_path.exists():
            load_dotenv(env_path, override=False)


# ---------------------------------------------------------------------------
# Checksum dedup (inlined from checksum_utils to avoid sys.path hacks)
# ---------------------------------------------------------------------------

def compute_checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def load_checksum_log(log_path: Path) -> Dict[str, str]:
    if not log_path.exists():
        return {}
    entries: Dict[str, str] = {}
    for line in log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            record = json.loads(line)
            if record.get("path") and record.get("checksum"):
                entries[record["path"]] = record["checksum"]
        except json.JSONDecodeError:
            pass
    return entries


def append_checksum(log_path: Path, pdf_path: Path, checksum: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"path": str(pdf_path.resolve()), "checksum": checksum}) + "\n")


# ---------------------------------------------------------------------------
# Front matter helpers
# ---------------------------------------------------------------------------

def parse_front_matter(text: str) -> Tuple[dict, str]:
    match = FRONT_MATTER_RE.match(text)
    if not match:
        return {}, text
    fm = yaml.safe_load(match.group(1)) or {}
    return fm, text[match.end():]


def dump_front_matter(front_matter: dict, body: str) -> str:
    fm_yaml = yaml.safe_dump(front_matter, sort_keys=False).strip()
    return f"---\n{fm_yaml}\n---\n\n{body.lstrip()}"


# ---------------------------------------------------------------------------
# Organizer artifact loading
# ---------------------------------------------------------------------------

def load_organizer_constraints(wiki_name: str) -> Tuple[Optional[List[str]], Optional[List[str]]]:
    """Load directories from plan and tags from tag plan. Returns (dirs, tags) or (None, None)."""
    slug = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in wiki_name) or "repo"

    # Tags
    tag_path = ORGANIZER_DIR / f"_organizer_tags_{slug}.json"
    tags = None
    if tag_path.exists():
        data = json.loads(tag_path.read_text(encoding="utf-8"))
        tags = [t["name"] for t in data.get("tags", []) if t.get("name")]

    # Directories from plan
    plan_path = ORGANIZER_DIR / f"_organizer_plan_{slug}.json"
    dirs = None
    if plan_path.exists():
        data = json.loads(plan_path.read_text(encoding="utf-8"))
        dir_set: Set[str] = set()
        for hub in data.get("hubs", []):
            hub_id = hub.get("id", "")
            for subdir in hub.get("subdirs", []):
                dir_set.add(f"{hub_id}/{subdir}")
            dir_set.add(hub_id)
        dirs = sorted(dir_set)

    return dirs, tags


def gather_directories(wiki_root: Path) -> List[str]:
    """Fallback: scan filesystem for existing directories."""
    directories: Set[str] = set()
    for md_path in wiki_root.rglob("*.md"):
        rel = md_path.relative_to(wiki_root).with_suffix("")
        parent = rel.parent.as_posix()
        if parent and parent != ".":
            directories.add(parent)
    return sorted(directories)


def gather_tags(wiki_root: Path) -> List[str]:
    """Fallback: scan front matter for existing tags."""
    tags: Set[str] = set()
    for md_path in wiki_root.rglob("*.md"):
        try:
            fm, _ = parse_front_matter(md_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        raw = fm.get("tags")
        if isinstance(raw, str):
            tags.update(t.strip() for t in raw.split(",") if t.strip())
        elif isinstance(raw, list):
            tags.update(str(t).strip() for t in raw if str(t).strip())
    return sorted(tags)


# ---------------------------------------------------------------------------
# WikiDocument + vector store upsert
# ---------------------------------------------------------------------------

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


def build_wiki_document(md_path: Path, front_matter: dict, base_url: str, wiki_root: Path) -> WikiDocument:
    fm = dict(front_matter)
    tags_raw = fm.get("tags")
    if isinstance(tags_raw, str):
        tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
    elif isinstance(tags_raw, list):
        tags = [str(t).strip() for t in tags_raw if str(t).strip()]
    else:
        tags = []

    path = fm.get("path")
    if not path:
        path = md_path.relative_to(wiki_root).with_suffix("").as_posix()
    title = fm.get("title") or md_path.stem
    wiki_url = f"{base_url.rstrip('/')}/{path}"

    _, body_text = parse_front_matter(md_path.read_text(encoding="utf-8"))

    return WikiDocument(
        path=path, url=wiki_url, title=title, tags=tags,
        year=str(fm.get("year")) if fm.get("year") else None,
        source_type=fm.get("kind") or "wiki",
        content=body_text.strip(),
        wiki_url=wiki_url,
        pdf_url=fm.get("pdf_url"),
        pdf_text_url=fm.get("pdf_text_url"),
    )


def upsert_to_vector_store(client, vector_store_id: str, doc: WikiDocument) -> None:
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
    ext = ".txt" if doc.source_type == "pdf_text" else ".md"
    file_name = f"{safe_path}{ext}"
    file_bytes = doc.content.encode("utf-8")

    try:
        client.vector_stores.files.upload(
            vector_store_id=vector_store_id,
            file={"name": file_name, "contents": file_bytes},
            metadata=metadata,
        )
    except (AttributeError, TypeError):
        upload = client.files.create(file=(file_name, file_bytes), purpose="assistants")
        try:
            client.vector_stores.file_batches.create(
                vector_store_id=vector_store_id, file_ids=[upload.id], metadata=metadata,
            )
        except TypeError:
            client.vector_stores.file_batches.create(
                vector_store_id=vector_store_id, file_ids=[upload.id],
            )


# ---------------------------------------------------------------------------
# Ingest subprocess calls
# ---------------------------------------------------------------------------

def run_preview(pdf_path: Path, wiki_root: Path) -> dict:
    """Call ingest_paper.py --dry-run --print-json to get structured summary."""
    cmd = [
        sys.executable, str(INGEST_SCRIPT), str(pdf_path),
        "--dry-run", "--print-json",
        "--base-dir", str(wiki_root / "__preview__"),
        "--wiki-root", str(wiki_root),
        "--log-file", "-", "--stdout-log-file", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"Preview failed (exit {proc.returncode}): {proc.stderr[:500]}")

    # Extract JSON from stdout
    decoder = json.JSONDecoder()
    idx = proc.stdout.find("{")
    while idx != -1:
        try:
            obj, _ = decoder.raw_decode(proc.stdout[idx:])
            return obj
        except json.JSONDecodeError:
            idx = proc.stdout.find("{", idx + 1)
    raise RuntimeError("Could not parse summary JSON from preview output.")


def run_ingest(
    pdf_path: Path, wiki_root: Path, directory: str,
    pdf_upload: Optional[str], pdf_url_base: Optional[str],
) -> Path:
    """Call ingest_paper.py for real, returning the written markdown path."""
    target_dir = wiki_root / directory
    target_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable, str(INGEST_SCRIPT), str(pdf_path),
        "--base-dir", str(target_dir),
        "--wiki-root", str(wiki_root),
        "--pdf-text-dir", str(PDF_TEXT_DIR),
        "--pdf-raw-renamed-dir", str(PDF_RAW_DIR),
    ]
    if pdf_upload:
        cmd.extend(["--pdf-upload", pdf_upload])
    if pdf_url_base:
        cmd.extend(["--pdf-url-base", pdf_url_base])

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.stdout:
        print(proc.stdout, end="")
    if proc.returncode != 0:
        raise RuntimeError(f"Ingest failed (exit {proc.returncode}): {proc.stderr[:500]}")

    match = WROTE_RE.search(proc.stdout)
    if not match:
        raise RuntimeError("Could not find markdown path in ingest output.")
    return Path(match.group(1)).expanduser().resolve()


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def classify_document(
    client, vector_store_id: str, wiki_name: str,
    summary: dict, directories: List[str], tags: List[str],
    model: str, max_tags: int,
) -> Tuple[str, List[str]]:
    """Ask LLM to pick the best directory and tags for a paper."""
    dir_blob = "\n".join(f"- {d}" for d in directories)
    tag_blob = ", ".join(tags)
    key_points = summary.get("key_points") or []
    kp_blob = "\n".join(f"- {kp}" for kp in key_points) if key_points else "- n/a"

    user_prompt = f"""Allowed directories (choose exactly one):
{dir_blob}

Allowed tags (choose up to {max_tags}):
{tag_blob}

Paper summary:
Title: {summary.get('title')}
One sentence takeaway: {summary.get('one_sentence_takeaway')}
Existing tags: {', '.join(summary.get('tags', []))}
Key points:
{kp_blob}
"""

    resp = client.responses.create(
        model=model,
        input=[
            {
                "role": "system",
                "content": (
                    f"You assign new papers to existing hubs in the '{wiki_name}' wiki."
                    ' Respond with STRICT JSON: {"directory": "path", "tags": ["tag1", ...]}'
                    " Use only the provided directories/tags. Never invent new values."
                ),
            },
            {"role": "user", "content": user_prompt},
        ],
        tools=[{"type": "file_search", "vector_store_ids": [vector_store_id]}],
    )
    raw = resp.output[0].content[0].text
    data = json.loads(raw)

    directory = data.get("directory", "").strip().strip("/")
    if directory not in directories:
        raise ValueError(f"Classifier chose unknown directory '{directory}'")

    chosen_tags = []
    for tag in data.get("tags", []):
        tag = str(tag).strip()
        if tag and tag in tags and tag not in chosen_tags:
            chosen_tags.append(tag)
        if len(chosen_tags) >= max_tags:
            break

    return directory, chosen_tags


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Quick-ingest: summarize, classify, file, and vectorize PDFs.",
    )
    parser.add_argument("input", type=Path, help="PDF file or directory of PDFs.")
    parser.add_argument("--wiki-root", type=Path, default=None, help="Wiki content repo root.")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH, help="Path to config.yaml.")
    parser.add_argument("--model", default="gpt-5.1", help="OpenAI model for classification.")
    parser.add_argument("--max-tags", type=int, default=5, help="Max tags per document.")
    parser.add_argument("--dry-run", action="store_true", help="Preview classification only.")
    parser.add_argument("--git-commit", action="store_true", help="Auto git add/commit/push after.")
    parser.add_argument("--deduplicate", action="store_true", help="Skip if vector store similarity > 0.9.")
    parser.add_argument("--no-vector-store", action="store_true", help="Skip vector store upsert.")
    parser.add_argument("--no-scp", action="store_true", help="Skip PDF upload via SCP.")
    parser.add_argument("--checksum-log", type=Path, default=CHECKSUM_LOG_PATH)
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def main() -> None:
    load_env()
    args = parse_args()

    # Config
    config = load_config(args.config)
    wiki_name = (os.getenv("WIKI_NAME") or config.get("wiki_name") or "my-wiki").strip()
    domain = (os.getenv("DOMAIN") or "").strip()

    wiki_root = args.wiki_root
    if not wiki_root:
        wiki_root = Path(config.get("wiki_repo_root") or os.getenv("WIKI_REPO_ROOT", ""))
    wiki_root = wiki_root.expanduser().resolve()
    if not wiki_root.exists():
        raise SystemExit(f"Wiki root not found: {wiki_root}")

    base_url = (
        os.getenv("WIKI_BASE_URL")
        or config.get("wiki_base_url")
        or f"https://{domain}" if domain else "https://example.com"
    ).rstrip("/")

    openai_cfg = config.get("openai") or {}
    vector_store_id = (os.getenv("OPENAI_VECTOR_STORE_ID") or openai_cfg.get("vector_store_id") or "").strip()

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("OPENAI_API_KEY not set. Check your .env file.")

    if not args.no_vector_store and (not vector_store_id or vector_store_id == "vs_TBD"):
        raise SystemExit("Vector store ID missing. Set it in config.yaml or OPENAI_VECTOR_STORE_ID env var.")

    # SCP defaults
    pdf_upload = None if args.no_scp else (
        os.getenv("PDF_UPLOAD_TARGET") or (f"root@{domain}:/opt/{wiki_name}-pdfs" if domain else None)
    )
    pdf_url_base = None if args.no_scp else (
        os.getenv("PDF_URL_BASE") or (f"https://{domain}/pdfs" if domain else None)
    )

    # OpenAI client (only needed for classification + vector store)
    from openai import OpenAI
    oai_client = OpenAI(api_key=api_key)

    # Discover PDFs
    input_path = args.input.expanduser().resolve()
    if input_path.is_file():
        pdfs = [input_path]
    elif input_path.is_dir():
        pdfs = sorted(p for p in input_path.rglob("*.pdf") if p.is_file())
    else:
        raise SystemExit(f"Input not found: {input_path}")

    if not pdfs:
        print("No PDFs found.")
        return

    # Checksum dedup
    checksums = load_checksum_log(args.checksum_log)
    known_values = set(checksums.values())
    new_pdfs = []
    for pdf in pdfs:
        cs = compute_checksum(pdf)
        if cs in known_values:
            if args.verbose:
                print(f"  SKIP (already ingested): {pdf.name}")
            continue
        new_pdfs.append((pdf, cs))

    if not new_pdfs:
        print("All PDFs already ingested (checksum match).")
        return

    print(f"Processing {len(new_pdfs)} new PDF(s)...")

    # Resolve directories and tags
    dirs, tags = load_organizer_constraints(wiki_name)
    if not dirs:
        print("No organizer plan found; scanning filesystem for directories...")
        dirs = gather_directories(wiki_root)
    if not tags:
        print("No organizer tags found; scanning front matter for tags...")
        tags = gather_tags(wiki_root)

    if not dirs:
        raise SystemExit("No directories found. Run the organizer pipeline first or create hub directories.")
    if not tags:
        raise SystemExit("No tags found. Run plan_tags.py first or add tags to wiki front matter.")

    print(f"Using {len(dirs)} directories and {len(tags)} tags for classification.")

    # Process each PDF
    succeeded = 0
    failed = 0
    for i, (pdf_path, checksum) in enumerate(new_pdfs):
        print(f"\n[{i+1}/{len(new_pdfs)}] {pdf_path.name}")
        try:
            # 1. Preview (get summary)
            print("  Summarizing...", flush=True)
            summary = run_preview(pdf_path, wiki_root)
            title = summary.get("title", "Unknown")
            print(f"  Title: {title}")

            # 2. Classify
            print("  Classifying...", flush=True)
            directory, chosen_tags = classify_document(
                oai_client, vector_store_id, wiki_name,
                summary, dirs, tags, args.model, args.max_tags,
            )
            print(f"  -> {directory}")
            print(f"  -> tags: {', '.join(chosen_tags)}")

            if args.dry_run:
                print("  (dry run — skipping ingest/upload)")
                continue

            # 3. Ingest (write markdown + SCP upload)
            print("  Ingesting...", flush=True)
            md_path = run_ingest(pdf_path, wiki_root, directory, pdf_upload, pdf_url_base)
            print(f"  Wrote: {md_path.relative_to(wiki_root)}")

            # 4. Update front matter
            text = md_path.read_text(encoding="utf-8")
            fm, body = parse_front_matter(text)
            slug = fm.get("slug") or md_path.stem
            fm["slug"] = slug
            fm["path"] = f"{directory}/{slug}"
            if chosen_tags:
                fm["tags"] = ", ".join(chosen_tags)
            md_path.write_text(dump_front_matter(fm, body), encoding="utf-8")

            # 5. Vector store upsert
            if not args.no_vector_store:
                print("  Upserting to vector store...", flush=True)
                wiki_doc = build_wiki_document(md_path, fm, base_url, wiki_root)
                upsert_to_vector_store(oai_client, vector_store_id, wiki_doc)

                # Also upsert PDF text if available
                pdf_text_url = fm.get("pdf_text_url")
                if pdf_text_url:
                    pdf_text_path = PDF_TEXT_DIR / Path(pdf_text_url).name
                    if pdf_text_path.exists():
                        pdf_text = pdf_text_path.read_text(encoding="utf-8", errors="ignore").strip()
                        if pdf_text:
                            pdf_doc = WikiDocument(
                                path=f"{wiki_doc.path}::pdf",
                                url=wiki_doc.pdf_url or wiki_doc.url,
                                title=f"{wiki_doc.title} (Full PDF Text)",
                                tags=wiki_doc.tags, year=wiki_doc.year,
                                source_type="pdf_text", content=pdf_text,
                                wiki_url=wiki_doc.wiki_url,
                                pdf_url=wiki_doc.pdf_url,
                                pdf_text_url=wiki_doc.pdf_text_url,
                            )
                            upsert_to_vector_store(oai_client, vector_store_id, pdf_doc)

            # 6. Record checksum
            append_checksum(args.checksum_log, pdf_path, checksum)
            succeeded += 1
            print("  Done.")

        except Exception as exc:
            failed += 1
            print(f"  ERROR: {exc}")

    print(f"\nComplete: {succeeded} succeeded, {failed} failed out of {len(new_pdfs)}.")

    # Git commit
    if args.git_commit and not args.dry_run and succeeded > 0:
        print("Running git sync...")
        try:
            subprocess.run(["git", "-C", str(wiki_root), "add", "-A"], check=True)
            subprocess.run(
                ["git", "-C", str(wiki_root), "commit", "-m",
                 f"quick-ingest: add {succeeded} paper(s)"],
                check=True,
            )
            subprocess.run(["git", "-C", str(wiki_root), "push"], check=True)
            print("Git push complete.")
        except subprocess.CalledProcessError as exc:
            print(f"Git failed: {exc}")


if __name__ == "__main__":
    main()
