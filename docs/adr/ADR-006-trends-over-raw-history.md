# ADR-006: Extract hourly trends, not raw history

**Status:** accepted · **Date:** 2026-07-29

## Context
The fleet has ~5,465 enabled items. At typical polling intervals raw history
is on the order of 8M rows per day. Zabbix also maintains a `trends` table
containing min/avg/max/count per item per hour, retained far longer than raw
history (commonly a year versus days or weeks).

The analytics this platform is for — capacity utilisation, growth rates,
headroom, depletion forecasting — operate on hours and days, not seconds.

## Decision
Extract `trends` as the primary telemetry stream. Raw `history` is reserved
for a narrow allow-list of metrics where sub-hour resolution genuinely
changes the answer (CPU and memory during simulation runs, for run
analytics).

## Consequences
- Roughly 130k rows/day instead of ~8M: about 60x less data for no loss of
  analytical value at the grains that matter.
- Warehouse footprint stays in the low tens of GB per year, which keeps
  compute costs and query times small.
- Trends carry min/avg/max, which is strictly more information per row than
  a single sampled value — spikes remain visible.
- History older than Zabbix's retention can never be backfilled; once the
  warehouse is running, it becomes the long-term record and Zabbix retention
  stops being the constraint on how far back questions can reach.
- Sub-hour analysis requires the separate history path, which must be
  targeted deliberately rather than collected by default.
