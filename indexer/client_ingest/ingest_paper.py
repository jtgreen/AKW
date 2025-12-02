#!/usr/bin/env python3
"""
Single-PDF ingestion pipeline:
- Extracts text from a PDF.
- Summarizes it via OpenAI into structured JSON.
- Writes a Markdown page with front matter for Wiki.js/git-backed wiki content.
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
import subprocess
import tempfile
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List

import math
import posixpath
import re
import shlex

from openai import OpenAI
from pypdf import PdfReader
from pypdf.errors import PdfReadWarning


DEFAULT_MODEL = "gpt-5.1"
DEFAULT_DOC_KIND = "wiki"
CHARS_PER_TOKEN_ESTIMATE = 4  # OpenAI tokens are ~0.75 words (~4 chars) on average.
DEFAULT_MAX_CHARS = 900_000  # ≈225k tokens, leaving headroom under GPT-5.1's 256k context.
MAX_FIGURE_CAPTIONS = 12
MAX_FIGURE_CAPTION_CHARS = 800
MAX_FILENAME_COMPONENT_LENGTH = 48
PRICING_PER_1K_TOKENS = {
    "gpt-5.1": {"input": 0.010, "output": 0.030},
    "gpt-4.1": {"input": 0.005, "output": 0.015},
}
MAX_DESCRIPTION_CHARS = 200
FAILURE_PAYLOAD_START = "===INGEST_FAILURE_PAYLOAD_START==="
FAILURE_PAYLOAD_END = "===INGEST_FAILURE_PAYLOAD_END==="
PLACEHOLDER_TOKENS = [
    "paper text is missing",
    "no substantive summary",
    "not provided",
    "cannot be derived",
    "unknown study",
    "unknown title",
]
DEFAULT_LOG_FILE = "ingest.log"


class IngestLogger:
    def __init__(self, log_path: Path | None):
        self.log_path = log_path
        if self.log_path:
            self.log_path = self.log_path.expanduser()
            self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, message: str) -> None:
        timestamp = datetime.now().isoformat(timespec="seconds")
        line = f"[{timestamp}] {message}"
        print(line)
        if self.log_path:
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")

SUMMARY_PROMPT_TEMPLATE = """You are building a research wiki on hemorrhagic shock and cardiac failure.

Summarize this paper into a JSON object with fields:
- title
- short_title (for page title)
- year
- authors (string)
- journal (string; e.g., journal or publication venue)
- tags (list of strings)
- key_points (3-7 bullets)
- methods (2-4 bullets)
- findings (3-6 bullets)
- implications (2-4 bullets)
- one_sentence_takeaway

Return ONLY valid JSON, no commentary.

Paper text:
\"\"\"{paper_text}\"\"\"
"""

FIGURE_PROMPT_TEMPLATE = """You are assisting with a research wiki. Given figure labels and raw captions,
produce a concise 1-2 sentence summary for each figure, focusing on what the figure demonstrates.

Respond with JSON as:
{{
  "figures": [
    {{"label": "...", "summary": "..."}},
    ...
  ]
}}

Figure data:
{figure_json}
"""

FIGURE_REGEX = re.compile(
    r"(?P<label>(?:Figure|Fig\.?)\s+\d+[A-Za-z]?)"
    r"(?:\s*[:\-–—]\s*|\s+)"
    r"(?P<caption>.+?)(?=(?:\n{2,})|(?:\n\s*(?:Figure|Fig\.?)\s+\d)|$)",
    re.IGNORECASE | re.DOTALL,
)


class IngestFailure(Exception):
    def __init__(self, reason: str, detail: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail

def extract_text_from_pdf(path: Path, logger: IngestLogger) -> str:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", PdfReadWarning)
        reader = PdfReader(str(path))
    for warning_obj in caught:
        logger.log(f"pypdf warning: {warning_obj.message}")
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def estimate_tokens(char_len: int) -> int:
    return math.ceil(char_len / CHARS_PER_TOKEN_ESTIMATE)


def usage_to_dict(usage_obj: Any) -> Dict[str, int]:
    if not usage_obj:
        return {}
    if isinstance(usage_obj, dict):
        return {
            "prompt_tokens": int(usage_obj.get("prompt_tokens") or 0),
            "completion_tokens": int(usage_obj.get("completion_tokens") or 0),
            "total_tokens": int(usage_obj.get("total_tokens") or 0),
        }
    return {
        "prompt_tokens": int(getattr(usage_obj, "prompt_tokens", 0) or 0),
        "completion_tokens": int(getattr(usage_obj, "completion_tokens", 0) or 0),
        "total_tokens": int(getattr(usage_obj, "total_tokens", 0) or 0),
    }


def summarize_paper(raw_text: str, model: str, max_chars: int) -> tuple[Dict[str, Any], Dict[str, int]]:
    client = OpenAI()
    if len(raw_text) > max_chars:
        print(
            (
                f"Warning: extracted text is ~{estimate_tokens(len(raw_text)):,} tokens "
                f"({len(raw_text):,} chars), exceeding the {estimate_tokens(max_chars):,}-token "
                f"({max_chars:,} chars) cap; truncating before summarization."
            ),
            file=sys.stderr,
        )
    trimmed = raw_text[:max_chars]
    prompt = SUMMARY_PROMPT_TEMPLATE.format(paper_text=trimmed)

    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content
    return json.loads(content), usage_to_dict(getattr(response, "usage", None))


def bullets(items: Iterable[str]) -> str:
    collected = list(items)
    if not collected:
        return "- TBD"
    return "\n".join(f"- {item}" for item in collected)


def format_tags_for_front_matter(tags: Iterable[str]) -> str:
    cleaned = [str(tag).strip() for tag in tags if str(tag).strip()]
    tag_string = ", ".join(cleaned)
    return f"tags: {json.dumps(tag_string)}"


def truncate_description(text: str, limit: int = MAX_DESCRIPTION_CHARS) -> str:
    if not text:
        return ""
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def slugify_component(text: str, max_length: int, separator: str = "_") -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if not cleaned:
        return ""
    if len(cleaned) > max_length:
        truncated = cleaned[:max_length].rsplit(" ", 1)[0]
        cleaned = truncated if truncated else cleaned[:max_length]
    slug = re.sub(r"[^A-Za-z0-9]+", separator, cleaned).strip(separator)
    return slug[:max_length] if slug else ""


def derive_slug_from_title(title: str) -> str:
    slug = slugify_component(title, 120, separator="-").lower()
    return slug or "page"


def summary_looks_placeholder(summary: Dict[str, Any]) -> bool:
    fields = [
        summary.get("title") or "",
        summary.get("short_title") or "",
        summary.get("one_sentence_takeaway") or "",
        summary.get("description") or "",
    ]
    lower_fields = [str(value).lower() for value in fields]
    return any(token in field for field in lower_fields for token in PLACEHOLDER_TOKENS)


def normalize_path_value(path_value: str | None) -> str | None:
    if not path_value:
        return None
    segments: List[str] = []
    for segment in str(path_value).split("/"):
        cleaned = slugify_component(segment, 64, separator="-").lower()
        if cleaned:
            segments.append(cleaned)
    return "/".join(segments) or None


def extract_primary_author(authors: str) -> str:
    cleaned = str(authors or "").strip()
    if not cleaned:
        return ""
    separators = [";", " and ", " & ", ",", "|", "/"]
    for sep in separators:
        if sep in cleaned:
            return cleaned.split(sep)[0].strip()
    return cleaned.split()[0].strip() if cleaned.split() else cleaned


def build_pdf_filename(summary: Dict[str, Any], original_suffix: str) -> str:
    suffix = original_suffix if original_suffix.startswith(".") else f".{original_suffix.lstrip('.') or 'pdf'}"
    short_title = slugify_component(summary.get("short_title") or summary.get("title") or "paper", MAX_FILENAME_COMPONENT_LENGTH)
    primary_author = slugify_component(extract_primary_author(summary.get("authors")), 24)
    year = re.sub(r"\D", "", str(summary.get("year") or ""))[:4]
    journal = slugify_component(summary.get("journal"), 24)

    components = [short_title]
    if primary_author:
        components.append(primary_author)
    if year:
        components.append(year)
    if journal:
        components.append(journal)

    filename = "_".join(filter(None, components)) or "paper"
    return f"{filename[:120].rstrip('_') or 'paper'}{suffix}"


def compose_wiki_path(prefix: str | None, slug: str) -> str:
    clean_slug = (slug or "page").strip("/")
    parts = [p.strip("/") for p in (prefix, clean_slug) if p and p.strip("/")]
    path = "/".join(parts)
    return path or clean_slug or "page"


def normalize_doc_id(value: str | None) -> str:
    if not value:
        return "doc"
    return slugify_component(str(value), 160, separator="-").lower() or "doc"


def derive_doc_id(summary: Dict[str, Any], explicit: str | None = None) -> str:
    if explicit:
        return normalize_doc_id(explicit)
    title = str(summary.get("short_title") or summary.get("title") or "doc")
    slug = derive_slug_from_title(title)
    year_digits = re.sub(r"\D", "", str(summary.get("year") or ""))[:4]
    parts = [slug]
    if year_digits:
        parts.append(year_digits)
    return normalize_doc_id("-".join(filter(None, parts)))


def build_markdown(
    summary: Dict[str, Any],
    pdf_url: str | None = None,
    figure_summaries: List[Dict[str, str]] | None = None,
    slug: str | None = None,
    wiki_path: str | None = None,
    doc_id: str | None = None,
    kind: str = DEFAULT_DOC_KIND,
) -> str:
    title = str(summary.get("title") or summary.get("short_title") or "Untitled")
    short_title = str(summary.get("short_title") or title)
    year = summary.get("year") or ""
    authors = str(summary.get("authors") or "")
    tags_value = summary.get("tags") or []
    tags = tags_value if isinstance(tags_value, list) else [tags_value]
    one_sentence = summary.get("one_sentence_takeaway") or ""
    journal = str(summary.get("journal") or "")
    description = truncate_description(summary.get("description") or one_sentence or f"{authors} ({year})")

    key_points = bullets(summary.get("key_points") or [])
    methods = bullets(summary.get("methods") or [])
    findings = bullets(summary.get("findings") or [])
    implications = bullets(summary.get("implications") or [])

    doc_id_value = doc_id or derive_slug_from_title(short_title)
    front_matter_lines: List[str] = [
        "---",
        f"doc_id: {json.dumps(doc_id_value)}",
        f"kind: {json.dumps(kind)}",
        f"title: {json.dumps(short_title)}",
        f"description: {json.dumps(description)}",
        "published: true",
        "editor: markdown",
        f"year: {json.dumps(str(year))}",
        f"authors: {json.dumps(authors)}",
    ]
    front_matter_lines.append(format_tags_for_front_matter(tags))
    front_matter_lines.append('source_type: "paper"')
    if journal:
        front_matter_lines.append(f"journal: {json.dumps(journal)}")
    if wiki_path:
        front_matter_lines.append(f"path: {json.dumps(wiki_path)}")
    if slug:
        front_matter_lines.append(f"slug: {json.dumps(slug)}")
    if pdf_url:
        front_matter_lines.append(f"pdf_url: {json.dumps(pdf_url)}")
    front_matter_lines.append("---")
    front_matter = "\n".join(front_matter_lines)

    figure_lines = []
    if figure_summaries:
        figure_lines.append("## Key Figures")
        figure_lines.append(
            "\n".join(
                f"- **{entry.get('label', 'Figure')}**: {entry.get('summary', '').strip() or 'Summary unavailable.'}"
                for entry in figure_summaries
            )
        )

    body_lines = [
        f"# {title}",
        "",
        f"**One-sentence takeaway:** {one_sentence}",
    ]
    if pdf_url:
        body_lines.extend(["", f"**PDF:** [{Path(pdf_url).name}]({pdf_url})"])

    def add_section(header: str, content: str) -> None:
        body_lines.extend(["", header])
        content = content.strip() or "- TBD"
        body_lines.append(content)

    add_section("## Key Points", key_points)
    add_section("## Methods", methods)
    add_section("## Findings", findings)
    add_section("## Implications", implications)

    if figure_lines:
        body_lines.extend(["", figure_lines[0], figure_lines[1]])

    body_lines.extend(
        [
            "",
            "## Related Work",
            "<!-- Placeholder to be populated by the linking step -->",
        ]
    )
    body = "\n".join(body_lines)

    return front_matter + "\n" + body.strip() + "\n"


def emit_failure_payload(reason: str, detail: str | None = None) -> None:
    payload: Dict[str, Any] = {"reason": reason}
    if detail is not None:
        payload["detail"] = detail
    print(FAILURE_PAYLOAD_START)
    print(json.dumps(payload, ensure_ascii=False))
    print(FAILURE_PAYLOAD_END)


def write_markdown(
    summary: Dict[str, Any],
    base_dir: Path,
    pdf_url: str | None = None,
    figure_summaries: List[Dict[str, str]] | None = None,
    wiki_path_prefix: str | None = None,
    doc_id: str | None = None,
    kind: str = DEFAULT_DOC_KIND,
) -> Path:
    short_title = str(summary.get("short_title") or summary.get("title") or "Untitled")
    slug = derive_slug_from_title(short_title)
    file_name = f"{slug}.md"
    output_path = base_dir / file_name
    output_path.parent.mkdir(parents=True, exist_ok=True)
    wiki_path = compose_wiki_path(wiki_path_prefix, slug)
    markdown = build_markdown(
        summary,
        pdf_url=pdf_url,
        figure_summaries=figure_summaries,
        slug=slug,
        wiki_path=wiki_path,
        doc_id=doc_id,
        kind=kind,
    )
    output_path.write_text(markdown, encoding="utf-8")
    return output_path


def extract_figure_captions(raw_text: str) -> List[Dict[str, str]]:
    candidates: List[Dict[str, str]] = []
    for match in FIGURE_REGEX.finditer(raw_text):
        label = match.group("label").strip()
        caption = " ".join(match.group("caption").split())
        if not caption:
            continue
        candidates.append(
            {
                "label": label,
                "caption": caption[:MAX_FIGURE_CAPTION_CHARS],
            }
        )
        if len(candidates) >= MAX_FIGURE_CAPTIONS:
            break
    return candidates


def summarize_figures(
    figures: List[Dict[str, str]], model: str
) -> tuple[List[Dict[str, str]], Dict[str, int]]:
    if not figures:
        return [], {}

    client = OpenAI()
    prompt = FIGURE_PROMPT_TEMPLATE.format(figure_json=json.dumps(figures, ensure_ascii=False))
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content
        parsed = json.loads(content)
        usage = usage_to_dict(getattr(response, "usage", None))
        if isinstance(parsed, dict):
            figures_list = parsed.get("figures")
            if isinstance(figures_list, list):
                return figures_list, usage
            return [], usage
        if isinstance(parsed, list):
            return parsed, usage
    except Exception as exc:  # pragma: no cover - best effort logging
        print(f"Figure summarization failed: {exc}", file=sys.stderr)

    # Fallback: return truncated captions as summaries.
    return [{"label": fig["label"], "summary": fig["caption"]} for fig in figures], {}


def compute_cost(
    prompt_tokens: int,
    completion_tokens: int,
    model: str,
    input_cost_override: float | None = None,
    output_cost_override: float | None = None,
) -> tuple[float | None, float | None, float | None]:
    pricing = PRICING_PER_1K_TOKENS.get(model, {})
    input_rate = input_cost_override if input_cost_override is not None else pricing.get("input")
    output_rate = output_cost_override if output_cost_override is not None else pricing.get("output")

    total_cost = 0.0
    cost_known = False
    if input_rate is not None:
        total_cost += (prompt_tokens / 1000) * input_rate
        cost_known = True
    if output_rate is not None:
        total_cost += (completion_tokens / 1000) * output_rate
        cost_known = True

    return (total_cost if cost_known else None, input_rate, output_rate)


def infer_wiki_prefix(base_dir: Path, wiki_root: Path | None, explicit_prefix: str | None) -> str | None:
    if explicit_prefix:
        return normalize_path_value(explicit_prefix.strip())

    if wiki_root:
        try:
            rel = base_dir.resolve().relative_to(wiki_root.resolve())
            prefix = normalize_path_value(rel.as_posix())
            if prefix:
                return prefix
        except ValueError:
            pass

    if not base_dir.is_absolute():
        prefix = base_dir.as_posix().lstrip("./")
        prefix = normalize_path_value(prefix)
        return prefix or None

    return None


def split_remote_target(remote_target: str) -> tuple[str, str]:
    if ":" not in remote_target:
        raise ValueError("Remote target must include ':' (e.g., user@host:/path)")
    ssh_target, remote_path = remote_target.rsplit(":", 1)
    return ssh_target.strip(), (remote_path.strip() or ".")


def ensure_remote_directory(ssh_target: str, remote_dir: str) -> None:
    if remote_dir in (".", ""):
        return
    quoted_dir = shlex.quote(remote_dir)
    subprocess.run(["ssh", ssh_target, f"mkdir -p {quoted_dir}"], check=True)


def upload_pdf(pdf_path: Path, remote_target: str, remote_filename: str, logger: IngestLogger) -> None:
    ssh_target, remote_dir = split_remote_target(remote_target)
    remote_dir = remote_dir or "."
    ensure_remote_directory(ssh_target, remote_dir)
    remote_path = posixpath.join(remote_dir, remote_filename)
    dest = f"{ssh_target}:{remote_path}"
    logger.log(f"Uploading PDF to {dest} ...")
    try:
        subprocess.run(["scp", str(pdf_path), dest], check=True)
    except subprocess.CalledProcessError as exc:
        logger.log(f"SCP upload failed for {pdf_path}: {exc}")
        raise
    logger.log(f"Upload complete: {dest}")


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest a PDF into the wiki as Markdown.")
    parser.add_argument("pdf", type=Path, help="Path to the PDF to ingest.")
    parser.add_argument(
        "--base-dir",
        type=Path,
        default=Path("research/cardiovascular/hemorrhagic_shock"),
        help="Root directory for generated Markdown.",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"OpenAI model to use (default: {DEFAULT_MODEL}).",
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=DEFAULT_MAX_CHARS,
        help="Maximum characters from the PDF text to send to the summary prompt.",
    )
    parser.add_argument(
        "--print-json",
        action="store_true",
        help="Print the structured summary JSON to stdout.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run summarization but do not write a Markdown file.",
    )
    parser.add_argument(
        "--pdf-upload",
        type=str,
        help="Optional scp target (e.g. user@host:/var/www/wiki/static/papers) to upload the PDF.",
    )
    parser.add_argument(
        "--pdf-url-base",
        type=str,
        help="Base URL for linking the uploaded PDF (e.g. https://wiki.example.com/static/papers).",
    )
    parser.add_argument(
        "--input-cost-per-1k",
        type=float,
        help="USD cost per 1K input tokens (overrides built-in pricing table).",
    )
    parser.add_argument(
        "--output-cost-per-1k",
        type=float,
        help="USD cost per 1K output tokens (overrides built-in pricing table).",
    )
    parser.add_argument(
        "--wiki-root",
        type=Path,
        help="Path to the wiki repo root. Used to compute front-matter path when --base-dir is absolute.",
    )
    parser.add_argument(
        "--wiki-path-prefix",
        type=str,
        help="Explicit wiki path prefix (e.g. research/cardiovascular). Overrides automatic inference.",
    )
    parser.add_argument(
        "--doc-id",
        type=str,
        help="Override the doc_id stored in front matter (defaults to slug-year).",
    )
    parser.add_argument(
        "--log-file",
        type=str,
        default=DEFAULT_LOG_FILE,
        help="Path to append ingest logs (pass '-' to disable file logging).",
    )
    return parser.parse_args(argv)


def main(argv: List[str]) -> int:
    args = parse_args(argv)
    if not args.pdf.exists():
        print(f"PDF not found: {args.pdf}", file=sys.stderr)
        return 1
    log_path = None if args.log_file == "-" else Path(args.log_file)
    logger = IngestLogger(log_path)
    logger.log(f"Starting ingest for {args.pdf}")

    try:
        logger.log(f"Extracting text from {args.pdf} ...")
        raw_text = extract_text_from_pdf(args.pdf, logger)
        if not raw_text.strip():
            raw_text = run_ocr_fallback(args.pdf, logger)
        if len(raw_text) > args.max_chars:
            raise IngestFailure(
                "too_long",
                detail=f"Extracted text length {len(raw_text):,} chars exceeds max {args.max_chars:,} chars.",
            )

        logger.log(f"Summarizing with model {args.model} ...")
        summary, summary_usage = summarize_paper(raw_text, model=args.model, max_chars=args.max_chars)
        doc_id = derive_doc_id(summary, args.doc_id)
        if summary_looks_placeholder(summary):
            slug_preview = derive_slug_from_title(
                str(summary.get("short_title") or summary.get("title") or "untitled")
            )
            detail_markdown = build_markdown(
                summary,
                figure_summaries=[],
                slug=slug_preview,
                wiki_path=None,
                doc_id=doc_id,
            )
            raise IngestFailure("llm_placeholder", detail=detail_markdown)

        figure_captions = extract_figure_captions(raw_text)
        figure_summaries, figure_usage = summarize_figures(figure_captions, model=args.model)
        wiki_path_prefix = infer_wiki_prefix(args.base_dir, args.wiki_root, args.wiki_path_prefix)

        total_prompt_tokens = summary_usage.get("prompt_tokens", 0) + figure_usage.get("prompt_tokens", 0)
        total_completion_tokens = summary_usage.get("completion_tokens", 0) + figure_usage.get("completion_tokens", 0)
        total_tokens = summary_usage.get("total_tokens", 0) + figure_usage.get("total_tokens", 0)
        estimated_cost, input_rate, output_rate = compute_cost(
            total_prompt_tokens,
            total_completion_tokens,
            args.model,
            input_cost_override=args.input_cost_per_1k,
            output_cost_override=args.output_cost_per_1k,
        )

        pdf_url = None
        if args.pdf_upload:
            pdf_remote_filename = build_pdf_filename(summary, args.pdf.suffix or ".pdf")
            upload_pdf(args.pdf, args.pdf_upload, pdf_remote_filename, logger)
            if args.pdf_url_base:
                pdf_url = f"{args.pdf_url_base.rstrip('/')}/{pdf_remote_filename}"

        if args.print_json:
            print(json.dumps(summary, indent=2))

        logger.log(
            f"Token usage: prompt {total_prompt_tokens:,}, completion {total_completion_tokens:,}, total {total_tokens:,}"
        )
        if estimated_cost is not None:
            rate_bits = []
            if input_rate is not None:
                rate_bits.append(f"input ${input_rate:.4f}/1k")
            if output_rate is not None:
                rate_bits.append(f"output ${output_rate:.4f}/1k")
            rate_suffix = f" ({', '.join(rate_bits)})" if rate_bits else ""
            logger.log(f"Estimated OpenAI cost: ${estimated_cost:.4f}{rate_suffix}")
        else:
            logger.log(
                "Set --input-cost-per-1k/--output-cost-per-1k (or use a model with predefined pricing) to see estimated cost."
            )

        if args.dry_run:
            logger.log("Dry run enabled; not writing Markdown.")
            return 0

        output_path = write_markdown(
            summary,
            args.base_dir,
            pdf_url=pdf_url,
            figure_summaries=figure_summaries,
            wiki_path_prefix=wiki_path_prefix,
            doc_id=doc_id,
            kind=DEFAULT_DOC_KIND,
        )
        logger.log(f"Wrote {output_path}")
        return 0
    except IngestFailure as exc:
        emit_failure_payload(exc.reason, exc.detail)
        logger.log(f"Ingest failed: {exc.reason}")
        if exc.detail:
            preview = exc.detail if len(exc.detail) <= 500 else exc.detail[:500] + "…"
            logger.log(f"Detail: {preview}")
            if exc.reason != "too_long":
                logger.log("Full failure payload written above for inspection.")
        return 2



def run_ocr_fallback(pdf_path: Path, logger: IngestLogger) -> str:
    try:
        import ocrmypdf
    except ImportError as exc:
        raise IngestFailure(
            "ocr_unavailable",
            "OCR fallback requested but ocrmypdf is not installed. Install ocrmypdf to process scanned PDFs.",
        ) from exc

    logger.log("No embedded text detected; running OCR fallback via ocrmypdf ...")
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_pdf = Path(tmpdir) / "ocr.pdf"
        sidecar = Path(tmpdir) / "sidecar.txt"
        try:
            ocrmypdf.ocr(
                str(pdf_path),
                str(tmp_pdf),
                force_ocr=True,
                # force_ocr already handles mixed text/PDFs; skip_text conflicts with it.
                sidecar=str(sidecar),
                progress_bar=False,
            )
        except Exception as exc:  # pragma: no cover - external tool
            raise IngestFailure("ocr_failed", f"OCR processing failed: {exc}") from exc

        if not sidecar.exists():
            raise IngestFailure("ocr_failed", "OCR completed but no sidecar text was produced.")
        text = sidecar.read_text(encoding="utf-8", errors="ignore")
        if not text.strip():
            raise IngestFailure("empty_pdf", "OCR fallback produced no extractable text.")

    logger.log("OCR fallback succeeded; continuing with summarized text.")
    return text


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
