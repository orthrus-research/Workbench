"""Terminal rendering for managed Groovy language sessions."""

from __future__ import annotations

import shlex
from typing import Any, Mapping


def render_session_event(event: Mapping[str, Any]) -> str:
    state = event.get("state")
    details = event.get("details") if isinstance(event.get("details"), Mapping) else {}
    if state == "preflight-complete":
        command = details.get("command")
        rendered = (
            shlex.join(str(item) for item in command)
            if isinstance(command, list)
            else "<unavailable>"
        )
        mutations = details.get("intended_mutations")
        lines = [
            "Managed Groovy language-session plan",
            f"  Runtime: {details.get('runtime_id')}",
            f"  Instance: {details.get('instance_id')}",
            f"  Working directory: {details.get('cwd')}",
            f"  Command: {rendered}",
            "  Checked temporary mutations:",
        ]
        if isinstance(mutations, list):
            lines.extend(f"    - {item}" for item in mutations)
        return "\n".join(lines) + "\n"
    if state == "endpoint-reserved":
        return f"Reserved loopback endpoint {details.get('host')}:{details.get('port')}.\n"
    if state == "overlays-applied":
        return "Applied checked JVM-start and language-server-port overlays.\n"
    if state == "client-launch-started":
        return f"Started the exact client launch (launcher PID {details.get('launcher_pid')}).\n"
    if state == "readiness-confirmed":
        return (
            "GroovyScript initialize + diagnostic canary confirmed "
            f"after {details.get('attempts')} attempt(s).\n"
        )
    if state == "ready":
        return (
            "Managed Groovy language session is ready.\n"
            f"  Endpoint: {details.get('host')}:{details.get('port')}\n"
            f"  IDE descriptor: {details.get('descriptor_path')}\n"
            "  IntelliJ, VS Code, or one terminal client may connect; Ctrl+C closes the owned session.\n"
        )
    if state == "failure":
        return f"Managed session failure: {details.get('message')}\n"
    if state == "closed":
        return f"Managed session closed cleanly ({details.get('shutdown_reason')}).\n"
    if state == "blocked":
        errors = details.get("cleanup_errors")
        suffix = ""
        if isinstance(errors, list) and errors:
            suffix = "\n" + "\n".join(f"  - {item}" for item in errors)
        return f"Managed session is blocked ({details.get('outcome')}).{suffix}\n"
    return ""


def render_session_receipt(receipt: Mapping[str, Any]) -> str:
    endpoint = receipt["endpoint"]
    readiness = receipt["readiness"]
    shutdown = receipt["shutdown"]
    handoff = receipt.get("handoff")
    descriptor_path = (
        handoff["artifacts"]["descriptor_path"]
        if isinstance(handoff, Mapping)
        else "not emitted"
    )
    return (
        "Managed Groovy language-session receipt\n"
        f"  State: {receipt['state']} ({receipt['outcome']})\n"
        f"  Session: {receipt['session_id']}\n"
        f"  Endpoint: {endpoint['host']}:{endpoint['port']}\n"
        f"  Readiness: {readiness['state']} ({readiness['attempts']} attempt(s))\n"
        f"  IDE descriptor: {descriptor_path}\n"
        f"  Shutdown: {shutdown['reason']}"
        f"; forced={str(shutdown['forced']).lower()}"
        f"; orphaned={len(shutdown['orphaned_pids'])}\n"
        f"  Receipt: {receipt['receipt_id']}\n"
    )


__all__ = ["render_session_event", "render_session_receipt"]
