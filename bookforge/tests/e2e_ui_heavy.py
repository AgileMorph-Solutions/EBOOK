"""Heavy UI e2e: two distinct topics, full books, each must reach >=200 pages
at recipe-grade quality and download a matching PDF via the UI.

WARNING: real API spend (~16 Opus calls + ~48 Imagen images per book) and
30-50 min per book. Run explicitly:

    .venv/bin/python -m tests.e2e_ui_heavy
"""
from __future__ import annotations

import sys
from pathlib import Path

from tests.e2e_ui import E2EFailure, run_case

ROOT = Path(__file__).resolve().parent.parent
PEST = (ROOT / "pest_control_source.txt").read_text()
# Second topic proves "any topic": reuse the food source if present, else pest.
_food = ROOT / "sample_us_food.txt"
FOOD = _food.read_text() if _food.exists() else PEST

CASES = [
    dict(name="heavy: household pests (200pg)",
         topic="The Homeowner's Guide to Common Household Pests",
         source_text=PEST, target_pages=200, min_pages=200,
         section_limit=None, timeout_s=4200),
    dict(name="heavy: budget home cooking (200pg)",
         topic="The Practical Guide to Cheap, Filling Home-Cooked Meals",
         source_text=FOOD, target_pages=200, min_pages=200,
         section_limit=None, timeout_s=4200),
]


def main() -> int:
    only = sys.argv[1] if len(sys.argv) > 1 else None  # "1" or "2" to run one
    cases = CASES if only is None else [CASES[int(only) - 1]]
    failures = []
    for c in cases:
        try:
            run_case(**c)
        except E2EFailure as e:
            print(f"\nFAIL [{c['name']}]: {e}")
            failures.append(c["name"])
    if failures:
        print(f"\n{len(failures)} case(s) FAILED: {failures}")
        return 1
    print("\nALL HEAVY CHECKS PASSED (>=200 pages, QA passed, PDFs downloaded)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
