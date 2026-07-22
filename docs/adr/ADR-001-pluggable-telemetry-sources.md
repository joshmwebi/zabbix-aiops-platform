# ADR-001: Zabbix-first ingestion behind a source-agnostic bronze schema

**Status:** accepted · **Date:** 2026-07-22

## Context
The platform needs fleet telemetry. Zabbix 7.0 already monitors the target
fleet (~37 hosts) and stores history in PostgreSQL, so it is the richest and
cheapest source available. The wider observability ecosystem, however, is
converging on OpenTelemetry, and portfolio/skill considerations favor not
hard-coupling the platform to one monitoring product.

## Decision
Ingest from Zabbix first, via its JSON-RPC API (not direct DB reads), and land
all telemetry in a source-agnostic bronze schema keyed on
(source, host, metric, timestamp, value). Additional sources — an OTel
Collector pipeline is the first roadmap candidate — implement the same
extractor interface and write to the same schema.

## Consequences
- Real production-shaped data from day one; no synthetic-only development.
- API-based extraction survives Zabbix schema changes between versions and
  works identically against the Docker sandbox and a real fleet.
- Slightly more modeling work up front to keep bronze generic.
- dbt models and everything downstream never know or care which product
  produced a metric.
