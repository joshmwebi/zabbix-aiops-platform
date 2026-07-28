"""Deliver triage summaries somewhere useful.

Terminal output proves the pipeline works; delivery is what makes it useful
when nobody is watching. Two sinks, both optional and driven entirely by
which variables exist in .env:

  TEAMS_WEBHOOK_URL   post a card to a Teams channel
  ZABBIX_WRITE_TOKEN  attach the triage as a message on the Zabbix problem
                      itself, so it shows up in the UI next to the alert

Unset sinks are skipped silently. Console + logs/enriched-alerts.jsonl always
happen regardless (handled in poll.py), so delivery failures never lose data.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pipeline.zabbix_client import ZabbixClient  # noqa: E402


# --------------------------------------------------------------------------
# Teams
# --------------------------------------------------------------------------

_CONF_COLOR = {"high": "attention", "medium": "warning", "low": "accent"}


def to_teams(ctx: dict[str, Any], triage: dict[str, Any]) -> bool:
    """Post one incident as an Adaptive Card.

    Payload shape works with Teams Workflows ("post to a channel when a
    webhook request is received") — the current mechanism, since O365
    incoming connectors were retired. Returns True on success.
    """
    url = os.environ.get("TEAMS_WEBHOOK_URL")
    if not url:
        return False

    hosts = ctx["affected_hosts"]
    host_str = ", ".join(hosts[:8]) + (f" (+{len(hosts) - 8} more)" if len(hosts) > 8 else "")
    conf = triage.get("confidence", "low")

    card = {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": [
                        {
                            "type": "TextBlock",
                            "text": triage.get("headline", ctx["problem"]),
                            "weight": "bolder",
                            "size": "medium",
                            "wrap": True,
                            "color": _CONF_COLOR.get(conf, "default"),
                        },
                        {
                            "type": "FactSet",
                            "facts": [
                                {"title": "Severity", "value": ctx["severity"]},
                                {"title": "Hosts", "value": f"{ctx['host_count']}: {host_str}"},
                                {"title": "First seen", "value": ctx["first_seen"]},
                                {"title": "Confidence", "value": conf},
                            ],
                        },
                        {
                            "type": "TextBlock",
                            "text": f"**Probable cause:** {triage.get('probable_cause', '-')}",
                            "wrap": True,
                        },
                        {
                            "type": "TextBlock",
                            "text": f"**Blast radius:** {triage.get('blast_radius', '-')}",
                            "wrap": True,
                        },
                        {
                            "type": "TextBlock",
                            "text": f"**First action:** {triage.get('first_action', '-')}",
                            "wrap": True,
                        },
                    ],
                },
            }
        ],
    }

    resp = requests.post(url, json=card, timeout=15)
    resp.raise_for_status()
    return True


# --------------------------------------------------------------------------
# Zabbix write-back
# --------------------------------------------------------------------------

# event.acknowledge action bitmask: 4 = add message.
_ACTION_ADD_MESSAGE = 4
# Zabbix caps acknowledge messages; stay under it.
_MAX_MESSAGE = 2048


def to_zabbix(ctx: dict[str, Any], triage: dict[str, Any], event_ids: list[str]) -> bool:
    """Attach the triage summary to the problem inside Zabbix itself.

    Uses a SEPARATE token from the read path. The polling account stays
    read-only; write-back needs a token whose role allows event.acknowledge
    (and 'Acknowledge problems' capability). Splitting the tokens keeps the
    blast radius of the always-in-use read credential at zero.
    """
    token = os.environ.get("ZABBIX_WRITE_TOKEN")
    url = os.environ.get("ZABBIX_API_URL")
    if not token or not url:
        return False

    msg = (
        f"[aiops triage — {triage.get('confidence', '?')} confidence]\n"
        f"{triage.get('headline', '')}\n\n"
        f"Probable cause: {triage.get('probable_cause', '-')}\n"
        f"Blast radius: {triage.get('blast_radius', '-')}\n"
        f"First action: {triage.get('first_action', '-')}"
    )[:_MAX_MESSAGE]

    zbx = ZabbixClient(url, token)
    zbx.call(
        "event.acknowledge",
        {
            "eventids": event_ids,
            "action": _ACTION_ADD_MESSAGE,
            "message": msg,
        },
    )
    return True


def deliver(ctx: dict[str, Any], triage: dict[str, Any], event_ids: list[str]) -> list[str]:
    """Run all configured sinks. Returns the names that succeeded.

    Each sink fails independently — a Teams outage must not stop the Zabbix
    write-back, and vice versa. Failures are reported, not raised.
    """
    delivered = []
    for name, fn in (
        ("teams", lambda: to_teams(ctx, triage)),
        ("zabbix", lambda: to_zabbix(ctx, triage, event_ids)),
    ):
        try:
            if fn():
                delivered.append(name)
        except Exception as exc:
            print(f"[deliver:{name}] {type(exc).__name__}: {exc}")
    return delivered
