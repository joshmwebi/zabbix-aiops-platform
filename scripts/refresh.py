"""Refresh the warehouse: extract new telemetry, then rebuild the models.

    python scripts/refresh.py

One command so the scheduler has one thing to call, and so the two halves
can never drift out of step — models are always rebuilt against data that
was just loaded, never against yesterday's.

Runs both steps with the same interpreter that started this script, which
means the venv is used automatically without anything having to activate it.
Exits non-zero if either step fails, so Task Scheduler records a failure
instead of reporting success on a broken run.
"""

from __future__ import annotations

import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

STEPS = [
    # Incremental: resumes from the watermark, so a missed run catches up
    # rather than leaving a gap in the history.
    ("extract", [sys.executable, str(REPO_ROOT / "pipeline" / "extract.py")]),
    # build = run models, then run tests. A failing test fails the refresh,
    # which is the point: silently publishing bad marts is worse than an
    # alert that the refresh broke.
    ("dbt build", [sys.executable, str(REPO_ROOT / "scripts" / "dbt.py"), "build"]),
]


def main() -> int:
    started = time.time()
    print(f"\n{'=' * 70}")
    print(f"refresh started {datetime.now():%Y-%m-%d %H:%M:%S}")
    print("=" * 70)

    for name, cmd in STEPS:
        step_start = time.time()
        print(f"\n--- {name} ---")
        code = subprocess.call(cmd, cwd=REPO_ROOT)
        elapsed = time.time() - step_start

        if code != 0:
            print(f"\n{name} FAILED (exit {code}) after {elapsed:.1f}s")
            print("Refresh aborted — later steps skipped.")
            return code

        print(f"{name} ok ({elapsed:.1f}s)")

    print(f"\nrefresh complete in {time.time() - started:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
