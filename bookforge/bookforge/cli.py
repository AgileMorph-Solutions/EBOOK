"""Command-line entrypoint — runs the Phase-0 spike end-to-end in one process.

Examples:
    python -m bookforge.cli --topic "Forgotten British Meals" \
        --text-file sample.txt --chapters 1 --pages 100

    python -m bookforge.cli --topic "..." \
        --youtube "https://youtu.be/VIDEOID" --chapters 2
"""
from __future__ import annotations

import argparse
import sys
import uuid

import structlog

from bookforge.pipeline.orchestrator import run_book

structlog.configure(
    processors=[structlog.processors.add_log_level,
                structlog.dev.ConsoleRenderer()],
)
log = structlog.get_logger("bookforge.cli")


def main(argv=None):
    ap = argparse.ArgumentParser(description="BookForge — generate an ebook")
    ap.add_argument("--topic", required=True)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--text-file", help="path to source text")
    src.add_argument("--text", help="source text inline")
    src.add_argument("--youtube", help="YouTube URL to transcribe")
    ap.add_argument("--pages", type=int, default=100, help="target page count")
    ap.add_argument("--chapters", type=int, default=None,
                    help="limit chapters/sections; omit for full book")
    ap.add_argument("--book-kind", choices=["practical_guide", "narrative"],
                    default="practical_guide", help="book layout pipeline")
    ap.add_argument("--profile-json", default=None,
                    help="JSON dict of EditorialProfile overrides "
                         '(e.g. \'{"genre": "cookbook", "audience": "..."}\')')
    args = ap.parse_args(argv)

    profile_overrides = None
    if args.profile_json:
        import json
        profile_overrides = json.loads(args.profile_json)

    text = None
    if args.text_file:
        text = open(args.text_file, encoding="utf-8").read()
    elif args.text:
        text = args.text

    book_id = uuid.uuid4().hex[:12]
    artifact = run_book(
        book_id, args.topic, text=text, youtube_url=args.youtube,
        target_pages=args.pages, chapter_limit=args.chapters,
        book_kind=args.book_kind, profile_overrides=profile_overrides,
    )

    print("\n" + "=" * 60)
    print(f"  Book:          {book_id}")
    print(f"  Interior PDF:  {artifact.interior_pdf_key}")
    print(f"  Pages:         {artifact.page_count}")
    print(f"  File size:     {artifact.file_size_mb} MB")
    if artifact.qa:
        print(f"  Groundedness:  {artifact.qa.groundedness:.0%} "
              f"(threshold met: {artifact.qa.passed})")
        print(f"  Prose score:   {artifact.qa.prose_quality_score:.2f}")
        if artifact.qa.unsupported_claims:
            print(f"  ⚠ {len(artifact.qa.unsupported_claims)} unsupported claims "
                  "need rewrite/cut before shipping")
        for issue in artifact.qa.layout_issues:
            print(f"  ⚠ layout: {issue}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
