"""Generic, bounded Groovy pack-program static analysis and comparison."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from bisect import bisect_left, bisect_right
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
from typing import Any, Iterable, Mapping, Sequence

from workbench_project_intelligence.git_observation import (
    GitObservationError,
    configured_git_executable,
    observation_environment,
    safe_git_prefix,
)

from .lexer import Argument, Call, Token, calls, linked_calls, normalize_tokens, tokenize
from .model import PROGRAM_FORMAT, PackProgramError, canonical_bytes, content_id, sha256_bytes
from .profile import (
    LoadedProfile,
    display_filesystem_path,
    native_filesystem_path,
    portable_relative_path,
    safe_regular_bytes,
    strict_json_file,
)


MAX_EFFECTS = 250_000
MAX_DEPENDENCY_EDGES = 250_000
MAX_DIFF_ROWS = 2_000
_PREPROCESSOR_RE = re.compile(
    r"^\s*//\s*(?P<key>no_run|debug_only|no_reload|mods_loaded|side|packmode)"
    r"(?:\s*:\s*(?P<value>.*?))?\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class AnalysisContext:
    side: str
    packmode: str | None = None
    debug: bool | None = None
    installed_mods: frozenset[str] | None = None


@dataclass(slots=True)
class _SourceFile:
    path: str
    absolute_path: Path
    source: str
    sha256: str
    size: int
    lines: int
    stage: str
    loader_entry: str | None
    execution_index: int | None
    preprocessors: list[dict[str, Any]]
    execution_state: str
    execution_reasons: list[str]
    tokens: tuple[Token, ...]
    calls: tuple[Call, ...]
    call_by_token: dict[int, Call] = field(init=False, repr=False)
    call_starts: tuple[int, ...] = field(init=False, repr=False)

    def __post_init__(self):
        self.call_by_token = {call.token_start: call for call in self.calls}
        self.call_starts = tuple(call.start for call in self.calls)

    def public(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "absolute_path": display_filesystem_path(self.absolute_path),
            "sha256": self.sha256,
            "size": self.size,
            "lines": self.lines,
            "stage": self.stage,
            "loader_entry": self.loader_entry,
            "execution_index": self.execution_index,
            "preprocessors": self.preprocessors,
            "execution_state": self.execution_state,
            "execution_reasons": self.execution_reasons,
        }


def analyze_program(
    source_root: Path,
    profile: LoadedProfile,
    *,
    context: AnalysisContext,
    git_binding_override: Mapping[str, Any] | None = None,
    source_bytes: Mapping[str, bytes] | None = None,
) -> dict[str, Any]:
    """Analyze one exact source tree without executing Groovy or Minecraft."""

    layout = profile.value["source_layout"]
    if source_bytes is None:
        pack_root, groovy_root = _resolve_source_root(source_root, profile)
    else:
        pack_root = source_root
        groovy_relative = portable_relative_path(layout["groovy_root"], "Groovy source root")
        groovy_root = pack_root.joinpath(*groovy_relative.parts)
        if git_binding_override is None:
            raise PackProgramError("immutable source analysis requires its observed Git binding")
        logical_names = set()
        for name, raw in source_bytes.items():
            portable_relative_path(name, "immutable Groovy source")
            if not isinstance(raw, bytes) or name.casefold() in logical_names:
                raise PackProgramError("immutable Groovy source bytes or case identity are invalid")
            logical_names.add(name.casefold())
    run_config_relative = portable_relative_path(
        layout["run_config"], "Groovy source layout run_config"
    )
    if source_bytes is None:
        run_config_path = _portable_child(groovy_root, run_config_relative, "Groovy runConfig path")
        run_config, run_config_raw = strict_json_file(run_config_path, maximum=int(layout["max_file_bytes"]))
    else:
        run_config_path = groovy_root.joinpath(*run_config_relative.parts)
        key = run_config_path.relative_to(pack_root).as_posix()
        if key not in source_bytes:
            raise PackProgramError("immutable source lacks Groovy runConfig")
        run_config_raw = source_bytes[key]
        if len(run_config_raw) > int(layout["max_file_bytes"]):
            raise PackProgramError("Groovy runConfig exceeds its byte bound")
        from .source_locations import JsonSource
        run_config = JsonSource(run_config_raw, key).value
    if not isinstance(run_config, dict):
        raise PackProgramError("Groovy runConfig root must be an object")
    run_config_warnings = _validate_run_config(run_config, profile)

    effective_debug = bool(run_config.get("debug", False)) if context.debug is None else context.debug
    effective_packmode = (
        context.packmode
        if context.packmode is not None
        else _default_packmode(run_config)
    )
    ordered, loader_warnings = _loader_inventory(
        groovy_root,
        run_config,
        profile,
        source_paths=None if source_bytes is None else tuple(
            pack_root / name for name in source_bytes
            if name.startswith(groovy_relative.as_posix() + "/") and name.lower().endswith(".groovy")
        ),
    )
    if source_bytes is None:
        all_paths, scan_warnings = _all_groovy_files(groovy_root)
    else:
        all_paths = {pack_root / name for name in source_bytes if name.startswith(groovy_relative.as_posix() + "/") and name.lower().endswith(".groovy")}
        scan_warnings = []
    configured = {path for path, _, _, _ in ordered}
    file_order = [*ordered]
    for path in sorted(all_paths - configured, key=lambda item: _logical_path(groovy_root, item)):
        file_order.append((path, "unconfigured", None, None))

    if len(file_order) > int(layout["max_files"]):
        raise PackProgramError(
            f"Groovy source contains {len(file_order)} files; profile limit is {layout['max_files']}"
        )
    files: list[_SourceFile] = []
    total_bytes = 0
    for path, stage, loader_entry, execution_index in file_order:
        raw = (safe_regular_bytes(path, maximum=int(layout["max_file_bytes"])) if source_bytes is None
               else source_bytes[path.relative_to(pack_root).as_posix()])
        if len(raw) > int(layout["max_file_bytes"]):
            raise PackProgramError("Groovy source exceeds profile file-byte limit")
        total_bytes += len(raw)
        if total_bytes > int(layout["max_total_bytes"]):
            raise PackProgramError(
                f"Groovy source exceeds profile total-byte limit {layout['max_total_bytes']}"
            )
        try:
            source = raw.decode("utf-8")
        except UnicodeError as exc:
            raise PackProgramError(f"Groovy source is not UTF-8: {path}: {exc}") from exc
        relative = _logical_path(groovy_root, path)
        preprocessors = _preprocessors(source)
        execution_state, execution_reasons = _execution_state(
            stage,
            preprocessors,
            side=context.side,
            packmode=effective_packmode,
            debug=effective_debug,
            installed_mods=context.installed_mods,
        )
        lexical = tokenize(source)
        files.append(
            _SourceFile(
                path=relative,
                absolute_path=path.resolve(),
                source=source,
                sha256=sha256_bytes(raw),
                size=len(raw),
                lines=source.count("\n") + (0 if source.endswith("\n") else 1),
                stage=stage,
                loader_entry=loader_entry,
                execution_index=execution_index,
                preprocessors=preprocessors,
                execution_state=execution_state,
                execution_reasons=execution_reasons,
                tokens=lexical,
                calls=calls(lexical),
            )
        )

    effects = _effects(files, profile)
    if len(effects) > MAX_EFFECTS:
        raise PackProgramError(
            f"Groovy static effect surface exceeds the hard limit of {MAX_EFFECTS}"
        )
    symbols, dependencies = _dependency_graph(files)
    collisions = _collisions(effects, profile)
    public_files = [source.public() for source in files]
    source_identity = sha256_bytes(
        canonical_bytes(
            [{"path": row["path"], "sha256": row["sha256"]} for row in public_files]
        )
    )
    binding = {
        "profile_id": profile.profile_id,
        "pack_profile_id": profile.pack_profile_id,
        "platform_profile_id": profile.platform_profile_id,
        "pack_root": display_filesystem_path(pack_root),
        "groovy_root": display_filesystem_path(groovy_root),
        "profile_path": display_filesystem_path(profile.path),
        "profile_sha256": profile.sha256,
        "platform_profile_path": display_filesystem_path(profile.platform_path),
        "platform_profile_sha256": profile.platform_sha256,
        "run_config_path": display_filesystem_path(run_config_path.resolve()),
        "run_config_sha256": sha256_bytes(run_config_raw),
        "source_sha256": source_identity,
        "git": (
            _git_binding(pack_root)
            if git_binding_override is None
            else _validated_git_binding(git_binding_override)
        ),
        "side": context.side,
        "physical_side": _physical_side(context.side),
        "packmode": effective_packmode,
        "debug": effective_debug,
        "installed_mods": (
            None if context.installed_mods is None else sorted(context.installed_mods)
        ),
    }
    identity_payload = {
        "profile_id": binding["profile_id"],
        "platform_profile_id": binding["platform_profile_id"],
        "source_sha256": binding["source_sha256"],
        "run_config_sha256": binding["run_config_sha256"],
        "side": binding["side"],
        "packmode": binding["packmode"],
        "debug": binding["debug"],
        "files": [
            {"path": row["path"], "sha256": row["sha256"], "stage": row["stage"]}
            for row in public_files
        ],
    }
    program_id = content_id("workbench-groovy-static-program:sha256:", identity_payload)
    stage_summary = _stage_summary(files, profile)
    summary = {
        "files": len(files),
        "configured_files": sum(row.stage != "unconfigured" for row in files),
        "enabled_files": sum(row.execution_state == "enabled" for row in files),
        "conditional_files": sum(row.execution_state == "conditional" for row in files),
        "excluded_files": sum(row.execution_state == "excluded" for row in files),
        "source_lines": sum(row.lines for row in files),
        "source_bytes": total_bytes,
        "effects": len(effects),
        "effects_by_rule": dict(sorted(Counter(row["rule_id"] for row in effects).items())),
        "effects_by_category": dict(sorted(Counter(row["category"] for row in effects).items())),
        "effects_by_kind": dict(sorted(Counter(row["kind"] for row in effects).items())),
        "literal_references": _reference_summary(effects),
        "declared_symbols": len(symbols["declarations"]),
        "dependency_edges": dependencies["summary"]["edges"],
        "dependency_cycles": dependencies["summary"]["cycles"],
        "collision_candidates": len(collisions),
    }
    limitations = list(
        dict.fromkeys(
            [
                *profile.value["limitations"],
                "Static call candidates do not prove that a Groovy branch, loop, closure, event, or registry mutation executed.",
                "Direct Java calls, dynamic dispatch, metaprogramming, generated identities, and mod-specific semantics remain unresolved unless separately observed.",
            ]
        )
    )
    return {
        "format": PROGRAM_FORMAT,
        "program_id": program_id,
        "binding": binding,
        "run_config": {
            "pack_name": run_config.get("packName"),
            "pack_id": run_config.get("packId"),
            "version": run_config.get("version"),
            "debug": effective_debug,
            "packmode": effective_packmode,
            "loaders": run_config.get("loaders", {}),
            "warnings": [*run_config_warnings, *loader_warnings, *scan_warnings],
        },
        "files": public_files,
        "stages": stage_summary,
        "symbols": symbols,
        "dependencies": dependencies,
        "effects": effects,
        "collisions": collisions,
        "summary": summary,
        "coverage": {
            "source_inventory": "complete",
            "configured_loader_order": "complete",
            "comment_aware_lexical_calls": "complete",
            "groovy_ast": "unavailable",
            "exact_compiler": "unavailable",
            "runtime_effects": "unavailable",
            "loop_expansion": "unavailable",
            "dynamic_java_and_metaclass": "candidate-only",
        },
        "limitations": limitations,
    }


def compare_programs(
    baseline: Mapping[str, Any], candidate: Mapping[str, Any]
) -> dict[str, Any]:
    baseline_files = {row["path"]: row for row in baseline["files"]}
    candidate_files = {row["path"]: row for row in candidate["files"]}
    added_files = sorted(set(candidate_files) - set(baseline_files))
    removed_files = sorted(set(baseline_files) - set(candidate_files))
    modified_files = sorted(
        path
        for path in set(baseline_files) & set(candidate_files)
        if baseline_files[path]["sha256"] != candidate_files[path]["sha256"]
        or baseline_files[path]["stage"] != candidate_files[path]["stage"]
    )
    if (
        baseline["binding"]["run_config_sha256"]
        != candidate["binding"]["run_config_sha256"]
    ):
        modified_files.append("runConfig.json")
        modified_files = sorted(set(modified_files))
    baseline_counter = Counter(row["semantic_key"] for row in baseline["effects"])
    candidate_counter = Counter(row["semantic_key"] for row in candidate["effects"])
    baseline_examples = _effect_examples(baseline["effects"])
    candidate_examples = _effect_examples(candidate["effects"])
    additions = candidate_counter - baseline_counter
    removals = baseline_counter - candidate_counter
    added_rows, added_truncated = _diff_rows(additions, candidate_examples)
    removed_rows, removed_truncated = _diff_rows(removals, baseline_examples)
    changed_files = [*added_files, *modified_files, *removed_files]
    state = "identical" if not changed_files else "changed"
    return {
        "state": state,
        "baseline_program_id": baseline["program_id"],
        "candidate_program_id": candidate["program_id"],
        "files": {
            "added": added_files,
            "modified": modified_files,
            "removed": removed_files,
            "changed": len(changed_files),
        },
        "effects": {
            "added": sum(additions.values()),
            "removed": sum(removals.values()),
            "added_rows": added_rows,
            "removed_rows": removed_rows,
            "truncated": added_truncated or removed_truncated,
        },
        "summary_delta": {
            "files": candidate["summary"]["files"] - baseline["summary"]["files"],
            "source_lines": candidate["summary"]["source_lines"] - baseline["summary"]["source_lines"],
            "effects": candidate["summary"]["effects"] - baseline["summary"]["effects"],
            "collision_candidates": len(candidate["collisions"]) - len(baseline["collisions"]),
        },
        "limitations": [
            "Effect comparison is a static multiset diff; it does not expand loops or prove effective registry state.",
            "Moving a script can change order even when its normalized calls remain semantically equal; file movement remains visible separately.",
        ],
    }


def assess_change(
    candidate: Mapping[str, Any],
    profile: LoadedProfile,
    *,
    baseline: Mapping[str, Any] | None,
    comparison: Mapping[str, Any],
    changed_paths: Sequence[str] | None,
) -> dict[str, Any]:
    files = {row["path"]: row for row in candidate["files"]}
    baseline_files = {} if baseline is None else {row["path"]: row for row in baseline["files"]}
    if changed_paths is None:
        if baseline is None:
            return {
                "state": "not-evaluated",
                "recommendation": "Select --baseline or --changed to assess reload and save boundaries.",
                "changed_files": [],
                "affected_stages": [],
                "affected_effects": 0,
                "reload_checks": [],
                "save_risks": [],
                "limitations": ["No change scope was supplied."],
            }
        paths = sorted(
            {
                *comparison["files"]["added"],
                *comparison["files"]["modified"],
                *comparison["files"]["removed"],
            }
        )
    else:
        paths = sorted(dict.fromkeys(_normalize_changed_path(path) for path in changed_paths))
        unknown = [path for path in paths if path not in files and path not in baseline_files and path != "runConfig.json"]
        if unknown:
            raise PackProgramError(
                "changed Groovy paths are not present in candidate or baseline: " + ", ".join(unknown)
            )
    if not paths:
        return {
            "state": "no-action",
            "recommendation": "Candidate and baseline source bindings are identical.",
            "changed_files": [],
            "affected_stages": [],
            "affected_effects": 0,
            "reload_checks": [],
            "save_risks": [],
            "limitations": [],
        }
    candidate_selected = [
        row for row in candidate["effects"] if row["source"]["path"] in paths
    ]
    if baseline is None:
        selected_effects = candidate_selected
    else:
        baseline_selected = [
            row for row in baseline["effects"] if row["source"]["path"] in paths
        ]
        candidate_counts = Counter(row["semantic_key"] for row in candidate_selected)
        baseline_counts = Counter(row["semantic_key"] for row in baseline_selected)
        changed_keys = {
            key
            for key in set(candidate_counts) | set(baseline_counts)
            if candidate_counts[key] != baseline_counts[key]
        }
        selected_effects = [
            row
            for row in [*candidate_selected, *baseline_selected]
            if row["semantic_key"] in changed_keys
        ]
    stages = sorted(
        {
            (files.get(path) or baseline_files.get(path) or {"stage": "run-config"})["stage"]
            for path in paths
        },
        key=lambda stage: _stage_order(profile, stage),
    )
    restart_stages = {
        stage
        for stage in stages
        if stage == "run-config"
        or stage == "unconfigured"
        or profile.platform["load_stages"].get(stage, {}).get("reload") != "reload-candidate"
    }
    unresolved_reload = [row for row in selected_effects if row["reload"]["state"] == "unresolved"]
    excluded_only = all(
        (files.get(path) or baseline_files.get(path) or {}).get("execution_state") == "excluded"
        for path in paths
        if path != "runConfig.json"
    )
    save_risks: list[dict[str, Any]] = []
    policies = {row["policy_id"]: row for row in profile.value["identity_policies"]}
    candidate_selected_ids = {row["effect_id"] for row in candidate_selected}
    selected_candidate_effect_ids = {
        row["effect_id"] for row in selected_effects if row["effect_id"] in candidate_selected_ids
    }
    for collision in candidate["collisions"]:
        if any(effect_id in selected_candidate_effect_ids for effect_id in collision["effect_ids"]):
            policy = policies[collision["policy_id"]]
            if policy["save_risk"] != "none":
                save_risks.append(
                    {
                        "policy_id": policy["policy_id"],
                        "risk": policy["save_risk"],
                        "identity_kind": policy["identity_kind"],
                        "value": collision["value"],
                    }
                )
    identity_policy_by_rule = defaultdict(list)
    for policy in profile.value["identity_policies"]:
        identity_policy_by_rule[policy["rule_id"]].append(policy)
    for effect in selected_effects:
        for policy in identity_policy_by_rule.get(effect["rule_id"], []):
            if policy["save_risk"] == "none" or policy["field"] not in effect["fields"]:
                continue
            save_risks.append(
                {
                    "policy_id": policy["policy_id"],
                    "risk": policy["save_risk"],
                    "identity_kind": policy["identity_kind"],
                    "value": effect["fields"][policy["field"]],
                }
            )
    save_risks = list({canonical_bytes(row): row for row in save_risks}.values())
    save_risks.sort(key=canonical_bytes)

    if excluded_only:
        state = "not-executed"
        recommendation = "The selected files are excluded in this exact side/debug/packmode context."
        checks: list[str] = []
    elif restart_stages:
        state = "restart-required"
        recommendation = (
            "Use a fresh disposable runtime; the change touches non-reloadable or loader-configuration state."
        )
        checks = ["cold-start", "registry-snapshot"]
    elif unresolved_reload:
        state = "restart-recommended"
        recommendation = (
            "Use a fresh disposable runtime until the dynamic/direct mutation has an admitted reload adapter and idempotence fixture."
        )
        checks = ["cold-start", "reload-once", "reload-twice", "effective-state-diff"]
    else:
        state = "reload-candidate"
        recommendation = (
            "Exercise the exact postInit reload path, then compare cold-start, first-reload, and second-reload effective state."
        )
        checks = ["cold-start", "reload-once", "reload-twice", "effective-state-diff"]
    if save_risks:
        checks.append("save-compatibility-review")
    return {
        "state": state,
        "recommendation": recommendation,
        "changed_files": paths,
        "affected_stages": stages,
        "affected_effects": len(selected_effects),
        "reload_checks": list(dict.fromkeys(checks)),
        "save_risks": save_risks,
        "limitations": [
            "This is a profile-classified static recommendation; it does not invoke GroovyScript reload or inspect effective registries.",
            *(
                ["At least one affected direct or dynamic operation has unresolved reload semantics."]
                if unresolved_reload
                else []
            ),
        ],
    }


def _resolve_source_root(source_root: Path, profile: LoadedProfile) -> tuple[Path, Path]:
    requested = native_filesystem_path(source_root.expanduser())
    try:
        before = requested.lstat()
    except OSError as exc:
        raise PackProgramError(f"cannot inspect Groovy source root {requested}: {exc}") from exc
    if (
        stat.S_ISLNK(before.st_mode)
        or _is_junction(requested, "Groovy source root")
        or not stat.S_ISDIR(before.st_mode)
    ):
        raise PackProgramError(f"Groovy source root is not a non-symlink directory: {requested}")
    resolved = requested.resolve()
    layout = profile.value["source_layout"]
    run_config_relative = portable_relative_path(
        layout["run_config"], "Groovy source layout run_config"
    )
    direct_config = _portable_child(
        resolved, run_config_relative, "Groovy runConfig path"
    )
    if direct_config.is_file():
        return resolved.parent, resolved
    groovy_relative = portable_relative_path(
        layout["groovy_root"], "Groovy source layout groovy_root"
    )
    groovy_root = _portable_child(
        resolved, groovy_relative, "configured Groovy root"
    )
    if (
        not groovy_root.is_dir()
        or groovy_root.is_symlink()
        or _is_junction(groovy_root, "configured Groovy root")
    ):
        raise PackProgramError(
            f"no Groovy root found at {groovy_root}; pass the pack root or its Groovy directory"
        )
    return resolved, groovy_root.resolve()


def _is_junction(path: Path, context: str) -> bool:
    predicate = getattr(path, "is_junction", None)
    if predicate is None:
        return False
    try:
        return bool(predicate())
    except OSError as exc:
        raise PackProgramError(f"cannot inspect {context} {path}: {exc}") from exc


def _portable_child(root: Path, relative: PurePosixPath, context: str) -> Path:
    """Join a validated logical path without permitting filesystem aliases."""

    requested = root.joinpath(*relative.parts)
    cursor = root
    for part in relative.parts:
        parent = cursor
        cursor = parent / part
        try:
            inspected = cursor.lstat()
        except FileNotFoundError:
            break
        except OSError as exc:
            raise PackProgramError(f"cannot inspect {context} {cursor}: {exc}") from exc
        try:
            directory_entries = os.listdir(parent)
        except OSError as exc:
            raise PackProgramError(f"cannot inspect {context} parent {parent}: {exc}") from exc
        if part not in directory_entries:
            raise PackProgramError(
                f"{context} does not use the source tree's exact path spelling: {cursor}"
            )
        if stat.S_ISLNK(inspected.st_mode) or _is_junction(cursor, context):
            raise PackProgramError(f"{context} traverses a filesystem link: {cursor}")
    return requested


def _logical_path(root: Path, path: Path) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise PackProgramError(f"Groovy source path escapes its root: {path}") from exc
    return portable_relative_path(
        relative.as_posix(), f"Groovy source path {relative.as_posix()!r}"
    ).as_posix()


def _validate_run_config(run_config: Mapping[str, Any], profile: LoadedProfile) -> list[str]:
    warnings: list[str] = []
    loaders = run_config.get("loaders")
    if not isinstance(loaders, dict):
        raise PackProgramError("Groovy runConfig loaders must be an object")
    unknown = sorted(set(loaders) - set(profile.platform["load_stages"]))
    if unknown:
        raise PackProgramError("Groovy runConfig uses unsupported loaders: " + ", ".join(unknown))
    expected = profile.value["expected_run_config"]
    for actual_key, expected_key in (("packName", "pack_name"), ("packId", "pack_id")):
        expected_value = expected[expected_key]
        if run_config.get(actual_key) != expected_value:
            warnings.append(
                f"runConfig {actual_key} is {run_config.get(actual_key)!r}; profile expects {expected_value!r}"
            )
    if "classes" in run_config:
        warnings.append("legacy runConfig classes entry is present; current GroovyScript expects loader paths")
    return warnings


def _default_packmode(run_config: Mapping[str, Any]) -> str | None:
    packmode = run_config.get("packmode")
    if isinstance(packmode, dict) and isinstance(packmode.get("default"), str):
        return packmode["default"]
    return None


def _loader_inventory(
    groovy_root: Path,
    run_config: Mapping[str, Any],
    profile: LoadedProfile,
    *, source_paths: tuple[Path, ...] | None = None,
) -> tuple[list[tuple[Path, str, str, int]], list[str]]:
    result: list[tuple[Path, str, str, int]] = []
    warnings: list[str] = []
    claimed_stage: dict[Path, str] = {}
    execution_index = 0
    stages = sorted(
        profile.platform["load_stages"],
        key=lambda value: profile.platform["load_stages"][value]["order"],
    )
    loaders = run_config["loaders"]
    for stage in stages:
        entries = loaders.get(stage, [])
        if not isinstance(entries, list) or any(not isinstance(item, str) or not item for item in entries):
            raise PackProgramError(f"Groovy runConfig loader {stage} must be a list of paths")
        for entry in entries:
            relative = portable_relative_path(
                entry,
                f"Groovy loader path {entry!r}",
                allow_directory_marker=True,
            )
            normalized_entry = relative.as_posix()
            requested = (groovy_root.joinpath(*relative.parts) if source_paths is not None
                         else _portable_child(groovy_root, relative, "Groovy loader path"))
            matches: list[Path]
            if source_paths is not None:
                matches = [path for path in source_paths if path == requested or requested in path.parents]
                if not matches:
                    warnings.append(f"configured Groovy loader path has no source files: {normalized_entry}")
            elif requested.is_dir() and not requested.is_symlink():
                matches, ignored = _walk_groovy(requested, logical_root=groovy_root)
                warnings.extend(
                    f"ignored symlink in loader {normalized_entry}: {path}"
                    for path in ignored
                )
            elif requested.is_file() and requested.suffix.lower() == ".groovy" and not requested.is_symlink():
                matches = [requested]
            elif requested.exists():
                raise PackProgramError(f"Groovy loader path is not a regular file/directory: {entry}")
            else:
                warnings.append(
                    f"configured Groovy loader path does not exist: {normalized_entry}"
                )
                matches = []
            for path in sorted(matches, key=lambda item: _logical_path(groovy_root, item)):
                previous = claimed_stage.get(path)
                if previous is not None:
                    if previous != stage:
                        raise PackProgramError(
                            f"Groovy file is configured in multiple stages: {_logical_path(groovy_root, path)} ({previous}, {stage})"
                        )
                    continue
                claimed_stage[path] = stage
                result.append((path, stage, normalized_entry, execution_index))
                execution_index += 1
    return result, warnings


def _all_groovy_files(root: Path) -> tuple[set[Path], list[str]]:
    files, ignored = _walk_groovy(root)
    return set(files), [f"ignored source symlink: {path}" for path in ignored]


def _walk_groovy(
    root: Path, *, logical_root: Path | None = None
) -> tuple[list[Path], list[str]]:
    result: list[Path] = []
    ignored: list[str] = []
    source_root = root if logical_root is None else logical_root

    def fail_walk(error: OSError) -> None:
        failed_path = error.filename or os.fspath(root)
        raise PackProgramError(
            f"cannot traverse Groovy source path {failed_path}: {error}"
        ) from error

    for directory, directories, files in os.walk(
        root, followlinks=False, onerror=fail_walk
    ):
        base = Path(directory)
        # Git metadata is outside the analyzed source program. Prune its exact
        # directory or worktree-file spelling before portable source admission;
        # case variants and any explicit profile/loader selection still fail.
        directories[:] = [name for name in directories if name != ".git"]
        files = [name for name in files if name != ".git"]
        names: dict[str, str] = {}
        for name in sorted([*directories, *files]):
            path = base / name
            logical = _logical_path(source_root, path)
            collision_key = name.casefold()
            previous = names.get(collision_key)
            if previous is not None and previous != name:
                raise PackProgramError(
                    "Groovy source paths collide by case: "
                    f"{_logical_path(source_root, base / previous)} and {logical}"
                )
            names[collision_key] = name
        retained_directories: list[str] = []
        for name in sorted(directories):
            path = base / name
            try:
                mode = path.lstat().st_mode
            except OSError as exc:
                raise PackProgramError(f"cannot inspect Groovy source path {path}: {exc}") from exc
            if stat.S_ISLNK(mode):
                ignored.append(_logical_path(source_root, path))
                continue
            if _is_junction(path, "Groovy source directory"):
                raise PackProgramError(f"Groovy source directory is a junction: {path}")
            if not stat.S_ISDIR(mode):
                raise PackProgramError(f"Groovy source directory changed while scanning: {path}")
            retained_directories.append(name)
        directories[:] = retained_directories
        for name in sorted(files):
            path = base / name
            try:
                mode = path.lstat().st_mode
            except OSError as exc:
                raise PackProgramError(f"cannot inspect Groovy source path {path}: {exc}") from exc
            if stat.S_ISLNK(mode):
                ignored.append(_logical_path(source_root, path))
            elif _is_junction(path, "Groovy source file"):
                raise PackProgramError(f"Groovy source path is a junction: {path}")
            elif stat.S_ISREG(mode) and path.suffix.lower() == ".groovy":
                result.append(path)
    return result, ignored


def _preprocessors(source: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for line_number, line in enumerate(source.splitlines(), 1):
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("//"):
            break
        match = _PREPROCESSOR_RE.match(line)
        if not match:
            continue
        key = match.group("key").casefold()
        raw_value = match.group("value")
        values = (
            []
            if raw_value is None
            else [part.strip().casefold() for part in raw_value.split(",") if part.strip()]
        )
        result.append({"kind": key, "values": values, "line": line_number})
    return result


def _execution_state(
    stage: str,
    preprocessors: Sequence[Mapping[str, Any]],
    *,
    side: str,
    packmode: str | None,
    debug: bool,
    installed_mods: frozenset[str] | None,
) -> tuple[str, list[str]]:
    if stage == "unconfigured":
        return "excluded", ["file is not selected by any runConfig loader"]
    state = "enabled"
    reasons: list[str] = []
    physical_side = _physical_side(side)
    for preprocessor in preprocessors:
        kind = preprocessor["kind"]
        values = list(preprocessor["values"])
        if kind == "no_run":
            return "excluded", ["no_run preprocessor"]
        if kind == "debug_only" and not debug:
            return "excluded", ["debug_only preprocessor with debug disabled"]
        if kind == "side" and values and physical_side not in values:
            return "excluded", [f"side preprocessor requires {', '.join(values)}"]
        if kind == "packmode" and values:
            if packmode is None:
                state = "conditional"
                reasons.append("packmode is unresolved")
            elif packmode.casefold() not in values:
                return "excluded", [f"packmode preprocessor excludes {packmode}"]
        if kind == "mods_loaded" and values:
            if installed_mods is None:
                state = "conditional"
                reasons.append("installed mod set is unresolved")
            else:
                missing = sorted(set(values) - installed_mods)
                if missing:
                    return "excluded", ["missing required mods: " + ", ".join(missing)]
    return state, reasons


def _physical_side(side: str) -> str:
    return "server" if side == "dedicated-server" else "client"


def _effects(files: Sequence[_SourceFile], profile: LoadedProfile) -> list[dict[str, Any]]:
    rules = profile.value["rules"]
    effects: list[dict[str, Any]] = []
    for source in files:
        for rule in rules:
            match = rule["match"]
            path_regex = match.get("path_regex")
            if path_regex and re.search(path_regex, source.path) is None:
                continue
            if "token" in match:
                for token in source.tokens:
                    if token.value == match["token"]:
                        effects.append(_token_effect(source, token, rule, profile))
                continue
            for call in source.calls:
                if _matches_call(call, match):
                    effects.append(_call_effect(source, call, rule, profile))
    effects.sort(
        key=lambda row: (
            row["source"]["path"],
            row["source"]["offset"],
            row["rule_id"],
            row["effect_id"],
        )
    )
    return effects


def _matches_call(call: Call, match: Mapping[str, Any]) -> bool:
    if "callee" in match and call.callee != match["callee"]:
        return False
    if "callee_prefix" in match and not call.callee.startswith(match["callee_prefix"]):
        return False
    excluded = match.get("exclude_callee_prefix", [])
    if isinstance(excluded, str):
        excluded = [excluded]
    if any(call.callee.startswith(prefix) for prefix in excluded):
        return False
    if "terminal" in match and call.terminal != match["terminal"]:
        return False
    if "terminal_regex" in match and re.fullmatch(match["terminal_regex"], call.terminal) is None:
        return False
    if "constructor" in match and call.constructor is not bool(match["constructor"]):
        return False
    return True


def _call_effect(
    source: _SourceFile,
    call: Call,
    rule: Mapping[str, Any],
    profile: LoadedProfile,
) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    field_states: dict[str, str] = {}
    for capture in rule["captures"]:
        field = capture["field"]
        mode = capture["mode"]
        value: Any = None
        if mode == "qualifier":
            value = call.qualifier
        elif mode == "terminal":
            value = call.terminal
        else:
            index = capture["argument"]
            argument = call.arguments[index] if index < len(call.arguments) else None
            if argument is not None:
                value = _capture_argument(argument, mode)
        if value is None:
            field_states[field] = "unresolved"
        else:
            fields[field] = value
            field_states[field] = "literal" if mode in {"integer", "string", "first-string", "wrapped-string"} else "static-expression"
    recipe = None
    if rule["kind"] == "machine-recipe" and call.terminal == "recipeBuilder":
        recipe = _recipe_chain(source, call, profile)
        if recipe.get("recipe_map") and "recipe_map" not in fields:
            fields["recipe_map"] = recipe["recipe_map"]
            field_states["recipe_map"] = "static-expression"
    expression = _call_expression(call)
    semantic_payload = {
        "rule_id": rule["rule_id"],
        "stage": source.stage,
        "callee": call.callee,
        "fields": fields,
        "expression": expression,
        "recipe": (
            None
            if recipe is None
            else {
                key: value
                for key, value in recipe.items()
                if key not in {"line_start", "line_end", "end_offset"}
            }
        ),
    }
    recipe_end = recipe.pop("end_offset") if recipe else call.end
    return _effect_record(
        source,
        rule,
        start=call.start,
        end=recipe_end,
        line=call.line,
        column=call.column,
        fields=fields,
        field_states=field_states,
        expression=expression,
        semantic_payload=semantic_payload,
        profile=profile,
        recipe=recipe,
    )


def _token_effect(
    source: _SourceFile,
    token: Token,
    rule: Mapping[str, Any],
    profile: LoadedProfile,
) -> dict[str, Any]:
    semantic_payload = {
        "rule_id": rule["rule_id"],
        "stage": source.stage,
        "token": token.value,
    }
    return _effect_record(
        source,
        rule,
        start=token.start,
        end=token.end,
        line=token.line,
        column=token.column,
        fields={},
        field_states={},
        expression=token.value,
        semantic_payload=semantic_payload,
        profile=profile,
        recipe=None,
    )


def _effect_record(
    source: _SourceFile,
    rule: Mapping[str, Any],
    *,
    start: int,
    end: int,
    line: int,
    column: int,
    fields: Mapping[str, Any],
    field_states: Mapping[str, str],
    expression: str,
    semantic_payload: Mapping[str, Any],
    profile: LoadedProfile,
    recipe: Mapping[str, Any] | None,
) -> dict[str, Any]:
    identity = {
        field: fields[field]
        for field in rule["identity_fields"]
        if field in fields
    }
    reload_state, reload_reason = _reload_state(source, rule, profile)
    source_pointer = {
        "path": source.path,
        "absolute_path": str(source.absolute_path),
        "sha256": source.sha256,
        "line": line,
        "column": column,
        "offset": start,
        "end_offset": end,
        "snippet": _snippet(source.source[start:end]),
    }
    effect_id = content_id(
        "workbench-groovy-static-effect:sha256:",
        {
            "source_sha256": source.sha256,
            "offset": start,
            "rule_id": rule["rule_id"],
            "fields": fields,
        },
    )
    semantic_key = content_id(
        "workbench-groovy-semantic-effect:sha256:", semantic_payload
    )
    limitations = ["This is a profile-classified static candidate, not an observed runtime effect."]
    if any(state == "unresolved" for state in field_states.values()):
        limitations.append("At least one configured identity field is not a bounded literal.")
    if rule["reload"] == "unknown":
        limitations.append("The profile does not claim transactional reload support for this operation.")
    return {
        "effect_id": effect_id,
        "semantic_key": semantic_key,
        "evidence_state": "static-candidate",
        "rule_id": rule["rule_id"],
        "category": rule["category"],
        "kind": rule["kind"],
        "operation": rule["operation"],
        "description": rule["description"],
        "identity": identity,
        "fields": dict(fields),
        "field_states": dict(field_states),
        "expression": expression,
        "recipe": None if recipe is None else dict(recipe),
        "source": source_pointer,
        "lifecycle": {
            "stage": source.stage,
            "execution_index": source.execution_index,
            "execution_state": source.execution_state,
            "conditions": source.preprocessors,
        },
        "reload": {"state": reload_state, "reason": reload_reason},
        "limitations": limitations,
    }


def _capture_argument(argument: Argument, mode: str) -> Any:
    if mode == "integer":
        return argument.integer()
    if mode == "string":
        return argument.exact_string()
    if mode == "first-string":
        return argument.first_string()
    if mode == "wrapped-string":
        return argument.wrapped_string()
    if mode == "expression":
        return argument.expression or None
    raise PackProgramError(f"unsupported Groovy capture mode {mode}")


def _call_expression(call: Call) -> str:
    suffix = " {" if call.closure_form else "(" + ", ".join(arg.expression for arg in call.arguments) + ")"
    return ("new " if call.constructor else "") + call.callee + suffix


def _recipe_chain(
    source: _SourceFile,
    builder: Call,
    profile: LoadedProfile,
) -> dict[str, Any]:
    linked = linked_calls(source.tokens, source.call_by_token, builder)
    terminator = linked[-1] if linked[-1].terminal == "buildAndRegister" else None
    chain_end = linked[-1].end
    chain_calls = source.calls[bisect_left(source.call_starts, builder.start):bisect_right(source.call_starts, chain_end)]
    reference_rules = [rule for rule in profile.value["rules"] if rule["category"] == "reference"]
    references: list[dict[str, Any]] = []
    for call in chain_calls:
        for rule in reference_rules:
            if _matches_call(call, rule["match"]):
                fields: dict[str, Any] = {}
                for capture in rule["captures"]:
                    if capture["mode"] in {"qualifier", "terminal"}:
                        continue
                    index = capture["argument"]
                    if index < len(call.arguments):
                        value = _capture_argument(call.arguments[index], capture["mode"])
                        if value is not None:
                            fields[capture["field"]] = value
                references.append(
                    {"kind": rule["kind"], "rule_id": rule["rule_id"], "fields": fields}
                )
    properties: dict[str, list[str]] = defaultdict(list)
    for call in linked:
        if call.terminal in {
            "inputs",
            "notConsumable",
            "outputs",
            "chancedOutput",
            "fluidInputs",
            "fluidOutputs",
            "duration",
            "EUt",
            "circuitMeta",
            "property",
        }:
            properties[call.terminal].append(
                ", ".join(argument.expression for argument in call.arguments)
            )
    normalized = [
        {"callee": call.callee, "arguments": [argument.expression for argument in call.arguments]}
        for call in chain_calls
    ]
    return {
        "recipe_map": builder.qualifier,
        "complete": terminator is not None,
        "terminator": None if terminator is None else terminator.terminal,
        "line_start": builder.line,
        "line_end": builder.line if terminator is None else terminator.line,
        "end_offset": chain_end,
        "properties": dict(sorted(properties.items())),
        "references": references,
        "chain_sha256": hashlib.sha256(canonical_bytes(normalized)).hexdigest(),
    }


def _reload_state(
    source: _SourceFile,
    rule: Mapping[str, Any],
    profile: LoadedProfile,
) -> tuple[str, str]:
    if source.stage == "unconfigured" or source.execution_state == "excluded":
        return "not-executed", "file is not active in this exact analysis context"
    if any(row["kind"] == "no_reload" for row in source.preprocessors):
        return "restart-required", "no_reload preprocessor"
    if rule["reload"] == "restart-required":
        return "restart-required", "pack profile marks this operation non-reloadable"
    if rule["reload"] == "unknown":
        return "unresolved", "pack profile has no admitted transactional reload behavior"
    stage = profile.platform["load_stages"].get(source.stage)
    if stage is None or stage["reload"] != "reload-candidate":
        return "restart-required", f"GroovyScript stage {source.stage} is not reloadable"
    return "reload-candidate", f"GroovyScript stage {source.stage} is reloadable; effect idempotence remains unobserved"


def _snippet(value: str, maximum: int = 240) -> str:
    rendered = " ".join(value.replace("\x00", "\\x00").split())
    return rendered[:maximum]


def _stage_summary(files: Sequence[_SourceFile], profile: LoadedProfile) -> list[dict[str, Any]]:
    stages = sorted(
        profile.platform["load_stages"],
        key=lambda value: profile.platform["load_stages"][value]["order"],
    )
    if any(row.stage == "unconfigured" for row in files):
        stages.append("unconfigured")
    result = []
    for stage in stages:
        rows = [row for row in files if row.stage == stage]
        policy = profile.platform["load_stages"].get(stage)
        result.append(
            {
                "stage": stage,
                "order": None if policy is None else policy["order"],
                "reload": "not-executed" if policy is None else policy["reload"],
                "files": len(rows),
                "enabled": sum(row.execution_state == "enabled" for row in rows),
                "conditional": sum(row.execution_state == "conditional" for row in rows),
                "excluded": sum(row.execution_state == "excluded" for row in rows),
                "first_execution_index": min(
                    (row.execution_index for row in rows if row.execution_index is not None),
                    default=None,
                ),
                "last_execution_index": max(
                    (row.execution_index for row in rows if row.execution_index is not None),
                    default=None,
                ),
            }
        )
    return result


def _reference_summary(effects: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_kind: dict[str, list[Any]] = defaultdict(list)
    for effect in effects:
        if effect["category"] != "reference":
            continue
        for field, value in effect["identity"].items():
            by_kind[effect["kind"]].append(value)
            break
    return {
        kind: {"occurrences": len(values), "unique_literals": len({json.dumps(value, sort_keys=True) for value in values})}
        for kind, values in sorted(by_kind.items())
    }


def _declarations(source: _SourceFile) -> tuple[str | None, list[dict[str, Any]], list[dict[str, Any]]]:
    package: str | None = None
    imports: list[dict[str, Any]] = []
    declarations: list[dict[str, Any]] = []
    tokens = source.tokens
    for index, token in enumerate(tokens):
        if token.value == "package":
            package = _qualified_line(tokens, index + 1, token.line).rstrip(".*") or None
        elif token.value == "import":
            cursor = index + 1
            static = cursor < len(tokens) and tokens[cursor].value == "static"
            if static:
                cursor += 1
            qualified = _qualified_line(tokens, cursor, token.line)
            if qualified:
                imports.append({"name": qualified, "static": static, "line": token.line})
        elif token.value in {"class", "interface", "trait", "enum"}:
            if index + 1 < len(tokens) and tokens[index + 1].kind == "identifier":
                name = tokens[index + 1].value
                qualified = f"{package}.{name}" if package else _path_qualified(source.path, name)
                declarations.append(
                    {
                        "kind": token.value,
                        "name": name,
                        "qualified_name": qualified,
                        "path": source.path,
                        "line": token.line,
                    }
                )
    return package, imports, declarations


def _qualified_line(tokens: tuple[Token, ...], start: int, line: int) -> str:
    parts: list[str] = []
    for token in tokens[start:]:
        if token.line != line or token.value == ";":
            break
        if token.kind == "identifier" or token.value in {".", "*"}:
            parts.append(token.value)
        else:
            break
    return "".join(parts)


def _path_qualified(path: str, name: str) -> str:
    parent = PurePosixPath(path).parent.as_posix()
    return name if parent == "." else parent.replace("/", ".") + "." + name


def _dependency_graph(files: Sequence[_SourceFile]) -> tuple[dict[str, Any], dict[str, Any]]:
    metadata: dict[str, tuple[str | None, list[dict[str, Any]], list[dict[str, Any]]]] = {
        source.path: _declarations(source) for source in files
    }
    declarations = [row for _, _, rows in metadata.values() for row in rows]
    by_qualified = {row["qualified_name"]: row for row in declarations}
    by_short: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in declarations:
        by_short[row["name"]].append(row)
    edge_basis: dict[tuple[str, str], set[str]] = defaultdict(set)
    external_imports: Counter[str] = Counter()
    source_by_path = {source.path: source for source in files}
    for path, (_, imports, _) in metadata.items():
        for imported in imports:
            target = _resolve_import(imported["name"], by_qualified)
            if target is not None and target["path"] != path:
                edge_basis[(path, target["path"])].add("static-import" if imported["static"] else "import")
            elif target is None:
                root = imported["name"].split(".", 1)[0]
                external_imports[root] += 1
        own_names = {row["name"] for row in metadata[path][2]}
        for token in source_by_path[path].tokens:
            if token.kind != "identifier" or token.value in own_names:
                continue
            candidates = by_short.get(token.value, [])
            if len(candidates) == 1 and candidates[0]["path"] != path:
                edge_basis[(path, candidates[0]["path"])].add("symbol-reference")
    if len(edge_basis) > MAX_DEPENDENCY_EDGES:
        raise PackProgramError(f"Groovy dependency graph exceeds {MAX_DEPENDENCY_EDGES} edges")
    edges = [
        {"source": source, "target": target, "basis": sorted(basis)}
        for (source, target), basis in sorted(edge_basis.items())
    ]
    components = _strong_components([source.path for source in files], edges)
    cycles = [component for component in components if len(component) > 1]
    inbound = Counter(edge["target"] for edge in edges)
    hubs = [
        {"path": path, "dependents": count}
        for path, count in sorted(inbound.items(), key=lambda item: (-item[1], item[0]))[:25]
    ]
    symbols = {
        "declarations": sorted(declarations, key=lambda row: (row["qualified_name"], row["path"], row["line"])),
        "imports": [
            {"path": path, **row}
            for path, (_, imports, _) in sorted(metadata.items())
            for row in imports
        ],
        "external_import_roots": [
            {"root": root, "occurrences": count}
            for root, count in sorted(external_imports.items(), key=lambda item: (-item[1], item[0]))
        ],
    }
    dependencies = {
        "edges": edges,
        "cycles": cycles,
        "hubs": hubs,
        "summary": {
            "nodes": len(files),
            "edges": len(edges),
            "cycles": len(cycles),
            "files_with_dependencies": len({edge["source"] for edge in edges}),
        },
        "limitations": [
            "Dependencies are derived from imports and unambiguous local type-name references; dynamic loading and generated symbols are not resolved."
        ],
    }
    return symbols, dependencies


def _resolve_import(name: str, by_qualified: Mapping[str, Mapping[str, Any]]) -> Mapping[str, Any] | None:
    candidate = name.rstrip(".*")
    parts = candidate.split(".")
    for end in range(len(parts), 0, -1):
        qualified = ".".join(parts[:end])
        if qualified in by_qualified:
            return by_qualified[qualified]
    return None


def _strong_components(nodes: Sequence[str], edges: Sequence[Mapping[str, Any]]) -> list[list[str]]:
    adjacency: dict[str, list[str]] = {node: [] for node in nodes}
    for edge in edges:
        adjacency.setdefault(edge["source"], []).append(edge["target"])
    index = 0
    indices: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    result: list[list[str]] = []

    def visit(node: str) -> None:
        nonlocal index
        indices[node] = index
        low[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for target in sorted(set(adjacency.get(node, []))):
            if target not in indices:
                visit(target)
                low[node] = min(low[node], low[target])
            elif target in on_stack:
                low[node] = min(low[node], indices[target])
        if low[node] != indices[node]:
            return
        component: list[str] = []
        while stack:
            value = stack.pop()
            on_stack.remove(value)
            component.append(value)
            if value == node:
                break
        result.append(sorted(component))

    for node in sorted(nodes):
        if node not in indices:
            visit(node)
    return sorted(result, key=lambda component: (-len(component), component))


def _collisions(effects: Sequence[Mapping[str, Any]], profile: LoadedProfile) -> list[dict[str, Any]]:
    by_rule: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for effect in effects:
        if effect["lifecycle"]["execution_state"] != "excluded":
            by_rule[effect["rule_id"]].append(effect)
    result: list[dict[str, Any]] = []
    for policy in profile.value["identity_policies"]:
        grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        field = policy["field"]
        for effect in by_rule.get(policy["rule_id"], []):
            if field not in effect["fields"]:
                continue
            key = json.dumps(effect["fields"][field], ensure_ascii=False, sort_keys=True)
            grouped[key].append(effect)
        for key, occurrences in sorted(grouped.items()):
            if len(occurrences) < 2:
                continue
            value = json.loads(key)
            occurrence_rows = [
                {
                    "effect_id": row["effect_id"],
                    "execution_state": row["lifecycle"]["execution_state"],
                    "source": {
                        "path": row["source"]["path"],
                        "line": row["source"]["line"],
                        "column": row["source"]["column"],
                    },
                    "identity": row["identity"],
                }
                for row in occurrences
            ]
            result.append(
                {
                    "collision_id": content_id(
                        "workbench-groovy-static-collision-candidate:sha256:",
                        {"policy_id": policy["policy_id"], "value": value, "effects": [row["effect_id"] for row in occurrences]},
                    ),
                    "evidence_state": "static-candidate",
                    "policy_id": policy["policy_id"],
                    "identity_kind": policy["identity_kind"],
                    "value": value,
                    "disposition": policy["disposition"],
                    "execution_state": (
                        "enabled"
                        if all(row["lifecycle"]["execution_state"] == "enabled" for row in occurrences)
                        else "conditional"
                    ),
                    "effect_ids": [row["effect_id"] for row in occurrences],
                    "occurrences": occurrence_rows,
                    "limitations": [
                        "Duplicate static literals are a review candidate; exact lifecycle execution and registry behavior have not been observed."
                    ],
                }
            )
    return sorted(result, key=lambda row: (row["policy_id"], json.dumps(row["value"], sort_keys=True)))


def _git_binding(pack_root: Path) -> dict[str, Any] | None:
    try:
        executable = configured_git_executable()
    except GitObservationError as exc:
        raise PackProgramError(f"configured Git binding is unavailable: {exc}") from exc
    if executable is None:
        return None
    try:
        top = subprocess.run(
            [
                executable,
                "-C",
                display_filesystem_path(pack_root),
                "rev-parse",
                "--show-toplevel",
            ],
            check=False,
            capture_output=True,
            timeout=5,
            env=observation_environment(),
        )
        if top.returncode:
            return None
        top_text = _git_single_line(top.stdout)
        if top_text is None:
            return None
        repository = native_filesystem_path(Path(top_text)).resolve()
        repository_argument = display_filesystem_path(repository)
        prefix = safe_git_prefix(
            repository,
            executable=executable,
            timeout=5,
        )
        head = subprocess.run(
            [*prefix, "-C", repository_argument, "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            timeout=5,
            env=observation_environment(),
        )
        status = subprocess.run(
            [
                *prefix,
                "-C",
                repository_argument,
                "status",
                "--porcelain=v1",
                "-z",
                "--untracked-files=normal",
            ],
            check=False,
            capture_output=True,
            timeout=5,
            env=observation_environment(),
        )
        if head.returncode or status.returncode:
            return None
        revision = _git_single_line(head.stdout)
        if revision is None or (
            len(revision) not in {40, 64}
            or any(character not in "0123456789abcdef" for character in revision)
        ):
            return None
        return {
            "repository_root": repository_argument,
            "revision": revision,
            "dirty": bool(status.stdout),
        }
    except GitObservationError as exc:
        raise PackProgramError(
            f"configured Git binding cannot be observed safely: {exc}"
        ) from exc
    except (OSError, UnicodeError, ValueError, subprocess.SubprocessError):
        return None


def _git_single_line(raw: bytes) -> str | None:
    if not isinstance(raw, bytes):
        return None
    if raw.endswith(b"\r\n"):
        raw = raw[:-2]
    elif raw.endswith(b"\n"):
        raw = raw[:-1]
    if not raw or b"\n" in raw or b"\r" in raw or b"\x00" in raw:
        return None
    return raw.decode("utf-8", "strict")


def _validated_git_binding(value: Mapping[str, Any]) -> dict[str, Any]:
    if set(value) != {"repository_root", "revision", "dirty"}:
        raise PackProgramError("Git binding override has unexpected or missing keys")
    repository_root = value["repository_root"]
    revision = value["revision"]
    dirty = value["dirty"]
    if not isinstance(repository_root, str) or not Path(repository_root).is_absolute():
        raise PackProgramError("Git binding override repository_root must be absolute")
    if (
        not isinstance(revision, str)
        or len(revision) not in {40, 64}
        or any(character not in "0123456789abcdef" for character in revision)
    ):
        raise PackProgramError("Git binding override revision must be a full object ID")
    if not isinstance(dirty, bool):
        raise PackProgramError("Git binding override dirty must be boolean")
    return {
        "repository_root": repository_root,
        "revision": revision,
        "dirty": dirty,
    }


def _effect_examples(effects: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for effect in effects:
        result.setdefault(effect["semantic_key"], effect)
    return result


def _diff_rows(
    counts: Counter[str], examples: Mapping[str, Mapping[str, Any]]
) -> tuple[list[dict[str, Any]], bool]:
    rows: list[dict[str, Any]] = []
    for key, count in sorted(counts.items()):
        if len(rows) >= MAX_DIFF_ROWS:
            return rows, True
        example = examples[key]
        rows.append(
            {
                "semantic_key": key,
                "count": count,
                "rule_id": example["rule_id"],
                "kind": example["kind"],
                "operation": example["operation"],
                "identity": example["identity"],
                "source": {
                    "path": example["source"]["path"],
                    "line": example["source"]["line"],
                },
            }
        )
    return rows, False


def _normalize_changed_path(value: str) -> str:
    if not isinstance(value, str):
        raise PackProgramError("changed path must be relative to the Groovy root")
    rendered = value.replace("\\", "/")
    if rendered.startswith("groovy/"):
        rendered = rendered[len("groovy/") :]
    return portable_relative_path(rendered, f"changed Groovy path {value!r}").as_posix()


def _stage_order(profile: LoadedProfile, stage: str) -> int:
    if stage == "run-config":
        return -1
    return int(profile.platform["load_stages"].get(stage, {"order": 999})["order"])


__all__ = [
    "AnalysisContext",
    "analyze_program",
    "assess_change",
    "compare_programs",
]
