#!/usr/bin/env python3
"""
Reset Wiki.js after a full reorganization.

Deletes all pages and tags from Wiki.js via the GraphQL API, then triggers
a git storage sync so Wiki.js re-imports everything from the repo cleanly.

Run this ON THE SERVER after:
  1. The wiki git repo has been updated (push from laptop or git pull on server)
  2. You want Wiki.js to reflect the new hub structure, tags, and paths

Usage:
  python3 reset_wikijs.py
  python3 reset_wikijs.py --dry-run          # list pages without deleting
  python3 reset_wikijs.py --skip-home        # keep home page (default: deletes all)
  python3 reset_wikijs.py --wiki-url https://aristotelian.ai --api-key "eyJ..."

Environment variables (alternative to CLI flags):
  WIKI_BASE_URL   - e.g. https://aristotelian.ai
  WIKIJS_API_KEY  - API key from Wiki.js Admin → API Access
  WIKI_NAME       - used to derive URL if WIKI_BASE_URL not set
  DOMAIN          - used to derive URL if WIKI_BASE_URL not set
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import List, Optional

try:
    import requests
except ImportError:
    print("requests not installed. Run: pip install requests")
    sys.exit(1)

# Try to load .env from common locations
try:
    from dotenv import load_dotenv
    wiki_name = (os.getenv("WIKI_NAME") or "my-wiki").strip()
    env_candidates = [
        Path(f"/opt/{wiki_name}-stack/.env"),
        Path(f"/opt/{wiki_name}-indexer/.env"),
        Path(__file__).resolve().parent.parent / "indexer" / ".env",
        Path(__file__).resolve().parent.parent / "indexer" / "client_ingest" / ".env",
    ]
    for env_path in env_candidates:
        if env_path.exists():
            load_dotenv(env_path, override=False)
except Exception:
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reset Wiki.js: delete all pages/tags, then trigger git re-sync.",
    )
    parser.add_argument("--wiki-url", type=str, default=None,
                        help="Wiki.js base URL (e.g. https://aristotelian.ai)")
    parser.add_argument("--api-key", type=str, default=None,
                        help="Wiki.js API key (from Admin → API Access)")
    parser.add_argument("--dry-run", action="store_true",
                        help="List pages without deleting")
    parser.add_argument("--skip-home", action="store_true",
                        help="Keep the home page")
    parser.add_argument("--batch-size", type=int, default=50,
                        help="Pages to delete per batch before pausing (default: 50)")
    parser.add_argument("--delay", type=float, default=0.1,
                        help="Seconds between delete calls (default: 0.1)")
    return parser.parse_args()


def resolve_wiki_url(cli_value: Optional[str]) -> str:
    if cli_value:
        return cli_value.rstrip("/")
    url = os.getenv("WIKI_BASE_URL", "").strip()
    if url:
        return url.rstrip("/")
    domain = os.getenv("DOMAIN", "").strip()
    if domain:
        return f"https://{domain}"
    raise SystemExit(
        "Wiki URL not set. Use --wiki-url, or set WIKI_BASE_URL or DOMAIN env var."
    )


def resolve_api_key(cli_value: Optional[str]) -> str:
    if cli_value:
        return cli_value
    key = os.getenv("WIKIJS_API_KEY", "").strip()
    if key:
        return key
    raise SystemExit(
        "API key not set. Use --api-key or set WIKIJS_API_KEY env var.\n"
        "Generate one at: Wiki.js Admin → API Access → New API Key"
    )


def graphql(url: str, api_key: str, query: str, variables: dict = None) -> dict:
    """Execute a GraphQL query/mutation against Wiki.js."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {"query": query}
    if variables:
        payload["variables"] = variables

    resp = requests.post(f"{url}/graphql", headers=headers, json=payload, timeout=30)
    if resp.status_code != 200:
        print(f"  HTTP {resp.status_code}: {resp.text[:500]}")
        resp.raise_for_status()
    data = resp.json()

    if "errors" in data:
        errors = json.dumps(data["errors"], indent=2)
        raise RuntimeError(f"GraphQL errors:\n{errors}")

    return data.get("data", {})


def list_all_pages(url: str, api_key: str) -> List[dict]:
    """Fetch all pages from Wiki.js."""
    query = """
    {
      pages {
        list {
          id
          path
          title
          tags {
            tag
          }
        }
      }
    }
    """
    data = graphql(url, api_key, query)
    return data.get("pages", {}).get("list", [])


def delete_page(url: str, api_key: str, page_id: int) -> bool:
    """Delete a single page by ID. Returns True on success."""
    query = """
    mutation ($id: Int!) {
      pages {
        delete(id: $id) {
          responseResult {
            succeeded
            message
          }
        }
      }
    }
    """
    data = graphql(url, api_key, query, {"id": page_id})
    result = data.get("pages", {}).get("delete", {}).get("responseResult", {})
    return result.get("succeeded", False)


def rebuild_search_index(url: str, api_key: str) -> bool:
    """Trigger a search index rebuild."""
    query = """
    mutation {
      search {
        rebuildIndex {
          responseResult {
            succeeded
            message
          }
        }
      }
    }
    """
    try:
        data = graphql(url, api_key, query)
        result = data.get("search", {}).get("rebuildIndex", {}).get("responseResult", {})
        return result.get("succeeded", False)
    except Exception as exc:
        print(f"  Warning: rebuild index failed: {exc}")
        return False


def main() -> None:
    args = parse_args()
    wiki_url = resolve_wiki_url(args.wiki_url)
    api_key = resolve_api_key(args.api_key)

    print(f"Wiki.js URL: {wiki_url}")
    print(f"Fetching page list...")

    pages = list_all_pages(wiki_url, api_key)
    print(f"Found {len(pages)} page(s).")

    if not pages:
        print("Nothing to delete.")
        return

    # Optionally skip home
    if args.skip_home:
        pages = [p for p in pages if p.get("path") not in ("home", "home.html")]
        print(f"After skipping home: {len(pages)} page(s) to delete.")

    if args.dry_run:
        print("\nDry run — pages that would be deleted:")
        for p in pages:
            tags = [t["tag"] for t in (p.get("tags") or [])]
            tag_str = f" [{', '.join(tags)}]" if tags else ""
            print(f"  [{p['id']}] {p['path']}{tag_str}")
        print(f"\nTotal: {len(pages)} pages. Re-run without --dry-run to delete.")
        return

    # Confirm
    print(f"\nAbout to DELETE {len(pages)} pages from {wiki_url}.")
    print("Wiki.js will re-import them from git on next sync.")
    confirm = input("Type 'yes' to proceed: ").strip().lower()
    if confirm != "yes":
        print("Aborted.")
        return

    # Delete pages
    deleted = 0
    failed = 0
    for i, page in enumerate(pages):
        page_id = page["id"]
        path = page.get("path", "?")
        try:
            success = delete_page(wiki_url, api_key, page_id)
            if success:
                deleted += 1
                if args.delay:
                    time.sleep(args.delay)
            else:
                failed += 1
                print(f"  Failed to delete [{page_id}] {path}")
        except Exception as exc:
            failed += 1
            print(f"  Error deleting [{page_id}] {path}: {exc}")

        # Progress
        if (i + 1) % args.batch_size == 0:
            print(f"  Progress: {i+1}/{len(pages)} ({deleted} deleted, {failed} failed)")

    print(f"\nDeletion complete: {deleted} deleted, {failed} failed.")

    # Rebuild search index
    print("Rebuilding search index...")
    if rebuild_search_index(wiki_url, api_key):
        print("Search index rebuild triggered.")
    else:
        print("Search index rebuild may have failed — check Wiki.js admin.")

    print(f"""
Next steps:
  1. Wiki.js will auto-sync from git within 5 minutes, or:
     - Go to Admin → Storage → Git → click "Force Sync"
  2. After sync completes, go to Admin → Utilities → Content → "Rerender All Pages"
  3. Verify pages and tags at {wiki_url}
""")


if __name__ == "__main__":
    main()
