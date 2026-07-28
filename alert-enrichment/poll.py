"""Poll Zabbix for active problems, correlate them, and enrich with an LLM.

Run once (useful for testing against whatever is currently broken):

    python alert-enrichment/poll.py --once

Run continuously (the service mode):

    python alert-enrichment/poll.py

Dry run — gather and correlate context but skip the LLM call, so you can see
exactly what would be sent and spend nothing:

    python alert-enrichment/poll.py --once --dry-run

Why polling rather than a Zabbix webhook: polling needs no inbound port, no
firewall change, and no Zabbix media-type configuration, so it runs the
moment you have an API token. See docs/adr/ADR-002. app.py adds the webhook
path for real-time delivery once that config is available.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# context.py and enrich.py sit next to this file. Python automatically puts a
# script's own directory first on sys.path, so plain imports find them.
import context  # noqa: E402
import deliver  # noqa: E402
import enrich  # noqa: E402
from pipeline.zabbix_client import ZabbixClient  # noqa: E402

STATE_PATH = REPO_ROOT / "logs" / "seen_events.json"
LOG_PATH = REPO_ROOT / "logs" / "enriched-alerts.jsonl"


def load_seen() -> set[str]:
    """Event IDs already enriched, so a long-running problem is not re-sent."""
    if STATE_PATH.exists():
        return set(json.loads(STATE_PATH.read_text()))
    return set()


def save_seen(seen: set[str]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Cap the file so it cannot grow without bound.
    STATE_PATH.write_text(json.dumps(sorted(seen)[-5000:]))


def write_log(record: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def run_once(zbx: ZabbixClient, seen: set[str], *, dry_run: bool) -> int:
    min_sev = int(os.environ.get("MIN_SEVERITY", "2"))
    threshold = int(os.environ.get("FLEET_INCIDENT_THRESHOLD", "3"))

    problems = context.get_active_problems(zbx, min_severity=min_sev)
    incidents = context.group_into_incidents(problems, fleet_threshold=threshold)

    new_incidents = [i for i in incidents if not set(i["event_ids"]) <= seen]

    print(
        f"{datetime.now():%H:%M:%S}  {len(problems)} active problem(s) -> "
        f"{len(incidents)} incident(s), {len(new_incidents)} new"
    )

    for incident in new_incidents:
        ctx = context.build_incident_context(zbx, incident)

        if dry_run:
            print("\n--- context that would be sent ---")
            print(json.dumps(ctx, indent=2))
            continue

        triage = enrich.enrich_incident(ctx)
        print(enrich.format_for_console(ctx, triage))
        sinks = deliver.deliver(ctx, triage, incident["event_ids"])
        if sinks:
            print(f"  delivered to: {', '.join(sinks)}\n")
        write_log(
            {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "context": ctx,
                "triage": triage,
                "delivered_to": sinks,
            }
        )
        seen.update(incident["event_ids"])

    if not dry_run:
        save_seen(seen)
    return len(new_incidents)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="single pass, then exit")
    parser.add_argument(
        "--dry-run", action="store_true", help="skip the LLM call; print context only"
    )
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")

    api_url = os.environ.get("ZABBIX_API_URL")
    api_token = os.environ.get("ZABBIX_API_TOKEN")
    if not api_url or not api_token:
        print("Missing ZABBIX_API_URL or ZABBIX_API_TOKEN — check .env")
        return 1
    if not args.dry_run and not os.environ.get("ANTHROPIC_API_KEY"):
        print("Missing ANTHROPIC_API_KEY — set it in .env, or use --dry-run")
        return 1

    zbx = ZabbixClient(api_url, api_token)
    seen = load_seen()

    if args.once:
        run_once(zbx, seen, dry_run=args.dry_run)
        return 0

    interval = int(os.environ.get("POLL_INTERVAL_SECONDS", "120"))
    print(f"Polling every {interval}s. Ctrl+C to stop.")
    while True:
        try:
            run_once(zbx, seen, dry_run=args.dry_run)
        except Exception as exc:  # keep the service alive through transient errors
            print(f"[error] {type(exc).__name__}: {exc}")
        time.sleep(interval)


if __name__ == "__main__":
    sys.exit(main())
