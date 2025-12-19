#!/usr/bin/env python3
"""
One-off helper to repair front-matter titles and primary headings.

For every Markdown file in the wiki repo:
- Reads the first H1 heading (which currently reflects the accurate paper title).
- Writes that title back into front matter (title field).
- Rewrites the heading to the new convention: # "Title" (First Author, Journal, Year).

Run with --apply to actually rewrite files; default is a dry run.
"""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path
from typing import Tuple

import yaml

DEFAULT_REPO_ROOT = Path("/opt/bsos-wiki-data/repo")
FRONT_MATTER_RE = re.compile(r"^---\n(.*?)\n---\n?", re.DOTALL)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rewrite front-matter titles and H1 headings based on existing metadata."
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        help="Path to the wiki repo root (defaults to config or /opt/bsos-wiki-data/repo).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually rewrite files (default: dry run).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Stop after touching this many files (for testing).",
    )
    return parser.parse_args()


def load_repo_root(cli_value: Path | None) -> Path:
    if cli_value:
        return cli_value.expanduser().resolve()

    env_value = os.getenv("WIKI_REPO_ROOT")
    if env_value:
        return Path(env_value).expanduser().resolve()

    config_path = Path(__file__).resolve().parent.parent / "config.yaml"
    if config_path.exists():
        cfg = yaml.safe_load(config_path.read_text()) or {}
        repo_root = cfg.get("wiki_repo_root")
        if repo_root:
            return Path(repo_root).expanduser().resolve()

    return DEFAULT_REPO_ROOT


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


def extract_primary_author(authors: str) -> str:
    cleaned = str(authors or "").strip()
    if not cleaned:
        return ""
    separators = [";", " and ", " & ", ",", "|", "/"]
    for sep in separators:
        if sep in cleaned:
            return cleaned.split(sep)[0].strip()
    parts = cleaned.split()
    return parts[0].strip() if parts else ""


def format_primary_heading(title: str, authors: str, journal: str, year: str) -> str:
    base_title = (title or "").strip()
    if not base_title:
        base_title = "Untitled"
    normalized = base_title.strip().strip('"').strip()
    quoted = f"\"{normalized or base_title}\""

    meta_bits = []
    primary_author = extract_primary_author(authors)
    if primary_author:
        meta_bits.append(primary_author)
    journal_clean = str(journal or "").strip()
    if journal_clean:
        meta_bits.append(journal_clean)
    year_clean = str(year or "").strip()
    if year_clean:
        meta_bits.append(year_clean)

    if meta_bits:
        return f"# {quoted} ({', '.join(meta_bits)})"
    return f"# {quoted}"


def extract_title_from_heading(line: str) -> str:
    stripped = line.strip()
    if not stripped.startswith("#"):
        return ""
    heading_text = stripped.lstrip("#").strip()
    if not heading_text:
        return ""
    match = re.match(r'^"(?P<title>.+?)"(?:\s*\(.*\))?$', heading_text)
    if match:
        return match.group("title").strip()
    return heading_text.strip()


def find_primary_heading(body: str) -> Tuple[int | None, list[str]]:
    lines = body.splitlines()
    for idx, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("# "):
            return idx, lines
    return None, lines


def process_file(path: Path, apply_changes: bool) -> bool:
    text = path.read_text(encoding="utf-8")
    front_matter, body = parse_front_matter(text)
    heading_idx, lines = find_primary_heading(body)
    if heading_idx is None:
        return False

    original_heading = lines[heading_idx].strip()
    title_from_heading = extract_title_from_heading(original_heading)
    if not title_from_heading:
        return False

    authors = front_matter.get("authors") or ""
    journal = front_matter.get("journal") or ""
    year = front_matter.get("year") or ""

    changed = False
    if front_matter.get("title") != title_from_heading:
        front_matter["title"] = title_from_heading
        changed = True

    new_heading = format_primary_heading(title_from_heading, authors, journal, year)
    if original_heading != new_heading:
        lines[heading_idx] = new_heading
        changed = True

    if not changed:
        return False

    if apply_changes:
        new_body = "\n".join(lines)
        new_text = dump_front_matter(front_matter, new_body)
        path.write_text(new_text, encoding="utf-8")
        print(f"[FIXED] {path}")
    else:
        print(f"[DRY] {path} -> {new_heading}")
    return True


def main() -> None:
    args = parse_args()
    repo_root = load_repo_root(args.repo_root)
    if not repo_root.exists():
        raise SystemExit(f"Repo root not found: {repo_root}")

    changed = 0
    for idx, md_path in enumerate(repo_root.rglob("*.md"), start=1):
        rel = md_path.relative_to(repo_root)
        if rel.parts and rel.parts[0].startswith("_"):
            continue
        try:
            updated = process_file(md_path, args.apply)
        except Exception as exc:
            print(f"[ERROR] {md_path}: {exc}")
            continue
        if updated:
            changed += 1
            if args.limit and changed >= args.limit:
                break

    if args.apply:
        print(f"Updated {changed} file(s).")
    else:
        print(f"Dry run complete. {changed} file(s) would be updated.")


if __name__ == "__main__":
    main()
