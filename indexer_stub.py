#!/usr/bin/env python3
import os
from pathlib import Path
import re
import yaml

from dotenv import load_dotenv

# Load .env (OPENAI_API_KEY etc.)
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not OPENAI_API_KEY:
    raise SystemExit("OPENAI_API_KEY not set in environment/.env")

# Repo root from config
CONFIG_PATH = Path(__file__).parent / "config.yaml"
CONFIG = yaml.safe_load(CONFIG_PATH.read_text())
REPO_ROOT = Path(CONFIG["wiki_repo_root"])

FRONT_MATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def parse_front_matter(md_text: str):
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


def walk_markdown_files():
    for path in REPO_ROOT.rglob("*.md"):
        # Skip hubs/tags/etc for now if you like; adjust as needed
        if any(part in ("hubs", "tags") for part in path.relative_to(REPO_ROOT).parts[:1]):
            continue
        yield path


def main():
    print(f"Using wiki repo at: {REPO_ROOT}")
    if not REPO_ROOT.exists():
        raise SystemExit("Repo root does not exist!")

    count = 0
    for md_path in walk_markdown_files():
        rel = md_path.relative_to(REPO_ROOT)
        text = md_path.read_text(encoding="utf-8")
        fm, body = parse_front_matter(text)
        title = fm.get("title", rel.stem)
        tags_raw = fm.get("tags", "") or ""
        if isinstance(tags_raw, str):
            tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
        else:
            tags = list(tags_raw)  # handle list form if present

        print(f"- {rel} | title={title!r} | tags={tags}")
        count += 1

    print(f"\nScanned {count} markdown files.")


if __name__ == "__main__":
    main()
    