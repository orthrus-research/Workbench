"""Launch one retained SUSY constituent candidate on a dedicated server.

This is deliberately a thin executable slice.  It consumes a passed retained
``workbench dev build`` result and an explicit, already-audited SUSY server
template.  The template and retained build stay immutable; each attempt gets a
fresh disposable projection, one exact Packwiz-baseline replacement, a fresh
world/log boundary, and direct process-group custody.
"""

from __future__ import annotations

from urllib.request import url2pathname

from workbench_profile_supersymmetry.server_observation import (FML_LOADED_TEXT, GROOVY_SCRIPT_FAILURE_TEXT, LOG_ENTRY_RE, SERVER_READY_RE, SHUTDOWN_ACKNOWLEDGMENTS, SUSY_PACK_READY_RE, TERMINAL_RE, _shutdown_acknowledgment)

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import stat
import subprocess
import time
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse
from zipfile import BadZipFile, ZipFile

from .runtime_compatibility import RuntimeCompatibilityError, apply_compatibility_patches
from workbench_core.runtime_java import JavaRuntimeError, ensure_java_runtime, host_platform
from .runtime_launch import RuntimeLaunchError, _selected_java
from workbench_api.state_paths import default_suite_state_root
from .susy_mod_dev import (
    RESULT_FORMAT,
    SusyModDevError,
    _digest,
    _load_pack,
    _runtime_tree,
)
from .susy_mod_launch import (
    MOD_ID_RE,
    RUN_ID_RE,
    SusyModLaunchError,
    _compatibility_experiment_scopes,
    _compile_probe_agent,
    _diagnose_crash,
    _file_digest,
    _read_json,
    _stop_process_group,
    _validate_loaded_source_proof,
)
from workbench_profile_supersymmetry.console import (
    COMPATIBILITY_EXPERIMENTS,
    SERVER_EXPERIMENTS,
    SERVER_SHUTDOWN_EXPERIMENT,
)


SERVER_RESULT_FORMAT = "workbench-susy-mod-server-launch-result-v2"
SERVER_RECEIPT_FORMAT = "workbench-susy-mod-server-launch-receipt-v2"
MAX_LOG_BYTES = 64 * 1024 * 1024
MAX_DIAGNOSTIC_SAMPLES = 12


class SusyModServerError(RuntimeError):
    """A retained SUSY candidate cannot be exercised safely on the server."""


def _direct_javaagent_argument(value: str) -> str:
    """Remove config-file quoting before passing one argument to execve."""

    quoted_prefix = '-javaagent:"'
    if value.startswith(quoted_prefix):
        closing = value.find('"=', len(quoted_prefix))
        if closing < 0:
            raise SusyModServerError("candidate load probe argument is malformed")
        return "-javaagent:" + value[len(quoted_prefix) : closing] + value[closing + 1 :]
    if not value.startswith("-javaagent:"):
        raise SusyModServerError("candidate load probe argument is malformed")
    return value




def _log_diagnostics(text: str) -> dict[str, Any]:
    counts = {level: 0 for level in ("INFO", "WARN", "ERROR", "FATAL")}
    logger_counts: dict[str, dict[str, int]] = {}
    samples: list[dict[str, Any]] = []
    total = 0
    for line_number, line in enumerate(text.splitlines(), start=1):
        match = LOG_ENTRY_RE.fullmatch(line)
        if match is None:
            continue
        total += 1
        level = match.group("level")
        logger = match.group("logger")
        counts[level] += 1
        by_level = logger_counts.setdefault(logger, {})
        by_level[level] = by_level.get(level, 0) + 1
        if level in {"ERROR", "FATAL"} and len(samples) < MAX_DIAGNOSTIC_SAMPLES:
            samples.append(
                {
                    "line": line_number,
                    "level": level,
                    "logger": logger,
                    "message": match.group("message")[:512],
                }
            )
    ranked_loggers = sorted(
        (
            {
                "logger": logger,
                "fatal": by_level.get("FATAL", 0),
                "error": by_level.get("ERROR", 0),
                "warn": by_level.get("WARN", 0),
            }
            for logger, by_level in logger_counts.items()
            if by_level.get("FATAL", 0) or by_level.get("ERROR", 0)
        ),
        key=lambda row: (-row["fatal"], -row["error"], str(row["logger"])),
    )
    issues = counts["FATAL"] > 0 or counts["ERROR"] > 0
    return {
        "format": "workbench-minecraft-log-diagnostics-v1",
        "scope": "fresh-world bootstrap through immediate shutdown",
        "state": "issues-observed" if issues else "no-error-or-fatal-observed",
        "smoke_outcome_affected": False,
        "candidate_attribution": "not-determined-no-baseline-control",
        "entry_counts": {
            "total": total,
            "info": counts["INFO"],
            "warn": counts["WARN"],
            "error": counts["ERROR"],
            "fatal": counts["FATAL"],
        },
        "top_issue_loggers": ranked_loggers[:12],
        "samples": samples,
    }


def _shutdown_bridge_observation(text: str) -> dict[str, Any]:
    entered = 0
    supplied: list[int] = []
    removed: list[int] = []
    supply_prefix = (
        "Workbench supplied the bounded shutdown ticket collection for dimension "
    )
    remove_prefix = (
        "Workbench removed the bounded shutdown ticket collection for dimension "
    )
    for match in LOG_ENTRY_RE.finditer(text):
        if (
            match.group("thread") != "Server thread"
            or match.group("level") != "INFO"
            or match.group("logger") != "workbench_runtime_graph"
        ):
            continue
        message = match.group("message")
        if message == "Workbench shutdown compatibility entered the stopping lifecycle":
            entered += 1
        elif message.startswith(supply_prefix):
            try:
                supplied.append(int(message.removeprefix(supply_prefix)))
            except ValueError:
                continue
        elif message.startswith(remove_prefix):
            try:
                removed.append(int(message.removeprefix(remove_prefix)))
            except ValueError:
                continue
    balanced = bool(
        supplied
        and len(supplied) == len(set(supplied))
        and len(removed) == len(set(removed))
        and sorted(supplied) == sorted(removed)
    )
    return {
        "complete": entered == 1 and balanced,
        "entered_count": entered,
        "supplied_dimensions": sorted(supplied),
        "removed_dimensions": sorted(removed),
        "balanced": balanced,
    }


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    raw = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise SusyModServerError(f"cannot retain server launch record: {path}") from exc


def _safe_run_root(suite: Path, run_id: str) -> Path:
    if RUN_ID_RE.fullmatch(run_id) is None:
        raise SusyModServerError("launch-server requires an exact retained SUSY run ID")
    runs = (default_suite_state_root(suite) / "dev-runs").resolve()
    lexical = runs / run_id
    if lexical.is_symlink():
        raise SusyModServerError(f"retained SUSY run is unsafe: {run_id}")
    resolved = lexical.resolve()
    if resolved.parent != runs or not resolved.is_dir():
        raise SusyModServerError(f"retained SUSY run is missing: {run_id}")
    return resolved


def _regular_file(path: Path, label: str) -> Path:
    try:
        info = path.lstat()
    except OSError as exc:
        raise SusyModServerError(f"{label} is missing: {path}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise SusyModServerError(f"{label} must be a regular file: {path}")
    return path.resolve()


def _validate_retained_build(
    suite: Path, run_id: str
) -> tuple[
    Path,
    dict[str, Any],
    Path,
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
]:
    run_root = _safe_run_root(suite, run_id)
    try:
        result = _read_json(run_root / "result.json", "SUSY mod build result")
    except SusyModLaunchError as exc:
        raise SusyModServerError(str(exc)) from exc
    if (
        result.get("format") != RESULT_FORMAT
        or result.get("schema_version") != 1
        or result.get("run_id") != run_id
        or result.get("outcome") != "passed"
        or result.get("failed_stage") is not None
    ):
        raise SusyModServerError("retained result is not a passing SUSY build")
    without_id = {key: value for key, value in result.items() if key != "result_id"}
    if result.get("result_id") != "workbench-susy-mod-dev-result:" + _digest(without_id):
        raise SusyModServerError("retained SUSY result identity has drifted")

    replacement = result.get("replacement")
    match = replacement.get("match") if isinstance(replacement, dict) else None
    selected = match.get("selected") if isinstance(match, dict) else None
    if (
        not isinstance(selected, dict)
        or match.get("state") != "exact"
        or selected.get("side") not in {"both", "server"}
    ):
        raise SusyModServerError(
            "retained candidate is not applicable to a dedicated server"
        )
    filename = selected.get("filename")
    baseline = selected.get("baseline")
    if (
        not isinstance(filename, str)
        or not filename
        or Path(filename).name != filename
        or not isinstance(baseline, dict)
        or baseline.get("hash_format") not in {"sha1", "sha256"}
        or not isinstance(baseline.get("hash"), str)
    ):
        raise SusyModServerError("retained Packwiz replacement identity is invalid")

    artifacts = result.get("artifact_set")
    if not isinstance(artifacts, list) or len(artifacts) != 1:
        raise SusyModServerError("retained build lacks one selected candidate artifact")
    artifact = artifacts[0]
    mod_ids = artifact.get("mod_ids") if isinstance(artifact, dict) else None
    if (
        not isinstance(artifact, dict)
        or not isinstance(artifact.get("path"), str)
        or not isinstance(artifact.get("sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", artifact["sha256"]) is None
        or isinstance(artifact.get("size"), bool)
        or not isinstance(artifact.get("size"), int)
        or artifact["size"] <= 0
        or not isinstance(mod_ids, list)
        or not mod_ids
        or len(set(mod_ids)) != len(mod_ids)
        or any(not isinstance(item, str) or MOD_ID_RE.fullmatch(item) is None for item in mod_ids)
    ):
        raise SusyModServerError("retained candidate artifact identity is invalid")
    lexical_artifact = Path(artifact["path"])
    if lexical_artifact.is_symlink():
        raise SusyModServerError("retained candidate artifact is a symbolic link")
    artifact_path = _regular_file(lexical_artifact, "retained candidate artifact")
    if not artifact_path.is_relative_to(run_root):
        raise SusyModServerError("retained candidate artifact escapes its run")
    observed_sha256, observed_size = _file_digest(artifact_path, "sha256")
    if observed_sha256 != artifact["sha256"] or observed_size != artifact["size"]:
        raise SusyModServerError("retained candidate artifact has drifted")

    retained_pack = result.get("supersymmetry")
    pack_root = retained_pack.get("root") if isinstance(retained_pack, dict) else None
    if not isinstance(pack_root, str):
        raise SusyModServerError("retained result lacks its Supersymmetry checkout")
    try:
        current_pack = _load_pack(pack_root)
    except SusyModDevError as exc:
        raise SusyModServerError(str(exc)) from exc
    public_pack = {key: value for key, value in current_pack.items() if key != "entries"}
    if public_pack != retained_pack:
        raise SusyModServerError(
            "Supersymmetry checkout identity has drifted since the retained build"
        )
    return (
        run_root,
        result,
        artifact_path,
        dict(artifact),
        dict(selected),
        current_pack,
    )


def _validate_template(
    template_value: Path | str,
    *,
    pack: Mapping[str, Any],
    filename: str,
    baseline: Mapping[str, Any],
    selected_experiments: Sequence[str],
    disabled_optional_metadata: frozenset[str] = frozenset(),
) -> tuple[Path, dict[str, Any], Path, str]:
    lexical = Path(template_value).expanduser()
    if lexical.is_symlink():
        raise SusyModServerError("server template must not be a symbolic link")
    template = lexical.resolve()
    if not template.is_dir():
        raise SusyModServerError(f"server template is missing: {template}")
    for relative in ("libraries", "mods"):
        path = template / relative
        if path.is_symlink() or not path.is_dir():
            raise SusyModServerError(f"server template lacks a safe {relative}/ directory")
    if (template / "world").exists() or (template / "world").is_symlink():
        raise SusyModServerError("server template must be world-free")
    eula = _regular_file(template / "eula.txt", "server template EULA")
    try:
        eula_text = eula.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise SusyModServerError("server template EULA is unreadable") from exc
    if not any(line.strip().casefold() == "eula=true" for line in eula_text.splitlines()):
        raise SusyModServerError("server template does not record EULA acceptance")
    _regular_file(template / "server.properties", "server template properties")
    launchers = [
        path
        for path in sorted(template.glob("cleanroom-*.jar"))
        if path.is_file() and not path.is_symlink()
    ]
    if len(launchers) != 1:
        raise SusyModServerError("server template must contain one Cleanroom launcher JAR")
    baseline_path = _regular_file(
        template / "mods" / filename, "server template baseline artifact"
    )
    algorithm = str(baseline["hash_format"])
    observed, _size = _file_digest(baseline_path, algorithm)
    if observed != baseline.get("hash"):
        raise SusyModServerError("server template baseline differs from Packwiz")
    try:
        summary, records = _runtime_tree(template)
    except (OSError, ValueError, SusyModDevError) as exc:
        raise SusyModServerError(f"server template is unsafe: {exc}") from exc

    inherited_overlays: list[dict[str, Any]] = []
    overlay_path = (
        template
        / "workbench-inputs"
        / "susy-reccomplex-overlay-receipt-v1.json"
    )
    if overlay_path.exists() or overlay_path.is_symlink():
        measured_overlay = _regular_file(
            overlay_path, "server template compatibility-overlay receipt"
        )
        try:
            overlay = json.loads(measured_overlay.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise SusyModServerError(
                "server template compatibility-overlay receipt is invalid"
            ) from exc
        if (
            not isinstance(overlay, dict)
            or overlay.get("format")
            != "workbench-cleanroom-compatibility-overlay-receipt-v1"
            or overlay.get("schema_version") != 1
            or overlay.get("overlay_id")
            != "susy-reccomplex-modify-variable-name-v1"
            or any(
                re.fullmatch(r"[0-9a-f]{64}", str(overlay.get(key))) is None
                for key in (
                    "input_artifact_sha256",
                    "input_entry_sha256",
                    "output_artifact_sha256",
                    "output_entry_sha256",
                    "overlay_manifest_sha256",
                )
            )
        ):
            raise SusyModServerError(
                "server template compatibility-overlay receipt is unsupported"
            )
        output_matches = [
            relative
            for relative, record in records.items()
            if relative.startswith("mods/")
            and record.get("sha256") == overlay["output_artifact_sha256"]
        ]
        if len(output_matches) != 1:
            raise SusyModServerError(
                "server template compatibility overlay does not bind one installed mod"
            )
        inherited_overlays.append(
            {
                **overlay,
                "artifact_path": output_matches[0],
                "receipt_sha256": sha256(measured_overlay.read_bytes()).hexdigest(),
            }
        )
        if "susy-reccomplex-arg3" in selected_experiments:
            raise SusyModServerError(
                "susy-reccomplex-arg3 conflicts with the template's inverse SusyCore overlay"
            )

    entries = pack.get("entries")
    if not isinstance(entries, list):
        raise SusyModServerError("retained Supersymmetry mod inventory is unavailable")
    server_entries = [
        entry
        for entry in entries
        if (
            isinstance(entry, dict)
            and entry.get("side") in {"both", "server"}
            and entry.get("metadata_path") not in disabled_optional_metadata
        )
    ]
    expected_filenames = [str(entry.get("filename")) for entry in server_entries]
    if len(set(expected_filenames)) != len(expected_filenames):
        raise SusyModServerError("Supersymmetry server mod filenames are ambiguous")
    overlay_outputs = {
        str(row["artifact_path"]): str(row["output_artifact_sha256"])
        for row in inherited_overlays
    }
    pack_matches = 0
    overlay_matches = 0
    for entry in server_entries:
        entry_filename = entry.get("filename")
        entry_baseline = entry.get("baseline")
        if (
            not isinstance(entry_filename, str)
            or Path(entry_filename).name != entry_filename
            or not isinstance(entry_baseline, dict)
            or entry_baseline.get("hash_format") not in {"sha1", "sha256"}
            or not isinstance(entry_baseline.get("hash"), str)
        ):
            raise SusyModServerError("Supersymmetry server mod identity is invalid")
        relative = "mods/" + entry_filename
        installed = _regular_file(
            template / relative, f"Supersymmetry server mod {entry_filename}"
        )
        observed, _ = _file_digest(installed, str(entry_baseline["hash_format"]))
        if observed == entry_baseline["hash"]:
            pack_matches += 1
            continue
        record = records.get(relative)
        if (
            relative not in overlay_outputs
            or not isinstance(record, dict)
            or record.get("sha256") != overlay_outputs[relative]
        ):
            raise SusyModServerError(
                f"server template mod differs from Packwiz without an admitted overlay: {entry_filename}"
            )
        overlay_matches += 1
    actual_mod_jars = {
        relative.removeprefix("mods/")
        for relative in records
        if relative.startswith("mods/")
        and "/" not in relative.removeprefix("mods/")
        and relative.casefold().endswith(".jar")
    }
    extra_mods = sorted(actual_mod_jars - set(expected_filenames))
    if len(extra_mods) > 1 or any(
        re.fullmatch(r"workbench-ultimate-runtime-graph-producer-[^/]+\.jar", item)
        is None
        for item in extra_mods
    ):
        raise SusyModServerError(
            "server template contains undeclared mod JARs: " + ", ".join(extra_mods)
        )
    launcher_sha256, launcher_size = _file_digest(launchers[0], "sha256")
    return template, {
        "root_uri": template.as_uri(),
        "payload": summary,
        "launcher": {
            "path": launchers[0].name,
            "sha256": launcher_sha256,
            "size": launcher_size,
        },
        "eula_accepted": True,
        "provenance": {
            "state": "explicit-experimental-input",
            "support_authority_inferred": False,
        },
        "pack_binding": {
            "manifest_sha256": pack.get("manifest_sha256"),
            "index": pack.get("index"),
            "server_entry_count": len(server_entries),
            "pack_baseline_matches": pack_matches,
            "inherited_overlay_matches": overlay_matches,
            "extra_experimental_mods": extra_mods,
        },
        "inherited_compatibility_overlays": inherited_overlays,
    }, baseline_path, launchers[0].name


def _set_properties(path: Path) -> dict[str, Any]:
    try:
        before = path.read_bytes()
        text = before.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise SusyModServerError("server.properties is unreadable") from exc
    replacements = {
        "server-ip": "127.0.0.1",
        "server-port": "0",
        "online-mode": "false",
        "enable-query": "false",
        "enable-rcon": "false",
        "level-name": "workbench-dev-world",
    }
    seen: set[str] = set()
    output: list[str] = []
    for line in text.splitlines():
        key, separator, _value = line.partition("=")
        if separator and key in replacements:
            output.append(f"{key}={replacements[key]}")
            seen.add(key)
        else:
            output.append(line)
    for key in sorted(set(replacements) - seen):
        output.append(f"{key}={replacements[key]}")
    after = ("\n".join(output) + "\n").encode("utf-8")
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("xb") as stream:
        stream.write(after)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    return {
        "before_sha256": sha256(before).hexdigest(),
        "after_sha256": sha256(after).hexdigest(),
        "values": replacements,
    }


def _clear_generated_state(server: Path) -> list[str]:
    removed: list[str] = []
    names = (
        "logs",
        "crash-reports",
        "world",
        "workbench-dev-world",
        "cache",
        "vintagefix/transformerCache",
    )
    for name in names:
        target = server / name
        if target.is_symlink():
            raise SusyModServerError(f"server generated-state path is a symlink: {name}")
        if target.is_dir():
            shutil.rmtree(target)
            removed.append(name)
        elif target.exists():
            target.unlink()
            removed.append(name)
    for pattern in ("hs_err_pid*.log", "replay_pid*.log"):
        for target in server.glob(pattern):
            if target.is_symlink() or not target.is_file():
                raise SusyModServerError("server JVM evidence path is unsafe")
            target.unlink()
            removed.append(target.name)
    return sorted(removed)


def _shutdown_bridge(
    server: Path,
    *,
    suite: Path,
    run_id: str,
    candidate_sha256: str,
    template_sha256: str,
) -> tuple[list[str], dict[str, Any]]:
    """Bind and activate the template's narrow UniversalModCore stop bridge."""

    manifest_path = _regular_file(
        server / "workbench-inputs/cleanroom-runtime-compatibility-shims-v1.json",
        "server shutdown-compatibility manifest",
    )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SusyModServerError("server shutdown-compatibility manifest is invalid") from exc
    shims = manifest.get("shims") if isinstance(manifest, dict) else None
    selected = [
        row
        for row in shims or []
        if isinstance(row, dict)
        and row.get("shim_id") == "universal-mod-core-clean-shutdown-v1"
    ]
    if (
        manifest.get("format")
        != "workbench-supersymmetry-cleanroom-runtime-compatibility-shims-v1"
        or manifest.get("schema_version") != 1
        or len(selected) != 1
    ):
        raise SusyModServerError("server template lacks the admitted shutdown bridge")

    authority_path = _regular_file(
        suite
        / "profiles/packs/supersymmetry/atlas/runtime-graph"
        / "cleanroom-runtime-compatibility-shims-v1.json",
        "Supersymmetry shutdown-compatibility authority",
    )
    try:
        authority = json.loads(authority_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SusyModServerError(
            "Supersymmetry shutdown-compatibility authority is invalid"
        ) from exc
    authority_rows = authority.get("shims") if isinstance(authority, dict) else None
    admitted = [
        row
        for row in authority_rows or []
        if isinstance(row, dict)
        and row.get("shim_id") == "universal-mod-core-clean-shutdown-v1"
    ]
    if (
        authority.get("format")
        != "workbench-supersymmetry-cleanroom-runtime-compatibility-shims-v1"
        or authority.get("schema_version") != 1
        or len(admitted) != 1
    ):
        raise SusyModServerError(
            "Supersymmetry shutdown-compatibility authority lacks one admitted bridge"
        )
    template_row = selected[0]
    authority_row = admitted[0]
    for key in ("failure_binding", "implementation_class", "profile_state"):
        if template_row.get(key) != authority_row.get(key):
            raise SusyModServerError(
                "server template shutdown bridge differs from pack authority"
            )
    declaration_differences: list[str] = []
    for section in ("activation", "behavior"):
        template_section = template_row.get(section)
        authority_section = authority_row.get(section)
        if not isinstance(template_section, dict) or not isinstance(
            authority_section, dict
        ):
            raise SusyModServerError(
                "server template shutdown bridge declaration is incomplete"
            )
        for key, value in template_section.items():
            if authority_section.get(key) != value:
                declaration_differences.append(f"{section}.{key}")

    failure_binding = authority_row.get("failure_binding")
    universal_mod_core = (
        failure_binding.get("universal_mod_core")
        if isinstance(failure_binding, dict)
        else None
    )
    if not isinstance(universal_mod_core, dict):
        raise SusyModServerError("shutdown bridge authority lacks its affected mod")
    affected_filename = universal_mod_core.get("filename")
    affected_sha256 = universal_mod_core.get("sha256")
    if not isinstance(affected_filename, str) or not isinstance(affected_sha256, str):
        raise SusyModServerError("shutdown bridge affected-mod identity is invalid")
    affected_path = _regular_file(
        server / "mods" / affected_filename,
        "shutdown bridge affected UniversalModCore artifact",
    )
    observed_affected_sha256, affected_size = _file_digest(affected_path, "sha256")
    if observed_affected_sha256 != affected_sha256:
        raise SusyModServerError(
            "server template UniversalModCore differs from shutdown-bridge authority"
        )
    producers = [
        path
        for path in server.glob("mods/workbench-ultimate-runtime-graph-producer-*.jar")
        if path.is_file() and not path.is_symlink()
    ]
    if len(producers) != 1:
        raise SusyModServerError("server shutdown bridge lacks one implementation JAR")
    implementation_entry = (
        str(authority_row.get("implementation_class", "")).replace(".", "/")
        + ".class"
    )
    try:
        with ZipFile(producers[0]) as archive:
            if implementation_entry not in archive.namelist():
                raise SusyModServerError(
                    "server shutdown bridge implementation JAR lacks the admitted class"
                )
    except (OSError, BadZipFile) as exc:
        raise SusyModServerError(
            "server shutdown bridge implementation JAR is invalid"
        ) from exc
    manifest_sha256, manifest_size = _file_digest(manifest_path, "sha256")
    authority_sha256, authority_size = _file_digest(authority_path, "sha256")
    producer_sha256, producer_size = _file_digest(producers[0], "sha256")
    binding = {
        "experiment_id": SERVER_SHUTDOWN_EXPERIMENT,
        "run_id": run_id,
        "candidate_sha256": candidate_sha256,
        "template_tree_sha256": template_sha256,
        "manifest_sha256": manifest_sha256,
        "producer_sha256": producer_sha256,
    }
    launch_sha256 = sha256(_canonical(binding)).hexdigest()
    properties = [
        "-Dworkbench.runtimeGraph.compatibility_only=true",
        "-Dworkbench.runtimeGraph.enabled=false",
        "-Dworkbench.runtimeGraph.shutdown_compatibility_id="
        "universal-mod-core-clean-shutdown-v1",
        "-Dworkbench.runtimeGraph.shutdown_compatibility_launch_sha256="
        + launch_sha256,
        "-Dworkbench.runtimeGraph.shutdown_compatibility_manifest_sha256="
        + manifest_sha256,
    ]
    return properties, {
        "experiment_id": SERVER_SHUTDOWN_EXPERIMENT,
        "operation": "template-provided-system-property-activation",
        "scope": "disposable dedicated-server projection only",
        "binding": binding,
        "launch_sha256": launch_sha256,
        "manifest": {
            "path": "workbench-inputs/cleanroom-runtime-compatibility-shims-v1.json",
            "sha256": manifest_sha256,
            "size": manifest_size,
        },
        "authority": {
            "path": authority_path.relative_to(suite).as_posix(),
            "sha256": authority_sha256,
            "size": authority_size,
            "profile_state": authority_row.get("profile_state"),
            "template_declaration_state": (
                "matches-current-authority"
                if not declaration_differences
                else "stale-relative-to-current-authority"
            ),
            "template_declaration_differences": declaration_differences,
        },
        "affected_mod": {
            "path": "mods/" + affected_filename,
            "sha256": observed_affected_sha256,
            "size": affected_size,
        },
        "implementation": {
            "path": "mods/" + producers[0].name,
            "sha256": producer_sha256,
            "size": producer_size,
        },
    }


def _copy_template(template: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=False)
    staging = destination.parent / ("." + destination.name + ".staging")
    if staging.exists() or staging.is_symlink() or destination.exists() or destination.is_symlink():
        raise SusyModServerError("server projection target already exists")
    try:
        shutil.copytree(template, staging, copy_function=shutil.copy2)
        staging.rename(destination)
    except OSError as exc:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise SusyModServerError("cannot create the disposable server projection") from exc


def _latest_text(path: Path) -> str:
    return _bounded_log_text(path, "server latest.log")


def _bounded_log_text(path: Path, label: str) -> str:
    if not path.is_file() or path.is_symlink():
        return ""
    try:
        size = path.stat().st_size
        if size > MAX_LOG_BYTES:
            raise SusyModServerError(f"{label} exceeds its byte bound")
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise SusyModServerError(f"{label} is unreadable") from exc


def _evidence_file(path: Path, label: str) -> dict[str, Any] | None:
    if path.is_symlink():
        raise SusyModServerError(f"{label} is a symbolic link")
    if not path.exists():
        return None
    measured = _regular_file(path, label)
    try:
        size = measured.stat().st_size
    except OSError as exc:
        raise SusyModServerError(f"{label} cannot be measured") from exc
    if size > MAX_LOG_BYTES:
        raise SusyModServerError(f"{label} exceeds its byte bound")
    digest, observed_size = _file_digest(measured, "sha256")
    return {"uri": measured.as_uri(), "sha256": digest, "size": observed_size}


def _crash_path(server: Path) -> Path | None:
    root = server / "crash-reports"
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise SusyModServerError("server crash-report path is unsafe")
    if not root.is_dir():
        return None
    all_candidates = list(root.glob("*.txt"))
    if any(path.is_symlink() or not path.is_file() for path in all_candidates):
        raise SusyModServerError("server crash report is unsafe")
    candidates = list(all_candidates)
    return max(candidates, key=lambda path: path.stat().st_mtime_ns) if candidates else None


def _stop_server(
    process: subprocess.Popen[bytes], timeout: float
) -> tuple[bool, bool, int | None]:
    command_sent = False
    if process.poll() is None and process.stdin is not None:
        try:
            process.stdin.write(b"stop\n")
            process.stdin.flush()
            command_sent = True
            process.stdin.close()
        except (BrokenPipeError, OSError):
            pass
    try:
        return command_sent, process.wait(timeout=timeout) == 0, process.returncode
    except subprocess.TimeoutExpired:
        return command_sent, False, process.poll()


def launch_susy_mod_server(
    suite_root: Path | str,
    run_id: str,
    *,
    server_template: Path | str | None = None,
    server_java: Path | str | None = None,
    accept_minecraft_eula: bool = False,
    compatibility_experiments: Sequence[str] = (),
    memory_mib: int = 8192,
    timeout_seconds: float = 600.0,
    shutdown_timeout_seconds: float = 180.0,
    poll_interval_seconds: float = 0.25,
    _subject: str = "candidate",
    _managed_materialization: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Exercise one retained candidate in a fresh dedicated-server copy."""

    if _subject not in {"candidate", "pack-baseline"}:
        raise SusyModServerError("dedicated-server runtime subject is unknown")
    selected_experiments = tuple(compatibility_experiments)
    if (
        len(set(selected_experiments)) != len(selected_experiments)
        or any(item not in SERVER_EXPERIMENTS for item in selected_experiments)
    ):
        raise SusyModServerError("runtime compatibility experiment is unknown or repeated")
    if isinstance(memory_mib, bool) or not isinstance(memory_mib, int) or not 1024 <= memory_mib <= 131072:
        raise SusyModServerError("server memory must be between 1024 and 131072 MiB")
    if any(
        not math.isfinite(value) or value <= 0
        for value in (timeout_seconds, shutdown_timeout_seconds, poll_interval_seconds)
    ):
        raise SusyModServerError("server launch timeouts must be positive")
    if os.name != "posix":
        raise SusyModServerError("this first dedicated-server slice requires a POSIX host")

    suite = Path(suite_root).resolve()
    (
        run_root,
        result,
        artifact_path,
        artifact,
        selected,
        pack,
    ) = _validate_retained_build(suite, run_id)
    baseline = selected["baseline"]
    managed_materialization = (
        deepcopy(dict(_managed_materialization))
        if isinstance(_managed_materialization, Mapping)
        else None
    )
    if server_template is None:
        from .susy_server_materialize import (
            MATERIALIZATION_RESULT_FORMAT_V2,
            SusyServerMaterializationError,
            materialize_susy_server,
            susy_server_materialization_version,
        )

        try:
            materialization_result = materialize_susy_server(
                suite,
                run_id,
                server_java=server_java,
                accept_minecraft_eula=accept_minecraft_eula,
            )
        except SusyServerMaterializationError as exc:
            raise SusyModServerError(str(exc)) from exc
        receipt_value = materialization_result.get("receipt")
        receipt_version_value = (
            susy_server_materialization_version(receipt_value)
            if isinstance(receipt_value, Mapping)
            else None
        )
        if (
            receipt_version_value != 2
            or materialization_result.get("format")
            != MATERIALIZATION_RESULT_FORMAT_V2
            or materialization_result.get("schema_version") != 2
        ):
            raise SusyModServerError(
                "managed SUSY server materialization result is invalid"
            )
        target_value = (
            receipt_value.get("target")
            if isinstance(receipt_value, Mapping)
            else None
        )
        template_uri = (
            target_value.get("template_uri")
            if isinstance(target_value, Mapping)
            else None
        )
        if not isinstance(template_uri, str):
            raise SusyModServerError(
                "managed SUSY server materialization lacks its template URI"
            )
        parsed_template = urlparse(template_uri)
        if (
            parsed_template.scheme != "file"
            or parsed_template.netloc not in {"", "localhost"}
        ):
            raise SusyModServerError(
                "managed SUSY server template must use a local file URI"
            )
        server_template = Path(url2pathname(parsed_template.path))
        managed_materialization = deepcopy(dict(receipt_value))
    elif accept_minecraft_eula:
        raise SusyModServerError(
            "--accept-minecraft-eula applies only to managed server materialization"
        )
    managed_materialization_version: int | None = None
    disabled_optional_metadata: frozenset[str] = frozenset()
    if managed_materialization is not None:
        from .susy_server_materialize import (
            MATERIALIZATION_RECEIPT_FORMAT_V2,
            SusyServerMaterializationError,
            susy_server_materialization_version,
            verify_susy_server_materialization_receipt_identity,
        )

        managed_materialization_version = (
            susy_server_materialization_version(managed_materialization)
        )
        managed_target = managed_materialization.get("target")
        if (
            managed_materialization_version != 2
            or managed_materialization.get("format")
            != MATERIALIZATION_RECEIPT_FORMAT_V2
            or managed_materialization.get("state") != "materialized"
            or not verify_susy_server_materialization_receipt_identity(
                managed_materialization
            )
            or not isinstance(managed_target, Mapping)
        ):
            raise SusyModServerError(
                "managed SUSY server materialization receipt is invalid"
            )
        source_variant_value = managed_materialization.get("source_variant")
        seed_value = managed_materialization.get("canonical_client_seed")
        seed_provenance = (
            seed_value.get("provenance")
            if isinstance(seed_value, Mapping)
            else None
        )
        if not isinstance(source_variant_value, Mapping):
            raise SusyModServerError(
                "managed SUSY server source variant is invalid"
            )
        variant_projection = {
            key: deepcopy(value)
            for key, value in source_variant_value.items()
            if key != "variant_id"
        }
        expected_variant_id = "sha256:" + sha256(
            _canonical(variant_projection) + b"\n"
        ).hexdigest()
        if (
            source_variant_value.get("format")
            != "workbench-susy-server-source-variant-v1"
            or source_variant_value.get("server_plan_id")
            != managed_materialization.get("plan_id")
            or source_variant_value.get("server_options")
            != {
                "policy": "pack-declared-defaults",
                "policy_version": 1,
                "side": "server",
            }
            or source_variant_value.get("variant_id")
            != expected_variant_id
            or source_variant_value.get("canonical_client")
            != seed_provenance
            or managed_target.get("variant") != "packwiz-source-v2"
            or managed_target.get("variant_id") != expected_variant_id
            or managed_target.get("variant_root_uri")
            != managed_target.get("fixture_root_uri")
            or managed_target.get("planned_fixture_root_uri")
            != source_variant_value.get("planned_fixture_root_uri")
        ):
            raise SusyModServerError(
                "managed SUSY server source variant does not bind its template"
            )
        options = managed_materialization.get("server_packwiz_options")
        rows = options.get("files") if isinstance(options, Mapping) else None
        if not isinstance(rows, list) or not all(
            isinstance(row, Mapping)
            and isinstance(row.get("metadata_path"), str)
            and type(row.get("declared_default")) is bool
            for row in rows
        ):
            raise SusyModServerError(
                "managed SUSY server Packwiz option authority is invalid"
            )
        disabled_optional_metadata = frozenset(
            str(row["metadata_path"])
            for row in rows
            if row.get("declared_default") is False
        )
    template, template_record, baseline_path, launcher_name = _validate_template(
        server_template,
        pack=pack,
        filename=selected["filename"],
        baseline=baseline,
        selected_experiments=selected_experiments,
        disabled_optional_metadata=disabled_optional_metadata,
    )
    patch_experiments = [
        item for item in selected_experiments if item in COMPATIBILITY_EXPERIMENTS
    ]
    compatibility_scopes: dict[str, dict[str, Any]] = {}
    try:
        _template_summary, template_records = _runtime_tree(template)
        if patch_experiments:
            compatibility_scopes = _compatibility_experiment_scopes(
                suite,
                patch_experiments,
                pack_version=pack.get("version"),
                runtime_records=template_records,
            )
    except (OSError, ValueError, SusyModDevError, SusyModLaunchError) as exc:
        raise SusyModServerError(str(exc)) from exc
    if managed_materialization is not None:
        from .susy_server_materialize import _validate_server_packwiz_options

        managed_target = managed_materialization.get("target")
        refreshed_pack_value = managed_materialization.get("refreshed_pack")
        if not isinstance(refreshed_pack_value, Mapping):
            raise SusyModServerError(
                "managed SUSY server materialization lacks refreshed Packwiz authority"
            )
        try:
            verified_disabled = _validate_server_packwiz_options(
                managed_materialization.get("server_packwiz_options"),
                runtime=template,
                refreshed_pack=refreshed_pack_value,
            )
        except SusyServerMaterializationError as exc:
            raise SusyModServerError(str(exc)) from exc
        if verified_disabled != disabled_optional_metadata:
            raise SusyModServerError(
                "managed SUSY server option decisions changed during validation"
            )
        if (
            not isinstance(managed_target, Mapping)
            or managed_target.get("template_uri") != template.as_uri()
            or managed_target.get("payload") != template_record.get("payload")
        ):
            raise SusyModServerError(
                "managed SUSY server materialization does not bind its template"
            )
        managed_provenance: dict[str, Any] = {
            "state": "managed-susy-server-materialization",
            "support_authority_inferred": False,
            "format": managed_materialization.get("format"),
            "schema_version": managed_materialization.get("schema_version"),
            "materialization_id": managed_materialization.get(
                "materialization_id"
            ),
            "plan_id": managed_materialization.get("plan_id"),
            "receipt_uri": managed_target.get("receipt_uri"),
        }
        managed_provenance["variant_id"] = managed_target.get("variant_id")
        managed_provenance["source_variant"] = deepcopy(
            managed_materialization.get("source_variant")
        )
        template_record["provenance"] = managed_provenance
        refreshed_pack = managed_materialization.get("refreshed_pack")
        refreshed_index = (
            refreshed_pack.get("index")
            if isinstance(refreshed_pack, Mapping)
            else None
        )
        pack_binding = template_record.get("pack_binding")
        if isinstance(pack_binding, dict) and isinstance(
            refreshed_index, Mapping
        ):
            pack_binding["materialized_index"] = deepcopy(dict(refreshed_index))
    if template.is_relative_to(run_root) or run_root.is_relative_to(template):
        raise SusyModServerError(
            "server template and retained run must be independent directories"
        )
    retained_artifact_path = artifact_path
    retained_artifact = dict(artifact)
    if _subject == "pack-baseline":
        baseline_sha256, baseline_size = _file_digest(baseline_path, "sha256")
        artifact_path = baseline_path
        artifact = {
            "path": str(baseline_path),
            "sha256": baseline_sha256,
            "size": baseline_size,
            # The selected project identity is the expected mod-container set
            # on both sides of the comparison. A baseline that exposes a
            # different set fails the loaded-source proof and is incomparable.
            "mod_ids": list(retained_artifact["mod_ids"]),
        }

    result_path = run_root / "result.json"
    result_digest_before, _ = _file_digest(result_path, "sha256")
    artifact_digest_before, _ = _file_digest(retained_artifact_path, "sha256")
    runner_path = _regular_file(Path(__file__), "dedicated-server runner source")
    runner_sha256, runner_size = _file_digest(runner_path, "sha256")

    native_host = host_platform()
    if native_host.get("os") != "linux":
        raise SusyModServerError("this first dedicated-server slice supports native Linux/WSL Java")
    candidates = None if server_java is None else [("server-java", Path(server_java).expanduser().resolve())]
    try:
        java_result = ensure_java_runtime(suite, host=native_host, candidates=candidates)
        java_executable, java_identity = _selected_java(java_result, native_host)
    except (JavaRuntimeError, RuntimeLaunchError) as exc:
        raise SusyModServerError(str(exc)) from exc
    if not java_executable.with_name("javac").is_file():
        raise SusyModServerError("selected server Java is not a full JDK; javac is required")

    started = datetime.now(timezone.utc)
    stamp = started.strftime("%Y%m%dT%H%M%S%fZ")
    attempt_id = f"workbench-susy-server-{artifact['sha256'][:12]}-{stamp}"
    runtime_root = run_root / "runtime"
    if runtime_root.is_symlink() or (
        runtime_root.exists() and not runtime_root.is_dir()
    ):
        raise SusyModServerError("retained runtime evidence root is unsafe")
    runtime_root.mkdir(exist_ok=True)
    launches = runtime_root / "server-launches"
    if launches.is_symlink() or (launches.exists() and not launches.is_dir()):
        raise SusyModServerError("server launch evidence root is unsafe")
    launches.mkdir(parents=True, exist_ok=True)
    if not launches.resolve().is_relative_to(run_root):
        raise SusyModServerError("server launch evidence root escapes its retained run")
    evidence = launches / attempt_id
    if evidence.exists() or evidence.is_symlink():
        raise SusyModServerError("server launch evidence target already exists")
    evidence.mkdir()
    lock = runtime_root / "server-launch.lock"
    try:
        with lock.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps({"pid": os.getpid(), "attempt_id": attempt_id}, sort_keys=True) + "\n")
    except FileExistsError as exc:
        raise SusyModServerError(f"another server launch owns retained run {run_id}") from exc

    projection = evidence / "projection"
    server = projection / ".minecraft"
    stdout_path = evidence / "server.stdout.log"
    nonce = secrets.token_hex(24)
    process: subprocess.Popen[bytes] | None = None
    probe_build: dict[str, Any] | None = None
    proof: dict[str, Any] | None = None
    compatibility_records: list[dict[str, Any]] = []
    removed_state: list[str] = []
    properties_record: dict[str, Any] | None = None
    command: list[str] = []
    failure_kind: str | None = None
    detail: str | None = None
    ready_marker: str | None = None
    pack_ready_marker: str | None = None
    stopped_cleanly = False
    stop_command_attempted = False
    stop_command_sent = False
    returncode: int | None = None
    ready_seen = False
    pack_ready_seen = False
    shutdown_bridge_record: dict[str, Any] | None = None
    shutdown_bridge_properties: list[str] = []
    cleanup: dict[str, Any] = {
        "process_group": None,
        "owned_processes_running": False,
        "errors": [],
    }
    template_after: dict[str, Any] | None = None
    cancellation_signal: str | None = None
    cleanup_started = False
    previous_signal_handlers: dict[int, Any] = {}

    def cancel_on_signal(signum: int, _frame: Any) -> None:
        nonlocal cancellation_signal
        cancellation_signal = signal.Signals(signum).name
        if not cleanup_started:
            raise KeyboardInterrupt

    for candidate_signal in (signal.SIGTERM, signal.SIGHUP):
        try:
            previous_signal_handlers[candidate_signal] = signal.getsignal(
                candidate_signal
            )
            signal.signal(candidate_signal, cancel_on_signal)
        except ValueError:
            previous_signal_handlers.clear()
            break

    try:
        _copy_template(template, server)
        copied_summary, _copied_records = _runtime_tree(server)
        if copied_summary != template_record["payload"]:
            raise SusyModServerError(
                "disposable server copy differs from its explicit template"
            )
        removed_state = _clear_generated_state(server)
        candidate_target = server / "mods" / selected["filename"]
        candidate_before_sha256, candidate_before_size = _file_digest(candidate_target, "sha256")
        if _subject == "candidate":
            if candidate_before_sha256 == artifact["sha256"]:
                raise SusyModServerError("server template already contains candidate bytes")
            temporary = candidate_target.with_name(candidate_target.name + ".workbench-candidate")
            with artifact_path.open("rb") as source, temporary.open("xb") as destination:
                shutil.copyfileobj(source, destination, 1024 * 1024)
                destination.flush()
                os.fsync(destination.fileno())
            os.replace(temporary, candidate_target)
        elif (
            candidate_before_sha256 != artifact["sha256"]
            or candidate_before_size != artifact["size"]
        ):
            raise SusyModServerError(
                "disposable server does not contain the exact Packwiz baseline"
            )
        candidate_after_sha256, candidate_after_size = _file_digest(candidate_target, "sha256")
        if candidate_after_sha256 != artifact["sha256"] or candidate_after_size != artifact["size"]:
            raise SusyModServerError("disposable server received the wrong candidate bytes")
        properties_record = _set_properties(server / "server.properties")
        if patch_experiments:
            try:
                compatibility_records = apply_compatibility_patches(
                    projection,
                    [suite / COMPATIBILITY_EXPERIMENTS[item] for item in patch_experiments],
                )
                for experiment_id, record in zip(
                    patch_experiments, compatibility_records, strict=True
                ):
                    record["experiment_id"] = experiment_id
                    record["applicability"] = compatibility_scopes[experiment_id]
            except RuntimeCompatibilityError as exc:
                raise SusyModServerError(str(exc)) from exc
        if SERVER_SHUTDOWN_EXPERIMENT in selected_experiments:
            shutdown_bridge_properties, shutdown_bridge_record = _shutdown_bridge(
                server,
                suite=suite,
                run_id=run_id,
                candidate_sha256=artifact["sha256"],
                template_sha256=template_record["payload"]["tree_sha256"],
            )
            compatibility_records.append(shutdown_bridge_record)

        probe_build, probe_path, javaagent, probe_jar = _compile_probe_agent(
            evidence / "probe-build",
            projected_instance=projection,
            java_executable=java_executable,
            launcher_host=native_host,
            nonce=nonce,
            expected_mod_ids=artifact["mod_ids"],
        )
        projected_probe = projection / ".workbench/candidate-loaded-probe/workbench-candidate-loaded-agent.jar"
        projected_probe.parent.mkdir(parents=True)
        shutil.copy2(probe_jar, projected_probe)
        probe_sha256, probe_size = _file_digest(projected_probe, "sha256")
        if probe_sha256 != probe_build["jar_sha256"] or probe_size != probe_build["jar_size"]:
            raise SusyModServerError("projected loaded-source probe differs from its build")

        home = evidence / "home"
        temporary_root = evidence / "tmp"
        home.mkdir()
        temporary_root.mkdir()
        command = [
            str(java_executable),
            "-Xms1024M",
            f"-Xmx{memory_mib}M",
            _direct_javaagent_argument(javaagent),
            *shutdown_bridge_properties,
            f"-Duser.home={home}",
            f"-Djava.io.tmpdir={temporary_root}",
            "-jar",
            launcher_name,
            "nogui",
        ]
        environment = dict(os.environ)
        for key in ("JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS", "JDK_JAVA_OPTIONS"):
            environment.pop(key, None)
        environment.update({
            "HOME": str(home),
            "TMPDIR": str(temporary_root),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TZ": "UTC",
        })
        with stdout_path.open("xb") as output:
            process = subprocess.Popen(
                command,
                cwd=server,
                env=environment,
                stdin=subprocess.PIPE,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            deadline = time.monotonic() + timeout_seconds
            while time.monotonic() < deadline:
                text = _latest_text(server / "logs/latest.log")
                terminal = TERMINAL_RE.search(text)
                if terminal is not None:
                    failure_kind = "fatal-startup-log"
                    detail = terminal.group(0)
                    break
                crash = _crash_path(server)
                if crash is not None:
                    failure_kind = "minecraft-crash-report"
                    detail = crash.as_uri()
                    break
                match = SERVER_READY_RE.search(text)
                pack_match = SUSY_PACK_READY_RE.search(text)
                ready_seen = ready_seen or bool(
                    match is not None and FML_LOADED_TEXT in text
                )
                pack_ready_seen = pack_ready_seen or pack_match is not None
                if (
                    match is not None
                    and pack_match is not None
                    and FML_LOADED_TEXT in text
                    and probe_path.is_file()
                ):
                    try:
                        proof = _validate_loaded_source_proof(
                            probe_path,
                            nonce=nonce,
                            expected_mod_ids=artifact["mod_ids"],
                            expected_source_path=candidate_target,
                            expected_sha256=artifact["sha256"],
                            expected_size=artifact["size"],
                            launcher_host=native_host,
                        )
                    except SusyModLaunchError as exc:
                        failure_kind = "candidate-source-proof-invalid"
                        detail = str(exc)
                        break
                    if proof["pid"] != process.pid:
                        failure_kind = "candidate-source-proof-wrong-process"
                        detail = f"expected PID {process.pid}, observed {proof['pid']}"
                        proof = None
                        break
                    ready_marker = match.group(0)
                    pack_ready_marker = pack_match.group(0)
                    break
                if process.poll() is not None:
                    returncode = process.returncode
                    failure_kind = "server-exited-before-ready"
                    detail = f"server exit code {returncode}"
                    break
                if stdout_path.stat().st_size > MAX_LOG_BYTES:
                    failure_kind = "server-output-too-large"
                    break
                time.sleep(poll_interval_seconds)
            else:
                failure_kind = (
                    "susy-pack-ready-timeout"
                    if ready_seen and not pack_ready_seen
                    else (
                        "candidate-source-proof-timeout"
                        if ready_seen
                        else "server-ready-timeout"
                    )
                )

            if ready_marker is not None and proof is not None:
                stop_command_attempted = True
                (
                    stop_command_sent,
                    stopped_cleanly,
                    returncode,
                ) = _stop_server(process, shutdown_timeout_seconds)
                if not stop_command_sent or not stopped_cleanly:
                    failure_kind = "server-clean-stop-failed"
            elif process.poll() is None:
                stop_command_attempted = True
                sent, _exited_zero, returncode = _stop_server(
                    process, min(shutdown_timeout_seconds, 30.0)
                )
                stop_command_sent = stop_command_sent or sent
            output.flush()
            os.fsync(output.fileno())
    except KeyboardInterrupt:
        failure_kind = failure_kind or "server-launch-cancelled"
        detail = detail or (
            "dedicated-server smoke was cancelled"
            + (f" by {cancellation_signal}" if cancellation_signal else "")
        )
    except (OSError, ValueError, SusyModLaunchError, SusyModServerError) as exc:
        failure_kind = failure_kind or "server-attempt-error"
        detail = detail or str(exc)
    finally:
        cleanup_started = True
        if process is not None:
            if process.stdin is not None and not process.stdin.closed:
                try:
                    process.stdin.close()
                except (BrokenPipeError, OSError):
                    pass
            try:
                group = _stop_process_group(process)
                cleanup["process_group"] = group
                cleanup["owned_processes_running"] = bool(group.get("running"))
                if group.get("forced"):
                    cleanup["errors"].append("server process group required forced cleanup")
            except (OSError, SusyModLaunchError) as exc:
                cleanup["owned_processes_running"] = True
                cleanup["errors"].append(str(exc))
        try:
            template_summary, _ = _runtime_tree(template)
            template_after = template_summary
        except (OSError, ValueError, SusyModDevError) as exc:
            cleanup["errors"].append(f"cannot remeasure server template: {exc}")
        try:
            lock.unlink()
        except OSError as exc:
            cleanup["errors"].append(f"cannot release server launch lock: {exc}")
        for candidate_signal, previous_handler in previous_signal_handlers.items():
            signal.signal(candidate_signal, previous_handler)

    result_digest_after, _ = _file_digest(result_path, "sha256")
    artifact_digest_after, _ = _file_digest(retained_artifact_path, "sha256")
    immutable_inputs = {
        "retained_result_unchanged": result_digest_before == result_digest_after,
        "candidate_artifact_unchanged": artifact_digest_before == artifact_digest_after,
        "server_template_unchanged": template_after == template_record["payload"],
    }
    final_latest = ""
    if server.is_dir():
        try:
            final_latest = _latest_text(server / "logs/latest.log")
        except SusyModServerError as exc:
            cleanup["errors"].append(str(exc))
    shutdown_acknowledgment = _shutdown_acknowledgment(final_latest)
    final_terminal = TERMINAL_RE.search(final_latest)
    try:
        groovy_server_text = _bounded_log_text(
            server / "logs/groovy_server.log",
            "SUSY groovy_server.log",
        )
    except SusyModServerError as exc:
        cleanup["errors"].append(str(exc))
        groovy_server_text = ""
    groovy_script_failed = GROOVY_SCRIPT_FAILURE_TEXT in groovy_server_text
    try:
        final_crash = _crash_path(server) if server.is_dir() else None
    except SusyModServerError as exc:
        cleanup["errors"].append(str(exc))
        final_crash = None
    if final_crash is not None and failure_kind is None:
        failure_kind = "minecraft-crash-report-after-ready"
        detail = final_crash.as_uri()
    if final_terminal is not None and failure_kind is None:
        failure_kind = "fatal-log-after-ready"
        detail = final_terminal.group(0)
    if groovy_script_failed and failure_kind is None:
        failure_kind = "groovy-script-failure"
        detail = GROOVY_SCRIPT_FAILURE_TEXT
    stopped_cleanly = bool(
        stopped_cleanly
        and stop_command_sent
        and shutdown_acknowledgment["complete"]
        and final_crash is None
        and final_terminal is None
    )
    if ready_marker is not None and proof is not None and not stopped_cleanly:
        if failure_kind is None:
            failure_kind = "server-clean-stop-failed"
        if detail is None and not shutdown_acknowledgment["complete"]:
            detail = "server did not acknowledge the stop/save lifecycle"
    diagnostics = _log_diagnostics(final_latest)
    bridge_observation = _shutdown_bridge_observation(final_latest)
    bridge_observed = bool(
        shutdown_bridge_record is not None and bridge_observation["complete"]
    )
    bridge_verified = shutdown_bridge_record is None or bridge_observed
    if shutdown_bridge_record is not None and not bridge_observed and failure_kind is None:
        failure_kind = "shutdown-bridge-not-observed"
    evidence_files: dict[str, Any] = {}
    try:
        for key, path, label in (
            ("stdout", stdout_path, "dedicated-server stdout"),
            ("latest_log", server / "logs/latest.log", "dedicated-server latest log"),
            (
                "groovy_server_log",
                server / "logs/groovy_server.log",
                "SUSY groovy_server log",
            ),
        ):
            record = _evidence_file(path, label)
            if record is not None:
                evidence_files[key] = record
        if final_crash is not None:
            record = _evidence_file(final_crash, "dedicated-server crash report")
            if record is not None:
                evidence_files["crash_report"] = record
    except SusyModServerError as exc:
        cleanup["errors"].append(str(exc))
    if "latest_log" in evidence_files:
        diagnostics["source"] = {
            "evidence_key": "latest_log",
            "sha256": evidence_files["latest_log"]["sha256"],
            "size": evidence_files["latest_log"]["size"],
        }
    diagnostics["smoke_outcome_affected"] = bool(
        final_terminal is not None
        or final_crash is not None
        or groovy_script_failed
    )
    diagnostics["groovy_server"] = {
        "available": bool(groovy_server_text),
        "script_failure_observed": groovy_script_failed,
        "failure_marker": (
            GROOVY_SCRIPT_FAILURE_TEXT if groovy_script_failed else None
        ),
    }
    if not groovy_server_text and failure_kind is None:
        failure_kind = "groovy-script-evidence-missing"
        detail = "SUSY groovy_server.log was missing or empty"
    diagnosis = _diagnose_crash(
        final_crash,
        suite,
        pack_version=str(pack.get("version")),
        runtime_records=template_records,
    )
    passed = bool(
        failure_kind is None
        and ready_marker is not None
        and proof is not None
        and stopped_cleanly
        and returncode == 0
        and not cleanup["owned_processes_running"]
        and not cleanup["errors"]
        and bridge_verified
        and all(immutable_inputs.values())
    )
    if not passed and failure_kind is None:
        failure_kind = "input-or-cleanup-integrity-failed"
    ended = datetime.now(timezone.utc)
    receipt: dict[str, Any] = {
        "format": SERVER_RECEIPT_FORMAT,
        "schema_version": 2,
        "attempt_id": attempt_id,
        "run_id": run_id,
        "outcome": "passed" if passed else "failed",
        "failure_kind": None if passed else failure_kind,
        "detail": None if passed else detail,
        "started_at": started.isoformat(),
        "ended_at": ended.isoformat(),
        "duration_seconds": round((ended - started).total_seconds(), 3),
        "side": "dedicated-server",
        "runner": {
            "source_uri": runner_path.as_uri(),
            "sha256": runner_sha256,
            "size": runner_size,
        },
        "candidate": {
            "path": f"mods/{selected['filename']}",
            "sha256": artifact["sha256"],
            "size": artifact["size"],
            "mod_ids": sorted(artifact["mod_ids"]),
        },
        "template": template_record,
        "projection": {
            "root_uri": projection.as_uri(),
            "removed_generated_state": removed_state,
            "server_properties": properties_record,
            "compatibility_experiments": compatibility_records,
        },
        "java": {
            "executable_uri": java_executable.as_uri(),
            "identity": java_identity,
        },
        "command": command,
        "probe_build": probe_build,
        "evidence": evidence_files,
        "diagnostics": diagnostics,
        "diagnosis": diagnosis,
        "checkpoint": (
            {
                "id": "supersymmetry-server-ready",
                "marker": ready_marker,
                "pack_marker": pack_ready_marker,
            }
            if ready_marker is not None
            else None
        ),
        "loaded_source_proof": proof,
        "shutdown_bridge_observation": bridge_observation,
        "process": {
            "pid": None if process is None else process.pid,
            "exit_code": returncode,
            "stop_command_attempted": stop_command_attempted,
            "stop_command_sent": stop_command_sent,
            "clean_stop": stopped_cleanly,
            "shutdown_acknowledgment": shutdown_acknowledgment,
        },
        "cleanup": cleanup,
        "immutable_inputs": immutable_inputs,
        "claims": {
            "exact_candidate_loaded": proof is not None and _subject == "candidate",
            "exact_runtime_subject_loaded": proof is not None,
            "dedicated_server_ready": ready_marker is not None,
            "supersymmetry_pack_ready": pack_ready_marker is not None,
            "clean_shutdown": stopped_cleanly and returncode == 0,
            "shutdown_bridge_observed": bridge_observed,
            "runtime_health_clean": diagnostics["state"]
            == "no-error-or-fatal-observed",
            "client_server_parity": False,
        },
        "limitations": [
            "This is one independent dedicated-server smoke, not a client/server parity decision.",
            "The server template is explicit experimental input and is not relabelled as a canonical materialization.",
            (
                "A passing result proves the exact candidate source JAR loaded and the server stopped cleanly; it does not prove gameplay correctness."
                if _subject == "candidate"
                else "This comparison-control result proves the exact Packwiz baseline source JAR loaded and the server stopped cleanly; it does not prove gameplay correctness."
            ),
            "Runtime FATAL/ERROR diagnostics are surfaced separately and are not attributed to the candidate without a baseline control.",
        ],
    }
    if _subject == "pack-baseline":
        receipt["runtime_subject"] = "pack-baseline"
        receipt["candidate"]["role"] = "pack-baseline-control"
    receipt["receipt_id"] = "workbench-susy-mod-server-launch:" + sha256(
        _canonical(receipt)
    ).hexdigest()
    receipt_path = evidence / "susy-mod-server-launch-v2.json"
    _write_json(receipt_path, receipt)
    retry = [
        "workbench",
        "dev",
        "launch-server",
        "--run",
        run_id,
    ]
    if managed_materialization is None:
        retry.extend(["--server-template", str(template)])
    else:
        retry.append("--accept-minecraft-eula")
    retry.extend(
        [
            "--server-java",
            str(java_executable),
            "--memory",
            str(memory_mib),
            "--launch-timeout",
            str(timeout_seconds),
            "--shutdown-timeout",
            str(shutdown_timeout_seconds),
        ]
    )
    for experiment in selected_experiments:
        retry.extend(["--runtime-experiment", experiment])
    known_shutdown_failure = (
        not passed
        and SERVER_SHUTDOWN_EXPERIMENT not in selected_experiments
        and "ForgeChunkManager.requestTicket" in final_latest
        and "Exception stopping the server" in final_latest
    )
    bridge_retry_available = known_shutdown_failure and managed_materialization is None
    if bridge_retry_available:
        retry.extend(["--runtime-experiment", SERVER_SHUTDOWN_EXPERIMENT])
    diagnosed_experiment = (
        diagnosis.get("available_experiment")
        if isinstance(diagnosis, Mapping)
        else None
    )
    if (
        isinstance(diagnosed_experiment, Mapping)
        and isinstance(diagnosed_experiment.get("id"), str)
        and diagnosed_experiment["id"] not in selected_experiments
    ):
        retry.extend(
            ["--runtime-experiment", str(diagnosed_experiment["id"])]
        )
        next_action = {
            "id": "try-susy-reccomplex-compatibility",
            "available": True,
            "argv": retry,
        }
    elif known_shutdown_failure and managed_materialization is not None:
        next_action = {
            "id": "shutdown-bridge-template-required",
            "available": False,
            "argv": [],
            "unavailable_reason": (
                "the managed Packwiz template has no admitted shutdown-bridge "
                "manifest or implementation; retry with a measured --server-template"
            ),
        }
    else:
        next_action = {
            "id": (
                "try-susy-server-shutdown-bridge"
                if bridge_retry_available
                else ("retry-server" if not passed else "launch-server-again")
            ),
            "available": True,
            "argv": retry,
        }
    return {
        "format": SERVER_RESULT_FORMAT,
        "schema_version": 2,
        "outcome": receipt["outcome"],
        "receipt": deepcopy(receipt),
        "next_actions": [next_action],
    }


def render_susy_mod_server(
    result: Mapping[str, Any], *, json_output: bool = False
) -> str:
    if json_output:
        return json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    receipt = result.get("receipt")
    if not isinstance(receipt, Mapping):
        raise SusyModServerError("SUSY server result lacks its receipt")
    proof = receipt.get("loaded_source_proof")
    cleanup = receipt.get("cleanup")
    diagnostics = receipt.get("diagnostics")
    counts = (
        diagnostics.get("entry_counts") if isinstance(diagnostics, Mapping) else None
    )
    lines = [
        f"SUSY dedicated-server load/stop smoke: {result.get('outcome')}",
        f"Run: {receipt.get('run_id')}",
        f"Candidate: {receipt.get('candidate', {}).get('path')} · {receipt.get('candidate', {}).get('sha256')}",
        "Loaded proof: " + ("exact Forge source JAR" if isinstance(proof, Mapping) else "unavailable"),
        "Minecraft server ready: "
        + (
            "yes"
            if receipt.get("claims", {}).get("dedicated_server_ready")
            else "no"
        ),
        "SUSY pack ready: "
        + (
            "yes"
            if receipt.get("claims", {}).get("supersymmetry_pack_ready")
            else "no"
        ),
        "Clean stop: " + ("yes" if receipt.get("process", {}).get("clean_stop") else "no"),
        "Cleanup: " + (
            "complete"
            if isinstance(cleanup, Mapping)
            and not cleanup.get("owned_processes_running")
            and not cleanup.get("errors")
            else "incomplete"
        ),
        f"Receipt: {receipt.get('receipt_id')}",
    ]
    template = receipt.get("template")
    pack_binding = (
        template.get("pack_binding") if isinstance(template, Mapping) else None
    )
    if isinstance(pack_binding, Mapping):
        index = pack_binding.get("index")
        source_index_matches = bool(
            isinstance(index, Mapping) and index.get("matches_declared_hash")
        )
        materialized_index = pack_binding.get("materialized_index")
        refreshed_index_verified = bool(
            isinstance(materialized_index, Mapping)
            and materialized_index.get("actual_sha256")
            == materialized_index.get("declared_hash")
        )
        lines.append(
            "Template pack: "
            + f"{pack_binding.get('server_entry_count')} server entries"
            + f" · {pack_binding.get('pack_baseline_matches')} Packwiz baselines"
            + f" · {pack_binding.get('inherited_overlay_matches')} inherited overlay"
            + (
                " · source index matched"
                if source_index_matches
                else " · source index required refresh"
            )
            + (
                " · refreshed index verified"
                if refreshed_index_verified
                else ""
            )
        )
    projection = receipt.get("projection")
    experiments = (
        projection.get("compatibility_experiments")
        if isinstance(projection, Mapping)
        else None
    )
    if isinstance(experiments, list):
        for experiment in experiments:
            if isinstance(experiment, Mapping):
                experiment_id = experiment.get("experiment_id") or experiment.get(
                    "patch_id"
                )
                if experiment_id:
                    lines.append("Runtime experiment: " + str(experiment_id))
            authority = (
                experiment.get("authority")
                if isinstance(experiment, Mapping)
                else None
            )
            if isinstance(authority, Mapping):
                lines.append(
                    "Compatibility authority: "
                    + str(authority.get("profile_state"))
                    + " · template declaration "
                    + str(authority.get("template_declaration_state"))
                )
    if isinstance(diagnostics, Mapping) and isinstance(counts, Mapping):
        lines.append(
            "Pack diagnostics: "
            + str(diagnostics.get("state"))
            + f" · {counts.get('fatal', 0)} FATAL"
            + f" · {counts.get('error', 0)} ERROR"
            + f" · {counts.get('warn', 0)} WARN"
        )
        lines.append(
            "Candidate attribution: "
            + str(diagnostics.get("candidate_attribution"))
        )
        evidence = receipt.get("evidence")
        latest = evidence.get("latest_log") if isinstance(evidence, Mapping) else None
        if isinstance(latest, Mapping) and latest.get("uri"):
            lines.append(f"Runtime log: {latest.get('uri')}")
    if receipt.get("failure_kind"):
        lines.append(f"Failure: {receipt.get('failure_kind')}")
    if receipt.get("detail"):
        lines.append(f"Detail: {receipt.get('detail')}")
    diagnosis = receipt.get("diagnosis")
    if isinstance(diagnosis, Mapping):
        interpretation = diagnosis.get("interpretation")
        if isinstance(interpretation, str) and interpretation:
            lines.append("Diagnosis: " + interpretation)
    actions = result.get("next_actions")
    if isinstance(actions, list) and actions and isinstance(actions[0], Mapping):
        action = actions[0]
        if action.get("available") is False:
            lines.append(
                "Next: blocked · " + str(action.get("unavailable_reason", "unavailable"))
            )
        else:
            lines.append("Next: " + json.dumps(action.get("argv"), ensure_ascii=False))
    return "\n".join(lines) + "\n"
