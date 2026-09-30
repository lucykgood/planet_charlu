#!/usr/bin/env python3
"""CLI wrapper: render an interactive HTML summary from a structured run log.

    python scripts/generate_run_summary.py path/to/run.jsonl
    python scripts/generate_run_summary.py path/to/run.jsonl --out report.html

The real logic lives in ``planet_charlu.run_summary`` (also used by
``main.py`` to write a summary automatically after every run). This wrapper
just adds ``src/`` to the path so the script runs standalone -- no
``pip install`` or ``PYTHONPATH`` needed -- straight from a fresh clone.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from planet_charlu.run_summary import write_summary  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log_path", type=Path, help="Path to a structured run log (JSON Lines)")
    parser.add_argument("--out", type=Path, default=None,
                         help="Output HTML path (default: <log_path stem>-summary.html next to the log)")
    args = parser.parse_args()

    if not args.log_path.exists() or args.log_path.stat().st_size == 0:
        raise SystemExit(f"no events found in {args.log_path}")

    out_path = write_summary(args.log_path, args.out)
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
