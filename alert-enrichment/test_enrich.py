"""Tests for parsing model responses.

The gateway occasionally returns JSON wrapped in prose or code fences.
These cover the shapes seen in practice. No network or API key needed.

    python -m pytest alert-enrichment/test_enrich.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from enrich import _extract_json  # noqa: E402

GOOD = {
    "headline": "Disk filling on one host",
    "probable_cause": "Log accumulation",
    "blast_radius": "single host",
    "first_action": "check temp dirs",
    "confidence": "medium",
}


def test_bare_json():
    import json

    assert _extract_json(json.dumps(GOOD)) == GOOD


def test_fenced_json():
    import json

    text = "```json\n" + json.dumps(GOOD) + "\n```"
    assert _extract_json(text) == GOOD


def test_json_with_prose_preamble():
    """The most common real failure: a sentence before the object."""
    import json

    text = "Here is the triage summary:\n\n" + json.dumps(GOOD)
    assert _extract_json(text) == GOOD


def test_json_with_prose_on_both_sides():
    import json

    text = "Sure — analysis below.\n" + json.dumps(GOOD) + "\nLet me know if you need more."
    assert _extract_json(text) == GOOD


def test_braces_inside_string_values_do_not_break_extraction():
    """A cause mentioning a path or template with braces must still parse."""
    import json

    payload = dict(GOOD, probable_cause="Template {HOST.NAME} misconfigured")
    assert _extract_json(json.dumps(payload)) == payload


def test_unparseable_returns_none():
    assert _extract_json("I cannot analyze this incident.") is None


def test_truncated_json_returns_none():
    assert _extract_json('{"headline": "cut off mid') is None


def test_non_object_json_returns_none():
    """A bare list is valid JSON but not the contract."""
    assert _extract_json("[1, 2, 3]") is None
