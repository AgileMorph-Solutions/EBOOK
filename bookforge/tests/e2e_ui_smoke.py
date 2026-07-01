"""Cheap UI e2e smoke: one section, must produce a valid downloadable PDF.

    .venv/bin/python -m tests.e2e_ui_smoke
"""
from __future__ import annotations

import sys
from pathlib import Path

from tests.e2e_ui import E2EFailure, run_case

SRC = Path(__file__).resolve().parent.parent / "pest_control_source.txt"


def main() -> int:
    try:
        run_case(
            "smoke: 1-section pest guide",
            topic="The Homeowner's Guide to Common Household Pests",
            source_text=SRC.read_text(),
            target_pages=12,
            section_limit=1,
            min_pages=6,
            timeout_s=600,
        )
    except E2EFailure as e:
        print(f"\nFAIL: {e}")
        return 1
    print("\nALL SMOKE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
