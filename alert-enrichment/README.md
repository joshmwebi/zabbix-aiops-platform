# alert-enrichment

Turns raw Zabbix problems into triage summaries: probable cause, blast
radius, and a concrete first action — with fleet-wide correlation so one
upstream failure reads as one incident instead of forty alerts.

## The problem it solves

When a domain controller hiccups, 37 VMs raise identical SQL authentication
alerts within seconds. Zabbix reports 37 problems. An engineer sees 37 pages.
The actual incident is one shared dependency failing.

This service groups problems by trigger name, treats any trigger active on
several hosts at once as a single incident, and asks an LLM to reason about
the pattern rather than the individual alerts.

Names are normalized before grouping: Windows generates per-logon instance
ids (`webthreatdefusersvc_14fa09`) and software embeds versions
(`GoogleUpdaterService152.0.7933.0`), so the same service otherwise appears
under a different name on every host. On a real 44-host fleet this took 96
problems from 42 incidents down to 17. See `docs/adr/ADR-003`.

## Two entry points

| File | Mode | Requires |
|---|---|---|
| `poll.py` | Polls the API on an interval | Only an API token |
| `app.py` | HTTP endpoint, real-time | Zabbix media type + inbound port |

Start with `poll.py`. It works immediately; the webhook path needs
configuration you may not control. See `docs/adr/ADR-002`.

## Usage

```bash
# see what it would send, spend nothing
python alert-enrichment/poll.py --once --dry-run

# enrich whatever is currently broken
python alert-enrichment/poll.py --once

# run continuously
python alert-enrichment/poll.py
```

Output goes to stdout and appends to `logs/enriched-alerts.jsonl`, one JSON
object per incident. `logs/` is gitignored.

## Configuration

Set in `.env` (see `.env.example`):

| Variable | Default | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | required unless using `--dry-run` |
| `ANTHROPIC_MODEL` | `claude-sonnet-5` | model used for triage |
| `MIN_SEVERITY` | `2` | ignore below Warning |
| `FLEET_INCIDENT_THRESHOLD` | `3` | hosts sharing a trigger before it counts as fleet-wide |
| `POLL_INTERVAL_SECONDS` | `120` | polling cadence |

## How the pieces fit

```
poll.py / app.py     entry points: when and how work is triggered
  -> context.py      gathers evidence from the Zabbix API, correlates
    -> enrich.py     sends context to the LLM, parses triage output
```

Retrieval and reasoning are deliberately separate: the same context can be
replayed against a different model or prompt, and a poor answer is debugged
by inspecting exactly what the model was shown (`--dry-run` prints it).

## Wiring up the webhook (later)

1. Alerts -> Media types -> Create media type, type **Webhook**
2. Add parameters `eventid` = `{EVENT.ID}`, `trigger_name` = `{EVENT.NAME}`,
   `host` = `{HOST.NAME}`, `severity` = `{EVENT.SEVERITY}`
3. Script: POST those parameters as JSON to
   `http://<this-host>:8000/webhook`
4. Create a user, give it this media type, and add an action that sends to it

Confirm the service is reachable from the Zabbix server before wiring the
action — `curl http://<this-host>:8000/health` from the Zabbix box.
