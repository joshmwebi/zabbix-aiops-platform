"""Tests for incident correlation.

These run without a Zabbix server — correlation is pure logic over problem
records, so it can be tested against fixtures. Run with:

    python -m pytest alert-enrichment/test_context.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import context  # noqa: E402


def make_problem(eventid: str, host: str, name: str, clock: int, severity: str = "4"):
    """Minimal stand-in for a problem record after _attach_hostnames."""
    return {
        "eventid": eventid,
        "name": name,
        "clock": str(clock),
        "severity": severity,
        "tags": [],
        "_host": host,
        "_hostid": f"host-{host}",
        "_items": [],
        "_severity_label": context.SEVERITY[severity],
    }


def test_fleet_wide_failure_collapses_to_one_incident():
    """The Netlogon case: identical trigger on many hosts is one incident."""
    problems = [
        make_problem(str(i), f"MTKWPTI{i:02d}", "MSSQL: login failed", 1_700_000_000)
        for i in range(1, 38)
    ]

    incidents = context.group_into_incidents(problems, fleet_threshold=3)

    assert len(incidents) == 1
    assert incidents[0]["kind"] == "fleet"
    assert incidents[0]["host_count"] == 37
    assert len(incidents[0]["event_ids"]) == 37


def test_unrelated_problems_stay_separate():
    problems = [
        make_problem("1", "MTKWPTI01", "Disk C: low on free space", 1_700_000_000),
        make_problem("2", "MTKWPTI02", "Service not running", 1_700_000_100),
    ]

    incidents = context.group_into_incidents(problems, fleet_threshold=3)

    assert len(incidents) == 2
    assert all(i["kind"] == "single" for i in incidents)


def test_below_threshold_is_not_fleet_wide():
    """Two hosts sharing a trigger is not yet evidence of a shared cause."""
    problems = [
        make_problem("1", "MTKWPTI01", "Disk C: low on free space", 1_700_000_000),
        make_problem("2", "MTKWPTI02", "Disk C: low on free space", 1_700_000_050),
    ]

    incidents = context.group_into_incidents(problems, fleet_threshold=3)

    assert len(incidents) == 1
    assert incidents[0]["kind"] == "single"
    assert incidents[0]["host_count"] == 2


def test_fleet_incidents_sort_before_single_host_ones():
    problems = [
        make_problem("1", "MTKWPTI01", "Disk C: low on free space", 1_699_000_000, "5"),
        *[
            make_problem(str(i + 10), f"MTKWPTI{i:02d}", "MSSQL: login failed", 1_700_000_000)
            for i in range(1, 6)
        ],
    ]

    incidents = context.group_into_incidents(problems, fleet_threshold=3)

    # Fleet-wide first even though the single-host problem is more severe and
    # older: a correlated multi-host event is the more urgent signal.
    assert incidents[0]["kind"] == "fleet"
    assert incidents[1]["kind"] == "single"


def test_worst_severity_wins_within_an_incident():
    problems = [
        make_problem("1", "MTKWPTI01", "Same trigger", 1_700_000_000, "2"),
        make_problem("2", "MTKWPTI02", "Same trigger", 1_700_000_000, "5"),
        make_problem("3", "MTKWPTI03", "Same trigger", 1_700_000_000, "3"),
    ]

    incidents = context.group_into_incidents(problems, fleet_threshold=3)

    assert incidents[0]["severity"] == 5
    assert incidents[0]["severity_label"] == "Disaster"
