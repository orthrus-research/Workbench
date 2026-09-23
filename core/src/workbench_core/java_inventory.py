"""Read-only Java choices for Core setup; discovery never selects a runtime."""

from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
from typing import Any, Mapping, Sequence

from .runtime_java import JavaRuntimeError, _probe_mismatch, host_platform, probe_java


def java_candidates(*, environment: Mapping[str, str] | None = None,
                    homes: Sequence[Path] = (), roots: Sequence[Path] | None = None,
                    host: Mapping[str, str] | None = None) -> list[tuple[str, Path]]:
    """Inspect explicit homes, standard installation directories and PATH.

    Only immediate children of installation directories are considered. No
    recursive disk search, download, registry edit or ambient selection occurs.
    """
    env = os.environ if environment is None else environment
    selected_host = host_platform() if host is None else host
    executable = "java.exe" if selected_host["os"] == "windows" else "java"
    candidates = [("explicit", Path(home) / "bin" / executable) for home in homes]
    for key in ("WORKBENCH_JAVA_HOME", "JAVA_HOME", "JDK_HOME", "JAVA21_HOME", "JAVA25_HOME"):
        if env.get(key):
            candidates.append((key, Path(env[key]) / "bin" / executable))
    found = shutil.which(executable, path=env.get("PATH", ""))
    if found:
        candidates.append(("PATH", Path(found)))
    if roots is None:
        if selected_host["os"] == "windows":
            roots = [Path(env[key]) / directory for key in ("ProgramFiles", "ProgramW6432")
                     if env.get(key) for directory in ("Java", "Eclipse Adoptium", "Microsoft", "Zulu")]
            if not roots and env.get("SystemRoot"):
                roots = [Path(env["SystemRoot"]).parent / "Program Files" / directory
                         for directory in ("Java", "Eclipse Adoptium", "Microsoft", "Zulu")]
        elif selected_host["os"] == "linux":
            roots = [Path("/usr/lib/jvm"), Path("/usr/java"), Path("/opt/java")]
        else:
            roots = [Path("/Library/Java/JavaVirtualMachines")]
    for root in roots:
        try:
            children = sorted(Path(root).iterdir())
        except OSError:
            continue
        for child in children:
            home = child / "Contents/Home" if selected_host["os"] == "mac" else child
            java = home / "bin" / executable
            if java.is_file():
                candidates.append(("installation-directory", java))
    seen, result = set(), []
    for origin, java in candidates:
        # Deduplicate PATH symlinks without replacing the selected lexical path.
        identity = os.path.normcase(str(java.expanduser().resolve()))
        if identity not in seen:
            seen.add(identity)
            result.append((origin, java.expanduser().absolute()))
    return result


def inspect_java_inventory(*, policy: Mapping[str, Any] | None = None,
                           environment: Mapping[str, str] | None = None,
                           homes: Sequence[Path] = (), roots: Sequence[Path] | None = None,
                           host: Mapping[str, str] | None = None) -> dict[str, Any]:
    selected_host = host_platform() if host is None else host
    rows = []
    for origin, java in java_candidates(environment=environment, homes=homes, roots=roots, host=selected_host):
        row = {"origin": origin, "executable": str(java), "state": "unavailable",
               "compatibility": "not-assessed", "reason": None, "probe": None,
               "feature_version": None, "jdk": False}
        try:
            probe = probe_java(java)
            version = re.match(r"^(?:1\.)?(\d+)(?:[.+_-]|$)", probe["java_version"])
            if version is None:
                raise JavaRuntimeError("Java reported an unsupported version string")
            compiler = Path(probe["java_home"]) / "bin" / ("javac.exe" if selected_host["os"] == "windows" else "javac")
            reason = _probe_mismatch(probe, policy, selected_host) if policy is not None else None
            row.update(state="available", probe=probe, feature_version=int(version.group(1)),
                       jdk=compiler.is_file(), reason=reason,
                       compatibility=("incompatible" if reason else "matches-profile") if policy else "not-assessed")
        except (JavaRuntimeError, OSError, UnicodeError) as error:
            row["reason"] = str(error)
        rows.append(row)
    return {"format": "workbench-java-inventory-v1", "schema_version": 1,
            "host": dict(selected_host), "policy": dict(policy) if policy else None,
            "candidates": rows, "selected": None,
            "scope": "Runtime identity only; native action requirements and qualification remain separate."}


def render_java_inventory(record: Mapping[str, Any]) -> str:
    policy = record["policy"]
    lines = ["Java installations (no runtime selected)",
             "Required by this profile: " + (policy["runtime_identity"] if policy else "no profile selected")]
    for index, row in enumerate(record["candidates"], 1):
        probe = row["probe"]
        label = f"Java {probe['runtime_version']} / {probe['vendor']} / {probe['os_arch']}" if probe else "Unavailable"
        lines.append(f"{index}. {label} [{row['compatibility']}]\n   {row['executable']}")
        if row["reason"]:
            lines.append("   " + row["reason"])
    if not record["candidates"]:
        lines.append("No Java installations found. Supply --java-home or use profile-managed setup.")
    lines.append(record["scope"])
    return "\n".join(lines) + "\n"
