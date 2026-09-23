"""Read-only, evidence-labelled workspace discovery for Workbench Doctor V1."""

from __future__ import annotations

from collections import Counter
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Mapping, Sequence

from .git_observation import (
    GitObservationError,
    configured_git_executable,
    safe_git_prefix,
)


REPORT_FORMAT = "workbench-project-intelligence-workspace-doctor-report-v1"
REPORT_SCHEMA_VERSION = 1
EVIDENCE_STATES = frozenset(
    {
        "observed",
        "declared",
        "known-absent",
        "unresolved",
        "ambiguous",
        "unavailable",
        "bounded",
    }
)
SEVERITIES = frozenset({"blocker", "warning", "info"})
SUMMARY_STATUSES = frozenset({"ready", "attention", "blocked"})

_BUILD_MARKERS = (
    "settings.gradle",
    "settings.gradle.kts",
    "build.gradle",
    "build.gradle.kts",
)
_SOURCE_KINDS = frozenset({"java", "groovy", "kotlin", "resources"})
_MOD_ID_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


class WorkspaceDoctorError(RuntimeError):
    """An invalid doctor input or internally inconsistent V1 report."""


def _run(
    command: Sequence[str], *, cwd: Path | None = None, timeout: float = 5.0
) -> subprocess.CompletedProcess[str] | None:
    environment = os.environ.copy()
    # Git status is an observation here.  Do not let it refresh the index or
    # invoke a repository-configured filesystem monitor while servicing a
    # command that promises to be read-only.
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    try:
        return subprocess.run(
            list(command),
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix() or "."
    except ValueError:
        return str(path)


def discover_workspace_root(path: Path) -> Path:
    """Resolve the nearest Gradle workspace, falling back to a repository root."""

    requested = path.expanduser()
    try:
        resolved = requested.resolve(strict=True)
    except OSError as exc:
        raise WorkspaceDoctorError(f"workspace path is unavailable: {requested}") from exc
    start = resolved.parent if resolved.is_file() else resolved
    if not start.is_dir():
        raise WorkspaceDoctorError(f"workspace path is not a directory: {start}")

    nearest_build: Path | None = None
    repository: Path | None = None
    for candidate in (start, *start.parents):
        pack_manifest = candidate / "pack.toml"
        if pack_manifest.is_file() or pack_manifest.is_symlink():
            return candidate
        if any((candidate / marker).is_file() for marker in _BUILD_MARKERS[:2]):
            return candidate
        if nearest_build is None and any(
            (candidate / marker).is_file() for marker in _BUILD_MARKERS[2:]
        ):
            nearest_build = candidate
        if repository is None and (candidate / ".git").exists():
            repository = candidate
        if nearest_build is not None and repository == candidate:
            break
    return nearest_build or repository or start


def _git_context(workspace: Path) -> dict[str, Any]:
    try:
        git = configured_git_executable()
    except GitObservationError as exc:
        return {
            "state": "unavailable",
            "root": None,
            "branch": None,
            "head": None,
            "dirty": None,
            "changes": {},
            "reason": str(exc),
        }
    if not git:
        return {
            "state": "unavailable",
            "root": None,
            "branch": None,
            "head": None,
            "dirty": None,
            "changes": {},
            "reason": "git executable is unavailable",
        }
    discovery_git = [
        git,
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.untrackedCache=false",
    ]
    root_result = _run(
        [*discovery_git, "-C", str(workspace), "rev-parse", "--show-toplevel"]
    )
    if root_result is None:
        return {
            "state": "unavailable",
            "root": None,
            "branch": None,
            "head": None,
            "dirty": None,
            "changes": {},
            "reason": "Git worktree discovery failed or timed out",
        }
    if root_result.returncode:
        detail = (root_result.stderr or root_result.stdout).strip()
        known_absent = "not a git repository" in detail.lower()
        return {
            "state": "known-absent" if known_absent else "unavailable",
            "root": None,
            "branch": None,
            "head": None,
            "dirty": None,
            "changes": {},
            "reason": (
                "workspace is not inside a Git worktree"
                if known_absent
                else f"Git worktree discovery failed: {detail or root_result.returncode}"
            ),
        }
    repository = Path(root_result.stdout.strip()).resolve()
    try:
        safe_git = safe_git_prefix(repository, executable=git)
    except GitObservationError as exc:
        return {
            "state": "unavailable",
            "root": str(repository),
            "branch": None,
            "head": None,
            "dirty": None,
            "changes": {},
            "reason": str(exc),
        }
    head_result = _run([*safe_git, "-C", str(repository), "rev-parse", "HEAD"])
    branch_result = _run(
        [
            *safe_git,
            "-C",
            str(repository),
            "symbolic-ref",
            "--quiet",
            "--short",
            "HEAD",
        ]
    )
    relative_workspace = _relative(workspace, repository)
    if head_result is None or branch_result is None:
        return {
            "state": "unavailable",
            "root": str(repository),
            "workspace_pathspec": relative_workspace,
            "branch": None,
            "head": None,
            "dirty": None,
            "changes": {},
            "reason": "Git HEAD or branch discovery failed or timed out",
        }
    if head_result.returncode:
        head_detail = (head_result.stderr or head_result.stdout).strip()
        unborn = any(
            marker in head_detail.lower()
            for marker in ("unknown revision", "ambiguous argument 'head'")
        )
        if not unborn:
            return {
                "state": "unavailable",
                "root": str(repository),
                "workspace_pathspec": relative_workspace,
                "branch": (
                    branch_result.stdout.strip()
                    if branch_result.returncode == 0
                    else None
                ),
                "head": None,
                "dirty": None,
                "changes": {},
                "reason": f"Git HEAD discovery failed: {head_detail or head_result.returncode}",
            }
    status_command = [
        *safe_git,
        "-C",
        str(repository),
        "status",
        "--porcelain=v1",
        "--untracked-files=normal",
        "--ignore-submodules=all",
    ]
    if relative_workspace != ".":
        status_command.extend(["--", relative_workspace])
    status_result = _run(status_command)
    if status_result is None or status_result.returncode:
        detail = (
            "Git status failed or timed out"
            if status_result is None
            else (status_result.stderr or status_result.stdout).strip()
            or f"Git status exited {status_result.returncode}"
        )
        return {
            "state": "unavailable",
            "root": str(repository),
            "workspace_pathspec": relative_workspace,
            "branch": (
                branch_result.stdout.strip()
                if branch_result is not None and branch_result.returncode == 0
                else None
            ),
            "head": (
                head_result.stdout.strip()
                if head_result is not None and head_result.returncode == 0
                else None
            ),
            "dirty": None,
            "changes": {},
            "reason": detail,
        }
    lines = [line for line in status_result.stdout.splitlines() if line]
    changes = Counter()
    for line in lines:
        code = line[:2]
        if code == "??":
            changes["untracked"] += 1
        elif code == "!!":
            changes["ignored"] += 1
        elif "U" in code or code in {"AA", "DD"}:
            changes["conflicted"] += 1
        else:
            if code[0] not in {" ", "?"}:
                changes["staged"] += 1
            if code[1] not in {" ", "?"}:
                changes["unstaged"] += 1
    return {
        "state": "observed",
        "root": str(repository),
        "workspace_pathspec": relative_workspace,
        "branch": (
            branch_result.stdout.strip()
            if branch_result is not None and branch_result.returncode == 0
            else None
        ),
        "head": (
            head_result.stdout.strip()
            if head_result is not None and head_result.returncode == 0
            else None
        ),
        "dirty": bool(lines),
        "changes": dict(sorted(changes.items())),
        "reason": None,
    }


def _read_properties(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file() or path.is_symlink():
        return values
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return values
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "!")):
            continue
        match = re.match(r"([^:=\s]+)\s*[:=]\s*(.*)$", stripped)
        if match:
            values[match.group(1)] = match.group(2).strip()
    return values


def _read_build_text(workspace: Path) -> tuple[list[str], str]:
    scripts: list[str] = []
    text: list[str] = []
    for name in _BUILD_MARKERS:
        path = workspace / name
        if not path.is_file() or path.is_symlink():
            continue
        scripts.append(name)
        try:
            text.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    return scripts, "\n".join(text)


def _first_property(properties: Mapping[str, str], *names: str) -> str | None:
    for name in names:
        value = properties.get(name)
        if value:
            return value
    return None


def _declared_java(build_text: str) -> dict[str, Any]:
    language = re.search(
        r"JavaLanguageVersion\s*\.\s*of\s*\(\s*([0-9]+)\s*\)", build_text
    )
    source = re.search(
        r"sourceCompatibility\s*=\s*(?:JavaVersion\.)?(?:VERSION_)?([0-9_]+)",
        build_text,
    )
    target = re.search(
        r"targetCompatibility\s*=\s*(?:JavaVersion\.)?(?:VERSION_)?([0-9_]+)",
        build_text,
    )

    def normalize(match: re.Match[str] | None) -> str | None:
        if match is None:
            return None
        return match.group(1).replace("_", ".")

    return {
        "toolchain_language": normalize(language),
        "source_compatibility": normalize(source),
        "target_compatibility": normalize(target),
    }


def _gradle_wrapper(workspace: Path) -> dict[str, Any]:
    script = workspace / ("gradlew.bat" if os.name == "nt" else "gradlew")
    properties = workspace / "gradle/wrapper/gradle-wrapper.properties"
    distribution: str | None = None
    version: str | None = None
    if properties.is_file() and not properties.is_symlink():
        values = _read_properties(properties)
        distribution = values.get("distributionUrl")
        if distribution:
            match = re.search(r"gradle-([0-9]+(?:\.[0-9]+)*)-", distribution)
            version = match.group(1) if match else None
    present = script.is_file() and not script.is_symlink()
    executable = present and (os.name == "nt" or os.access(script, os.X_OK))
    return {
        "state": "observed" if present else "known-absent",
        "script": str(script) if present else None,
        "executable": executable,
        "distribution_url": distribution,
        "version": version,
    }


def _build_context(workspace: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    scripts, build_text = _read_build_text(workspace)
    properties_path = workspace / "gradle.properties"
    properties = _read_properties(properties_path)
    plugins = sorted(
        set(
            re.findall(
                r"\bid\s*(?:\(|\s)\s*['\"]([^'\"]+)['\"]", build_text
            )
        )
    )
    dependencies = sorted(
        set(
            re.findall(
                r"['\"]([A-Za-z0-9_.-]+:[A-Za-z0-9_.-]+:[^'\"\s)]+)['\"]",
                build_text,
            )
        )
    )
    provider = "gradle" if scripts else None
    cleanroom_version = _first_property(
        properties, "cleanroom_version", "cleanroomVersion"
    )
    minecraft_version = _first_property(
        properties, "minecraft_version", "minecraftVersion", "mc_version"
    )
    forge_version = _first_property(properties, "forge_version", "forgeVersion")
    mappings_channel = _first_property(properties, "mcp_channel", "mappings_channel")
    mappings_version = _first_property(properties, "mcp_version", "mappings_version")
    cleanroom_marker = bool(
        cleanroom_version
        or re.search(r"\bcleanroom\s*\{", build_text)
        or any("cleanroom" in item.lower() for item in plugins)
        or any("cleanroom" in item.lower() for item in dependencies)
    )
    explicit_forge_marker = bool(
        any("forge" in item.lower() for item in plugins)
        or any(
            item.lower().startswith("net.minecraftforge:")
            or ":forge:" in item.lower()
            for item in dependencies
        )
    )
    forge_marker = bool(forge_version or explicit_forge_marker)
    platform: dict[str, Any]
    if cleanroom_marker and explicit_forge_marker:
        platform = {
            "state": "ambiguous",
            "kind": "cleanroom-and-legacy-forge",
            "minecraft_version": minecraft_version,
            "cleanroom_version": cleanroom_version,
            "forge_version": forge_version,
            "mappings": {
                "channel": mappings_channel,
                "version": mappings_version,
            },
            "maturity": None,
            "profile_id": None,
            "evidence": [
                item
                for item in (
                    "gradle.properties" if properties else None,
                    *scripts,
                )
                if item
            ],
        }
    elif cleanroom_marker:
        platform = {
            "state": "declared" if cleanroom_version else "unresolved",
            "kind": "cleanroom",
            "minecraft_version": minecraft_version,
            "cleanroom_version": cleanroom_version,
            "forge_version": forge_version,
            "mappings": {
                "channel": mappings_channel,
                "version": mappings_version,
            },
            "maturity": None,
            "profile_id": None,
            "evidence": [
                item
                for item in (
                    "gradle.properties" if properties else None,
                    *scripts,
                )
                if item
            ],
        }
    elif forge_marker:
        platform = {
            "state": "declared",
            "kind": "legacy-forge",
            "minecraft_version": minecraft_version,
            "cleanroom_version": None,
            "forge_version": forge_version,
            "mappings": {
                "channel": mappings_channel,
                "version": mappings_version,
            },
            "maturity": "historical-or-unresolved",
            "profile_id": None,
            "evidence": [
                item
                for item in (
                    "gradle.properties" if properties else None,
                    *scripts,
                )
                if item
            ],
        }
    else:
        platform = {
            "state": "unresolved",
            "kind": None,
            "minecraft_version": minecraft_version,
            "cleanroom_version": None,
            "forge_version": forge_version,
            "mappings": {
                "channel": mappings_channel,
                "version": mappings_version,
            },
            "maturity": None,
            "profile_id": None,
            "evidence": scripts,
        }
    build = {
        "state": "observed" if provider else "known-absent",
        "provider": provider,
        "scripts": scripts,
        "properties_file": (
            str(properties_path) if properties_path.is_file() else None
        ),
        "plugins": plugins,
        "dependency_coordinates": dependencies,
        "wrapper": _gradle_wrapper(workspace),
        "declared_java": _declared_java(build_text),
    }
    return build, platform


def _source_roots(workspace: Path) -> dict[str, list[str]]:
    roots: dict[str, list[str]] = {
        "source": [],
        "resources": [],
        "generated": [],
        "groovy": [],
        "configuration": [],
        "mixin_configs": [],
    }
    src = workspace / "src"
    if src.is_dir() and not src.is_symlink():
        for candidate in sorted(src.glob("*/*")):
            if not candidate.is_dir() or candidate.is_symlink():
                continue
            kind = candidate.name.lower()
            relative = _relative(candidate, workspace)
            if kind == "resources":
                roots["resources"].append(relative)
            elif kind in _SOURCE_KINDS:
                roots["source"].append(relative)
                if kind == "groovy":
                    roots["groovy"].append(relative)
        for generated in sorted(src.glob("generated*")):
            if generated.is_dir() and not generated.is_symlink():
                roots["generated"].append(_relative(generated, workspace))
    for relative in (
        "build/generated",
        "generated",
        "groovy",
        "scripts",
        "examples/groovy",
        "config",
        "configs",
        "defaultconfigs",
    ):
        candidate = workspace / relative
        if not candidate.is_dir() or candidate.is_symlink():
            continue
        bucket = (
            "generated"
            if "generated" in relative
            else "groovy"
            if "groovy" in relative or relative == "scripts"
            else "configuration"
        )
        roots[bucket].append(relative)
    for raw_root in roots["resources"]:
        resource = workspace / raw_root
        try:
            configs = sorted(resource.rglob("mixins*.json"))
        except OSError:
            configs = []
        for config in configs[:256]:
            if config.is_file() and not config.is_symlink():
                roots["mixin_configs"].append(_relative(config, workspace))
    return {key: sorted(set(values)) for key, values in roots.items()}


def _read_mcmod(path: Path) -> list[dict[str, Any]]:
    if not path.is_file() or path.is_symlink():
        return []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    if isinstance(value, dict) and isinstance(value.get("modList"), list):
        rows = value["modList"]
    elif isinstance(value, list):
        rows = value
    elif isinstance(value, dict):
        rows = [value]
    else:
        rows = []
    result: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        mod_id = row.get("modid")
        if not isinstance(mod_id, str) or not _MOD_ID_RE.fullmatch(mod_id):
            continue
        result.append(
            {
                "mod_id": mod_id,
                "name": row.get("name") if isinstance(row.get("name"), str) else None,
                "version": (
                    row.get("version")
                    if isinstance(row.get("version"), str)
                    else None
                ),
                "descriptor": str(path),
            }
        )
    return result


def _mod_descriptors(workspace: Path, surfaces: Mapping[str, list[str]]) -> list[dict[str, Any]]:
    mods: list[dict[str, Any]] = []
    for relative in surfaces.get("resources", []):
        mods.extend(_read_mcmod(workspace / relative / "mcmod.info"))
    return sorted(mods, key=lambda row: (row["mod_id"], row["descriptor"]))


def new_report(workspace: Path, *, requested_path: Path | None = None) -> dict[str, Any]:
    """Inspect project-owned files and Git state without executing the build."""

    root = discover_workspace_root(workspace)
    # ``requested_path`` is user-facing context rather than filesystem
    # authority.  Keep the caller's case spelling on case-insensitive Windows
    # volumes while still making it absolute and collapsing relative segments;
    # ``Path.resolve`` canonicalizes ``C:\\WINDOWS`` to ``C:\\Windows`` and
    # would make otherwise deterministic native-host tests and output diverge.
    requested = Path(
        os.path.abspath(os.fspath((requested_path or workspace).expanduser()))
    )
    repository = _git_context(root)
    build, platform = _build_context(root)
    surfaces = _source_roots(root)
    surfaces["mods"] = _mod_descriptors(root, surfaces)
    report: dict[str, Any] = {
        "format": REPORT_FORMAT,
        "schema_version": REPORT_SCHEMA_VERSION,
        "read_only": True,
        "capability": "workspace-context",
        "summary": {
            "status": "ready",
            "blockers": 0,
            "warnings": 0,
            "information": 0,
        },
        "target": {
            "workspace": {
                "state": "observed",
                "requested_path": str(requested),
                "root": str(root),
                "kind": "gradle-project" if build["provider"] else "repository",
            },
            "repository": repository,
            "profile": {
                "state": "unresolved",
                "path": None,
                "sha256": None,
                "profile_id": None,
                "pack_profile_id": None,
                "selection": "none",
            },
            "platform": platform,
            "build": build,
            "java_roles": [],
            "surfaces": surfaces,
            "runtime": {
                "state": "unavailable",
                "reason": "runtime inspection requires an applicable capability profile",
            },
            "integrations": {
                "state": "unavailable",
                "items": [],
                "reason": "integration inspection requires an applicable capability profile",
            },
        },
        "findings": [],
        "repair_plan": [],
        "next_commands": [],
        "limitations": [
            "V1 reads declared project files and bounded local tool/runtime state; it does not run Gradle, launch Minecraft, or prove runtime behavior.",
            "Static declarations can describe possible configuration; Atlas and Crucible remain authoritative for observed assembled-game behavior.",
            "Archive-wide Mixin, class/resource collision, dependency closure, client-side, and save-compatibility checks are unavailable unless a later capability adapter explicitly supplies them.",
        ],
    }
    if repository["state"] == "observed" and repository["dirty"]:
        add_finding(
            report,
            finding_id="WORKTREE_DIRTY",
            severity="info",
            title="The selected workspace has uncommitted Git state",
            detail="Doctor records this context because a build can differ from HEAD; it does not require a clean tree for a local experiment.",
            evidence=[
                f"pathspec={repository['workspace_pathspec']}",
                f"changes={json.dumps(repository['changes'], sort_keys=True)}",
            ],
            repair_action="Review or retain the changes intentionally before sharing a reproduction.",
        )
    if repository["state"] == "unavailable":
        add_finding(
            report,
            finding_id="GIT_CONTEXT_UNAVAILABLE",
            severity="warning",
            title="Git worktree state could not be inspected",
            detail="Doctor will not report an unreadable or timed-out repository probe as a clean worktree.",
            evidence=[repository.get("reason") or str(root)],
            repair_action="Restore local Git access or rerun after the transient probe failure.",
        )
    if build["state"] == "known-absent":
        add_finding(
            report,
            finding_id="BUILD_PROVIDER_UNRESOLVED",
            severity="warning",
            title="No supported build root was discovered",
            detail="V1 recognizes Gradle settings/build files at the selected root.",
            evidence=[str(root)],
            repair_action="Select the exact mod subproject or supply a supported capability profile.",
            repair_command=None,
        )
    if platform["state"] == "ambiguous":
        add_finding(
            report,
            finding_id="PLATFORM_AMBIGUOUS",
            severity="warning",
            title="Both Cleanroom and legacy Forge platform markers were discovered",
            detail="Repository URLs alone are not platform authority, but conflicting plugin or dependency declarations must be resolved explicitly.",
            evidence=platform["evidence"] or build["scripts"] or [str(root)],
            repair_action="Select one exact platform profile and remove or explain the conflicting declaration.",
        )
    elif platform["state"] == "unresolved" and platform["kind"] == "cleanroom":
        add_finding(
            report,
            finding_id="CLEANROOM_VERSION_UNRESOLVED",
            severity="warning",
            title="Cleanroom is declared without an exact release",
            detail="A Cleanroom marker was found, but its exact version is not declared in the bounded Gradle surface.",
            evidence=platform["evidence"] or build["scripts"] or [str(root)],
            repair_action="Declare or select the exact Cleanroom platform profile before construction.",
        )
    elif platform["state"] == "unresolved":
        add_finding(
            report,
            finding_id="PLATFORM_UNRESOLVED",
            severity="warning",
            title="The active Minecraft platform is unresolved",
            detail="No Cleanroom or Forge declaration was found in the bounded Gradle surface.",
            evidence=build["scripts"] or [str(root)],
            repair_action="Select an exact Cleanroom profile or add an explicit project declaration.",
        )
    elif platform["kind"] == "legacy-forge":
        add_finding(
            report,
            finding_id="LEGACY_FORGE_OBSERVATION_ONLY",
            severity="warning",
            title="The discovered target is Forge without a Cleanroom declaration",
            detail="Historical Forge evidence is valid only for its legacy profile; active Workbench construction targets Cleanroom.",
            evidence=platform["evidence"],
            repair_action="Select or provision a declared Cleanroom development profile before construction.",
        )
    finalize_report(report)
    return report


def add_finding(
    report: dict[str, Any],
    *,
    finding_id: str,
    severity: str,
    title: str,
    detail: str,
    evidence: Sequence[str],
    repair_action: str,
    repair_command: str | None = None,
) -> None:
    if severity not in SEVERITIES:
        raise WorkspaceDoctorError(f"unsupported finding severity: {severity}")
    if any(row.get("id") == finding_id for row in report.get("findings", [])):
        raise WorkspaceDoctorError(f"duplicate doctor finding ID: {finding_id}")
    report.setdefault("findings", []).append(
        {
            "id": finding_id,
            "severity": severity,
            "title": title,
            "detail": detail,
            "evidence": [str(item) for item in evidence],
            "repair": {
                "action": repair_action,
                "command": repair_command,
                "mutates": False,
            },
        }
    )


def add_next_command(
    report: dict[str, Any],
    *,
    command_id: str,
    purpose: str,
    command: str,
    blocked_by: Sequence[str] = (),
) -> None:
    report.setdefault("next_commands", []).append(
        {
            "id": command_id,
            "purpose": purpose,
            "command": command,
            "available": not blocked_by,
            "blocked_by": list(blocked_by),
        }
    )


def merge_worldgen_capability_report(
    report: dict[str, Any], fragment: Mapping[str, Any]
) -> dict[str, Any]:
    """Merge one Crucible worldgen preflight into the product-owned report envelope."""

    if fragment.get("capability") != "worldgen-dev":
        raise WorkspaceDoctorError("unsupported Doctor capability fragment")
    target = report.get("target")
    if not isinstance(target, dict):
        raise WorkspaceDoctorError("workspace Doctor target envelope is invalid")
    report["capability"] = fragment["capability"]
    for key in ("profile", "platform", "java_roles", "runtime", "integrations"):
        if key not in fragment:
            raise WorkspaceDoctorError(f"Doctor capability fragment is missing {key}")
        target[key] = fragment[key]
    build = target.get("build")
    if not isinstance(build, dict) or "build_tool" not in fragment:
        raise WorkspaceDoctorError("Doctor capability build context is invalid")
    build["selected_tool"] = fragment["build_tool"]
    roles = target["java_roles"]
    if not isinstance(roles, list):
        raise WorkspaceDoctorError("Doctor capability Java roles are invalid")
    for role in roles:
        if isinstance(role, dict) and role.get("role") == "emitted-bytecode":
            role["selected"] = build.get("declared_java")
    surfaces = target.get("surfaces")
    profile = target["profile"]
    if not isinstance(surfaces, dict) or not isinstance(profile, Mapping):
        raise WorkspaceDoctorError("Doctor capability surfaces are invalid")
    plan = profile.get("plan")
    if isinstance(plan, Mapping) and isinstance(plan.get("path"), str):
        surfaces["selected_groovy_plan"] = [plan["path"]]
    findings = report.get("findings")
    fragment_findings = fragment.get("findings")
    if not isinstance(findings, list) or not isinstance(fragment_findings, list):
        raise WorkspaceDoctorError("Doctor capability findings are invalid")
    findings.extend(fragment_findings)
    limitations = report.get("limitations")
    fragment_limitations = fragment.get("limitations")
    if not isinstance(limitations, list) or not isinstance(fragment_limitations, list):
        raise WorkspaceDoctorError("Doctor capability limitations are invalid")
    limitations.extend(item for item in fragment_limitations if item not in limitations)
    all_blockers = {
        row["id"]
        for row in findings
        if isinstance(row, Mapping)
        and row.get("severity") == "blocker"
        and isinstance(row.get("id"), str)
    }
    commands = report.get("next_commands")
    fragment_commands = fragment.get("next_commands")
    if not isinstance(commands, list) or not isinstance(fragment_commands, list):
        raise WorkspaceDoctorError("Doctor capability commands are invalid")
    for command in fragment_commands:
        if not isinstance(command, Mapping):
            raise WorkspaceDoctorError("Doctor capability command is invalid")
        row = dict(command)
        blocked_by = list(row.get("blocked_by", []))
        if row.get("id") == "worldgen-dev":
            blocked_by = sorted(set(blocked_by) | all_blockers)
        row["blocked_by"] = blocked_by
        row["available"] = not blocked_by
        commands.append(row)
    return finalize_report(report)


def finalize_report(report: dict[str, Any]) -> dict[str, Any]:
    findings = report.get("findings", [])
    counts = Counter(row.get("severity") for row in findings)
    status = (
        "blocked"
        if counts["blocker"]
        else "attention"
        if counts["warning"]
        else "ready"
    )
    report["findings"] = sorted(
        findings,
        key=lambda row: (
            {"blocker": 0, "warning": 1, "info": 2}.get(row["severity"], 9),
            row["id"],
        ),
    )
    report["repair_plan"] = [
        row["id"] for row in report["findings"] if row["severity"] != "info"
    ]
    report["summary"] = {
        "status": status,
        "blockers": counts["blocker"],
        "warnings": counts["warning"],
        "information": counts["info"],
    }
    validate_workspace_doctor_report(report)
    return report


def validate_workspace_doctor_report(report: Mapping[str, Any]) -> None:
    expected = {
        "format",
        "schema_version",
        "read_only",
        "capability",
        "summary",
        "target",
        "findings",
        "repair_plan",
        "next_commands",
        "limitations",
    }
    if set(report) != expected:
        raise WorkspaceDoctorError("workspace doctor report has unsupported top-level fields")
    if report.get("format") != REPORT_FORMAT or report.get("schema_version") != 1:
        raise WorkspaceDoctorError("unsupported workspace doctor report version")
    if report.get("read_only") is not True:
        raise WorkspaceDoctorError("workspace doctor V1 must be read-only")
    if not isinstance(report.get("capability"), str) or not report["capability"]:
        raise WorkspaceDoctorError("workspace doctor capability must be named")
    summary = report.get("summary")
    if (
        not isinstance(summary, Mapping)
        or set(summary) != {"status", "blockers", "warnings", "information"}
        or summary.get("status") not in SUMMARY_STATUSES
    ):
        raise WorkspaceDoctorError("workspace doctor summary is invalid")
    target = report.get("target")
    required_target = {
        "workspace",
        "repository",
        "profile",
        "platform",
        "build",
        "java_roles",
        "surfaces",
        "runtime",
        "integrations",
    }
    if not isinstance(target, Mapping) or set(target) != required_target:
        raise WorkspaceDoctorError("workspace doctor target context is invalid")
    for key in ("workspace", "repository", "profile", "platform", "build", "runtime", "integrations"):
        value = target[key]
        if not isinstance(value, Mapping) or value.get("state") not in EVIDENCE_STATES:
            raise WorkspaceDoctorError(f"workspace doctor target.{key} state is invalid")
    roles = target["java_roles"]
    if not isinstance(roles, list):
        raise WorkspaceDoctorError("workspace doctor Java roles must be an array")
    for role in roles:
        if not isinstance(role, Mapping) or role.get("state") not in EVIDENCE_STATES:
            raise WorkspaceDoctorError("workspace doctor Java role state is invalid")
    surfaces = target["surfaces"]
    if not isinstance(surfaces, Mapping) or not all(
        isinstance(name, str) and name and isinstance(values, list)
        for name, values in surfaces.items()
    ):
        raise WorkspaceDoctorError("workspace doctor surfaces must be named arrays")
    findings = report.get("findings")
    if not isinstance(findings, list):
        raise WorkspaceDoctorError("workspace doctor findings must be an array")
    ids: set[str] = set()
    counts = Counter()
    for finding in findings:
        if not isinstance(finding, Mapping) or set(finding) != {
            "id",
            "severity",
            "title",
            "detail",
            "evidence",
            "repair",
        }:
            raise WorkspaceDoctorError("workspace doctor finding must be an object")
        finding_id = finding.get("id")
        severity = finding.get("severity")
        if (
            not isinstance(finding_id, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", finding_id)
            or finding_id in ids
        ):
            raise WorkspaceDoctorError("workspace doctor finding IDs must be unique")
        if severity not in SEVERITIES:
            raise WorkspaceDoctorError("workspace doctor finding severity is invalid")
        if not all(
            isinstance(finding.get(key), str) and finding[key]
            for key in ("title", "detail")
        ):
            raise WorkspaceDoctorError("workspace doctor finding text is invalid")
        evidence = finding.get("evidence")
        if not isinstance(evidence, list) or not evidence or not all(
            isinstance(item, str) and item for item in evidence
        ):
            raise WorkspaceDoctorError("workspace doctor finding evidence is invalid")
        repair = finding.get("repair")
        if (
            not isinstance(repair, Mapping)
            or set(repair) != {"action", "command", "mutates"}
            or not isinstance(repair.get("action"), str)
            or not repair["action"]
            or (
                repair.get("command") is not None
                and (
                    not isinstance(repair["command"], str)
                    or not repair["command"]
                )
            )
            or repair.get("mutates") is not False
        ):
            raise WorkspaceDoctorError("workspace doctor repairs must be non-applied proposals")
        ids.add(finding_id)
        counts[severity] += 1
    expected_status = (
        "blocked" if counts["blocker"] else "attention" if counts["warning"] else "ready"
    )
    if summary != {
        "status": expected_status,
        "blockers": counts["blocker"],
        "warnings": counts["warning"],
        "information": counts["info"],
    }:
        raise WorkspaceDoctorError("workspace doctor summary does not match findings")
    expected_plan = [row["id"] for row in findings if row["severity"] != "info"]
    if report.get("repair_plan") != expected_plan:
        raise WorkspaceDoctorError("workspace doctor repair plan is not finding-ordered")
    commands = report.get("next_commands")
    if not isinstance(commands, list):
        raise WorkspaceDoctorError("workspace doctor next commands must be an array")
    command_ids: set[str] = set()
    for command in commands:
        if not isinstance(command, Mapping) or set(command) != {
            "id",
            "purpose",
            "command",
            "available",
            "blocked_by",
        }:
            raise WorkspaceDoctorError("workspace doctor command must be an object")
        command_id = command.get("id")
        if (
            not isinstance(command_id, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", command_id)
            or command_id in command_ids
            or not all(
                isinstance(command.get(key), str) and command[key]
                for key in ("purpose", "command")
            )
        ):
            raise WorkspaceDoctorError("workspace doctor command identity is invalid")
        blocked_by = command.get("blocked_by")
        if (
            not isinstance(blocked_by, list)
            or len(blocked_by) != len(set(blocked_by))
            or not set(blocked_by).issubset(ids)
        ):
            raise WorkspaceDoctorError("workspace doctor command references unknown blockers")
        if not isinstance(command.get("available"), bool) or command.get(
            "available"
        ) is not (not blocked_by):
            raise WorkspaceDoctorError("workspace doctor command availability is inconsistent")
        command_ids.add(command_id)
    limitations = report.get("limitations")
    if (
        not isinstance(limitations, list)
        or len(limitations) != len(set(limitations))
        or not all(isinstance(item, str) and item for item in limitations)
    ):
        raise WorkspaceDoctorError("workspace doctor limitations are invalid")


def render_workspace_doctor_report(report: Mapping[str, Any]) -> str:
    validate_workspace_doctor_report(report)
    summary = report["summary"]
    target = report["target"]
    workspace = target["workspace"]
    repository = target["repository"]
    platform = target["platform"]
    profile = target["profile"]
    build = target["build"]
    runtime = target["runtime"]
    lines = [
        "Workbench doctor",
        f"Status: {summary['status']} ({summary['blockers']} blockers, {summary['warnings']} warnings, {summary['information']} info)",
        f"Capability: {report['capability']}",
    ]
    if workspace.get("requested_path") != workspace.get("root"):
        lines.append(f"Requested context: {workspace.get('requested_path')}")
    lines.append(f"Workspace: {workspace.get('root')} [{workspace.get('kind')}]")
    if repository.get("state") == "observed":
        revision = repository.get("branch") or "detached"
        if repository.get("head"):
            revision += f"@{str(repository['head'])[:12]}"
        cleanliness = "dirty" if repository.get("dirty") else "clean"
        lines.append(
            f"Repository: {repository.get('root')} ({revision}, {cleanliness})"
        )
    lines.extend(
        [
            "Profile: "
        + (
            f"{profile.get('profile_id')} ({profile.get('state')})"
            if profile.get("profile_id")
            else f"unresolved ({profile.get('selection')})"
        ),
            "Platform: "
        + (
            " / ".join(
                item
                for item in (
                    platform.get("kind"),
                    platform.get("minecraft_version"),
                    platform.get("cleanroom_version"),
                )
                if item
            )
            or "unresolved"
        )
        + f" [{platform.get('state')}]",
        ]
    )
    mappings = platform.get("mappings")
    if isinstance(mappings, Mapping) and any(mappings.values()):
        lines.append(
            "Mappings: "
            + " / ".join(
                str(item)
                for item in (
                    mappings.get("channel"),
                    mappings.get("version"),
                    mappings.get("coordinate"),
                )
                if item
            )
        )
    selected_tool = build.get("selected_tool")
    selected_build = (
        selected_tool.get("selected")
        if isinstance(selected_tool, Mapping)
        and isinstance(selected_tool.get("selected"), Mapping)
        else {}
    )
    build_version = selected_build.get("version") if selected_build else None
    lines.append(
        "Build: "
        + " / ".join(
            str(item)
            for item in (build.get("provider"), build_version)
            if item
        )
        + f" [{build.get('state')}]"
    )
    if target["java_roles"]:
        lines.append("Java roles:")
        for role in target["java_roles"]:
            selected = role.get("selected") or {}
            value = (
                selected.get("version")
                or selected.get("target_compatibility")
                or selected.get("toolchain_language")
                or selected.get("path")
                or role.get("reason")
                or "unresolved"
            )
            lines.append(f"  {role.get('role')}: {value} [{role.get('state')}]")
    if runtime.get("template"):
        server = runtime.get("server_jar")
        server_name = (
            server.get("relative_path") if isinstance(server, Mapping) else None
        )
        jars = runtime.get("jar_inventory")
        jar_count = len(jars) if isinstance(jars, list) else 0
        safety = "safe" if runtime.get("safe_to_provision") else "unsafe"
        lines.append(
            f"Runtime: {runtime.get('template')} ({safety}, {jar_count} jars, server={server_name or 'unresolved'})"
        )
    else:
        lines.append(f"Runtime: {runtime.get('reason') or 'unresolved'} [{runtime.get('state')}]")
    integrations = target["integrations"].get("items")
    if isinstance(integrations, list) and integrations:
        lines.append(
            "Integrations: "
            + ", ".join(
                f"{row.get('id')}={row.get('state')}"
                for row in integrations
                if isinstance(row, Mapping)
            )
        )
    findings = report["findings"]
    if findings:
        lines.append("Findings:")
        for row in findings:
            lines.append(f"  {row['severity'].upper()} {row['id']}: {row['title']}")
            lines.append(f"    {row['detail']}")
            lines.append(f"    Repair: {row['repair']['action']}")
            if row["repair"].get("command"):
                lines.append(f"    Command: {row['repair']['command']}")
    else:
        lines.append("Findings: none in the bounded V1 checks")
    if report["next_commands"]:
        lines.append("Next commands:")
        for row in report["next_commands"]:
            availability = "available" if row["available"] else "blocked"
            lines.append(f"  {row['id']} [{availability}]: {row['command']}")
            lines.append(f"    {row['purpose']}")
    lines.append("No project files, builds, runtimes, or worlds were changed.")
    return "\n".join(lines) + "\n"


__all__ = [
    "EVIDENCE_STATES",
    "REPORT_FORMAT",
    "REPORT_SCHEMA_VERSION",
    "WorkspaceDoctorError",
    "add_finding",
    "add_next_command",
    "discover_workspace_root",
    "finalize_report",
    "merge_worldgen_capability_report",
    "new_report",
    "render_workspace_doctor_report",
    "validate_workspace_doctor_report",
]
