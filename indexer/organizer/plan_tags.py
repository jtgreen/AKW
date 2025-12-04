#!/usr/bin/env python3
"""Stage 2a: normalize/prune wiki tags before planning directories."""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Dict, List

from dotenv import load_dotenv
from openai import OpenAI
import yaml

DEFAULT_REPO_ROOT = Path("/opt/bsos-wiki-data/repo")
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent
DEFAULT_MAX_TAGS = 150
DEFAULT_MAX_TAGS_PER_DOC = 5
SYSTEM_PROMPT_TEMPLATE = """You are a taxonomy editor for the BSOS wiki.

Given a list of documents with existing tags, summaries, and key points, produce:
{{
  "tags": [
    {{"name": "snake_case_tag", "description": "concise description"}}
  ],
  "docs": [
    {{"path": "...", "new_tags": ["tag_one", "tag_two"]}}
  ]
}}

Rules:
- Use lowercase snake_case tag names (letters, numbers, underscores only).
- At most {max_tags_per_doc} tags per document.
- Reuse tags consistently; do not invent more than {max_tags} unique tags overall.
- Prefer meaningful, reusable concepts over hyper-specific phrases.
- Provide short descriptions (<=140 chars) explaining each tag.
- Do NOT include duplicate tags within a document.
- Use only information present in the supplied document data.
- The tag set should be defined by the total context of the documents.
- Iterate at least 3 times to refine and improve the tag set based on this global context.
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Use an LLM to normalize/prune wiki tags."
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
        help="Where to write the tag plan JSON (defaults to organizer/_organizer_tags_<repo>.json).",
    )
    parser.add_argument(
        "--model",
        default="gpt-5.1",
        help="OpenAI model to call for tag normalization.",
    )
    parser.add_argument(
        "--max-tags",
        type=int,
        default=DEFAULT_MAX_TAGS,
        help="Maximum number of distinct tags allowed after pruning.",
    )
    parser.add_argument(
        "--max-tags-per-doc",
        type=int,
        default=DEFAULT_MAX_TAGS_PER_DOC,
        help="Maximum number of tags per document.",
    )
    parser.add_argument(
        "--no-stream",
        action="store_true",
        help="Disable streaming output from OpenAI (default streams incremental text).",
    )
    parser.add_argument(
        "--print-only",
        action="store_true",
        help="Skip the LLM call and just summarize an existing tag plan.",
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
    return (DEFAULT_OUTPUT_DIR / f"_organizer_tags_{slug}.json").resolve()


def load_catalog(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    return json.loads(text)


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


def canonicalize_tag(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", str(name or "").lower()).strip("_")
    return slug[:80] if slug else ""


def dedupe(seq: List[str]) -> List[str]:
    seen = set()
    out = []
    for item in seq:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def prepare_docs_payload(catalog: dict) -> list[dict]:
    docs_payload = []
    for doc in catalog.get("docs", []):
        docs_payload.append(
            {
                "path": doc.get("path"),
                "title": doc.get("title"),
                "existing_tags": doc.get("tags"),
                "one_sentence_takeaway": doc.get("one_sentence_takeaway"),
                "key_points": doc.get("key_points"),
            }
        )
    return docs_payload


def call_model(client: OpenAI, model: str, prompt_docs: str, max_tags: int, max_tags_per_doc: int, stream: bool) -> dict:
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        max_tags=max_tags,
        max_tags_per_doc=max_tags_per_doc,
    )
    request = dict(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt_docs},
        ],
        response_format={"type": "json_object"},
    )
    if stream:
        print("Waiting for model response (streaming)...", flush=True)
        resp = client.chat.completions.create(**request, stream=True)
        fragments: list[str] = []
        for chunk in resp:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            content = getattr(delta, "content", None)
            if not content:
                continue
            text_piece = ""
            if isinstance(content, list):
                text_piece = "".join(
                    part.get("text", "") if isinstance(part, dict) else str(part) for part in content
                )
            elif isinstance(content, str):
                text_piece = content
            if text_piece:
                fragments.append(text_piece)
                print(text_piece, end="", flush=True)
        print("\nDone streaming.\n")
        content = "".join(fragments)
    else:
        print("Waiting for model response...", flush=True)
        resp = client.chat.completions.create(**request)
        message = resp.choices[0].message
        content = message.content
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in content
            )
    return json.loads(content)


def enforce_limits(
    plan: dict, max_tags: int, max_tags_per_doc: int
) -> tuple[int, int, int]:
    docs = plan.get("docs") or []
    tags = plan.get("tags") or []

    # Canonicalize tag definitions
    tag_defs: Dict[str, dict] = {}
    for tag in tags:
        slug = canonicalize_tag(tag.get("name"))
        if not slug:
            continue
        if slug not in tag_defs:
            tag_defs[slug] = {"name": slug, "description": tag.get("description", "")[:200]}

    # Clean doc tags
    for doc in docs:
        cleaned = []
        for tag in doc.get("new_tags") or []:
            slug = canonicalize_tag(tag)
            if slug:
                cleaned.append(slug)
        cleaned = dedupe(cleaned)[:max_tags_per_doc]
        doc["new_tags"] = cleaned
        for slug in cleaned:
            if slug not in tag_defs:
                tag_defs[slug] = {"name": slug, "description": ""}

    counts = Counter()
    for doc in docs:
        for tag in doc["new_tags"]:
            counts[tag] += 1

    original_unique = len(counts)
    allowed = [tag for tag, _ in counts.most_common(max_tags)]
    allowed_set = set(allowed)
    if len(counts) > max_tags:
        for doc in docs:
            doc["new_tags"] = [tag for tag in doc["new_tags"] if tag in allowed_set]

    pruned_counts = Counter()
    for doc in docs:
        for tag in doc["new_tags"]:
            pruned_counts[tag] += 1

    plan["tags"] = [
        {**tag_defs[tag], "count": pruned_counts.get(tag, 0)} for tag in allowed if tag in pruned_counts
    ]

    return original_unique, len(pruned_counts), sum(len(doc["new_tags"]) for doc in docs)


def summarize_plan(plan: dict) -> None:
    docs = plan.get("docs") or []
    tags = plan.get("tags") or []
    meta = plan.get("meta", {})
    counts = Counter()
    for doc in docs:
        for tag in doc.get("new_tags") or []:
            counts[tag] += 1

    print("\nTag plan summary:")
    print(f"  Docs: {len(docs)}")
    print(f"  Unique tags (after pruning): {len(counts)}")
    print(f"  Unique tags before pruning: {meta.get('original_tag_count', 'unknown')}")
    print(f"  Max tags per doc: {meta.get('max_tags_per_doc')}")
    print(f"  Max tags allowed: {meta.get('max_tags')}")

    if not counts:
        print("  (no tags assigned)")
        return

    print("\n  Tags (sorted by doc count):")
    for tag, count in counts.most_common():
        desc = ""
        for tag_def in tags:
            if tag_def.get("name") == tag:
                desc = tag_def.get("description", "")
                break
        desc_suffix = f" — {desc}" if desc else ""
        print(f"    {tag}:{count}{desc_suffix}")


def main() -> None:
    args = parse_args()
    repo_root = load_repo_root(args.repo_root)
    plan_path = (
        args.plan_path.expanduser().resolve()
        if args.plan_path
        else derive_default_plan_path(repo_root)
    )

    if args.print_only:
        if not plan_path.exists():
            raise SystemExit(f"Tag plan not found: {plan_path}")
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        summarize_plan(plan)
        return

    catalog_path = (
        args.catalog_path.expanduser().resolve()
        if args.catalog_path
        else derive_default_catalog_path(repo_root)
    )
    if not catalog_path.exists():
        raise SystemExit(f"Catalog not found: {catalog_path}")

    catalog = load_catalog(catalog_path)
    docs_payload = prepare_docs_payload(catalog)
    existing_tags = {
        canonicalize_tag(tag)
        for doc in catalog.get("docs", [])
        for tag in (doc.get("tags") or [])
        if canonicalize_tag(tag)
    }

    payload_text = json.dumps(docs_payload, ensure_ascii=False)
    client = get_openai_client()
    plan = call_model(
        client,
        args.model,
        "Return ONLY valid JSON. Here is the document catalog with existing tags:\n"
        f"{payload_text}\n\nRemember: respond with JSON only.",
        args.max_tags,
        args.max_tags_per_doc,
        stream=not args.no_stream,
    )

    original_unique, pruned_unique, total_assignments = enforce_limits(
        plan, args.max_tags, args.max_tags_per_doc
    )
    plan.setdefault("meta", {})
    plan["meta"].update(
        {
            "repo_root": str(repo_root),
            "catalog_path": str(catalog_path),
            "max_tags": args.max_tags,
            "max_tags_per_doc": args.max_tags_per_doc,
            "original_tag_count": len(existing_tags),
            "llm_initial_unique_tags": original_unique,
            "final_unique_tags": pruned_unique,
            "total_tag_assignments": total_assignments,
        }
    )

    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    print(f"Wrote tag plan to {plan_path}")
    summarize_plan(plan)


if __name__ == "__main__":
    main()
