"""Compare preflight page estimates against finished fit_report counts.

Usage:
  .venv/bin/python -m scripts.calibrate_density
  .venv/bin/python -m scripts.calibrate_density /path/to/artifacts/books
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from bookforge.pipeline.page_limits import estimate_rendered_pages
from bookforge.schemas import BookBible


def _artifacts_root() -> Path:
    if len(sys.argv) > 1:
        return Path(sys.argv[1])
    return Path(__file__).resolve().parents[3] / "artifacts" / "books"


def main() -> None:
    root = _artifacts_root()
    if not root.is_dir():
        print(f"No artifacts dir: {root}")
        sys.exit(1)

    rows: list[tuple[str, int, int, float]] = []
    for book_dir in sorted(root.iterdir()):
        fit_path = book_dir / "fit_report.json"
        bible_path = book_dir / "bible.json"
        if not fit_path.is_file() or not bible_path.is_file():
            continue
        fit = json.loads(fit_path.read_text())
        actual = int(fit.get("count") or len(fit.get("pages") or []))
        bible = BookBible.model_validate_json(bible_path.read_text())
        profile = bible.profile
        if not profile or not bible.sections:
            continue
        target = bible.target_pages or 100
        est = estimate_rendered_pages(
            bible.sections, profile, pamphlet=target <= 15,
        )
        err = (est - actual) / max(actual, 1)
        rows.append((book_dir.name, actual, est, err))

    if not rows:
        print("No books with fit_report.json + bible.json found.")
        return

    print(f"{'book':<52} {'actual':>6} {'est':>6} {'err%':>7}")
    for name, actual, est, err in rows:
        print(f"{name[:52]:<52} {actual:>6} {est:>6} {err * 100:>+6.1f}")

    mean_abs = sum(abs(e) for _, _, _, e in rows) / len(rows)
    print(f"\n{len(rows)} books — mean absolute error {mean_abs * 100:.1f}%")


if __name__ == "__main__":
    main()
