"""Minimal Zabbix API client + smoke test.

Reads connection details from .env (see .env.example). Run directly to
verify connectivity: prints every host the API can see, then pulls the
most recent values for a few items on the first host.

    python pipeline/zabbix_client.py

Works identically against the local Docker sandbox and a real fleet —
only the .env differs. This module is the seed of the extraction layer;
ADR-001 explains why sources stay pluggable behind a common schema.
"""

from __future__ import annotations

import os
import sys
from typing import Any

import requests
from dotenv import load_dotenv


class ZabbixClient:
    """Thin JSON-RPC wrapper around the Zabbix API (7.0+, token auth)."""

    def __init__(self, api_url: str, api_token: str, timeout: int = 30):
        self.api_url = api_url
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Content-Type": "application/json-rpc",
                "Authorization": f"Bearer {api_token}",
            }
        )
        self._id = 0

    def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Invoke a Zabbix API method and return its result payload."""
        self._id += 1
        resp = self._session.post(
            self.api_url,
            json={
                "jsonrpc": "2.0",
                "method": method,
                "params": params or {},
                "id": self._id,
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        body = resp.json()
        if "error" in body:
            raise RuntimeError(f"Zabbix API error in {method}: {body['error']}")
        return body["result"]

    # --- convenience methods -------------------------------------------------

    def get_hosts(self) -> list[dict[str, Any]]:
        return self.call(
            "host.get",
            {"output": ["hostid", "host", "status"], "selectInterfaces": ["ip"]},
        )

    def get_items(self, hostid: str, limit: int = 10) -> list[dict[str, Any]]:
        return self.call(
            "item.get",
            {
                "output": ["itemid", "name", "key_", "lastvalue", "units"],
                "hostids": hostid,
                "sortfield": "name",
                "limit": limit,
                "filter": {"status": "0"},  # enabled items only
            },
        )


def main() -> int:
    load_dotenv()
    api_url = os.environ.get("ZABBIX_API_URL")
    api_token = os.environ.get("ZABBIX_API_TOKEN")
    if not api_url or not api_token or "your-api-token" in api_token:
        print("Missing config: copy .env.example to .env and fill in "
              "ZABBIX_API_URL and ZABBIX_API_TOKEN.")
        return 1

    zbx = ZabbixClient(api_url, api_token)

    hosts = zbx.get_hosts()
    print(f"API reachable — {len(hosts)} host(s) visible:\n")
    for h in hosts:
        ips = ", ".join(i["ip"] for i in h.get("interfaces", [])) or "-"
        state = "enabled" if h["status"] == "0" else "disabled"
        print(f"  [{h['hostid']:>6}] {h['host']:<30} {ips:<18} {state}")

    if hosts:
        first = hosts[0]
        print(f"\nSample items on {first['host']}:")
        for item in zbx.get_items(first["hostid"]):
            val = item.get("lastvalue", "")
            units = item.get("units", "")
            print(f"  {item['name']:<50} = {val} {units}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
