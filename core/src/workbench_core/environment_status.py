"""Read-only execution and Pixi environment observation for Workbench.

This module reports local materialization evidence.  It deliberately does not
validate or authorize a release, construct an environment, select Java, or
remove storage.  Those decisions remain with their existing owners.
"""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tomllib
from typing import Any, TextIO

from workbench_core.human_presentation import HumanPresentation, human_presentation


FORMAT = "workbench-environment-status-v2"
SCHEMA_VERSION = 2
MAX_IDENTITY_BYTES = 64 * 1024 * 1024
MAX_METADATA_BYTES = 64 * 1024
WORKBENCH_DISTRIBUTION = "workbench-core"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PIXI_VERSION = re.compile(
    r"(?:^|\s)(?:pixi\s+)?"
    r"(?P<version>[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?)"
    r"(?:\s|$)",
    re.IGNORECASE,
)


def _absolute(path: Path | str) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _identity(path: Path) -> tuple[dict[str, Any], bytes | None]:
    """Return a bounded regular-file identity without following symlinks."""

    record: dict[str, Any] = {"path": str(path)}
    try:
        info = path.lstat()
    except FileNotFoundError:
        record["state"] = "missing"
        return record, None
    except OSError as exc:
        record.update(state="unreadable", error=type(exc).__name__)
        return record, None
    if stat.S_ISLNK(info.st_mode):
        record["state"] = "unsafe-symlink"
        return record, None
    if not stat.S_ISREG(info.st_mode):
        record["state"] = "not-regular-file"
        return record, None
    if not 1 <= info.st_size <= MAX_IDENTITY_BYTES:
        record.update(state="outside-size-bound", size_bytes=info.st_size)
        return record, None
    descriptor: int | None = None
    try:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            record["state"] = "not-regular-file"
            return record, None
        if not 1 <= opened.st_size <= MAX_IDENTITY_BYTES:
            record.update(state="outside-size-bound", size_bytes=opened.st_size)
            return record, None
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(MAX_IDENTITY_BYTES + 1)
        if not 1 <= len(raw) <= MAX_IDENTITY_BYTES:
            record.update(state="outside-size-bound", size_bytes=len(raw))
            return record, None
    except OSError as exc:
        record.update(state="unreadable", error=type(exc).__name__)
        return record, None
    finally:
        if descriptor is not None:
            os.close(descriptor)
    record.update(
        state="present",
        size_bytes=len(raw),
        sha256=sha256(raw).hexdigest(),
    )
    return record, raw


def _manifest_environment_names(
    raw: bytes | None,
) -> tuple[set[str], str | None, str | None]:
    if raw is None:
        return set(), None, None
    try:
        value = tomllib.loads(raw.decode("utf-8", errors="strict"))
    except (UnicodeError, tomllib.TOMLDecodeError):
        return set(), None, "Pixi manifest is not valid UTF-8 TOML"
    workspace = value.get("workspace")
    required_pixi = (
        workspace.get("requires-pixi")
        if type(workspace) is dict
        and type(workspace.get("requires-pixi")) is str
        else None
    )
    environments = value.get("environments", {})
    if type(environments) is not dict:
        return {"default"}, required_pixi, "Pixi environments table is invalid"
    names = {"default"}
    invalid = False
    for name in environments:
        if type(name) is str and _safe_environment_name(name):
            names.add(name)
        else:
            invalid = True
    error = "Pixi manifest contains an unsafe environment name" if invalid else None
    return names, required_pixi, error


def _safe_environment_name(name: str) -> bool:
    return bool(
        name
        and name not in {".", ".."}
        and "/" not in name
        and "\\" not in name
        and "\x00" not in name
    )


def _is_within(path: Path | str, parent: Path) -> bool:
    try:
        _absolute(path).relative_to(_absolute(parent))
    except (OSError, ValueError):
        return False
    return True


def _tree_footprint(root: Path) -> dict[str, Any]:
    """Measure logical bytes without following environment symlinks."""

    logical_bytes = 0
    entry_count = 0
    error_count = 0
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    entry_count += 1
                    try:
                        info = entry.stat(follow_symlinks=False)
                    except OSError:
                        error_count += 1
                        continue
                    is_junction = (
                        entry.is_junction()
                        if hasattr(entry, "is_junction")
                        else False
                    )
                    if stat.S_ISDIR(info.st_mode) and not is_junction:
                        pending.append(Path(entry.path))
                    elif stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
                        logical_bytes += info.st_size
        except OSError:
            error_count += 1
    return {
        "state": "complete" if error_count == 0 else "partial",
        "logical_bytes": logical_bytes,
        "entry_count": entry_count,
        "error_count": error_count,
    }


def _source_environments(
    suite_root: Path,
    declared_names: set[str],
    *,
    environ: Mapping[str, str],
) -> tuple[dict[str, Any], str | None]:
    environments_root = suite_root / ".pixi" / "envs"
    observed_names: set[str] = set()
    root_state = "missing"
    try:
        root_info = environments_root.lstat()
    except FileNotFoundError:
        pass
    except OSError:
        root_state = "unreadable"
    else:
        if stat.S_ISLNK(root_info.st_mode):
            root_state = "unsafe-symlink"
        elif not stat.S_ISDIR(root_info.st_mode):
            root_state = "not-directory"
        else:
            root_state = "present"
            try:
                with os.scandir(environments_root) as entries:
                    for entry in entries:
                        if _safe_environment_name(entry.name):
                            observed_names.add(entry.name)
            except OSError:
                root_state = "unreadable"

    active_name: str | None = None
    active_detection: str | None = None
    claimed_name = environ.get("PIXI_ENVIRONMENT_NAME")
    claimed_root = environ.get("PIXI_PROJECT_ROOT")
    if (
        claimed_name is not None
        and _safe_environment_name(claimed_name)
        and claimed_root is not None
        and _absolute(claimed_root) == suite_root
    ):
        active_name = claimed_name
        active_detection = "pixi-process-environment"

    all_names = declared_names | observed_names
    if active_name is not None:
        all_names.add(active_name)
    python_candidates = (Path(sys.executable), Path(sys.prefix))
    for name in sorted(all_names):
        environment_path = environments_root / name
        if any(_is_within(candidate, environment_path) for candidate in python_candidates):
            active_name = name
            active_detection = "python-prefix-or-executable"
            break

    records: list[dict[str, Any]] = []
    total_logical_bytes = 0
    for name in sorted(all_names):
        path = environments_root / name
        record: dict[str, Any] = {
            "name": name,
            "path": str(path),
            "declaration": (
                "manifest"
                if name in declared_names and name != "default"
                else "implicit-default"
                if name == "default" and name in declared_names
                else "materialized-only"
            ),
            "active": name == active_name,
        }
        try:
            info = path.lstat()
        except FileNotFoundError:
            record.update(materialization_state="absent", footprint=None)
        except OSError:
            record.update(materialization_state="unreadable", footprint=None)
        else:
            if stat.S_ISLNK(info.st_mode):
                record.update(materialization_state="unsafe-symlink", footprint=None)
            elif not stat.S_ISDIR(info.st_mode):
                record.update(materialization_state="not-directory", footprint=None)
            else:
                footprint = _tree_footprint(path)
                total_logical_bytes += footprint["logical_bytes"]
                record.update(
                    materialization_state="present",
                    footprint=footprint,
                )
        records.append(record)
    return (
        {
            "root": str(environments_root),
            "root_state": root_state,
            "environments": records,
            "total_logical_bytes": total_logical_bytes,
            "active_detection": active_detection,
            "measurement": "logical regular-file and symlink bytes; symlink targets are not followed",
        },
        active_name,
    )


def _distribution_text(distribution_name: str, filename: str) -> str | None:
    try:
        distribution = metadata.distribution(distribution_name)
    except metadata.PackageNotFoundError:
        return None
    try:
        raw = distribution.read_text(filename)
    except (OSError, UnicodeError):
        return None
    if raw is None or len(raw.encode("utf-8")) > MAX_METADATA_BYTES:
        return None
    return raw


def _installed_provenance() -> dict[str, Any]:
    """Observe native metadata, never infer artifact verification from its presence."""

    try:
        distribution = metadata.distribution(WORKBENCH_DISTRIBUTION)
        version = distribution.version
        location = Path(distribution.locate_file("")).resolve()
        implementation = Path(__file__).resolve()
        installed = implementation.is_relative_to(location) and any(
            str(member) == "workbench_core/environment_status.py"
            and Path(distribution.locate_file(member)).resolve() == implementation
            for member in (distribution.files or ())
        )
    except metadata.PackageNotFoundError:
        return {"selected": "absent", "direct_archive": {"state": "absent"}}
    except (OSError, UnicodeError, ValueError, AttributeError):
        return {"selected": "invalid-metadata", "direct_archive": {"state": "unreadable"}}
    raw = _distribution_text(WORKBENCH_DISTRIBUTION, "direct_url.json")
    archive: dict[str, Any] = {"state": "absent"}
    if raw is not None:
        try:
            def unique(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError("duplicate metadata key")
                    result[key] = value
                return result
            direct = json.loads(raw, object_pairs_hook=unique)
            info = direct.get("archive_info") if type(direct) is dict else None
            hashes = info.get("hashes") if type(info) is dict else None
            digest = hashes.get("sha256") if type(hashes) is dict else None
            if type(digest) is str and _SHA256.fullmatch(digest):
                archive = {"state": "observed", "workbench_wheel_sha256": digest}
            elif type(direct) is dict and direct.get("dir_info", {}).get("editable") is True:
                archive = {"state": "editable-source"}
            else:
                archive = {"state": "invalid"}
        except (ValueError, TypeError, AttributeError):
            archive = {"state": "invalid"}
    return {
        "selected": "native-wheel" if installed else "source-metadata",
        "distribution": WORKBENCH_DISTRIBUTION,
        "version": version,
        "direct_archive": archive,
        "artifact_verified": False,
        "authority": "Observed installed metadata only; not release or artifact verification.",
    }


def _probe_pixi(
    required_constraint: str | None,
    *,
    environ: Mapping[str, str],
) -> dict[str, Any]:
    declared_executable = environ.get("PIXI_EXE")
    discovery = "PIXI_EXE" if declared_executable else "PATH"
    if declared_executable:
        candidate = _absolute(declared_executable)
        executable = (
            str(candidate)
            if candidate.is_file() and os.access(candidate, os.X_OK)
            else None
        )
    else:
        executable = shutil.which("pixi", path=environ.get("PATH", ""))
    if executable is None:
        return {
            "state": "unavailable",
            "available": False,
            "executable": None,
            "discovery": discovery,
            "version": None,
            "required_constraint": required_constraint,
            "matches_required_constraint": None,
        }
    try:
        completed = subprocess.run(
            [executable, "--version"],
            check=False,
            env=dict(environ),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "state": "probe-failed",
            "available": True,
            "executable": executable,
            "discovery": discovery,
            "version": None,
            "required_constraint": required_constraint,
            "matches_required_constraint": None,
            "error": type(exc).__name__,
        }
    output = (completed.stdout or completed.stderr)[:4096]
    match = _PIXI_VERSION.search(output)
    version = match.group("version") if match else None
    exact_required = (
        required_constraint.removeprefix("==")
        if type(required_constraint) is str and required_constraint.startswith("==")
        else None
    )
    matches = version == exact_required if exact_required is not None and version else None
    return {
        "state": (
            "available"
            if completed.returncode == 0 and version is not None
            else "probe-failed"
        ),
        "available": True,
        "executable": executable,
        "discovery": discovery,
        "version": version,
        "required_constraint": required_constraint,
        "matches_required_constraint": matches,
        **(
            {"exit_code": completed.returncode}
            if completed.returncode != 0
            else {}
        ),
    }


def inspect_environment_status(
    suite_root: Path | str,
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Inspect Workbench's local execution substrate without changing it."""

    root = _absolute(suite_root)
    process_environment = dict(os.environ if environ is None else environ)
    manifest, manifest_raw = _identity(root / "pixi.toml")
    lock, _lock_raw = _identity(root / "pixi.lock")
    declared_names, required_pixi, manifest_error = _manifest_environment_names(
        manifest_raw
    )
    pixi = _probe_pixi(required_pixi, environ=process_environment)
    provenance = _installed_provenance()
    packaged = provenance["selected"] == "native-wheel"
    mode_evidence: list[str] = []
    source_checkout = (
        not packaged
        and manifest["state"] == "present"
        and lock["state"] == "present"
    )

    source_environments: dict[str, Any] | None = None
    active_environment: str | None = None
    if packaged:
        mode = "native-installed"
        mode_evidence.append("running-core-is-owned-by-installed-distribution")
    elif source_checkout:
        source_environments, active_environment = _source_environments(
            root,
            declared_names,
            environ=process_environment,
        )
        mode = "source-pixi" if active_environment is not None else "source-alternate"
        mode_evidence.append(
            "active-pixi-environment-detected-by-"
            + str(source_environments["active_detection"])
            if active_environment is not None
            else "python-is-outside-pixi-environments"
        )
    else:
        mode = "unknown"
        mode_evidence.append("no-source-or-packaged-execution-evidence")

    findings: list[str] = []
    if manifest_error is not None:
        findings.append(manifest_error)
    if source_checkout:
        for label, identity in (("Pixi manifest", manifest), ("Pixi lock", lock)):
            if identity["state"] != "present":
                findings.append(f"{label} is {identity['state']}")
    if mode == "source-pixi":
        if pixi["state"] != "available":
            findings.append("Pixi is not callable for source environment repair")
        elif pixi["matches_required_constraint"] is False:
            findings.append("Detected Pixi version differs from the manifest constraint")
        active = next(
            (
                row
                for row in source_environments["environments"]
                if row["active"]
            ),
            None,
        )
        if active is None or active["materialization_state"] != "present":
            findings.append("Active Pixi source environment is not materialized")
    elif mode == "source-alternate":
        findings.append("Workbench is running outside its declared Pixi environments")
        if pixi["state"] != "available":
            findings.append("Pixi is unavailable for source environment repair")
    elif packaged:
        if provenance["direct_archive"]["state"] == "invalid":
            findings.append("Installed Core archive metadata is invalid")
    elif mode == "unknown":
        findings.append("Execution materialization could not be identified")

    if source_environments is not None:
        if source_environments["root_state"] not in {"present", "missing"}:
            findings.append(
                "Source Pixi environment root is "
                + source_environments["root_state"]
            )
        if any(
            row["footprint"] is not None
            and row["footprint"]["state"] != "complete"
            for row in source_environments["environments"]
        ):
            findings.append("One or more source environment footprints are partial")

    state = "ready" if not findings else "attention"
    if mode == "unknown":
        state = "unknown"

    inactive_candidates = (
        [
            {
                "name": row["name"],
                "path": row["path"],
                "logical_bytes": row["footprint"]["logical_bytes"],
            }
            for row in source_environments["environments"]
            if not row["active"]
            and row["materialization_state"] == "present"
            and row["footprint"] is not None
        ]
        if source_environments is not None
        else []
    )
    if source_checkout:
        repair_state = (
            "available"
            if pixi["state"] == "available"
            and pixi["matches_required_constraint"] is not False
            else "blocked"
        )
        repair_command: list[str] | None = [
            "pixi",
            "install",
            "--locked",
            "--no-config",
        ]
        repair_note = (
            "Review and run this command to reconcile declared source environments; "
            "status did not run it."
        )
        gc_state = "review"
        gc_command: list[str] | None = ["pixi", "clean", "--help"]
        gc_note = (
            "Review Pixi's cleanup scope and the inactive candidates before choosing "
            "a cleanup command; status did not delete anything."
        )
    elif packaged:
        repair_state = "reinstall-artifact"
        repair_command = None
        repair_note = (
            "Build a replacement isolated environment from the reviewed native "
            "wheelhouse, then switch launchers after verification. Do not overwrite "
            "an active environment; Pixi is not required at runtime."
        )
        gc_state = "not-applicable"
        gc_command = None
        gc_note = "This packaged runtime owns no source-checkout Pixi environments."
    else:
        repair_state = "manual-review"
        repair_command = None
        repair_note = "Locate a Workbench source checkout or verified package first."
        gc_state = "not-applicable"
        gc_command = None
        gc_note = "No Workbench-owned Pixi environment root was identified."

    return {
        "format": FORMAT,
        "schema_version": SCHEMA_VERSION,
        "operation_class": "read-only",
        "state": state,
        "authority": {
            "classification": "local-execution-observation",
            "normative": False,
            "release_authority": False,
            "profile_authority": False,
        },
        "execution": {
            "mode": mode,
            "suite_root": str(root),
            "source_checkout": source_checkout,
            "packaged": packaged,
            "active_pixi_environment": active_environment,
            "evidence": mode_evidence,
        },
        "identities": {
            "pixi_manifest": manifest,
            "pixi_lock": lock,
            "installed_provenance": provenance,
        },
        "pixi": {
            **pixi,
            "required_for_current_execution": mode == "source-pixi",
            "required_for_packaged_runtime": False,
        },
        "python": {
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
            "executable": sys.executable,
            "prefix": sys.prefix,
            "base_prefix": sys.base_prefix,
        },
        "source_environments": source_environments,
        "findings": findings,
        "guidance": {
            "status": {
                "command": ["workbench", "environment", "status", "--json"],
                "performed": True,
                "mutated": False,
            },
            "repair": {
                "state": repair_state,
                "command": repair_command,
                "note": repair_note,
                "performed": False,
            },
            "garbage_collection": {
                "state": gc_state,
                "review_command": gc_command,
                "inactive_environment_candidates": inactive_candidates,
                "note": gc_note,
                "performed": False,
            },
        },
        "authority_boundaries": {
            "java": {
                "inspected": False,
                "owner": "explicit platform and pack profiles",
                "note": (
                    "Java selection and receipts remain separate and profile-owned; "
                    "this environment status does not inspect or authorize Java."
                ),
            }
        },
    }


def _human_bytes(value: int) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{int(amount)} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024
    raise AssertionError("unreachable")


def _environment_state_label(
    presentation: HumanPresentation,
    state: str,
) -> str:
    if state == "ready":
        return presentation.label("ready", "good")
    return presentation.label("attention", "attention")


def _identity_state_label(
    presentation: HumanPresentation,
    state: str,
) -> str:
    if state == "present":
        return presentation.label("ready", "good")
    if state == "missing":
        return presentation.label("missing", "blocked")
    return presentation.label("blocked", "blocked")


_MODE_LABELS = {
    "source-pixi": "Source checkout via Pixi",
    "source-alternate": "Source checkout outside Pixi",
    "native-installed": "Installed Workbench",
    "unknown": "Unknown execution mode",
}


def render_environment_status(
    result: Mapping[str, Any],
    *,
    stream: TextIO | None = None,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Render the concise view from the same data returned as JSON."""

    presentation = human_presentation(stream, environment=environ)
    execution = result["execution"]
    pixi = result["pixi"]
    identities = result["identities"]
    python = result["python"]
    state = str(result["state"])
    lines = [
        "Workbench environment "
        + _environment_state_label(presentation, state)
        + (f" ({state})" if state not in {"ready", "attention"} else ""),
        "  Mode: "
        + _MODE_LABELS.get(
            str(execution["mode"]),
            str(execution["mode"]).replace("-", " ").capitalize(),
        ),
        "  Python: "
        f"{python['implementation']} {python['version']} "
        + presentation.label("ready", "good"),
    ]
    if pixi["available"]:
        detail = pixi["version"] or "version probe failed"
        pixi_ready = (
            pixi.get("state", "available") == "available"
            and pixi.get("matches_required_constraint") is not False
        )
        pixi_label = presentation.label(
            "ready" if pixi_ready else "attention",
            "good" if pixi_ready else "attention",
        )
        lines.append(f"  Pixi: {detail} {pixi_label}")
    else:
        if execution["source_checkout"]:
            requirement = "required for source repair"
            pixi_label = presentation.label("missing", "blocked")
        else:
            requirement = "not required by this runtime"
            pixi_label = presentation.label("optional", "attention")
        lines.append(f"  Pixi: unavailable ({requirement}) {pixi_label}")
    input_details: list[str] = []
    for label, key in (("Manifest", "pixi_manifest"), ("Lock", "pixi_lock")):
        identity = identities[key]
        detail = (
            "sha256 " + str(identity["sha256"])[:12] + "…"
            if identity["state"] == "present"
            else identity["state"]
        )
        identity_label = _identity_state_label(
            presentation,
            str(identity["state"]),
        )
        input_details.append(f"{label.lower()} {detail} {identity_label}")
    lines.append("  Inputs: " + " · ".join(input_details))
    provenance = identities["installed_provenance"]
    if provenance is not None:
        selected = str(provenance["selected"])
        if selected == "native-wheel":
            provenance_label = presentation.label("ready", "good")
        elif selected == "invalid-metadata":
            provenance_label = presentation.label("blocked", "blocked")
        else:
            provenance_label = presentation.label("attention", "attention")
        lines.append(
            f"  Installed provenance: {selected} {provenance_label}"
        )
    source = result["source_environments"]
    if source is not None:
        lines.append(
            "  Source environments: "
            f"{len(source['environments'])} ({_human_bytes(source['total_logical_bytes'])})"
        )
        for environment in source["environments"]:
            footprint = environment["footprint"]
            size = (
                _human_bytes(footprint["logical_bytes"])
                if footprint is not None
                else environment["materialization_state"]
            )
            active = ", active" if environment["active"] else ""
            materialization_state = str(environment["materialization_state"])
            if materialization_state == "present":
                if footprint is not None and footprint["state"] == "complete":
                    environment_label = presentation.label("ready", "good")
                else:
                    environment_label = presentation.label(
                        "attention", "attention"
                    )
            elif materialization_state == "absent":
                environment_label = presentation.label("missing", "blocked")
            else:
                environment_label = presentation.label("blocked", "blocked")
            lines.append(
                f"    {environment['name']}: {size}{active} {environment_label}"
            )
    if result["findings"]:
        attention_label = presentation.label("attention", "attention")
        lines.append("  Findings")
        lines.extend(
            f"    {attention_label} {finding}" for finding in result["findings"]
        )
    repair = result["guidance"]["repair"]
    repair_command = (
        shlex.join(repair["command"])
        if repair["command"] is not None
        else "reinstall/manual review"
    )
    garbage_collection = result["guidance"]["garbage_collection"]
    gc_command = (
        shlex.join(garbage_collection["review_command"])
        if garbage_collection["review_command"] is not None
        else "not applicable"
    )
    repair_state = str(repair["state"])
    if repair_state == "available":
        repair_label = presentation.label("available", "good")
    elif repair_state == "blocked":
        repair_label = presentation.label("blocked", "blocked")
    elif repair_state == "reinstall-artifact":
        repair_label = presentation.label("optional", "attention")
    else:
        repair_label = presentation.label("attention", "attention")
    gc_state = str(garbage_collection["state"])
    gc_label = presentation.label(
        "review" if gc_state == "review" else "optional",
        "attention",
    )
    lines.extend(
        [
            "  Guidance",
            f"    Repair: {repair_command} ({repair_state}) {repair_label}",
            f"    GC review: {gc_command} ({gc_state}) {gc_label}",
            "    Java: checked during full developer setup; exact selection remains "
            "profile-owned. "
            + presentation.label("setup", "attention"),
            "  No files, environments, caches, or Java runtimes were changed.",
        ]
    )
    return "\n".join(lines)


__all__ = [
    "FORMAT",
    "SCHEMA_VERSION",
    "inspect_environment_status",
    "render_environment_status",
]
