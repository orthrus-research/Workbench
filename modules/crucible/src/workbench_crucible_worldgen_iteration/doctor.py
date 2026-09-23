"""Read-only readiness checks for the existing ``worldgen dev`` capability."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
from typing import Any, Mapping, Sequence

from .iteration import (
    WorldgenIterationError,
    audit_runtime_template,
    discover_gradle,
    discover_java,
    discover_runtime_template,
    executable_identity,
    find_built_artifact,
    gradle_version,
    java_major,
    java_version,
    load_profile,
    resolve_profile_path,
    sha256_file,
)


def _finding(
    finding_id: str,
    severity: str,
    title: str,
    detail: str,
    evidence: Sequence[str],
    action: str,
    command: str | None = None,
) -> dict[str, Any]:
    return {
        "id": finding_id,
        "severity": severity,
        "title": title,
        "detail": detail,
        "evidence": list(evidence),
        "repair": {"action": action, "command": command, "mutates": False},
    }


def _candidate_java_commands(root: Path, minimum_major: int) -> list[Path]:
    candidates: list[Path] = []
    patterns = (
        root / ".workbench/jdks",
        Path.home() / ".gradle/jdks",
    )
    for base in patterns:
        if not base.is_dir():
            continue
        for path in sorted(base.glob("**/bin/java"))[:16]:
            if not path.is_file() or not os.access(path, os.X_OK):
                continue
            try:
                identity = executable_identity(
                    path.resolve(), ["-version"], timeout_seconds=3.0
                )
                if java_major(identity) >= minimum_major:
                    candidates.append(path.resolve())
            except (OSError, ValueError, WorldgenIterationError):
                continue
    return sorted(set(candidates))


def _candidate_lock(root: Path, cleanroom_version: str) -> tuple[dict[str, Any] | None, Path]:
    base = root / "profiles/platforms/cleanroom/candidates"
    path = base / cleanroom_version / "candidate-lock-v1.json"
    try:
        relative_parts = path.relative_to(root).parts
        current = root
        for part in relative_parts:
            current = current / part
            if current.is_symlink():
                return None, path
        resolved_base = base.resolve(strict=True)
        resolved = path.resolve(strict=True)
        resolved.relative_to(resolved_base)
    except (OSError, ValueError):
        return None, path
    if resolved != path or not path.is_file():
        return None, path
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None, path
    if not isinstance(value, dict):
        return None, path
    return value, path


def _valid_candidate_lock(value: Mapping[str, Any]) -> bool:
    if set(value) != {
        "candidate_id",
        "cleanroom",
        "forge",
        "format",
        "mappings",
        "maturity",
        "minecraft",
        "schema_version",
    }:
        return False
    cleanroom = value.get("cleanroom")
    forge = value.get("forge")
    mappings = value.get("mappings")
    minecraft = value.get("minecraft")
    if (
        value.get("format") != "workbench-cleanroom-candidate-lock-v1"
        or value.get("schema_version") != 1
        or not isinstance(value.get("candidate_id"), str)
        or not value["candidate_id"]
        or value.get("maturity") not in {"experimental", "stable"}
        or not isinstance(cleanroom, Mapping)
        or set(cleanroom) != {"release", "source_repository", "source_revision", "version"}
        or not isinstance(forge, Mapping)
        or set(forge) != {"version"}
        or not isinstance(mappings, Mapping)
        or set(mappings) != {"channel", "coordinate", "mcp_version", "version"}
        or not isinstance(minecraft, Mapping)
        or set(minecraft) != {"version"}
    ):
        return False
    release = cleanroom.get("release")
    if not isinstance(release, Mapping) or set(release) != {"sha256", "size", "url"}:
        return False
    strings = [
        cleanroom.get("source_repository"),
        cleanroom.get("version"),
        forge.get("version"),
        mappings.get("channel"),
        mappings.get("coordinate"),
        mappings.get("mcp_version"),
        mappings.get("version"),
        minecraft.get("version"),
        release.get("url"),
    ]
    expected_candidate_id = (
        f"workbench-platform:cleanroom:{cleanroom.get('version')}"
        f"+mc-{minecraft.get('version')}+forge-{forge.get('version')}"
        f"+mcp-{mappings.get('mcp_version')}-{mappings.get('coordinate')}"
    )
    return bool(
        all(isinstance(item, str) and item for item in strings)
        and value.get("candidate_id") == expected_candidate_id
        and mappings.get("coordinate")
        == f"{mappings.get('channel')}_{mappings.get('version')}"
        and isinstance(cleanroom.get("source_revision"), str)
        and re.fullmatch(r"[0-9a-f]{40}", cleanroom["source_revision"])
        and isinstance(release.get("sha256"), str)
        and re.fullmatch(r"[0-9a-f]{64}", release["sha256"])
        and isinstance(release.get("size"), int)
        and not isinstance(release["size"], bool)
        and release["size"] > 0
    )


def _artifact_context(root: Path, profile: Mapping[str, Any]) -> dict[str, Any]:
    try:
        artifact = find_built_artifact(root, profile)
    except WorldgenIterationError as exc:
        return {
            "state": "known-absent",
            "path": None,
            "sha256": None,
            "size_bytes": None,
            "reason": str(exc),
        }
    return {
        "state": "observed",
        "path": str(artifact),
        "sha256": sha256_file(artifact),
        "size_bytes": artifact.stat().st_size,
        "reason": None,
    }


def _strata_context(root: Path, configured: Path | None) -> dict[str, Any]:
    raw = configured or (
        Path(os.environ["WORKBENCH_STRATA_ROOT"])
        if os.environ.get("WORKBENCH_STRATA_ROOT")
        else root.parent / "strata"
    )
    path = raw.expanduser().resolve()
    entry = path / "tools/capture_dense_chunk_package.py"
    return {
        "state": "observed" if entry.is_file() and not entry.is_symlink() else "unavailable",
        "root": str(path),
        "entrypoint": str(entry),
        "reason": None if entry.is_file() and not entry.is_symlink() else "compatible Strata capture entrypoint is absent",
    }


def inspect_worldgen_development_target(
    root: Path,
    *,
    profile_name: str | None,
    profile_file: Path | None = None,
    runtime_template: Path | None = None,
    strata_root: Path | None = None,
    java_cmd: str | None = None,
    gradle_cmd: str | None = None,
) -> dict[str, Any]:
    """Resolve the runner's preflight inputs without creating iteration state."""

    root = root.resolve(strict=True)
    findings: list[dict[str, Any]] = []
    limitations = [
        "The worldgen-dev adapter covers the exact dedicated-server Supersymmetry iteration profile; integrated client, stable profile promotion, automatic acquisition, and general modpack provisioning are unavailable.",
        "Runtime mod inventory proves local archive bytes and mcmod.info IDs only; it does not prove loader selection, Mixin application, registry state, or successful launch.",
        "The Cleanroom Mixin Doctor remains a separate bounded static policy evaluation and is offered as a copyable command rather than silently folded into runtime truth.",
    ]
    result: dict[str, Any] = {
        "capability": "worldgen-dev",
        "workspace": None,
        "profile": {
            "state": "unresolved",
            "path": None,
            "sha256": None,
            "profile_id": None,
            "pack_profile_id": None,
            "selection": "none",
        },
        "platform": {
            "state": "unresolved",
            "kind": "cleanroom",
            "minecraft_version": None,
            "cleanroom_version": None,
            "forge_version": None,
            "mappings": {"channel": None, "version": None},
            "maturity": None,
            "profile_id": None,
            "candidate_lock": None,
            "evidence": [],
        },
        "java_roles": [],
        "build_tool": {
            "state": "unresolved",
            "selected": None,
            "required_version": None,
        },
        "runtime": {
            "state": "unavailable",
            "reason": "profile has not been resolved",
        },
        "integrations": {"state": "bounded", "items": []},
        "findings": findings,
        "next_commands": [],
        "limitations": limitations,
    }
    if profile_file is None and profile_name is None:
        findings.append(
            _finding(
                "PROFILE_SELECTION_REQUIRED",
                "blocker",
                "No pack capability profile was selected",
                "Workbench does not silently treat Supersymmetry as a universal modded-Minecraft target.",
                ["worldgen-dev requires an explicit --profile or --profile-file"],
                "Select the exact pack profile that owns the target.",
                "python3 tools/workbench.py doctor --profile supersymmetry",
            )
        )
        return result

    try:
        profile_path = (
            profile_file.expanduser().resolve()
            if profile_file is not None
            else resolve_profile_path(root, str(profile_name))
        )
        profile = load_profile(profile_path, root)
    except (OSError, ValueError, WorldgenIterationError) as exc:
        unresolved_path = (
            str(profile_file.expanduser())
            if profile_file is not None
            else f"profile-name:{profile_name}"
        )
        findings.append(
            _finding(
                "PROFILE_INVALID",
                "blocker",
                "The selected worldgen development profile is unavailable or invalid",
                str(exc),
                [unresolved_path],
                "Repair the versioned profile or select a different exact profile.",
            )
        )
        return result

    fixture = Path(profile["_fixture"])
    plan = Path(profile["_plan"])
    profile_identity = str(profile["profile_id"])
    expected_platform_id = (
        f"workbench-platform:cleanroom:{profile['cleanroom']}"
    )
    profile_identity_valid = bool(
        re.fullmatch(
            r"workbench-pack:[a-z0-9][a-z0-9_-]*:worldgen-[a-z0-9_.-]+",
            profile_identity,
        )
        and (
            profile_name is None
            or profile_identity.startswith(f"workbench-pack:{profile_name}:")
        )
    )
    if not profile_identity_valid:
        findings.append(
            _finding(
                "PACK_PROFILE_ID_INVALID",
                "blocker",
                "The selected pack capability profile has an invalid identity",
                "A path or profile name match does not authorize Workbench to invent or repair pack identity.",
                [str(profile_path), f"profile_id={profile_identity}"],
                "Repair the versioned pack profile identity before using it.",
            )
        )
    if profile["platform_profile_id"] != expected_platform_id:
        findings.append(
            _finding(
                "CLEANROOM_PLATFORM_PROFILE_ID_MISMATCH",
                "blocker",
                "The worldgen profile's Cleanroom identity disagrees with its version",
                "Exact platform selection must be self-consistent before tool or runtime checks can authorize a run.",
                [
                    str(profile_path),
                    f"declared={profile['platform_profile_id']}",
                    f"expected={expected_platform_id}",
                ],
                "Select or repair a profile whose platform ID binds the declared Cleanroom version.",
            )
        )
    result["workspace"] = str(fixture)
    result["profile"] = {
        "state": "declared",
        "path": str(profile_path),
        "sha256": sha256_file(profile_path),
        "profile_id": profile_identity,
        "pack_profile_id": profile_identity.split(":worldgen-", 1)[0],
        "selection": "explicit-file" if profile_file is not None else "explicit-name",
        "world_type": profile["world_type"],
        "plan": {
            "state": "observed",
            "path": str(plan),
            "sha256": sha256_file(plan),
        },
    }

    candidate, candidate_path = _candidate_lock(root, profile["cleanroom"])
    platform = result["platform"]
    platform.update(
        {
            "minecraft_version": profile["minecraft_version"],
            "cleanroom_version": profile["cleanroom"],
            "profile_id": profile["platform_profile_id"],
            "evidence": [str(profile_path)],
        }
    )
    candidate_shape_valid = bool(
        candidate is not None and _valid_candidate_lock(candidate)
    )
    if candidate is None:
        platform["state"] = "unresolved"
        findings.append(
            _finding(
                "CLEANROOM_CANDIDATE_LOCK_MISSING",
                "blocker",
                "The profile's exact Cleanroom candidate lock is unavailable",
                "The doctor will not infer Forge, mappings, or maturity from the version label alone.",
                [str(candidate_path)],
                "Restore or select the exact profile-owned candidate lock.",
            )
        )
    elif not candidate_shape_valid:
        platform["state"] = "unresolved"
        findings.append(
            _finding(
                "CLEANROOM_CANDIDATE_LOCK_INVALID",
                "blocker",
                "The profile's Cleanroom candidate lock has an unsupported shape",
                "Doctor V1 accepts only the preserved candidate-lock V1 identity and required exact fields.",
                [str(candidate_path)],
                "Restore the valid identity-bearing V1 candidate lock without rewriting its semantics.",
            )
        )
    else:
        declared_cleanroom = candidate.get("cleanroom", {}).get("version")
        declared_minecraft = candidate.get("minecraft", {}).get("version")
        if (
            declared_cleanroom != profile["cleanroom"]
            or declared_minecraft != profile["minecraft_version"]
        ):
            platform["state"] = "ambiguous"
            findings.append(
                _finding(
                    "CLEANROOM_CANDIDATE_PROFILE_MISMATCH",
                    "blocker",
                    "The pack profile and Cleanroom candidate lock disagree",
                    "Exact platform identity must close before a disposable run.",
                    [str(profile_path), str(candidate_path)],
                    "Select matching versioned profile and candidate-lock documents.",
                )
            )
        else:
            mappings = candidate.get("mappings", {})
            platform.update(
                {
                    "state": "declared",
                    "forge_version": candidate.get("forge", {}).get("version"),
                    "mappings": {
                        "channel": mappings.get("channel"),
                        "version": mappings.get("version"),
                        "coordinate": mappings.get("coordinate"),
                    },
                    "maturity": candidate.get("maturity"),
                    "candidate_lock": {
                        "path": str(candidate_path),
                        "sha256": sha256_file(candidate_path),
                        "candidate_id": candidate.get("candidate_id"),
                    },
                    "evidence": [str(profile_path), str(candidate_path)],
                }
            )
            if candidate.get("maturity") != "stable":
                findings.append(
                    _finding(
                        "CLEANROOM_PROFILE_EXPERIMENTAL",
                        "info",
                        "The selected Cleanroom candidate is experimental",
                        "It is admitted for reversible development experiments, not stable release claims.",
                        [str(candidate_path)],
                        "Use the bounded disposable lane and retain the experimental label.",
                    )
                )

    toolchains = profile["toolchains"]
    minimum_java = toolchains.get("minimum_cleanroom_java_major")
    proven_java = toolchains.get("proven_cleanroom_java")
    proven_gradle = toolchains.get("proven_gradle")
    if (
        not isinstance(minimum_java, int)
        or minimum_java < 1
        or not isinstance(proven_java, str)
        or not proven_java
        or not isinstance(proven_gradle, str)
        or not proven_gradle
    ):
        findings.append(
            _finding(
                "PROFILE_TOOLCHAIN_REQUIREMENTS_INVALID",
                "blocker",
                "The selected profile does not declare usable Java and Gradle requirements",
                "The runner cannot safely select automatic toolchains from incomplete requirements.",
                [str(profile_path), f"toolchains={json.dumps(toolchains, sort_keys=True)}"],
                "Repair the exact profile's minimum/proven Java and proven Gradle declarations.",
            )
        )
    if not isinstance(minimum_java, int) or minimum_java < 1:
        minimum_java = 1
    if not isinstance(proven_java, str) or not proven_java:
        proven_java = None
    if not isinstance(proven_gradle, str) or not proven_gradle:
        proven_gradle = None
    doctor_profile_args = (
        ["--profile-file", str(profile_path)]
        if profile_file is not None
        else ["--profile", str(profile_name)]
    )
    configured_java = java_cmd or os.environ.get("WORKBENCH_CLEANROOM_JAVA")
    selected_java: dict[str, Any] | None = None
    try:
        java = discover_java(root, java_cmd)
        selected_java = executable_identity(java, ["-version"])
        selected_java["version"] = java_version(selected_java)
        selected_java["major"] = java_major(selected_java)
        java_state = "observed"
        if selected_java["major"] < minimum_java:
            java_state = "ambiguous"
            findings.append(
                _finding(
                    "CLEANROOM_JAVA_TOO_OLD",
                    "blocker",
                    "The selected Cleanroom runtime Java is below the profile minimum",
                    f"Selected major {selected_java['major']}; required {minimum_java}+.",
                    [selected_java["path"], str(profile_path)],
                    "Select a modern JDK explicitly for this run.",
                )
            )
        elif (
            not configured_java
            and isinstance(proven_java, str)
            and selected_java["version"] != proven_java
        ):
            java_state = "ambiguous"
            findings.append(
                _finding(
                    "CLEANROOM_JAVA_AUTOMATIC_NOT_PROVEN",
                    "blocker",
                    "Automatic Java selection does not match the profile-proven release",
                    f"The selected Java satisfies the minimum but is not the proven {proven_java} release.",
                    [selected_java["path"], str(profile_path)],
                    "Pass --java-cmd to make the override intentional or select the proven release.",
                )
            )
    except (OSError, ValueError, WorldgenIterationError) as exc:
        java_state = "unavailable"
        candidates = _candidate_java_commands(root, minimum_java)
        repair = (
            shlex.join(
                [
                    "python3",
                    "tools/workbench.py",
                    "doctor",
                    *doctor_profile_args,
                    "--java-cmd",
                    str(candidates[0]),
                ]
            )
            if len(candidates) == 1
            else None
        )
        findings.append(
            _finding(
                "CLEANROOM_JAVA_UNAVAILABLE",
                "blocker",
                "No exact Cleanroom runtime Java was selected",
                str(exc),
                [f"required_major={minimum_java}", *[str(path) for path in candidates]],
                "Select one executable modern JDK explicitly; Doctor will not download it.",
                repair,
            )
        )
    for role_name, requirement in (
        ("cleanroom-runtime", {"minimum_major": minimum_java, "proven": proven_java}),
        ("mod-source-compiler", {"selection": "same executable as current worldgen runner"}),
        ("gradle-control-process", {"selection": "JAVA_HOME of selected Cleanroom Java"}),
    ):
        result["java_roles"].append(
            {
                "role": role_name,
                "state": java_state,
                "required": requirement,
                "selected": selected_java,
                "reason": None if selected_java else "Java discovery failed",
            }
        )
    result["java_roles"].append(
        {
            "role": "emitted-bytecode",
            "state": "declared",
            "required": {"consumer": "exact Cleanroom fixture"},
            "selected": None,
            "reason": "read from the fixture build declaration by Project Intelligence; output classfiles are not inspected by Doctor V1",
        }
    )

    build_tool = result["build_tool"]
    build_tool["required_version"] = proven_gradle
    try:
        gradle = discover_gradle(
            fixture,
            gradle_cmd,
            proven_gradle if isinstance(proven_gradle, str) else None,
            allow_wrapper=False,
        )
        environment = os.environ.copy()
        if selected_java:
            environment["JAVA_HOME"] = str(Path(selected_java["path"]).parent.parent)
        identity = executable_identity(gradle, ["--version"], environment=environment)
        selected_gradle_version = gradle_version(identity)
        identity["version"] = f"Gradle {selected_gradle_version}"
        build_tool.update({"state": "observed", "selected": identity})
        if (
            gradle_cmd is None
            and not os.environ.get("WORKBENCH_GRADLE")
            and isinstance(proven_gradle, str)
            and selected_gradle_version != proven_gradle
        ):
            build_tool["state"] = "ambiguous"
            findings.append(
                _finding(
                    "GRADLE_AUTOMATIC_NOT_PROVEN",
                    "blocker",
                    "Automatic Gradle selection does not match the profile-proven release",
                    f"The worldgen runner requires an intentional override when it is not Gradle {proven_gradle}.",
                    [identity["path"], str(profile_path)],
                    "Pass --gradle-cmd explicitly or install the profile-proven release in ignored tool storage.",
                )
            )
    except (OSError, ValueError, WorldgenIterationError) as exc:
        build_tool.update({"state": "unavailable", "selected": None})
        findings.append(
            _finding(
                "GRADLE_UNAVAILABLE",
                "blocker",
                "No usable Gradle executable was resolved",
                str(exc),
                [str(fixture), f"proven_gradle={proven_gradle}"],
                "Select a supported Gradle executable explicitly; Doctor will not install it.",
            )
        )

    configured_template = runtime_template
    if configured_template is None and os.environ.get("WORKBENCH_WORLDGEN_RUNTIME_TEMPLATE"):
        configured_template = Path(os.environ["WORKBENCH_WORLDGEN_RUNTIME_TEMPLATE"])
    template_path: Path | None = None
    try:
        template_path = discover_runtime_template(root, profile, configured_template)
        audit = audit_runtime_template(template_path, profile)
        result["runtime"] = {"state": "observed", **audit}
        non_duplicate_problems = [
            row
            for row in audit.get("problems", [])
            if row.get("kind") != "duplicate_mod_ids"
        ]
        if audit.get("unsafe_entries") or non_duplicate_problems:
            result["runtime"]["state"] = "bounded"
            findings.append(
                _finding(
                    "RUNTIME_TEMPLATE_UNSAFE",
                    "blocker",
                    "The selected runtime template cannot be provisioned safely",
                    "The same preflight audit is enforced by the runner before any copy.",
                    [
                        *[str(item) for item in audit.get("unsafe_entries", [])],
                        *[str(item) for item in non_duplicate_problems],
                    ],
                    "Replace or explicitly remove unsafe links/special entries from the disposable template; do not use a personal instance.",
                )
            )
        duplicates = audit.get("duplicate_mod_ids", [])
        if duplicates:
            findings.append(
                _finding(
                    "RUNTIME_DUPLICATE_MOD_IDS",
                    "blocker",
                    "The runtime template contains duplicate Forge mod IDs",
                    "Loader ownership is ambiguous before launch.",
                    [
                        f"{row['mod_id']}: {', '.join(row['paths'])}"
                        for row in duplicates
                    ],
                    "Select one exact artifact for each duplicated mod ID in the disposable template.",
                )
            )
        if audit.get("skipped_entries"):
            findings.append(
                _finding(
                    "RUNTIME_RESIDUE_WILL_BE_EXCLUDED",
                    "info",
                    "Existing worlds and run residue will be excluded from the disposable clone",
                    "This is expected and preserves the fresh-world invariant.",
                    [str(item) for item in audit["skipped_entries"][:32]],
                    "No repair is required; review the exclusions before running.",
                )
            )
    except (OSError, ValueError, WorldgenIterationError) as exc:
        message = str(exc)
        state = "ambiguous" if "found " in message and "none" not in message else "unavailable"
        result["runtime"] = {"state": state, "reason": message}
        findings.append(
            _finding(
                "RUNTIME_TEMPLATE_AMBIGUOUS" if state == "ambiguous" else "RUNTIME_TEMPLATE_UNAVAILABLE",
                "blocker",
                "The exact disposable runtime template could not be selected",
                message,
                [str(profile_path)],
                "Pass --runtime-template with one profile-matching disposable source instance.",
            )
        )

    artifact = _artifact_context(root, profile)
    strata = _strata_context(root, strata_root)
    mixin_tool = root / "profiles/platforms/cleanroom/tools/run_mixin_doctor.py"
    mixin_policy = root / "profiles/platforms/cleanroom/mixins/cleanroom-mixin-doctor-policy-v1.json"
    mixin_tool_available = mixin_tool.is_file() and not mixin_tool.is_symlink()
    mixin_policy_available = mixin_policy.is_file() and not mixin_policy.is_symlink()
    mixin_doctor_available = mixin_tool_available and mixin_policy_available
    result["integrations"] = {
        "state": "bounded",
        "items": [
            {"id": "current-worldgen-artifact", **artifact},
            {"id": "strata", **strata},
            {
                "id": "cleanroom-mixin-doctor",
                "state": "bounded" if mixin_doctor_available else "unavailable",
                "tool": str(mixin_tool),
                "policy": str(mixin_policy),
                "executed": False,
                "reason": "available as a separate exact static scan; runtime application remains unobserved",
            },
        ],
    }
    if artifact["state"] == "known-absent":
        findings.append(
            _finding(
                "WORLDGEN_ARTIFACT_NOT_BUILT",
                "info",
                "No single current remapped worldgen artifact is available yet",
                "The normal worldgen dev command builds and remaps current source before provisioning.",
                [artifact["reason"]],
                "Run the normal loop without --skip-build.",
            )
        )
    if strata["state"] == "unavailable":
        findings.append(
            _finding(
                "STRATA_UNAVAILABLE",
                "blocker",
                "The checked Strata capture integration is unavailable",
                strata["reason"],
                [strata["entrypoint"]],
                "Select a compatible local Strata checkout explicitly; Doctor will not acquire it.",
            )
        )
    if not mixin_doctor_available:
        findings.append(
            _finding(
                "MIXIN_DOCTOR_POLICY_UNAVAILABLE",
                "warning",
                "The bounded Cleanroom Mixin Doctor is unavailable",
                "Doctor will not offer a static Mixin command without its exact profile-owned tool and policy.",
                [str(mixin_tool), str(mixin_policy)],
                "Restore the regular, non-symlinked Cleanroom Mixin Doctor tool and V1 policy.",
            )
        )

    profile_args = (
        ["--profile-file", str(profile_path)]
        if profile_file is not None
        else ["--profile", str(profile_name)]
    )
    optional_args: list[str] = []
    if template_path is not None:
        optional_args.extend(["--runtime-template", str(template_path)])
    if strata.get("root"):
        optional_args.extend(["--strata-root", str(strata["root"])])
    if selected_java is not None:
        optional_args.extend(["--java-cmd", str(selected_java["path"])])
    selected_gradle = build_tool.get("selected")
    if isinstance(selected_gradle, Mapping) and selected_gradle.get("path"):
        optional_args.extend(["--gradle-cmd", str(selected_gradle["path"])])
    run_command = shlex.join(
        ["python3", "tools/workbench.py", "worldgen", "dev", *profile_args, *optional_args]
    )
    result["next_commands"].append(
        {
            "id": "worldgen-dev",
            "purpose": "Build current source, create a fresh disposable runtime/world, capture the fixed-seed sample, and inspect the checked viewer handoff.",
            "command": run_command,
            "blocked_by": [row["id"] for row in findings if row["severity"] == "blocker"],
        }
    )
    if template_path is not None:
        mod_archives = [
            row["path"]
            for row in result["runtime"].get("jar_inventory", [])
            if str(row.get("relative_path", "")).startswith("mods/")
        ]
    else:
        mod_archives = []
    if mod_archives and mixin_doctor_available:
        result["next_commands"].append(
            {
                "id": "cleanroom-mixin-doctor",
                "purpose": "Run the profile-owned bounded static Mixin policy over top-level runtime mod archives.",
                "command": shlex.join(
                    [
                        "python3",
                        str(mixin_tool),
                        "--policy",
                        str(mixin_policy),
                        "--fail-on",
                        "reject",
                        *mod_archives,
                    ]
                ),
                "blocked_by": [],
            }
        )
    return result


__all__ = ["inspect_worldgen_development_target"]
