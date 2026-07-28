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
        make_problem(str(i), f"node{i:02d}", "MSSQL: login failed", 1_700_000_000)
        for i in range(1, 38)
    ]

    incidents = context.group_into_incidents(problems, fleet_threshold=3)

    assert len(incidents) == 1
    assert incidents[0]["kind"] == "fleet"
    assert incidents[0]["host_count"] == 37
    assert len(incidents[0]["event_ids"]) == 37


def test_unrelated_problems_stay_separate():
    problems = [
        make_problem("1", "node01", "Disk C: low on free space", 1_700_000_000),
        make_problem("2", "node02", "Service not running", 1_700_000_100),
    ]

    incidents = context.group_into_incidents(problems, fleet_threshold=3)

    assert len(incidents) == 2
    assert all(i["kind"] == "single" for i in incidents)


def test_below_threshold_is_not_fleet_wide():
    """Two hosts sharing a trigger is not yet evidence of a shared cause."""
    problems = [
        make_problem("1", "node01", "Disk C: low on free space", 1_700_000_000),
        make_problem("2", "node02", "Disk C: low on free space", 1_700_000_050),
    ]

    incidents = context.group_into_incidents(problems, fleet_threshold=3)

    assert len(incidents) == 1
    assert incidents[0]["kind"] == "single"
    assert incidents[0]["host_count"] == 2


def test_fleet_incidents_sort_before_single_host_ones():
    problems = [
        make_problem("1", "node01", "Disk C: low on free space", 1_699_000_000, "5"),
        *[
            make_problem(str(i + 10), f"node{i:02d}", "MSSQL: login failed", 1_700_000_000)
            for i in range(1, 6)
        ],
    ]

    incidents = context.group_into_incidents(problems, fleet_threshold=3)

    # Fleet-wide first even though the single-host problem is more severe and
    # older: a correlated multi-host event is the more urgent signal.
    assert incidents[0]["kind"] == "fleet"
    assert incidents[1]["kind"] == "single"


def test_windows_instance_ids_normalize_to_one_key():
    """Real trigger names observed in the lab, differing only by instance id."""
    a = 'Windows: "webthreatdefusersvc_14fa09" (Web Threat Defense User Service_14fa09) is not running (startup type automatic)'
    b = 'Windows: "webthreatdefusersvc_4258fcc7" (Web Threat Defense User Service_4258fcc7) is not running (startup type automatic)'

    assert context.normalize_problem_name(a) == context.normalize_problem_name(b)


def test_version_numbers_normalize_to_one_key():
    a = 'Windows: "GoogleUpdaterService152.0.7933.0" (Google Updater Service (GoogleUpdaterService152.0.7933.0)) is not running (startup type automatic)'
    b = 'Windows: "GoogleUpdaterService150.0.7863.0" (Google Updater Service (GoogleUpdaterService150.0.7863.0)) is not running (startup type automatic)'

    assert context.normalize_problem_name(a) == context.normalize_problem_name(b)


def test_genuinely_different_problems_do_not_normalize_together():
    """Normalization must not over-merge unrelated triggers."""
    disk = "Windows: FS [(C:)]: Space is critically low (used > 90%, total 255.8GB)"
    agent = "Windows: Zabbix agent is not available (or nodata for 30m)"
    camsvc = 'Windows: "camsvc" (Capability Access Manager Service) is not running (startup type automatic)'

    keys = {context.normalize_problem_name(n) for n in (disk, agent, camsvc)}
    assert len(keys) == 3


def test_disk_thresholds_stay_separate():
    """'low' and 'critically low' are different triggers despite similar text."""
    low = "Windows: FS [(C:)]: Space is low (used > 80%, total 255.8GB)"
    crit = "Windows: FS [(C:)]: Space is critically low (used > 90%, total 255.8GB)"

    assert context.normalize_problem_name(low) != context.normalize_problem_name(crit)


def test_scattered_service_instances_collapse_into_one_incident():
    """Several instance-suffixed alerts for one service on a single host."""
    suffixes = ["190490", "642e4d9", "8139a4", "179687e", "1bfbe82"]
    problems = [
        make_problem(
            str(i),
            "node01",
            f'Windows: "webthreatdefusersvc_{s}" (Web Threat Defense User Service_{s}) is not running (startup type automatic)',
            1_700_000_000 + i,
        )
        for i, s in enumerate(suffixes)
    ]

    incidents = context.group_into_incidents(problems, fleet_threshold=3)

    assert len(incidents) == 1
    assert incidents[0]["instances"] == 5
    assert incidents[0]["name_variants"] == 5
    assert incidents[0]["host_count"] == 1
    # Display name is a real trigger name, annotated, never the normalized key.
    assert "webthreatdefusersvc_" in incidents[0]["name"]
    assert "variants" in incidents[0]["name"]


def test_same_service_across_hosts_becomes_fleet_incident():
    """After normalization, per-host instance ids no longer hide fleet events."""
    problems = [
        make_problem(
            str(i),
            f"node{i:02d}",
            f'Windows: "webthreatdefusersvc_{i:06x}" (Web Threat Defense User Service_{i:06x}) is not running (startup type automatic)',
            1_700_000_000,
        )
        for i in range(1, 9)
    ]

    incidents = context.group_into_incidents(problems, fleet_threshold=3)

    assert len(incidents) == 1
    assert incidents[0]["kind"] == "fleet"
    assert incidents[0]["host_count"] == 8


def test_worst_severity_wins_within_an_incident():
    problems = [
        make_problem("1", "node01", "Same trigger", 1_700_000_000, "2"),
        make_problem("2", "node02", "Same trigger", 1_700_000_000, "5"),
        make_problem("3", "node03", "Same trigger", 1_700_000_000, "3"),
    ]

    incidents = context.group_into_incidents(problems, fleet_threshold=3)

    assert incidents[0]["severity"] == 5
    assert incidents[0]["severity_label"] == "Disaster"
