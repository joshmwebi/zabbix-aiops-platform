"""Turn assembled alert context into a triage summary using an LLM.

The model is given only what context.py collected — it never queries Zabbix
itself. Keeping retrieval and reasoning separate means the same context can
be replayed against a different model or prompt, and a bad answer can be
debugged by looking at exactly what the model was shown.
"""

from __future__ import annotations

import json
import os
from typing import Any

from anthropic import Anthropic

SYSTEM_PROMPT = """\
You are an infrastructure triage assistant for a Zabbix-monitored fleet of
Windows VMs running power-systems simulation workloads (Aurora, PLEXOS,
PSS/E) plus supporting SQL Server and license infrastructure.

You will be given the context around one incident. Produce a concise triage
summary for an on-call engineer.

Rules:
- Be concrete. Name the most likely cause, not a list of everything possible.
- If several hosts show an identical trigger at the same moment, treat a
  shared upstream dependency (domain controller, DNS, network path, storage,
  license server) as far more likely than N independent failures, and say so.
- Recommend a first diagnostic step that is specific and safe to run.
- If the evidence is thin, say the confidence is low rather than inventing a
  cause. Guessing confidently is worse than admitting uncertainty.
- Never recommend destructive actions (restarting production services,
  deleting data) as a first step.

Your entire response must be a single JSON object and nothing else — no
preamble, no explanation before or after it, no markdown code fences.
Keys:
  "headline": one sentence, under 100 characters, what is happening
  "probable_cause": 1-2 sentences
  "blast_radius": what is affected in practice, and what is likely fine
  "first_action": the single next thing to check or run
  "confidence": "high" | "medium" | "low"
"""


def _extract_json(text: str) -> dict[str, Any] | None:
    """Pull a JSON object out of a model response.

    Models sometimes wrap the object in code fences, or prefix it with a
    sentence of preamble despite instructions. Rather than trusting the
    response to be bare JSON, scan for the first balanced {...} span and
    parse that. Returns None if nothing parses.
    """
    stripped = text.strip()

    # Fast path: the whole thing is JSON, possibly fenced.
    for candidate in (
        stripped,
        stripped.removeprefix("```json").removeprefix("```").removesuffix("```").strip(),
    ):
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    # Slow path: find the first balanced brace span, ignoring braces that
    # appear inside string literals.
    depth = 0
    start = None
    in_string = False
    escaped = False
    for i, ch in enumerate(stripped):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    parsed = json.loads(stripped[start : i + 1])
                    if isinstance(parsed, dict):
                        return parsed
                except json.JSONDecodeError:
                    pass
                start = None
    return None


def enrich_incident(ctx: dict[str, Any]) -> dict[str, Any]:
    """Send one incident's context to the model and return parsed triage output."""
    # base_url lets this point at an internal/enterprise LLM gateway instead of
    # the public API — useful where telemetry must stay on approved
    # infrastructure. Unset means the SDK's default endpoint.
    client = Anthropic(
        api_key=os.environ["ANTHROPIC_API_KEY"],
        base_url=os.environ.get("ANTHROPIC_BASE_URL") or None,
    )
    model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")

    message = client.messages.create(
        model=model,
        # Generous ceiling: a response cut off mid-object produces JSON with
        # no closing brace, which no parser can recover. Truncation was the
        # cause of the remaining parse failures in early runs.
        max_tokens=int(os.environ.get("ANTHROPIC_MAX_TOKENS", "1500")),
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": "Incident context:\n\n" + json.dumps(ctx, indent=2),
            }
        ],
    )

    text = "".join(block.text for block in message.content if block.type == "text")

    # A truncated response can't be parsed; surface the reason rather than
    # reporting a generic "not valid JSON".
    truncated = getattr(message, "stop_reason", None) == "max_tokens"

    parsed = _extract_json(text)
    if parsed is not None:
        # Guarantee the keys the formatter and sinks expect, so a partial
        # response can't cause a KeyError downstream.
        return {
            "headline": parsed.get("headline", ctx.get("problem", "")),
            "probable_cause": parsed.get("probable_cause", "-"),
            "blast_radius": parsed.get("blast_radius", "-"),
            "first_action": parsed.get("first_action", "-"),
            "confidence": parsed.get("confidence", "low"),
        }

    # Never let a formatting failure lose the alert. Degrade, and keep the
    # raw text so the failure can be diagnosed from the log.
    return {
        "headline": ctx.get("problem", "Unparsed enrichment"),
        "probable_cause": (
            "Model response was truncated (hit max_tokens) — raise "
            "ANTHROPIC_MAX_TOKENS."
            if truncated
            else "Model response was not valid JSON."
        ),
        "blast_radius": "unknown",
        "first_action": "Review the raw response in logs/enriched-alerts.jsonl.",
        "confidence": "low",
        "_raw": text[:2000],
    }


def format_for_console(ctx: dict[str, Any], triage: dict[str, Any]) -> str:
    """Human-readable rendering for stdout and the log file."""
    hosts = ctx["affected_hosts"]
    host_str = ", ".join(hosts[:6]) + (f" (+{len(hosts) - 6} more)" if len(hosts) > 6 else "")
    conf = triage.get("confidence", "?").upper()

    return "\n".join(
        [
            "=" * 78,
            f"[{ctx['severity']}] {triage.get('headline', ctx['problem'])}",
            "=" * 78,
            f"Trigger      : {ctx['problem']}",
            f"Hosts ({ctx['host_count']:>2})   : {host_str}",
            f"First seen   : {ctx['first_seen']}",
            "",
            f"Probable cause ({conf} confidence):",
            f"  {triage.get('probable_cause', '-')}",
            "",
            "Blast radius:",
            f"  {triage.get('blast_radius', '-')}",
            "",
            "First action:",
            f"  {triage.get('first_action', '-')}",
            "",
        ]
    )
