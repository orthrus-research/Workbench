"""Concise terminal rendering for Groovy language-service results."""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any


_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SEVERITY = {1: "error", 2: "warning", 3: "information", 4: "hint", None: "unspecified"}


def render_language_result(result: Mapping[str, Any]) -> str:
    summary = result["summary"]
    runtime = result["runtime"]
    service = result["service"]
    endpoint = service["endpoint"]
    lines = [
        "GroovyScript Exact Diagnostics",
        f"Status      {summary['status']} · {summary['compiler_state']}",
        f"Result      {_short(result['result_id'])}",
        f"Program     {_short(result['program']['program_id'])}",
        (
            f"Runtime     {_short(runtime['runtime_id'])} · "
            f"{runtime['mod_graph']['artifact_count']} mod artifacts · "
            f"cache {runtime['class_cache']['state']}"
        ),
        (
            f"Service     {_clean(endpoint['host'])}:{endpoint['port']} · "
            f"{service['state']} · endpoint identity unavailable"
        ),
        "",
        (
            f"{summary['checked_files']}/{summary['selected_files']} files checked · "
            f"{summary['diagnostics']} diagnostics"
        ),
    ]
    for row in service["files"]:
        lines.append(
            f"  {row['state']:<16} {_clean(row['path'])} · "
            f"canary {row['canary']['state']} · {row['compile_latency_ms']}ms"
        )
        for diagnostic in row["diagnostics"][:20]:
            start = diagnostic["range"]["start"]
            lines.append(
                "    "
                + f"{start['line'] + 1}:{start['character'] + 1} "
                + f"{_SEVERITY.get(diagnostic['severity'], 'unknown')} "
                + _clean(diagnostic["message"])
            )
        if len(row["diagnostics"]) > 20:
            lines.append(
                f"    … {len(row['diagnostics']) - 20} additional diagnostics in JSON output"
            )
    if service["failure"] is not None:
        lines.extend(
            [
                "",
                "Service failure",
                f"  {_clean(service['failure']['kind'])}: {_clean(service['failure']['message'])}",
            ]
        )
    lines.extend(
        [
            "",
            "Boundary",
            "  The canary binds diagnostics to exact in-memory source. Canonicalization is not script execution or registry acceptance.",
            "  GroovyScript 1.4.3 cannot authenticate which runtime owns the TCP endpoint; the exact local runtime inventory remains caller context.",
        ]
    )
    return "\n".join(lines) + "\n"


def _clean(value: object) -> str:
    text = str(value).replace("\x1b", "\\x1b").replace("\r", "\\r").replace("\n", "\\n")
    return _CONTROL_RE.sub(lambda match: f"\\x{ord(match.group(0)):02x}", text)


def _short(value: str) -> str:
    return value.rsplit(":", 1)[-1][:16]


__all__ = ["render_language_result"]
