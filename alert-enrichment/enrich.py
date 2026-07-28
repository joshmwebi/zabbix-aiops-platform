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

Respond with ONLY a JSON object, no markdown fences, with these keys:
  "headline": one sentence, under 100 characters, what is happening
  "probable_cause": 1-2 sentences
  "blast_radius": what is affected in practice, and what is likely fine
  "first_action": the single next thing to check or run
  "confidence": "high" | "medium" | "low"
"""


def enrich_incident(ctx: dict[str, Any]) -> dict[str, Any]:
    """Send one incident's context to the model and return parsed triage output."""
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")

    message = client.messages.create(
        model=model,
        max_tokens=800,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": "Incident context:\n\n" + json.dumps(ctx, indent=2),
            }
        ],
    )

    text = "".join(block.text for block in message.content if block.type == "text")

    # Models occasionally wrap JSON in code fences despite instructions.
    cleaned = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```")

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Never let a formatting failure lose the alert. Degrade instead.
        return {
            "headline": ctx.get("problem", "Unparsed enrichment"),
            "probable_cause": "Model response was not valid JSON.",
            "blast_radius": "unknown",
            "first_action": "Review the raw response below.",
            "confidence": "low",
            "_raw": text,
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
