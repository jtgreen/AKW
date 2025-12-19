#!/usr/bin/env python3
"""Stage 3: apply the reorg plan by moving files and updating front matter."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

import yaml

DEFAULT_WIKI_NAME = (os.getenv("WIKI_NAME") or "my-wiki").strip()
DEFAULT_REPO_ROOT = Path(f"/opt/{DEFAULT_WIKI_NAME}-data/repo")
FRONT_MATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Apply a reorganization plan by moving Markdown files and updating tags."
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Path to the wiki repo root (overrides config/env defaults).",
    )
    parser.add_argument(
        "--plan-path",
        type=Path,
        default=None,
        help="Path to the plan JSON (defaults to organizer/_organizer_plan_<repo>.json).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually perform the moves/updates (default is dry-run).",
    )
    parser.add_argument(
        "--add-hub-ids",
        action="store_true",
        help="Write hub_ids from the plan into each file's front matter.",
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


def repo_slug(repo_root: Path) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in repo_root.name) or "repo"


def derive_default_plan_path(repo_root: Path) -> Path:
    slug = repo_slug(repo_root)
    return (DEFAULT_OUTPUT_DIR / f"_organizer_plan_{slug}.json").resolve()


def parse_front_matter(text: str) -> tuple[dict, str]:
    match = FRONT_MATTER_RE.match(text)
    if not match:
        return {}, text
    front_matter = yaml.safe_load(match.group(1)) or {}
    body = text[match.end() :]
    return front_matter, body


def dump_front_matter(front_matter: dict, body: str) -> str:
    fm_yaml = yaml.safe_dump(front_matter, sort_keys=False).strip()
    return f"---\n{fm_yaml}\n---\n\n{body.lstrip()}"


def ensure_doc_id(front_matter: dict, new_rel: str) -> None:
    if front_matter.get("doc_id"):
        return
    front_matter["doc_id"] = Path(new_rel).stem


def update_path_metadata(front_matter: dict, new_rel: str) -> None:
    rel_without_ext = Path(new_rel).with_suffix("").as_posix()
    front_matter["path"] = rel_without_ext
    # Keep existing slug if present; otherwise set to filename stem
    front_matter.setdefault("slug", Path(new_rel).stem)


def normalize_tags(tags) -> str:
    if not tags:
        return ""
    unique = []
    seen = set()
    for tag in tags:
        value = str(tag).strip()
        if not value or value.lower() in seen:
            continue
        seen.add(value.lower())
        unique.append(value)
    return ", ".join(unique)


def list_moves(plan_docs: list[dict]) -> list[tuple[str, str]]:
    return [(doc["old_path"], doc["new_path"]) for doc in plan_docs]


def apply_plan(
    repo_root: Path,
    plan_docs: list[dict],
    add_hub_ids: bool = False,
) -> None:
    for entry in plan_docs:
        old_rel = entry["old_path"]
        new_rel = entry["new_path"]
        new_tags = entry.get("new_tags") or []
        hub_ids = entry.get("hub_ids") or []

        src = repo_root / old_rel
        dst = repo_root / new_rel

        if not src.exists():
            raise FileNotFoundError(f"Source file not found: {src}")
        if dst.exists() and dst.resolve() != src.resolve():
            raise FileExistsError(f"Destination already exists: {dst}")

        dst.parent.mkdir(parents=True, exist_ok=True)
        text = src.read_text(encoding="utf-8")
        front_matter, body = parse_front_matter(text)

        ensure_doc_id(front_matter, new_rel)
        update_path_metadata(front_matter, new_rel)
        if new_tags:
            front_matter["tags"] = normalize_tags(new_tags)
        if add_hub_ids and hub_ids:
            front_matter["hub_ids"] = hub_ids

        new_text = dump_front_matter(front_matter, body)
        dst.write_text(new_text, encoding="utf-8")

        if dst.resolve() != src.resolve():
            src.unlink()


def main() -> None:
    args = parse_args()
    repo_root = load_repo_root(args.repo_root)
    plan_path = (
        args.plan_path.expanduser().resolve()
        if args.plan_path
        else derive_default_plan_path(repo_root)
    )

    if not plan_path.exists():
        raise SystemExit(f"Plan not found: {plan_path}")
    if not repo_root.exists():
        raise SystemExit(f"Repo root does not exist: {repo_root}")

    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    docs = plan.get("docs") or []
    moves = list_moves(docs)

    print(f"Plan: {len(plan.get('hubs', []))} hubs, {len(docs)} documents.")
    print(f"Repo root: {repo_root}")
    print(f"Plan path: {plan_path}")

    if not moves:
        print("No document entries found; nothing to do.")
        return

    preview_count = min(25, len(moves))
    print(f"\nFirst {preview_count} move(s):")
    for old_rel, new_rel in moves[:preview_count]:
        print(f"  {old_rel} -> {new_rel}")
    if len(moves) > preview_count:
        print(f"  ... and {len(moves) - preview_count} more")

    if not args.apply:
        print("\nDry run only. Re-run with --apply to perform the moves.")
        return

    print("\nApplying plan...")
    apply_plan(repo_root, docs, add_hub_ids=args.add_hub_ids)
    print("Done. Files moved and front matter updated.")


if __name__ == "__main__":
    main()
