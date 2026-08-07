"""Run dbt with the repo's .env loaded into the environment.

    python scripts/dbt.py deps
    python scripts/dbt.py build
    python scripts/dbt.py run --select gold_host_headroom
    python scripts/dbt.py test

dbt reads connection settings from profiles.yml, which uses env_var()
lookups rather than literal credentials — so profiles.yml is safe to commit
and there is exactly one place secrets live. dbt does not read .env files
itself, so running plain `dbt run` would find nothing. This wrapper loads
.env first and then hands off, keeping the extractor and dbt on identical
configuration.

Arguments are passed through unchanged, so anything dbt accepts works here.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
DBT_DIR = REPO_ROOT / "dbt"

REQUIRED = [
    "SNOWFLAKE_ACCOUNT",
    "SNOWFLAKE_USER",
    "SNOWFLAKE_ROLE",
    "SNOWFLAKE_DATABASE",
    "SNOWFLAKE_WAREHOUSE",
    "SNOWFLAKE_PRIVATE_KEY_PATH",
]


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")

    missing = [k for k in REQUIRED if not os.environ.get(k)]
    if missing:
        print("Missing from .env: " + ", ".join(missing))
        print("The pipeline and dbt share the same connection settings.")
        return 1

    # Passphrase is optional but must be defined; env_var() with a default
    # still requires the variable to be absent rather than empty in some
    # dbt versions, so normalise it here.
    os.environ.setdefault("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE", "")
    os.environ.setdefault("SNOWFLAKE_SCHEMA", "TELEMETRY")

    # Look for profiles.yml next to the project rather than in ~/.dbt, so a
    # fresh clone works without per-machine setup.
    args = sys.argv[1:] or ["build"]
    cmd = ["dbt", *args, "--project-dir", str(DBT_DIR), "--profiles-dir", str(DBT_DIR)]

    print(f"> {' '.join(cmd)}\n")
    try:
        return subprocess.call(cmd)
    except FileNotFoundError:
        print("dbt not found. Install it with:  pip install dbt-snowflake")
        return 1


if __name__ == "__main__":
    sys.exit(main())
