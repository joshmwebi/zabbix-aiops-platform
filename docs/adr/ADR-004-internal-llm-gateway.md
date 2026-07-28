# ADR-004: Route LLM calls through a configurable base URL

**Status:** accepted · **Date:** 2026-07-28

## Context
Alert enrichment sends fleet telemetry — hostnames, service names, disk
utilization, problem history — to a language model. In an enterprise setting
that data should not leave sanctioned infrastructure, and the organisation
may already operate an internal LLM gateway that is API-compatible.

Hardcoding the public API endpoint would force a choice between using the
component and respecting that boundary.

## Decision
Read an optional `ANTHROPIC_BASE_URL` from configuration and pass it to the
client. Unset means the public endpoint; set means all traffic goes to that
gateway instead. The API key is supplied the same way in either case.

## Consequences
- The same code runs against a public endpoint in a home sandbox and an
  internal gateway in production, changing only `.env` — consistent with how
  the Zabbix endpoint is already handled.
- Telemetry can be kept within approved infrastructure without forking the
  code or maintaining a separate deployment.
- Model availability and naming may differ between endpoints, so
  `ANTHROPIC_MODEL` is configurable alongside it.
- The gateway becomes an availability dependency for enrichment; polling
  already tolerates transient errors and retries on the next cycle.
