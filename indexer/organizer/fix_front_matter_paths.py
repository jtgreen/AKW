#!/usr/bin/env python3
"""One-off helper to sync front-matter path/slug with actual file locations."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
from typing import Tuple

import yaml

DEFAULT_WIKI_NAME = (os.getenv("WIKI_NAME") or "my-wiki").strip()
DEFAULT_REPO_ROOT = Path(f"/opt/{DEFAULT_WIKI_NAME}-data/repo")
FRONT_MATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ensure every Markdown file's front matter path/slug matches its filesystem location."
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Wiki repo root (defaults to config.yaml → wiki_repo_root or /opt/<wiki-name>-data/repo).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually rewrite front matter (default: dry run).",
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


def iter_markdown_files(repo_root: Path):
    for path in repo_root.rglob("*.md"):
        rel = path.relative_to(repo_root)
        if not rel.parts:
            continue
        if rel.parts[0].startswith("_"):
            continue
        yield path, rel


def ensure_path_slug(front_matter: dict, rel_path: Path) -> tuple[str | None, str | None]:
    expected_path = rel_path.with_suffix("").as_posix()
    expected_slug = rel_path.stem
    current_path = front_matter.get("path")
    current_slug = front_matter.get("slug")

    updated_path = None
    updated_slug = None

    if current_path != expected_path:
        front_matter["path"] = expected_path
        updated_path = expected_path
    if current_slug != expected_slug:
        front_matter["slug"] = expected_slug
        updated_slug = expected_slug
    return updated_path, updated_slug


def main() -> None:
    args = parse_args()
    repo_root = load_repo_root(args.repo_root)
    if not repo_root.exists():
        raise SystemExit(f"Repo root does not exist: {repo_root}")

    print(f"Scanning {repo_root} for Markdown files...")
    updated_files = 0
    mismatches = 0

    for path, rel in iter_markdown_files(repo_root):
        text = path.read_text(encoding="utf-8")
        front_matter, body = parse_front_matter(text)
        new_path, new_slug = ensure_path_slug(front_matter, rel)

        if not new_path and not new_slug:
            continue

        mismatches += 1
        print(f"- {rel.as_posix()}")
        if new_path:
            print(f"    path -> {new_path}")
        if new_slug:
            print(f"    slug -> {new_slug}")

        if args.apply:
            new_text = dump_front_matter(front_matter, body)
            path.write_text(new_text, encoding="utf-8")
            updated_files += 1

    if not mismatches:
        print("All files already match their paths/slugs.")
    else:
        if args.apply:
            print(f"\nUpdated {updated_files} file(s).")
        else:
            print("\nDry run complete. Re-run with --apply to rewrite front matter.")


if __name__ == "__main__":
    main()
