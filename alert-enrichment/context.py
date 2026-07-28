"""Gather the evidence an engineer would collect before triaging an alert.

A raw Zabbix problem tells you almost nothing on its own: one host, one
trigger name, one timestamp. What actually determines the response is the
surrounding context — what else is broken on that host, whether the same
thing is breaking fleet-wide, and what the underlying metric has been doing.

This module collects that context. It does not interpret it; enrich.py does.
"""

from __future__ import annotations

import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

# The shared Zabbix client lives in pipeline/. Adding the repo root to the
# import path lets us reuse it without duplicating code or installing the
# project as a package.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pipeline.zabbix_client import ZabbixClient  # noqa: E402

# Zabbix severity codes -> human labels.
SEVERITY = {
    "0": "Not classified",
    "1": "Information",
    "2": "Warning",
    "3": "Average",
    "4": "High",
    "5": "Disaster",
}


def get_active_problems(zbx: ZabbixClient, min_severity: int = 2) -> list[dict[str, Any]]:
    """Return currently-active problems at or above a severity threshold.

    Zabbix's problem.get returns *unresolved* problems by default, which is
    what we want — resolved ones are history, not something to triage.
    """
    problems = zbx.call(
        "problem.get",
        {
            "output": "extend",
            "selectTags": "extend",
            "severities": list(range(min_severity, 6)),
            "recent": False,
            "sortfield": ["eventid"],
            "sortorder": "DESC",
        },
    )
    return _attach_hostnames(zbx, problems)


def _attach_hostnames(zbx: ZabbixClient, problems: list[dict]) -> list[dict]:
    """Resolve each problem's host.

    problem.get returns trigger objects but not host names, so we look up the
    triggers and graft the host name onto each problem. One batched call
    rather than one call per problem.
    """
    if not problems:
        return problems

    trigger_ids = list({p["objectid"] for p in problems})
    triggers = zbx.call(
        "trigger.get",
        {
            "output": ["triggerid", "description"],
            "triggerids": trigger_ids,
            "selectHosts": ["hostid", "host"],
            "selectItems": ["itemid", "name", "key_", "units"],
        },
    )
    by_trigger = {t["triggerid"]: t for t in triggers}

    for p in problems:
        trig = by_trigger.get(p["objectid"], {})
        hosts = trig.get("hosts", [])
        p["_host"] = hosts[0]["host"] if hosts else "unknown"
        p["_hostid"] = hosts[0]["hostid"] if hosts else None
        p["_items"] = trig.get("items", [])
        p["_severity_label"] = SEVERITY.get(p.get("severity", "0"), "Unknown")
    return problems


def group_into_incidents(
    problems: list[dict], fleet_threshold: int = 3
) -> list[dict[str, Any]]:
    """Collapse many alerts into a smaller number of incidents.

    Zabbix triggers come from templates, so the same failure on 37 hosts
    produces 37 problems that share an identical trigger name. Grouping by
    name is therefore a cheap and surprisingly effective correlation: if the
    same problem is active on several hosts at once, it is almost certainly
    one upstream cause rather than N unrelated failures.

    Returns a list of incidents, each with a 'kind' of either 'fleet'
    (multiple hosts affected) or 'single'.
    """
    by_name: dict[str, list[dict]] = defaultdict(list)
    for p in problems:
        by_name[p["name"]].append(p)

    incidents = []
    for name, group in by_name.items():
        hosts = sorted({p["_host"] for p in group})
        earliest = min(int(p["clock"]) for p in group)
        worst = max(int(p.get("severity", 0)) for p in group)
        incidents.append(
            {
                "kind": "fleet" if len(hosts) >= fleet_threshold else "single",
                "name": name,
                "hosts": hosts,
                "host_count": len(hosts),
                "severity": worst,
                "severity_label": SEVERITY.get(str(worst), "Unknown"),
                "first_seen_epoch": earliest,
                "first_seen": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(earliest)),
                "event_ids": [p["eventid"] for p in group],
                "problems": group,
            }
        )

    # Fleet-wide incidents first, then by severity, then by age.
    incidents.sort(
        key=lambda i: (i["kind"] != "fleet", -i["severity"], i["first_seen_epoch"])
    )
    return incidents


def host_neighbourhood(zbx: ZabbixClient, hostid: str, exclude_name: str) -> list[str]:
    """Other problems currently active on the same host.

    A disk-space alert next to a backup-job failure on the same box is a very
    different story than a disk-space alert on its own.
    """
    if not hostid:
        return []
    others = zbx.call(
        "problem.get",
        {"output": ["name", "severity"], "hostids": hostid, "recent": False},
    )
    return [o["name"] for o in others if o["name"] != exclude_name]


def item_trend(zbx: ZabbixClient, itemid: str, hours: int = 6) -> dict[str, Any] | None:
    """Recent values for the metric behind a trigger.

    Distinguishes "this crossed the threshold ten minutes ago" from "this has
    been creeping up all week", which changes the recommended action.
    """
    if not itemid:
        return None

    # history.get needs to be told which value type it is reading; item.get
    # tells us. 0 = float, 3 = unsigned integer are the numeric ones.
    items = zbx.call("item.get", {"output": ["value_type", "units", "name"], "itemids": itemid})
    if not items:
        return None
    value_type = int(items[0]["value_type"])
    if value_type not in (0, 3):
        return None  # text/log items have no meaningful trend

    since = int(time.time()) - hours * 3600
    history = zbx.call(
        "history.get",
        {
            "output": "extend",
            "itemids": itemid,
            "history": value_type,
            "time_from": since,
            "sortfield": "clock",
            "sortorder": "DESC",
            "limit": 200,
        },
    )
    if not history:
        return None

    values = [float(h["value"]) for h in history]
    return {
        "item": items[0]["name"],
        "units": items[0].get("units", ""),
        "window_hours": hours,
        "samples": len(values),
        "latest": values[0],
        "min": min(values),
        "max": max(values),
        "mean": round(sum(values) / len(values), 4),
        # Oldest-to-newest direction, useful for spotting a ramp.
        "first_in_window": values[-1],
    }


def build_incident_context(zbx: ZabbixClient, incident: dict) -> dict[str, Any]:
    """Assemble everything the LLM should see for one incident."""
    representative = incident["problems"][0]
    items = representative.get("_items") or []
    itemid = items[0]["itemid"] if items else None

    ctx = {
        "problem": incident["name"],
        "severity": incident["severity_label"],
        "kind": incident["kind"],
        "affected_hosts": incident["hosts"],
        "host_count": incident["host_count"],
        "first_seen": incident["first_seen"],
        "tags": [f"{t['tag']}: {t['value']}" for t in representative.get("tags", [])],
    }

    # Only worth pulling per-host detail for single-host incidents; for a
    # fleet event the pattern itself is the signal.
    if incident["kind"] == "single":
        ctx["other_problems_on_host"] = host_neighbourhood(
            zbx, representative.get("_hostid"), incident["name"]
        )
        ctx["metric_trend"] = item_trend(zbx, itemid)
    else:
        ctx["note"] = (
            f"Identical trigger active on {incident['host_count']} hosts "
            "simultaneously — likely a single shared cause."
        )

    return ctx
