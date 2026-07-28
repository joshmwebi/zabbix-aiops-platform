# ADR-005: Delivery sinks and running as a scheduled task

**Status:** accepted · **Date:** 2026-07-28

## Context
With enrichment proven, the component printed triage to a terminal — useful
only while someone is running it by hand. The goal is passive value: the
operator learns about incidents without asking.

Two questions: where summaries go, and how the process stays running.

## Decisions

**Delivery.** Two optional sinks, enabled purely by the presence of their
configuration: a Teams webhook (Adaptive Card via a Workflows endpoint) and
write-back into Zabbix itself via `event.acknowledge`, which attaches the
triage to the problem so it appears in the UI next to the alert it explains.
Console output and the JSONL log always happen first, so a sink outage never
loses an enrichment. Sinks fail independently of each other.

**Separate write token.** The polling account remains read-only. Zabbix
write-back requires a second token (`ZABBIX_WRITE_TOKEN`) from an account
whose role permits `event.acknowledge` and nothing more. The always-in-use
read credential keeps zero write capability; the write credential is used
only at the moment of delivery.

**Task Scheduler over NSSM.** NSSM produces a cleaner service, but it is a
third-party download that a future administrator would have to recognise.
Task Scheduler ships with Windows, appears in `taskschd.msc`, restarts the
process on failure, and survives reboots — sufficient for this workload and
maximally discoverable during handoff. The task runs the venv's Python
directly, so no activation state is involved.

## Consequences
- Enrichment now reaches the operator (Teams) and the tool of record
  (Zabbix) with no one at a keyboard.
- Two tokens to manage instead of one; each is least-privilege for its path.
- Acknowledge-based write-back marks problems as having update actions,
  visible in the problem view. This is intended — the annotation IS the
  feature — but means "unacknowledged" filters change meaning slightly.
- Task Scheduler restart-on-failure is coarse (fixed interval, capped
  count); acceptable because poll.py already survives transient errors
  internally and the task restarts it after a crash.
