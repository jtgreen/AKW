#!/usr/bin/env python3
"""Stage 2b: design hub/directory layout using curated tags.

Uses a two-phase approach:
  Phase 1: Design hub structure (single lightweight LLM call).
  Phase 2: Assign docs to hubs in batches (respects --max-batch-docs).
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path
from typing import Dict, List, Tuple, Set

from dotenv import load_dotenv
from openai import OpenAI
import yaml

DEFAULT_WIKI_NAME = (os.getenv("WIKI_NAME") or "my-wiki").strip()
DEFAULT_REPO_ROOT = Path(f"/opt/{DEFAULT_WIKI_NAME}-data/repo")
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent
DEFAULT_MAX_CATALOG_BYTES = 900_000
DEFAULT_MAX_BATCH_DOCS = 150
IGNORE_FILE = Path(__file__).resolve().parents[1] / "organizer.ignore.json"
SYSTEM_PROMPT_TEMPLATE = """You are a wiki information architect.

You are given:
- A curated tag taxonomy (already normalized and limited).
- A list of documents with summaries, key points, and their assigned tags.

Design a new directory tree under these constraints:
- Define at most {max_hubs} top-level hubs (directories).
- Directory depth beneath each hub must be ≤ {max_depth} (excluding the hub itself).
- Use hub IDs that are safe for directories (lowercase, underscores).
- Iterate at least three times to refine and improve the hub structure.
- The hub structure should be the most effective way of organizing the information.
- Feel free to use information density measurements to guide your design.

For every document:
- Assign it to one or more hubs (by ID).
- Propose a new POSIX path that respects the depth limit.
- Reuse the supplied tags verbatim; do NOT invent new tags.

Output STRICT JSON with keys:
- "hubs": list of {{"id", "title", "description"}}
- "docs": list of {{
    "old_path",
    "new_path",
    "hub_ids",
    "old_tags",
    "new_tags"
  }}

Do not invent content; reason only from the provided summaries, key points, and curated tags. Aim for concise, semantically meaningful directory names that capture the dominant themes."""

HUB_DESIGN_SYSTEM_PROMPT = """You are a wiki information architect.

Given a curated tag taxonomy and a list of document titles with their assigned tags,
design a directory tree. Output STRICT JSON with a single key:
- "hubs": list of {{"id", "title", "description", "subdirs": ["subdir_name", ...]}}

Constraints:
- At most {max_hubs} top-level hubs.
- Directory depth beneath each hub must be ≤ {max_depth}.
- Hub IDs and subdir names must be lowercase with underscores only.
- Iterate at least three times to refine the hub structure.
- The hub structure should be the most effective way of organizing the information.
"""

DOC_ASSIGN_SYSTEM_PROMPT = """You are a wiki information architect.

You are given a fixed hub structure and a batch of documents. For each document,
assign it to one or more hubs and propose a new POSIX path using ONLY the provided
hub IDs and their subdirectories. Reuse tags verbatim; do NOT invent new tags.

Output STRICT JSON with a single key:
- "docs": list of {{
    "old_path",
    "new_path",
    "hub_ids",
    "new_tags"
  }}

The new_path MUST start with a valid hub ID and use only subdirs listed in the hub structure.
You MUST return exactly one entry per input document. Do not skip any."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Use an LLM to propose hubs/directory layout using curated tags."
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
        "--tag-plan-path",
        type=Path,
        default=None,
        help="Path to the curated tag plan JSON (defaults to organizer/_organizer_tags_<repo>.json).",
    )
    parser.add_argument(
        "--plan-path",
        type=Path,
        default=None,
        help="Where to write the directory plan JSON (defaults to organizer/_organizer_plan_<repo>.json).",
    )
    parser.add_argument(
        "--model",
        default="gpt-5.1",
        help="OpenAI model to call for the planning pass.",
    )
    parser.add_argument(
        "--max-catalog-bytes",
        type=int,
        default=DEFAULT_MAX_CATALOG_BYTES,
        help="Warn if the catalog exceeds this many bytes (no truncation by default).",
    )
    parser.add_argument(
        "--max-hubs",
        type=int,
        default=5,
        help="Maximum number of hubs the planner can propose.",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=3,
        help="Maximum directory depth (beneath each hub) allowed in new paths.",
    )
    parser.add_argument(
        "--no-stream",
        action="store_true",
        help="Disable streaming output from OpenAI (default streams incremental text).",
    )
    parser.add_argument(
        "--print-only",
        action="store_true",
        help="Skip the LLM call and just pretty-print the hubs/tags from an existing plan.",
    )
    parser.add_argument(
        "--max-batch-chars",
        type=int,
        default=700_000,
        help="Max JSON chars per LLM batch (default 700000, ~175k tokens).",
    )
    parser.add_argument(
        "--max-batch-docs",
        type=int,
        default=DEFAULT_MAX_BATCH_DOCS,
        help="Max docs per LLM batch (default 150). Limits output size to avoid truncation.",
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


def derive_default_tag_plan_path(repo_root: Path) -> Path:
    slug = repo_slug(repo_root)
    return (DEFAULT_OUTPUT_DIR / f"_organizer_tags_{slug}.json").resolve()


def derive_default_plan_path(repo_root: Path) -> Path:
    slug = repo_slug(repo_root)
    return (DEFAULT_OUTPUT_DIR / f"_organizer_plan_{slug}.json").resolve()


def load_catalog(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    return json.loads(text)


def load_tag_plan(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    return json.loads(text)


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


def batch_docs(docs: list[dict], max_batch_chars: int, max_batch_docs: int) -> list[list[dict]]:
    """Split docs into batches respecting both char and doc-count limits."""
    batches: list[list[dict]] = []
    current: list[dict] = []
    current_len = 2  # opening "[]"
    for doc in docs:
        doc_len = len(json.dumps(doc, ensure_ascii=False)) + 2
        chars_exceeded = current and current_len + doc_len > max_batch_chars
        count_exceeded = len(current) >= max_batch_docs
        if current and (chars_exceeded or count_exceeded):
            batches.append(current)
            current = [doc]
            current_len = 2 + doc_len
        else:
            current.append(doc)
            current_len += doc_len
    if current:
        batches.append(current)
    return batches


def prepare_docs_and_taxonomy(
    catalog: dict, tag_plan: dict, ignore_set: Set[str]
) -> Tuple[list[dict], list[dict], Dict[str, List[str]], Dict[str, List[str]]]:
    """Return (tag_taxonomy, docs_payload, tag_lookup, original_tags) as structured data."""
    tag_lookup = {doc["path"]: doc.get("new_tags", []) for doc in tag_plan.get("docs", [])}
    original_tags = {doc["path"]: doc.get("tags") or [] for doc in catalog.get("docs", [])}
    docs_payload = []
    missing = []
    for doc in catalog.get("docs", []):
        path = doc.get("path")
        if path in ignore_set or Path(path).name in ignore_set:
            continue
        tags = tag_lookup.get(path)
        if tags is None:
            tags = []
            missing.append(path)
        docs_payload.append(
            {
                "path": path,
                "title": doc.get("title"),
                "one_sentence_takeaway": doc.get("one_sentence_takeaway"),
                "key_points": doc.get("key_points"),
                "tags": tags,
            }
        )
    if missing:
        print(f"Warning: {len(missing)} docs missing curated tags (falling back to empty tags).")
    tag_taxonomy = tag_plan.get("tags", [])
    return tag_taxonomy, docs_payload, tag_lookup, original_tags


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


def call_model(
    client: OpenAI,
    model: str,
    system_prompt: str,
    payload_json: str,
    stream: bool,
) -> dict:
    request = dict(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": payload_json},
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


def summarize_tags(docs: list[dict]) -> Tuple[int, List[Tuple[str, int]]]:
    old_tags = Counter()
    new_tags = Counter()
    for doc in docs:
        for tag in doc.get("old_tags") or []:
            tag_str = str(tag).strip().lower()
            if tag_str:
                old_tags[tag_str] += 1
        for tag in doc.get("new_tags") or []:
            tag_str = str(tag).strip().lower()
            if tag_str:
                new_tags[tag_str] += 1
    sorted_new = sorted(new_tags.items(), key=lambda item: (-item[1], item[0]))
    return len(old_tags), sorted_new


def print_structure(plan: dict) -> None:
    hubs = plan.get("hubs") or []
    docs = plan.get("docs") or []
    meta = plan.get("meta", {})

    old_tag_count, tag_counts = summarize_tags(docs)
    new_tag_count = len(tag_counts)

    print("\nPlan summary:")
    print(f"  Hubs: {len(hubs)}")
    print(f"  Docs: {len(docs)}")
    original = meta.get("original_tag_count")
    if original is not None:
        print(f"  Unique tags before pruning: {original}")
    else:
        print(f"  Unique tags before pruning: {old_tag_count}")
    print(f"  Unique tags after pruning:  {new_tag_count}")

    if tag_counts:
        print("  Tags after pruning (sorted by doc count):")
        for tag, count in tag_counts:
            print(f"    {tag}:{count}")
    else:
        print("  Tags after pruning: (none)")

    print("\nHub directory layout (directories only):")
    for hub in hubs:
        hub_id = hub.get("id", "unknown_hub")
        title = hub.get("title") or hub_id
        desc = hub.get("description") or ""
        print(f"- {hub_id} ({title})")
        if desc:
            print(f"    {desc}")
        hub_docs = [doc for doc in docs if hub_id in (doc.get("hub_ids") or [])]
        if not hub_docs:
            print("    (no docs assigned)")
            continue
        directories = set()
        for doc in hub_docs:
            candidate = doc.get("new_path") or doc.get("old_path") or ""
            parts = candidate.split("/")
            if len(parts) > 1:
                directories.add("/".join(parts[:-1]))
            else:
                directories.add(".")
        for subdir in sorted(directories):
            print(f"    - {subdir}")


def main() -> None:
    args = parse_args()
    repo_root = load_repo_root(args.repo_root)
    catalog_path = (
        args.catalog_path.expanduser().resolve()
        if args.catalog_path
        else derive_default_catalog_path(repo_root)
    )
    tag_plan_path = (
        args.tag_plan_path.expanduser().resolve()
        if args.tag_plan_path
        else derive_default_tag_plan_path(repo_root)
    )
    plan_path = (
        args.plan_path.expanduser().resolve()
        if args.plan_path
        else derive_default_plan_path(repo_root)
    )

    print(
        "Stage 2b inputs:\n"
        f"  repo_root    : {repo_root}\n"
        f"  catalog_path : {catalog_path}\n"
        f"  tag_plan_path: {tag_plan_path}\n"
        f"  plan_path    : {plan_path}\n",
        flush=True,
    )

    if args.print_only:
        if not plan_path.exists():
            raise SystemExit(f"Plan not found: {plan_path}")
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        print_structure(plan)
        return

    if not catalog_path.exists():
        raise SystemExit(
            f"Catalog not found: {catalog_path}\n"
            "Use --catalog-path to point at the correct _organizer_catalog_<repo>.json."
        )
    if not tag_plan_path.exists():
        raise SystemExit(
            f"Tag plan not found: {tag_plan_path}\n"
            "Use --tag-plan-path to point at the _organizer_tags_<repo>.json generated by plan_tags.py."
        )
    if not repo_root.exists():
        raise SystemExit(f"Repo root does not exist: {repo_root}")

    catalog_text = catalog_path.read_text(encoding="utf-8")
    size_bytes = len(catalog_text.encode("utf-8"))
    if args.max_catalog_bytes and size_bytes > args.max_catalog_bytes:
        print(
            f"Warning: catalog is {size_bytes:,} bytes (> {args.max_catalog_bytes:,}). "
            "Using batched mode to stay within context limits.",
            flush=True,
        )

    catalog = json.loads(catalog_text)
    tag_plan = load_tag_plan(tag_plan_path)
    ignore_set = load_ignore_list()
    tag_taxonomy, docs_payload, tag_lookup, original_tags = prepare_docs_and_taxonomy(
        catalog, tag_plan, ignore_set
    )

    client = get_openai_client()

    # --- Phase 1: Design hub structure using lightweight doc summaries ---
    print("Phase 1: Designing hub structure...", flush=True)
    lightweight_docs = [
        {"path": d["path"], "title": d["title"], "tags": d["tags"]}
        for d in docs_payload
    ]
    phase1_payload = json.dumps(
        {"tag_taxonomy": tag_taxonomy, "docs": lightweight_docs},
        ensure_ascii=False,
    )
    print(f"  Phase 1 payload: {len(phase1_payload):,} chars ({len(lightweight_docs)} docs)")

    hub_system_prompt = HUB_DESIGN_SYSTEM_PROMPT.format(
        max_hubs=args.max_hubs,
        max_depth=args.max_depth,
    )
    hub_result = call_model(
        client,
        args.model,
        hub_system_prompt,
        "Return ONLY valid JSON. Design the hub structure (with subdirs) for these documents.\n"
        f"{phase1_payload}\n\nReturn JSON with just the 'hubs' key.",
        stream=not args.no_stream,
    )
    hubs = hub_result.get("hubs", [])
    print(f"Phase 1 complete: {len(hubs)} hubs designed.\n")

    # --- Phase 2: Assign docs to hubs in batches ---
    batches = batch_docs(docs_payload, args.max_batch_chars, args.max_batch_docs)
    print(f"Phase 2: Assigning {len(docs_payload)} docs in {len(batches)} batch(es) "
          f"(max {args.max_batch_docs} docs per batch)...")

    doc_assign_prompt = DOC_ASSIGN_SYSTEM_PROMPT.format()
    all_doc_assignments: list[dict] = []

    for i, batch in enumerate(batches):
        print(f"\n--- Batch {i+1}/{len(batches)} ({len(batch)} docs) ---")
        batch_payload = json.dumps(
            {"hubs": hubs, "tag_taxonomy": tag_taxonomy, "docs": batch},
            ensure_ascii=False,
        )
        batch_result = call_model(
            client,
            args.model,
            doc_assign_prompt,
            f"Return ONLY valid JSON. There are {len(batch)} documents — "
            f"return exactly {len(batch)} entries in 'docs'.\n"
            f"Assign each document to hubs and propose new paths.\n"
            f"{batch_payload}\n\nReturn JSON with just the 'docs' key.",
            stream=not args.no_stream,
        )
        batch_docs_returned = batch_result.get("docs", [])
        all_doc_assignments.extend(batch_docs_returned)

        if len(batch_docs_returned) < len(batch):
            print(f"  WARNING: batch had {len(batch)} docs but LLM returned {len(batch_docs_returned)}. "
                  f"Missing {len(batch) - len(batch_docs_returned)} doc assignments.")

    plan = {"hubs": hubs, "docs": all_doc_assignments}

    if len(all_doc_assignments) < len(docs_payload):
        print(f"\nWARNING: Expected {len(docs_payload)} docs but got {len(all_doc_assignments)}. "
              f"{len(docs_payload) - len(all_doc_assignments)} docs have no hub assignments.")

    # Ensure final tags exactly match the curated tag plan
    for doc in plan.get("docs", []):
        lookup_key = doc.get("old_path")
        if lookup_key:
            if lookup_key in tag_lookup:
                doc["new_tags"] = tag_lookup[lookup_key]
            if lookup_key in original_tags:
                doc["old_tags"] = original_tags[lookup_key]

    plan.setdefault("meta", {})
    plan["meta"].update(
        {
            "repo_root": str(repo_root),
            "catalog_path": str(catalog_path),
            "tag_plan_path": str(tag_plan_path),
            "max_hubs": args.max_hubs,
            "max_depth": args.max_depth,
            "original_tag_count": tag_plan.get("meta", {}).get("original_tag_count"),
            "final_unique_tags": len(tag_plan.get("tags", [])),
            "batches": len(batches),
            "docs_returned": len(all_doc_assignments),
            "docs_expected": len(docs_payload),
        }
    )

    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    print(f"Wrote reorg plan to {plan_path}")
    print_structure(plan)


if __name__ == "__main__":
    main()
