# pipeline

Extracts Zabbix telemetry into the warehouse bronze layer.

## What it pulls

Hourly **trends** (min/avg/max/count per item per hour) rather than raw
history — the right grain for capacity work and roughly 60x less data. See
`docs/adr/ADR-006`. Item and host metadata is refreshed each run into
`bronze_items`.

## Usage

```bash
python pipeline/extract.py --dry-run            # count rows, write nothing
python pipeline/extract.py --backfill-days 30   # first load
python pipeline/extract.py                      # incremental thereafter
```

Incremental runs resume from a watermark in `pipeline_state`, so a missed
run catches up instead of leaving a gap, and re-running does not duplicate.

## Backends

`WAREHOUSE_TYPE` selects `postgres` or `snowflake`; connection details for
each live in `.env`. The loader is written against a small interface so the
backend is a configuration choice, not a code change (`docs/adr/ADR-007`).
Install only the adapter you need:

```bash
pip install psycopg2-binary                # postgres
pip install snowflake-connector-python     # snowflake
```

## Bronze tables

| Table | Grain | Notes |
|---|---|---|
| `bronze_trends` | item × hour | append-only; the telemetry record |
| `bronze_items` | item | current fleet state, replaced each run |
| `pipeline_state` | stream | extraction watermark |

Bronze stays close to what the API returned. Cleaning and reshaping happen
in dbt, so a transformation bug never requires re-extracting from Zabbix.
