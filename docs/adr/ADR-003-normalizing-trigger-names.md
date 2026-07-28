# ADR-003: Normalize volatile identifiers before correlating alerts

**Status:** accepted · **Date:** 2026-07-28

## Context
Correlation originally grouped problems by exact trigger name, on the
assumption that template-generated triggers produce identical names across
hosts. The first run against a real 44-host fleet disproved that for a large
class of alerts.

Windows generates per-logon instance identifiers for some services, so the
same service appears under a different name on every host and sometimes
several times on one host: `webthreatdefusersvc_14fa09`,
`webthreatdefusersvc_4258fcc7`, and so on. Auto-updating software embeds its
version the same way: `GoogleUpdaterService152.0.7933.0` alongside
`GoogleUpdaterService150.0.7863.0`.

Exact-name grouping shattered these into one incident each. Of 42 incidents
produced from 96 problems, 24 were instances of a single service.

## Decision
Derive a grouping key by substituting two classes of volatile token out of
the trigger name: underscore-prefixed hex instance identifiers, and dotted
version numbers. Group on that key rather than the raw name.

The raw name is retained on the incident for display; where several distinct
names merged, the incident is annotated with the variant count and the LLM
context includes an explanation of why they were combined.

Grouping by tag (`component`, `service`) was considered and rejected: tags
are coarse enough that unrelated failures would merge, and a false
correlation is more damaging than a missed one — it invites an engineer to
chase a shared cause that does not exist.

## Consequences
- On the observed fleet, incidents fall from 42 to 17, and LLM calls with
  them.
- Grouping is now heuristic. Patterns are unit-tested in both directions:
  that known variants merge, and that genuinely different triggers
  (`Space is low` vs `Space is critically low`) do not.
- New naming schemes will need new patterns. `_NOISE_PATTERNS` in
  `context.py` is the single place to add them.
- Merging is presentational only; no alert is dropped, and instance counts
  are preserved on the incident.
