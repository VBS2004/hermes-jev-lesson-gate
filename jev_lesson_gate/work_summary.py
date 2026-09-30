"""Compact summary of an agent's WORK (OpenAI-format messages) for the work-lesson Noul.

It lists what a reviewer needs in order to judge whether something reusable was discovered: the commands run, the errors
hit, the files changed, and what the assistant said along the way (its reasoning about causes and fixes), ending with the
final outcome. User messages are left out on purpose: the user-side Nouls already read those.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List

_ERROR = re.compile(r"(Traceback|\w*Error\b|error:|\bFAILED\b|\b[1-9]\d* failed|No such file|command not found|"
                    r"exit code [1-9]|panic|\w*Exception\b)")
_CHANGE_TOOLS = {"write_file", "patch"}


def _args(call: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return json.loads(call.get("function", {}).get("arguments") or "{}")
    except (TypeError, ValueError):
        return {}


def summarize_work(messages: List[Dict[str, Any]], limit: int = 3500) -> str:
    lines: List[str] = []
    names: Dict[str, str] = {}
    for m in messages:
        role = m.get("role")
        if role == "assistant":
            text = m.get("content")
            if isinstance(text, str) and text.strip():
                lines.append("SAID: " + " ".join(text.split())[:300])
            for c in m.get("tool_calls") or []:
                name = c.get("function", {}).get("name", "?")
                names[c.get("id", "")] = name
                a = _args(c)
                if name == "terminal":
                    lines.append("RAN: " + " ".join(str(a.get("command", "")).split())[:160])
                elif name in _CHANGE_TOOLS:
                    lines.append(f"CHANGED: {a.get('path', '?')}")
        elif role == "tool":
            content = m.get("content") if isinstance(m.get("content"), str) else ""
            hit = _ERROR.search(content or "")
            if hit:
                start = max(0, hit.start() - 60)
                lines.append(f"ERROR ({names.get(m.get('tool_call_id', ''), 'tool')}): " + " ".join(content[start:start + 200].split()))
    # Keep the end (the outcome) and as much of the path to it as fits.
    out, size = [], 0
    for line in reversed(lines):
        if size + len(line) > limit:
            out.append("…(earlier work omitted)")
            break
        out.append(line)
        size += len(line) + 1
    return "\n".join(reversed(out)) or "(no tool work)"
