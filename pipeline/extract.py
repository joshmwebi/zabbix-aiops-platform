"""Extract Zabbix telemetry into the warehouse bronze layer.

    python pipeline/extract.py --backfill-days 30   # first run
    python pipeline/extract.py                      # incremental, from watermark
    python pipeline/extract.py --dry-run            # count rows, write nothing

Pulls hourly trends rather than raw history: Zabbix pre-aggregates history
into min/avg/max per item per hour and retains it far longer, which is both
the right grain for capacity analysis and ~60x less data. See ADR-006.

Incremental by watermark: each run records the newest timestamp it loaded
and resumes there, so a missed run catches up rather than creating a gap,
and a repeated run does not duplicate.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pipeline.warehouse import get_warehouse  # noqa: E402
from pipeline.zabbix_client import ZabbixClient  # noqa: E402

STREAM = "trends"

# The API rejects unbounded item lists and very wide time ranges. These keep
# each request well inside those limits; tune only if you hit timeouts.
ITEM_CHUNK = 250
WINDOW_HOURS = 24


def load_items(zbx: ZabbixClient, wh, dry_run: bool) -> list[dict]:
    """Refresh item/host metadata. Replaced wholesale — it is current state,
    not history, and it is small enough that a full reload is simpler and
    safer than reconciling changes."""
    items = zbx.get_all_items()
    print(f"  items: {len(items)} numeric items across the fleet")
    if dry_run:
        return items

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = []
    for it in items:
        hosts = it.get("hosts") or [{}]
        rows.append(
            (
                int(it["itemid"]),
                int(hosts[0].get("hostid", 0)),
                hosts[0].get("host", ""),
                it.get("name", "")[:1024],
                it.get("key_", "")[:1024],
                it.get("units", "")[:64],
                int(it.get("value_type", 0)),
                str(it.get("delay", ""))[:64],
                now,
            )
        )

    wh.execute("DELETE FROM bronze_items")
    wh.insert_many(
        "bronze_items",
        ["itemid", "hostid", "host", "name", "key_", "units", "value_type", "delay", "_loaded_at"],
        rows,
    )
    return items


def load_trends(
    zbx: ZabbixClient, wh, itemids: list[str], since: int, until: int, dry_run: bool
) -> tuple[int, int]:
    """Pull trends in time windows and item chunks.

    Returns (rows_loaded, newest_clock_seen). Requesting every item for a
    month in one call would time out or exhaust memory on the server, so the
    range is walked a day at a time and items are batched.
    """
    total = 0
    newest = since
    now_ts = datetime.now(timezone.utc).replace(tzinfo=None)

    window = WINDOW_HOURS * 3600
    win_start = since
    while win_start < until:
        win_end = min(win_start + window, until)
        window_rows = 0

        # Accumulate the whole window before loading: bulk loading is far
        # more efficient with one large batch than many small ones.
        batch: list[tuple] = []
        for i in range(0, len(itemids), ITEM_CHUNK):
            chunk = itemids[i : i + ITEM_CHUNK]
            trends = zbx.get_trends(chunk, win_start, win_end)
            if not trends:
                continue

            window_rows += len(trends)
            newest = max(newest, max(int(t["clock"]) for t in trends))
            if not dry_run:
                batch.extend(
                    (
                        int(t["itemid"]),
                        int(t["clock"]),
                        int(t.get("num", 0)),
                        float(t.get("value_min", 0)),
                        float(t.get("value_avg", 0)),
                        float(t.get("value_max", 0)),
                        now_ts,
                    )
                    for t in trends
                )

        if batch:
            wh.insert_many(
                "bronze_trends",
                ["itemid", "clock", "num", "value_min", "value_avg", "value_max", "_loaded_at"],
                batch,
            )

        stamp = datetime.fromtimestamp(win_start, timezone.utc).strftime("%Y-%m-%d")
        print(f"  {stamp}: {window_rows:>7,} trend rows")
        total += window_rows
        win_start = win_end

    return total, newest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backfill-days",
        type=int,
        default=0,
        help="ignore the watermark and pull this many days of history",
    )
    parser.add_argument("--dry-run", action="store_true", help="count rows, write nothing")
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")

    api_url = os.environ.get("ZABBIX_API_URL")
    api_token = os.environ.get("ZABBIX_API_TOKEN")
    if not api_url or not api_token:
        print("Missing ZABBIX_API_URL / ZABBIX_API_TOKEN — check .env")
        return 1

    zbx = ZabbixClient(api_url, api_token)
    started = time.time()
    until = int(time.time())

    backend = os.environ.get("WAREHOUSE_TYPE", "postgres")
    print(f"{datetime.now():%H:%M:%S}  extract -> {backend}" + ("  [DRY RUN]" if args.dry_run else ""))

    if args.dry_run:
        # A dry run's purpose is sizing the load before a warehouse exists,
        # so it must not require one: no connection, no adapter installed,
        # no watermark. Range defaults to --backfill-days or 1 day.
        items = load_items(zbx, None, dry_run=True)
        itemids = [i["itemid"] for i in items]
        since = until - (args.backfill_days or 1) * 86400
        rows, _ = load_trends(zbx, None, itemids, since, until, dry_run=True)
        elapsed = time.time() - started
        print(f"  {rows:,} rows in {elapsed:.1f}s (nothing written)")
        return 0

    with get_warehouse() as wh:
        items = load_items(zbx, wh, args.dry_run)
        itemids = [i["itemid"] for i in items]

        if args.backfill_days:
            since = until - args.backfill_days * 86400
            print(f"  backfill: {args.backfill_days} days")
        else:
            # Default first-run window is short; --backfill-days is explicit
            # so nobody accidentally pulls a year on a whim.
            since = wh.get_watermark(STREAM, default=until - 86400)
            print(f"  incremental from {datetime.fromtimestamp(since):%Y-%m-%d %H:%M}")

        rows, newest = load_trends(zbx, wh, itemids, since, until, args.dry_run)

        if rows:
            wh.set_watermark(STREAM, newest)

    elapsed = time.time() - started
    print(f"  {rows:,} rows in {elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
