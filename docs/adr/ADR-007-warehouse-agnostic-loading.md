# ADR-007: Keep the loader warehouse-agnostic

**Status:** accepted · **Date:** 2026-07-29

## Context
The target warehouse was undecided while the pipeline needed to be built:
PostgreSQL was available immediately, Snowflake was pending procurement.
Waiting would have stalled the work; committing to one risked a rewrite.

## Decision
Put loading behind a small interface (`pipeline/warehouse.py`) with two
adapters. `WAREHOUSE_TYPE` in configuration selects one. Bronze DDL is
written in types both backends accept, and the watermark is maintained with
delete-then-insert rather than backend-specific upsert syntax.

dbt models sit on top of whichever backend is configured, since dbt is
adapter-portable by design.

## Consequences
- Development proceeded before procurement completed.
- Migrating backends is a configuration change plus a re-load, not a code
  change — the same property that lets the Zabbix endpoint move between a
  sandbox and production.
- Avoiding dialect-specific SQL costs some efficiency: no `ON CONFLICT` or
  `MERGE`, so the watermark update is two statements. Negligible at this
  scale.
- Backend-specific optimisation (Snowflake clustering keys, Postgres
  partitioning) is deferred; if either becomes necessary it belongs in the
  adapter, not the caller.
- Adapter libraries are imported lazily, so only the backend actually in use
  needs to be installed.
