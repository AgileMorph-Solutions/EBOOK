"""Generate a practical guide (or a sample of it).

Usage:
  .venv/bin/python -m scripts.gen_guide hydrogen_peroxide          # full book
  .venv/bin/python -m scripts.gen_guide hydrogen_peroxide 2        # first 2 sections (sample)
"""
from __future__ import annotations

import sys

from bookforge.pipeline.guide_orchestrator import run_practical_book


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "reuse"]
    reuse = "reuse" in sys.argv
    name = args[0] if args else "hydrogen_peroxide"
    limit = int(args[1]) if len(args) > 1 else None
    res = run_practical_book(name, limit_sections=limit, render_pngs=True, reuse=reuse)
    print("\n=== RESULT ===")
    for k, v in res.items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    main()
