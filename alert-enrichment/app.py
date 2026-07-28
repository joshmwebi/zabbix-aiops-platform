"""HTTP endpoint that enriches a Zabbix alert the moment it fires.

This is the real-time counterpart to poll.py. It requires Zabbix-side
configuration (a Webhook media type plus an action that uses it) and an
inbound port on this host, which is why polling exists first.

Start it:

    uvicorn app:app --host 0.0.0.0 --port 8000 --app-dir alert-enrichment

Then POST an event id to /webhook. Zabbix media-type setup is documented in
this directory's README.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import context  # noqa: E402
import enrich  # noqa: E402
from pipeline.zabbix_client import ZabbixClient  # noqa: E402

load_dotenv(REPO_ROOT / ".env")

LOG_PATH = REPO_ROOT / "logs" / "enriched-alerts.jsonl"

app = FastAPI(title="Zabbix alert enrichment", version="0.1.0")


class AlertIn(BaseModel):
    """Payload Zabbix sends. eventid is the only field we strictly need —
    everything else is re-fetched from the API so the enrichment always
    reflects current state rather than whatever was true at send time."""

    eventid: str
    trigger_name: str | None = None
    host: str | None = None
    severity: str | None = None


def _client() -> ZabbixClient:
    url = os.environ.get("ZABBIX_API_URL")
    token = os.environ.get("ZABBIX_API_TOKEN")
    if not url or not token:
        raise HTTPException(500, "ZABBIX_API_URL / ZABBIX_API_TOKEN not configured")
    return ZabbixClient(url, token)


@app.get("/health")
def health() -> dict:
    """Liveness check — also confirms the Zabbix API is reachable."""
    try:
        version = _client().call("apiinfo.version")
        return {"status": "ok", "zabbix_api": version}
    except Exception as exc:
        return {"status": "degraded", "error": f"{type(exc).__name__}: {exc}"}


@app.post("/webhook")
def webhook(alert: AlertIn) -> dict:
    """Enrich a single alert and return the triage summary."""
    zbx = _client()

    problems = context.get_active_problems(zbx, min_severity=0)
    match = [p for p in problems if p["eventid"] == alert.eventid]
    if not match:
        raise HTTPException(404, f"event {alert.eventid} is not an active problem")

    threshold = int(os.environ.get("FLEET_INCIDENT_THRESHOLD", "3"))
    incidents = context.group_into_incidents(problems, fleet_threshold=threshold)

    # Find the incident this event was folded into — a fleet-wide event gets
    # the correlated view rather than a single-host one.
    incident = next(i for i in incidents if alert.eventid in i["event_ids"])

    ctx = context.build_incident_context(zbx, incident)
    triage = enrich.enrich_incident(ctx)

    record = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "source": "webhook",
        "context": ctx,
        "triage": triage,
    }
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")

    return triage
