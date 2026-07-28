# ADR-002: Poll for problems before adding webhook delivery

**Status:** accepted · **Date:** 2026-07-24

## Context
Alert enrichment can be triggered two ways: Zabbix pushes each event to an
HTTP endpoint (webhook media type), or the service polls the API for active
problems on an interval.

The webhook is the better production design — lower latency, no wasted
requests, event-driven. But it requires a Zabbix media type, an action
configuration, an inbound listening port on the enrichment host, and a
network path from the Zabbix server to that port. In a corporate environment
several of those may sit with other teams.

## Decision
Implement polling first, as `poll.py`, needing nothing beyond the API token
that already exists. Implement the webhook receiver as `app.py` against the
same `context.py` / `enrich.py` core, so switching delivery mechanisms
changes no enrichment logic.

## Consequences
- The component is usable on day one, with no external dependencies.
- Detection latency equals the poll interval (default 120s), acceptable for
  triage assistance but not for anything time-critical.
- Redundant API calls when nothing is broken; negligible at this fleet size.
- Both paths share correlation and enrichment code, so the webhook is a
  delivery change rather than a rewrite.
- State (`logs/seen_events.json`) is needed for polling to avoid re-enriching
  a problem that stays active across cycles. The webhook path does not need
  it, since each event fires once.
