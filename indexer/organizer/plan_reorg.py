#!/usr/bin/env python3
"""Stage 2: ask an LLM to draft a reorganization plan."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
import yaml

DEFAULT_REPO_ROOT = Path("/opt/bsos-wiki-data/repo")
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent
DEFAULT_MAX_CATALOG_BYTES = 900_000  # ≈225k tokens with generous headroom
SYSTEM_PROMPT = """You are a wiki information architect.

You are given a catalog of wiki documents (Markdown files) with their current paths, titles, and tags. The wiki is currently flat and disorganized.

Your job is to propose a new directory structure and hub layout that:

- Groups related documents into a small set of coherent "hubs".
- Assigns each document to one or more hubs.
- Proposes a new path for each document under those hubs.
- Cleans and normalizes tags (merge synonyms, remove noise, standardize casing).

You must output STRICT JSON with keys:
- "hubs": list of { "id", "title", "description" }
- "docs": list of {
    "old_path",
    "new_path",
    "hub_ids",
    "old_tags",
    "new_tags"
  }

Do not invent content; only reorganize. Use hub IDs that are safe as directory names (lowercase, underscores)."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Use an LLM to draft a reorg plan given a catalog JSON."
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
        help="Path to _organizer_catalog.json (defaults to organizer/_organizer_catalog_<repo>.json).",
    )
    parser.add_argument(
        "--plan-path",
        type=Path,
        default=None,
        help="Where to write the plan JSON (defaults to organizer/_organizer_plan_<repo>.json).",
    )
    parser.add_argument(
        "--model",
        default="gpt-5.1",
        help="OpenAI model to call for the planning pass (default: gpt-5.1).",
    )
    parser.add_argument(
        "--max-catalog-bytes",
        type=int,
        default=DEFAULT_MAX_CATALOG_BYTES,
        help="Soft cap on catalog size sent to the LLM (truncates with warning if exceeded).",
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


def derive_default_catalog_path(repo_root: Path) -> Path:
    slug = repo_slug(repo_root)
    return (DEFAULT_OUTPUT_DIR / f"_organizer_catalog_{slug}.json").resolve()


def derive_default_plan_path(repo_root: Path) -> Path:
    slug = repo_slug(repo_root)
    return (DEFAULT_OUTPUT_DIR / f"_organizer_plan_{slug}.json").resolve()


def load_catalog(path: Path, max_bytes: int | None = None) -> str:
    text = path.read_text(encoding="utf-8")
    if max_bytes and len(text.encode("utf-8")) > max_bytes:
        print(
            f"Warning: catalog {path} is {len(text):,} bytes; truncating to {max_bytes:,} bytes for prompt."
        )
        encoded = text.encode("utf-8")[:max_bytes]
        text = encoded.decode("utf-8", errors="ignore")
    return text


def get_openai_client() -> OpenAI:
    root = Path(__file__).resolve().parent.parent
    env_stack = root / "stack" / ".env"
    if env_stack.exists():
        load_dotenv(env_stack)
    else:
        load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("OPENAI_API_KEY is not set; cannot call OpenAI.")
    return OpenAI(api_key=api_key)


def call_model(client: OpenAI, model: str, catalog_json: str) -> dict:
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Here is the current catalog:\n{catalog_json}"},
        ],
        response_format={"type": "json_object"},
    )
    message = resp.choices[0].message
    content = message.content
    if isinstance(content, list):
        # SDK sometimes returns list of dicts; stitch together strings.
        content = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part) for part in content
        )
    plan = json.loads(content)
    return plan


def main() -> None:
    args = parse_args()
    repo_root = load_repo_root(args.repo_root)
    catalog_path = (
        args.catalog_path.expanduser().resolve()
        if args.catalog_path
        else derive_default_catalog_path(repo_root)
    )
    plan_path = (
        args.plan_path.expanduser().resolve()
        if args.plan_path
        else derive_default_plan_path(repo_root)
    )

    if not catalog_path.exists():
        raise SystemExit(f"Catalog not found: {catalog_path}")

    catalog_text = load_catalog(catalog_path, max_bytes=args.max_catalog_bytes)
    client = get_openai_client()
    plan = call_model(client, args.model, catalog_text)
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    hubs = len(plan.get("hubs", []))
    docs = len(plan.get("docs", []))
    print(f"Wrote reorg plan with {hubs} hub(s) and {docs} doc(s) to {plan_path}")


if __name__ == "__main__":
    main()
