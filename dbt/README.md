# dbt

Transforms the bronze landing tables into cleaned silver models and
purpose-built gold marts.

## Running

```bash
pip install dbt-snowflake
python scripts/dbt.py deps     # install dbt_utils (once)
python scripts/dbt.py build    # run all models, then all tests
```

Use `scripts/dbt.py`, not bare `dbt` — it loads the repo's `.env` so dbt and
the extractor share one set of credentials. `profiles.yml` contains only
`env_var()` lookups, which is why it is safe to commit.

Useful variants:

```bash
python scripts/dbt.py run --select gold_host_headroom
python scripts/dbt.py test --select silver
python scripts/dbt.py docs generate
```

## Layers

| Model | Grain | Purpose |
|---|---|---|
| `silver_items` | item | Zabbix key parsed into metric family, filesystem, mode |
| `silver_metric_hours` | item × hour | timestamped, deduplicated, joined to host metadata |
| `gold_filesystem_daily` | host × volume × day | daily utilisation |
| `gold_host_headroom` | host × volume | current %, growth rate, days to full, status |
| `gold_fleet_daily` | day | fleet utilisation and reporting coverage |

Everything materialises into the `TELEMETRY` schema with layer prefixes on
the model names rather than separate schemas — see `docs/adr/ADR-008`.

## What silver is for

Bronze keeps `vfs.fs.dependent.size[C:,pused]` exactly as Zabbix returned
it. Silver splits that into `metric_family`, `filesystem`, and
`measurement_mode`, so no downstream query ever pattern-matches a key
string. It also flags `zabbix[...]` internal metrics, which otherwise
surface as phantom filesystems on the monitoring server.

Silver also deduplicates on `(itemid, hour)`. The extractor resumes from a
watermark, so the boundary hour can be pulled twice; enforcing uniqueness
here keeps the loader simple and puts idempotency where a test can verify
it.

## What gold answers

`gold_host_headroom` is the model monitoring cannot replace. Zabbix knows a
volume is over 80%; it does not know whether that volume is flat or filling.
The model fits a least-squares slope over a trailing 14 days and projects
days to full, so a volume climbing steadily toward a deadline is
distinguishable from one that has simply been full for months.
