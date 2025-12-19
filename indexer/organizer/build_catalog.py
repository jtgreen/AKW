#!/usr/bin/env python3
"""Build a machine-readable catalog of all wiki Markdown files."""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import date, datetime, time
from pathlib import Path
from typing import Iterable, Tuple, Set

import yaml

DEFAULT_REPO_ROOT = Path("/opt/bsos-wiki-data/repo")
FRONT_MATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent
IGNORE_FILE = Path(__file__).resolve().parents[1] / "organizer.ignore.json"
SUMMARY_RE = re.compile(r"\*\*One-sentence takeaway:\*\*\s*(.+)", re.IGNORECASE)
KEY_POINTS_HEADER_RE = re.compile(r"^#{2,6}\s+key points\s*$", re.IGNORECASE)


def load_ignore_list() -> Set[str]:
    if IGNORE_FILE.exists():
        try:
            data = json.loads(IGNORE_FILE.read_text(encoding="utf-8"))
            entries = data.get("ignore") or []
            normalized = {entry.strip() for entry in entries if entry and entry.strip()}
            return normalized
        except json.JSONDecodeError:
            print(f"Warning: {IGNORE_FILE} is not valid JSON; ignoring.")
    return set()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scan a wiki repo and emit an organizer catalog JSON."
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Path to the wiki repo root (overrides config/env defaults).",
    )
    parser.add_argument(
        "--catalog-path",
        type=Path,
        default=None,
        help="Where to write the catalog (defaults to knowledge-wiki-remote/indexer/organizer/_organizer_catalog_<repo>.json).",
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
        with config_path.open("r", encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
        repo_root = cfg.get("wiki_repo_root")
        if repo_root:
            return Path(repo_root).expanduser().resolve()

    return DEFAULT_REPO_ROOT


def parse_front_matter(text: str) -> Tuple[dict, str]:
    match = FRONT_MATTER_RE.match(text)
    if not match:
        return {}, text
    front_matter = yaml.safe_load(match.group(1)) or {}
    body = text[match.end() :]
    return front_matter, body


def normalize_tags(raw) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(tag).strip() for tag in raw if str(tag).strip()]
    if isinstance(raw, str):
        return [tag.strip() for tag in raw.split(",") if tag.strip()]
    return []


def walk_markdown_files(repo_root: Path, ignore_set: Set[str]) -> Iterable[Path]:
    for path in repo_root.rglob("*.md"):
        rel = path.relative_to(repo_root)
        top_level = rel.parts[0] if rel.parts else ""
        if top_level.startswith("_") or top_level.startswith("."):
            continue
        rel_posix = rel.as_posix()
        if rel_posix in ignore_set or path.name in ignore_set:
            continue
        yield path


def sanitize_for_json(value):
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): sanitize_for_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [sanitize_for_json(v) for v in value]
    return str(value)


def derive_default_catalog_path(repo_root: Path) -> Path:
    repo_slug = re.sub(r"[^A-Za-z0-9_-]+", "-", repo_root.resolve().name or "repo")
    out_path = DEFAULT_OUTPUT_DIR / f"_organizer_catalog_{repo_slug}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    return out_path


def extract_summary_and_key_points(body: str) -> Tuple[str, list[str]]:
    summary = ""
    key_points: list[str] = []
    lines = body.splitlines()
    for line in lines:
        match = SUMMARY_RE.search(line)
        if match:
            summary = match.group(1).strip()
            break

    capturing = False
    for line in lines:
        stripped = line.strip()
        if KEY_POINTS_HEADER_RE.match(stripped):
            capturing = True
            continue
        if capturing:
            if stripped.startswith("#"):
                break
            if stripped.startswith(("-", "*")):
                point = stripped.lstrip("-*").strip()
                if point:
                    key_points.append(point)
            elif not stripped:
                continue
            else:
                # treat plain text sentences as part of previous bullet if bullets missing
                if key_points:
                    key_points[-1] = f"{key_points[-1]} {stripped}"
                else:
                    key_points.append(stripped)
    return summary, key_points


def build_catalog(repo_root: Path) -> dict:
    ignore_set = load_ignore_list()
    docs = []
    for path in walk_markdown_files(repo_root, ignore_set):
        rel = path.relative_to(repo_root).as_posix()
        text = path.read_text(encoding="utf-8")
        front_matter, body = parse_front_matter(text)
        summary, key_points = extract_summary_and_key_points(body)
        title = front_matter.get("title") or Path(rel).stem
        tags = normalize_tags(front_matter.get("tags"))
        docs.append(
            {
                "path": rel,
                "title": title,
                "tags": tags,
                "raw_front_matter": sanitize_for_json(front_matter),
                "one_sentence_takeaway": summary,
                "key_points": key_points,
            }
        )
    return {"docs": docs}


def main() -> None:
    args = parse_args()
    repo_root = load_repo_root(args.repo_root)
    catalog_path = (
        args.catalog_path.expanduser().resolve()
        if args.catalog_path
        else derive_default_catalog_path(repo_root)
    )

    if not repo_root.exists():
        raise SystemExit(f"Repo root does not exist: {repo_root}")

    catalog = build_catalog(repo_root)
    catalog_path.write_text(json.dumps(catalog, indent=2), encoding="utf-8")
    print(f"Wrote catalog for {len(catalog['docs'])} docs to {catalog_path}")


if __name__ == "__main__":
    main()
