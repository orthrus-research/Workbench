"""Resolve pack-owned managed run recipes into exact, inert execution plans."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shlex
from typing import Any, Callable, Mapping, Sequence

from workbench_crucible_worldgen_iteration.iteration import (
    WorldgenIterationError,
    audit_runtime_template,
    load_profile,
    parse_region,
    resolve_profile_path,
    sha256_file,
)


CATALOG_FORMAT = "workbench-managed-run-profile-catalog-v1"
PLAN_FORMAT = "workbench-managed-run-plan-v1"
CANONICALIZATION_ID = "workbench-canonical-json-v1"
DOCTOR_FORMAT = "workbench-project-intelligence-workspace-doctor-report-v1"
PLAN_ID_PREFIX = "workbench-managed-run-plan:sha256:"
TARGET_ID_PREFIX = "workbench-managed-run-target:sha256:"
PACK_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
RECIPE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
HEAP_RE = re.compile(r"^[1-9][0-9]*[KMG]$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CATALOG_KEYS = frozenset(
    {"catalog_id", "format", "pack_profile", "recipes", "schema_version", "target"}
)
TARGET_KEYS = frozenset({"capability", "side", "worldgen_profile"})
RECIPE_KEYS = frozenset(
    {
        "availability",
        "diagnostic_sample_modulo",
        "id",
        "mode",
        "open_viewer",
        "purpose",
        "record_jfr",
        "region_default",
    }
)
AVAILABILITY_KEYS = frozenset({"reason", "state"})
RUNNER_MODES = frozenset({"fast", "debug", "performance"})
REGION_DEFAULTS = frozenset({"fast_region", "debug_region"})


class ManagedRunProfileError(RuntimeError):
    """An invalid catalog, recipe, preview input, or execution request."""


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ManagedRunProfileError(f"JSON object repeats key {key!r}")
        value[key] = item
    return value


def _regular_json(path: Path, context: str) -> dict[str, Any]:
    expanded = path.expanduser()
    if expanded.is_symlink():
        raise ManagedRunProfileError(f"{context} cannot be a symlink: {expanded}")
    try:
        resolved = expanded.resolve(strict=True)
    except OSError as exc:
        raise ManagedRunProfileError(f"{context} is unavailable: {expanded}") from exc
    if not resolved.is_file():
        raise ManagedRunProfileError(f"{context} is not a regular file: {resolved}")
    try:
        value = json.loads(
            resolved.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except ManagedRunProfileError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ManagedRunProfileError(f"cannot parse {context} {resolved}: {exc}") from exc
    if not isinstance(value, dict):
        raise ManagedRunProfileError(f"{context} must contain a JSON object")
    return value


def _regular_file(path: Path, context: str) -> Path:
    expanded = path.expanduser()
    if expanded.is_symlink():
        raise ManagedRunProfileError(f"{context} cannot be a symlink: {expanded}")
    try:
        resolved = expanded.resolve(strict=True)
    except OSError as exc:
        raise ManagedRunProfileError(f"{context} is unavailable: {expanded}") from exc
    if not resolved.is_file():
        raise ManagedRunProfileError(f"{context} is not a regular file: {resolved}")
    return resolved


def _mapping(value: Any, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ManagedRunProfileError(f"{context} must be an object")
    return value


def _exact_keys(value: Mapping[str, Any], keys: set[str], context: str) -> None:
    if set(value) != keys:
        raise ManagedRunProfileError(f"{context} has an unsupported V1 shape")


def resolve_catalog_path(root: Path, pack_profile: str) -> Path:
    if not PACK_NAME_RE.fullmatch(pack_profile):
        raise ManagedRunProfileError(f"invalid pack profile name: {pack_profile!r}")
    return (
        root
        / "profiles/packs"
        / pack_profile
        / "run-profiles/managed-run-profiles-v1.json"
    )


def validate_catalog(value: Mapping[str, Any], *, expected_pack: str | None = None) -> None:
    if set(value) != CATALOG_KEYS:
        raise ManagedRunProfileError("managed run profile catalog has an unsupported V1 shape")
    if value.get("format") != CATALOG_FORMAT or value.get("schema_version") != 1:
        raise ManagedRunProfileError("unsupported managed run profile catalog version")
    pack = value.get("pack_profile")
    if not isinstance(pack, str) or not PACK_NAME_RE.fullmatch(pack):
        raise ManagedRunProfileError("catalog pack_profile is invalid")
    if expected_pack is not None and pack != expected_pack:
        raise ManagedRunProfileError(
            f"catalog pack identity mismatch: expected {expected_pack}, found {pack}"
        )
    if value.get("catalog_id") != f"workbench-pack:{pack}:managed-run-profiles-v1":
        raise ManagedRunProfileError("catalog_id does not bind the declared pack profile")
    target = value.get("target")
    if not isinstance(target, Mapping) or set(target) != TARGET_KEYS:
        raise ManagedRunProfileError("catalog target has an unsupported V1 shape")
    if (
        target.get("capability") != "worldgen-dev"
        or target.get("side") != "dedicated-server"
        or not isinstance(target.get("worldgen_profile"), str)
        or not PACK_NAME_RE.fullmatch(target["worldgen_profile"])
    ):
        raise ManagedRunProfileError("catalog target is invalid or unsupported by V1")
    recipes = value.get("recipes")
    if not isinstance(recipes, list) or not recipes:
        raise ManagedRunProfileError("catalog recipes must be a non-empty array")
    recipe_ids: set[str] = set()
    for recipe in recipes:
        if not isinstance(recipe, Mapping) or set(recipe) != RECIPE_KEYS:
            raise ManagedRunProfileError("managed run recipe has an unsupported V1 shape")
        recipe_id = recipe.get("id")
        purpose = recipe.get("purpose")
        availability = recipe.get("availability")
        if (
            not isinstance(recipe_id, str)
            or not RECIPE_NAME_RE.fullmatch(recipe_id)
            or recipe_id in recipe_ids
            or not isinstance(purpose, str)
            or not purpose
            or not isinstance(availability, Mapping)
            or set(availability) != AVAILABILITY_KEYS
        ):
            raise ManagedRunProfileError("managed run recipe identity is invalid")
        state = availability.get("state")
        reason = availability.get("reason")
        mode = recipe.get("mode")
        region_default = recipe.get("region_default")
        modulo = recipe.get("diagnostic_sample_modulo")
        open_viewer = recipe.get("open_viewer")
        record_jfr = recipe.get("record_jfr")
        if (
            not isinstance(reason, str)
            or not reason
            or not isinstance(open_viewer, bool)
            or not isinstance(record_jfr, bool)
        ):
            raise ManagedRunProfileError("managed run recipe availability is invalid")
        if state == "available":
            if (
                mode not in RUNNER_MODES
                or region_default not in REGION_DEFAULTS
                or not isinstance(modulo, int)
                or isinstance(modulo, bool)
                or modulo < 1
                or record_jfr != (mode == "performance")
            ):
                raise ManagedRunProfileError(
                    f"available managed run recipe {recipe_id!r} is inconsistent"
                )
        elif state == "unavailable":
            if (
                mode is not None
                or region_default is not None
                or modulo is not None
                or open_viewer
                or record_jfr
            ):
                raise ManagedRunProfileError(
                    f"unavailable managed run recipe {recipe_id!r} is inconsistent"
                )
        else:
            raise ManagedRunProfileError(
                f"managed run recipe {recipe_id!r} availability is invalid"
            )
        recipe_ids.add(recipe_id)


def load_catalog(path: Path, *, expected_pack: str | None = None) -> dict[str, Any]:
    resolved = _regular_file(path, "managed run profile catalog")
    value = _regular_json(resolved, "managed run profile catalog")
    validate_catalog(value, expected_pack=expected_pack)
    value["_path"] = str(resolved)
    value["_sha256"] = sha256_file(resolved)
    value["_canonical_sha256"] = _canonical_sha256(
        {key: item for key, item in value.items() if not key.startswith("_")}
    )
    return value


def _recipe(catalog: Mapping[str, Any], recipe_name: str) -> dict[str, Any]:
    if not RECIPE_NAME_RE.fullmatch(recipe_name):
        raise ManagedRunProfileError(f"invalid managed run recipe name: {recipe_name!r}")
    matches = [row for row in catalog["recipes"] if row["id"] == recipe_name]
    if len(matches) != 1:
        available = ", ".join(row["id"] for row in catalog["recipes"])
        raise ManagedRunProfileError(
            f"unknown managed run recipe {recipe_name!r}; available recipes: {available}"
        )
    return dict(matches[0])


def _doctor_context(report: Mapping[str, Any]) -> tuple[Mapping[str, Any], Mapping[str, Any], list[str]]:
    if (
        report.get("format") != DOCTOR_FORMAT
        or report.get("schema_version") != 1
        or report.get("read_only") is not True
        or report.get("capability") != "worldgen-dev"
    ):
        raise ManagedRunProfileError("managed run requires one full worldgen-dev Doctor V1 report")
    summary = _mapping(report.get("summary"), "Doctor summary")
    if set(summary) != {"status", "blockers", "warnings", "information"}:
        raise ManagedRunProfileError("Doctor summary has an unsupported V1 shape")
    status = summary.get("status")
    counts = [summary.get(name) for name in ("blockers", "warnings", "information")]
    if status not in {"ready", "attention", "blocked"} or not all(
        isinstance(item, int) and not isinstance(item, bool) and item >= 0
        for item in counts
    ):
        raise ManagedRunProfileError("Doctor summary is invalid")
    findings = report.get("findings")
    if not isinstance(findings, list):
        raise ManagedRunProfileError("Doctor findings must be an array")
    severities = {"blocker": 0, "warning": 0, "info": 0}
    blocker_ids: list[str] = []
    for finding in findings:
        if not isinstance(finding, Mapping) or finding.get("severity") not in severities:
            raise ManagedRunProfileError("Doctor finding is invalid")
        finding_id = finding.get("id")
        if not isinstance(finding_id, str) or not finding_id:
            raise ManagedRunProfileError("Doctor finding ID is invalid")
        severities[finding["severity"]] += 1
        if finding["severity"] == "blocker":
            blocker_ids.append(finding_id)
    blocker_ids = sorted(blocker_ids)
    if len(blocker_ids) != len(set(blocker_ids)):
        raise ManagedRunProfileError("Doctor blocker IDs are not unique")
    expected_status = (
        "blocked" if severities["blocker"] else "attention" if severities["warning"] else "ready"
    )
    if summary != {
        "status": expected_status,
        "blockers": severities["blocker"],
        "warnings": severities["warning"],
        "information": severities["info"],
    }:
        raise ManagedRunProfileError("Doctor summary does not match its findings")
    target = _mapping(report.get("target"), "Doctor target")
    return summary, target, blocker_ids


def _doctor_command_arguments(report: Mapping[str, Any]) -> list[str]:
    commands = report.get("next_commands")
    if not isinstance(commands, list):
        raise ManagedRunProfileError("Doctor returned an invalid next-command collection")
    matches = [
        row
        for row in commands
        if isinstance(row, Mapping) and row.get("id") == "worldgen-dev"
    ]
    if len(matches) != 1 or not isinstance(matches[0].get("command"), str):
        raise ManagedRunProfileError("Doctor did not resolve one worldgen-dev command")
    fields = shlex.split(matches[0]["command"])
    prefix = ["python3", "tools/workbench.py", "worldgen", "dev"]
    if fields[:4] != prefix:
        raise ManagedRunProfileError("Doctor returned an unsupported worldgen command")
    return fields[4:]


def _selected_tool(target: Mapping[str, Any], kind: str) -> dict[str, Any] | None:
    if kind == "java":
        roles = target.get("java_roles")
        if not isinstance(roles, list):
            return None
        selected: Mapping[str, Any] | None = None
        for role in roles:
            if isinstance(role, Mapping) and role.get("role") == "cleanroom-runtime":
                candidate = role.get("selected")
                selected = candidate if isinstance(candidate, Mapping) else None
                break
        keys = ("path", "sha256", "version_output", "version", "major")
    else:
        build = target.get("build")
        wrapper = build.get("selected_tool") if isinstance(build, Mapping) else None
        candidate = wrapper.get("selected") if isinstance(wrapper, Mapping) else None
        selected = candidate if isinstance(candidate, Mapping) else None
        keys = ("path", "sha256", "version_output", "version")
    if selected is None or not all(key in selected for key in keys):
        return None
    return {key: selected[key] for key in keys}


def _strata_root(target: Mapping[str, Any]) -> str | None:
    integrations = target.get("integrations")
    items = integrations.get("items") if isinstance(integrations, Mapping) else None
    if not isinstance(items, list):
        return None
    for item in items:
        if isinstance(item, Mapping) and item.get("id") == "strata":
            root = item.get("root")
            return root if isinstance(root, str) and root else None
    return None


def _runtime_binding(target: Mapping[str, Any]) -> dict[str, Any]:
    runtime = target.get("runtime")
    if not isinstance(runtime, Mapping):
        return {"state": "unavailable", "path": None, "jar_inventory_sha256": None, "server_jar": None}
    template = runtime.get("template")
    inventory = runtime.get("jar_inventory")
    server = runtime.get("server_jar")
    if (
        runtime.get("state") == "observed"
        and isinstance(template, str)
        and template
        and isinstance(inventory, list)
        and isinstance(server, Mapping)
        and isinstance(server.get("path"), str)
        and isinstance(server.get("size_bytes"), int)
        and isinstance(server.get("sha256"), str)
        and SHA256_RE.fullmatch(server["sha256"])
    ):
        return {
            "state": "resolved",
            "path": template,
            "jar_inventory_sha256": _canonical_sha256(
                _jar_inventory_projection(inventory)
            ),
            "server_jar": {
                "path": server["path"],
                "size": server["size_bytes"],
                "sha256": server["sha256"],
            },
        }
    state = "unresolved" if runtime.get("state") in {"known-absent", "unresolved", "ambiguous"} else "unavailable"
    return {"state": state, "path": None, "jar_inventory_sha256": None, "server_jar": None}


def _jar_inventory_projection(inventory: Sequence[Any]) -> list[dict[str, Any]]:
    projected: list[dict[str, Any]] = []
    for row in inventory:
        if not isinstance(row, Mapping):
            raise ManagedRunProfileError("runtime JAR inventory contains an invalid row")
        mod_ids = row.get("mod_ids")
        if not isinstance(mod_ids, list) or not all(
            isinstance(item, str) and item for item in mod_ids
        ):
            raise ManagedRunProfileError("runtime JAR inventory has invalid mod IDs")
        binding = {
            "path": row.get("path"),
            "relative_path": row.get("relative_path"),
            "sha256": row.get("sha256"),
            "size_bytes": row.get("size_bytes"),
            "mod_ids": sorted(mod_ids),
        }
        if (
            not isinstance(binding["path"], str)
            or not binding["path"]
            or not isinstance(binding["relative_path"], str)
            or not binding["relative_path"]
            or not isinstance(binding["sha256"], str)
            or not SHA256_RE.fullmatch(binding["sha256"])
            or not isinstance(binding["size_bytes"], int)
            or isinstance(binding["size_bytes"], bool)
            or binding["size_bytes"] < 1
        ):
            raise ManagedRunProfileError("runtime JAR inventory binding is invalid")
        projected.append(binding)
    return sorted(projected, key=lambda row: row["relative_path"])


def _candidate_binding(target: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    platform = target.get("platform")
    candidate = platform.get("candidate_lock") if isinstance(platform, Mapping) else None
    if not isinstance(candidate, Mapping):
        return None, None
    path_value = candidate.get("path")
    candidate_id = candidate.get("candidate_id")
    reported_sha = candidate.get("sha256")
    if not all(isinstance(item, str) and item for item in (path_value, candidate_id, reported_sha)):
        return None, None
    try:
        path = _regular_file(Path(path_value), "Doctor candidate lock")
        raw = _regular_json(path, "Doctor candidate lock")
    except ManagedRunProfileError:
        return None, None
    file_sha = sha256_file(path)
    if file_sha != reported_sha or raw.get("candidate_id") != candidate_id:
        raise ManagedRunProfileError("Doctor candidate-lock binding drifted from its source")
    canonical = _canonical_sha256(raw)
    return {
        "candidate_id": candidate_id,
        "path": str(path),
        "file_sha256": file_sha,
        "canonical_sha256": canonical,
    }, canonical


def _default_label(recipe_name: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    return f"managed-{recipe_name}-{stamp}"


def _stage_ids(*, mode: str, open_viewer: bool) -> list[str]:
    stages = ["preflight", "build", "provision", "configure", "capture", "summarize"]
    if mode == "performance":
        stages.append("performance")
    stages.append("handoff")
    if open_viewer:
        stages.append("open_viewer")
    return stages


RUNNER_VALUE_OPTIONS = frozenset(
    {
        "--profile",
        "--runtime-template",
        "--strata-root",
        "--java-cmd",
        "--gradle-cmd",
        "--mode",
        "--seed",
        "--region",
        "--diagnostic-sample-modulo",
        "--plan",
        "--label",
        "--heap",
    }
)
RUNNER_FLAG_OPTIONS = frozenset({"--no-open"})


def _runner_options(arguments: Sequence[str]) -> tuple[dict[str, str], set[str]]:
    values: dict[str, str] = {}
    flags: set[str] = set()
    index = 0
    while index < len(arguments):
        option = arguments[index]
        if option in RUNNER_FLAG_OPTIONS:
            if option in flags:
                raise ManagedRunProfileError(f"managed run repeats runner flag {option}")
            flags.add(option)
            index += 1
            continue
        if option not in RUNNER_VALUE_OPTIONS:
            raise ManagedRunProfileError(f"managed run contains unsupported runner option {option!r}")
        if option in values or index + 1 >= len(arguments):
            raise ManagedRunProfileError(f"managed run runner option {option} is repeated or missing a value")
        value = arguments[index + 1]
        if not isinstance(value, str) or not value:
            raise ManagedRunProfileError(f"managed run runner option {option} has an invalid value")
        values[option] = value
        index += 2
    if set(values) != RUNNER_VALUE_OPTIONS:
        missing = sorted(RUNNER_VALUE_OPTIONS - set(values))
        extra = sorted(set(values) - RUNNER_VALUE_OPTIONS)
        raise ManagedRunProfileError(
            f"managed run runner option set is incomplete: missing={missing}; extra={extra}"
        )
    return values, flags


def _without_value_option(arguments: Sequence[str], option: str) -> list[str]:
    result: list[str] = []
    index = 0
    found = False
    while index < len(arguments):
        item = arguments[index]
        if item == option:
            if found or index + 1 >= len(arguments):
                raise ManagedRunProfileError(f"managed run option {option} is invalid")
            found = True
            index += 2
            continue
        result.append(item)
        index += 1
    if not found:
        raise ManagedRunProfileError(f"managed run option {option} is missing")
    return result


def resolve_managed_run_plan(
    root: Path,
    *,
    profile_name: str,
    recipe_name: str,
    doctor_report: Mapping[str, Any],
    side: str = "dedicated-server",
    seed: int | None = None,
    region: str | None = None,
    label: str | None = None,
    heap: str | None = None,
) -> dict[str, Any]:
    """Resolve an inert plan; no build, runtime, world, or report is created."""

    try:
        root = root.expanduser().resolve(strict=True)
    except OSError as exc:
        raise ManagedRunProfileError(f"Workbench root is unavailable: {root}") from exc
    if side != "dedicated-server":
        raise ManagedRunProfileError(
            f"managed run plan V1 supports only dedicated-server, not {side!r}"
        )
    catalog = load_catalog(
        resolve_catalog_path(root, profile_name), expected_pack=profile_name
    )
    recipe = _recipe(catalog, recipe_name)
    target_declaration = catalog["target"]
    if target_declaration["side"] != side:
        raise ManagedRunProfileError("managed run catalog does not support the requested side")
    worldgen_profile_name = target_declaration["worldgen_profile"]
    profile_path = resolve_profile_path(root, worldgen_profile_name)
    raw_worldgen = _regular_json(profile_path, "worldgen iteration profile")
    try:
        worldgen = load_profile(profile_path, root)
    except WorldgenIterationError as exc:
        raise ManagedRunProfileError(str(exc)) from exc

    summary, doctor_target, blocker_ids = _doctor_context(doctor_report)
    doctor_profile = _mapping(doctor_target.get("profile"), "Doctor profile")
    if (
        doctor_profile.get("profile_id") != worldgen["profile_id"]
        or doctor_profile.get("pack_profile_id") != f"workbench-pack:{profile_name}"
        or doctor_profile.get("path") != str(profile_path.resolve())
        or doctor_profile.get("sha256") != sha256_file(profile_path)
    ):
        raise ManagedRunProfileError("Doctor report targets a different pack or worldgen profile")

    requested_plan = _regular_file(Path(worldgen["_plan"]), "managed run Groovy plan")
    doctor_plan = _mapping(doctor_profile.get("plan"), "Doctor Groovy plan binding")
    if (
        doctor_plan.get("state") != "observed"
        or doctor_plan.get("path") != str(requested_plan)
        or doctor_plan.get("sha256") != sha256_file(requested_plan)
    ):
        raise ManagedRunProfileError(
            "Doctor report did not inspect the exact managed-run Groovy plan"
        )
    raw_seed = seed if seed is not None else worldgen["defaults"]["seed"]
    if (
        not isinstance(raw_seed, int)
        or isinstance(raw_seed, bool)
        or not -(2**63) <= raw_seed <= 2**63 - 1
    ):
        raise ManagedRunProfileError("managed run seed must be a signed 64-bit integer")
    effective_heap = heap if heap is not None else worldgen["defaults"]["heap"]
    if not isinstance(effective_heap, str) or not HEAP_RE.fullmatch(effective_heap):
        raise ManagedRunProfileError("managed run heap must look like 4096M")
    effective_label = label if label is not None else _default_label(recipe_name)
    if not isinstance(effective_label, str) or not LABEL_RE.fullmatch(effective_label):
        raise ManagedRunProfileError("managed run label is not path-safe")

    recipe_available = recipe["availability"]["state"] == "available"
    region_binding: dict[str, Any] | None = None
    diagnostics: dict[str, Any] | None = None
    mode = recipe["mode"]
    modulo = recipe["diagnostic_sample_modulo"]
    if recipe_available:
        if region is not None:
            region_text = region
            region_source = "explicit"
        else:
            default_name = recipe["region_default"]
            default_region = worldgen["defaults"].get(default_name)
            if not isinstance(default_region, list):
                raise ManagedRunProfileError("managed run region default is invalid")
            region_text = ",".join(str(item) for item in default_region)
            region_source = f"worldgen-profile:{default_name}"
        try:
            min_x, min_z, width, height = parse_region(region_text)
        except WorldgenIterationError as exc:
            raise ManagedRunProfileError(str(exc)) from exc
        region_binding = {
            "source": region_source,
            "dimension": 0,
            "min_x": min_x,
            "min_z": min_z,
            "width": width,
            "height": height,
            "chunk_count": width * height,
            "halo": worldgen["defaults"]["halo_chunks"],
        }
        diagnostics = {
            "sample_modulo": modulo,
            "structured_logs": True,
            "record_jfr": recipe["record_jfr"],
        }

    runner_arguments: list[str] = []
    command: str | None = None
    reproduction_command: str | None = None
    if recipe_available and summary["status"] != "blocked":
        runner_arguments = _doctor_command_arguments(doctor_report)
        assert region_binding is not None and diagnostics is not None
        runner_arguments.extend(
            [
                "--mode",
                str(mode),
                "--seed",
                str(raw_seed),
                "--region",
                f"{region_binding['min_x']},{region_binding['min_z']},{region_binding['width']},{region_binding['height']}",
                "--diagnostic-sample-modulo",
                str(modulo),
                "--plan",
                str(requested_plan),
                "--label",
                effective_label,
                "--heap",
                effective_heap,
            ]
        )
        if not recipe["open_viewer"]:
            runner_arguments.append("--no-open")
        command = shlex.join(
            ["python3", "tools/workbench.py", "worldgen", "dev", *runner_arguments]
        )
        reproduction_command = shlex.join(
            [
                "python3",
                "tools/workbench.py",
                "worldgen",
                "dev",
                *_without_value_option(runner_arguments, "--label"),
            ]
        )

    candidate_lock, platform_canonical = _candidate_binding(doctor_target)
    platform = doctor_target.get("platform")
    platform_profile_id = (
        platform.get("profile_id") if isinstance(platform, Mapping) else None
    )
    workspace = _mapping(doctor_target.get("workspace"), "Doctor workspace")
    fixture_path = workspace.get("root")
    workspace_path = workspace.get("requested_path", str(root))
    if not isinstance(fixture_path, str) or not fixture_path:
        raise ManagedRunProfileError("Doctor did not bind the worldgen fixture")
    if not isinstance(workspace_path, str) or not workspace_path:
        raise ManagedRunProfileError("Doctor did not bind the requested workspace")
    if summary["status"] != "blocked" and (
        candidate_lock is None
        or platform_profile_id != worldgen["platform_profile_id"]
        or _runtime_binding(doctor_target)["state"] != "resolved"
        or _selected_tool(doctor_target, "java") is None
        or _selected_tool(doctor_target, "gradle") is None
        or _strata_root(doctor_target) is None
    ):
        raise ManagedRunProfileError("ready Doctor report is missing an executable target binding")

    target_value: dict[str, Any] = {
        "target_id": None,
        "side": side,
        "workspace_path": workspace_path,
        "minecraft_version": worldgen["minecraft_version"],
        "platform_profile_id": platform_profile_id,
        "platform_profile_canonical_sha256": platform_canonical,
        "candidate_lock": candidate_lock,
        "pack_id": f"workbench-pack:{profile_name}",
        "fixture_path": fixture_path,
        "runtime_template": _runtime_binding(doctor_target),
        "world_type": worldgen["world_type"],
        "java": _selected_tool(doctor_target, "java"),
        "gradle": _selected_tool(doctor_target, "gradle"),
        "strata_root": _strata_root(doctor_target),
        "disposable_runtime": True,
        "fresh_world_required": True,
    }
    target_value["target_id"] = TARGET_ID_PREFIX + _canonical_sha256(
        {key: value for key, value in target_value.items() if key != "target_id"}
    )

    stages = (
        _stage_ids(mode=str(mode), open_viewer=recipe["open_viewer"])
        if recipe_available
        else []
    )
    jvm_arguments: list[str] = []
    if recipe_available:
        jvm_arguments = [
            "-Dworkbench.worldgen.diagnostics=true",
            f"-Dworkbench.worldgen.diagnostics.sample_modulo={modulo}",
            "-Dworkbench.worldgen.jfr.biome_points=false",
        ]
        if recipe["record_jfr"]:
            recording = (
                root
                / ".workbench/iterations/worldgen"
                / effective_label
                / "runtime/worldgen-iteration.jfr"
            )
            jvm_arguments.extend(
                [
                    "-XX:FlightRecorderOptions=stackdepth=128",
                    "-XX:StartFlightRecording="
                    f"filename={recording},settings=profile,dumponexit=true",
                ]
            )

    status = "blocked" if not recipe_available else str(summary["status"])
    capability_path = _regular_file(
        root / "modules/crucible/contracts/managed-run-plan-v1.md",
        "managed run capability definition",
    )
    runner_path = _regular_file(
        root
        / "modules/crucible/src/workbench_crucible_worldgen_iteration/cli.py",
        "worldgen runner implementation",
    )
    plan_value: dict[str, Any] = {
        "format": PLAN_FORMAT,
        "schema_version": 1,
        "canonicalization_id": CANONICALIZATION_ID,
        "plan_id": None,
        "read_only_preview": True,
        "capability": {
            "capability_id": "workbench-crucible:managed-run-v1",
            "definition_path": str(capability_path),
            "definition_sha256": sha256_file(capability_path),
        },
        "profile": {
            "pack": profile_name,
            "catalog_id": catalog["catalog_id"],
            "catalog_path": catalog["_path"],
            "catalog_file_sha256": catalog["_sha256"],
            "catalog_canonical_sha256": catalog["_canonical_sha256"],
            "worldgen_profile_id": worldgen["profile_id"],
            "worldgen_profile_format": worldgen["format"],
            "worldgen_profile_schema_version": worldgen["schema_version"],
            "worldgen_profile_path": str(profile_path.resolve()),
            "worldgen_profile_file_sha256": sha256_file(profile_path),
            "worldgen_profile_canonical_sha256": _canonical_sha256(raw_worldgen),
        },
        "recipe": {
            "recipe_id": f"workbench-pack:{profile_name}:managed-run:{recipe['id']}",
            "name": recipe["id"],
            "purpose": recipe["purpose"],
            "canonical_sha256": _canonical_sha256(recipe),
        },
        "availability": dict(recipe["availability"]),
        "status": status,
        "target": target_value,
        "runner": {
            "runner_id": "workbench-crucible:worldgen-iteration-v1",
            "implementation_path": str(runner_path),
            "implementation_sha256": sha256_file(runner_path),
            "working_directory": str(root),
            "command": command,
            "arguments": runner_arguments,
            "reproduction_command": reproduction_command,
        },
        "effective": {
            "mode": mode,
            "seed": raw_seed,
            "region": region_binding,
            "diagnostics": diagnostics,
            "heap": effective_heap,
            "plan": {"path": str(requested_plan), "sha256": sha256_file(requested_plan)},
            "open_viewer": recipe["open_viewer"],
            "record_jfr": recipe["record_jfr"],
            "skip_build": False,
            "jvm_arguments": jvm_arguments,
            "mutations": (
                [
                    "compile and production-remap the current World Studio source",
                    "copy the selected template into a new disposable iteration runtime",
                    "create offline server configuration and a fresh world",
                    "install the current remapped mod and freeze one Groovy plan",
                    "launch and stop the dedicated Cleanroom server for bounded capture",
                ]
                if recipe_available
                else []
            ),
            "stages": stages,
            "outputs": (
                [
                    "incremental iteration report",
                    "fresh disposable runtime and world",
                    "Cleanroom, build, and capture logs",
                    "World Studio diagnostic summary",
                    "validated Strata package and viewer handoff",
                    *(
                        ["JFR recording and bounded World Studio event summary"]
                        if recipe["record_jfr"]
                        else []
                    ),
                ]
                if recipe_available
                else []
            ),
            "retention": (
                [
                    "retain the report, logs, summaries, capture, and disposable runtime under .workbench/iterations/worldgen",
                    "never modify the selected runtime template or a personal world",
                ]
                if recipe_available
                else []
            ),
            "restart_boundary": "Every managed run provisions a fresh runtime and world; Java, registry, frozen-plan, and generated-chunk changes are never hot reloaded.",
        },
        "doctor": {
            "report_format": doctor_report["format"],
            "report_schema_version": doctor_report["schema_version"],
            "report_canonical_sha256": _canonical_sha256(doctor_report),
            "status": summary["status"],
            "blocker_count": summary["blockers"],
            "warning_count": summary["warnings"],
            "information_count": summary["information"],
            "blocker_ids": blocker_ids,
        },
        "limitations": sorted(
            [
                "Managed run profile V1 supports only the exact dedicated-server worldgen-dev adapter; client and integrated-server targets are unavailable.",
                "A ready plan is a read-only preflight, not proof that the later build, launch, capture, or game behavior will succeed.",
                "Proof remains a separate Observatory authority and is never inferred from a development run.",
                "Runtime-template V1 binds the JAR inventory; non-JAR configuration and scripts are structurally re-audited but not content-bound.",
            ]
        ),
    }
    plan_value["plan_id"] = PLAN_ID_PREFIX + _canonical_sha256(
        {key: value for key, value in plan_value.items() if key != "plan_id"}
    )
    validate_managed_run_plan(plan_value)
    return plan_value


def validate_managed_run_plan(plan: Mapping[str, Any]) -> None:
    """Validate the closed V1 projection and its semantic identities."""

    top_keys = {
        "format", "schema_version", "canonicalization_id", "plan_id",
        "read_only_preview", "capability", "profile", "recipe", "availability",
        "status", "target", "runner", "effective", "doctor", "limitations",
    }
    _exact_keys(plan, top_keys, "managed run plan")
    if (
        plan.get("format") != PLAN_FORMAT
        or plan.get("schema_version") != 1
        or plan.get("canonicalization_id") != CANONICALIZATION_ID
        or plan.get("read_only_preview") is not True
    ):
        raise ManagedRunProfileError("unsupported managed run plan version")
    expected_plan_id = PLAN_ID_PREFIX + _canonical_sha256(
        {key: value for key, value in plan.items() if key != "plan_id"}
    )
    if plan.get("plan_id") != expected_plan_id:
        raise ManagedRunProfileError("managed run plan identity does not match its contents")

    def text_value(value: Any, context: str) -> str:
        if not isinstance(value, str) or not value or "\x00" in value:
            raise ManagedRunProfileError(f"{context} must be non-empty text")
        return value

    def digest_value(value: Any, context: str) -> str:
        text = text_value(value, context)
        if not SHA256_RE.fullmatch(text):
            raise ManagedRunProfileError(f"{context} is not a SHA-256 digest")
        return text

    def integer_value(value: Any, context: str, *, minimum: int | None = None) -> int:
        if not isinstance(value, int) or isinstance(value, bool):
            raise ManagedRunProfileError(f"{context} must be an integer")
        if minimum is not None and value < minimum:
            raise ManagedRunProfileError(f"{context} is below its minimum")
        return value

    capability = _mapping(plan.get("capability"), "managed run capability")
    _exact_keys(
        capability,
        {"capability_id", "definition_path", "definition_sha256"},
        "managed run capability",
    )
    text_value(capability["capability_id"], "managed run capability ID")
    text_value(capability["definition_path"], "managed run capability path")
    digest_value(capability["definition_sha256"], "managed run capability digest")
    if capability["capability_id"] != "workbench-crucible:managed-run-v1":
        raise ManagedRunProfileError("managed run capability identity is invalid")

    profile = _mapping(plan.get("profile"), "managed run profile binding")
    profile_keys = {
        "pack", "catalog_id", "catalog_path", "catalog_file_sha256",
        "catalog_canonical_sha256", "worldgen_profile_id",
        "worldgen_profile_format", "worldgen_profile_schema_version",
        "worldgen_profile_path", "worldgen_profile_file_sha256",
        "worldgen_profile_canonical_sha256",
    }
    _exact_keys(profile, profile_keys, "managed run profile binding")
    for key in (
        "pack", "catalog_id", "catalog_path", "worldgen_profile_id",
        "worldgen_profile_format", "worldgen_profile_path",
    ):
        text_value(profile[key], f"managed run profile {key}")
    for key in (
        "catalog_file_sha256", "catalog_canonical_sha256",
        "worldgen_profile_file_sha256", "worldgen_profile_canonical_sha256",
    ):
        digest_value(profile[key], f"managed run profile {key}")
    integer_value(
        profile["worldgen_profile_schema_version"],
        "managed run worldgen profile schema version",
        minimum=1,
    )
    if (
        profile["catalog_id"]
        != f"workbench-pack:{profile['pack']}:managed-run-profiles-v1"
        or not profile["worldgen_profile_id"].startswith(
            f"workbench-pack:{profile['pack']}:worldgen-"
        )
    ):
        raise ManagedRunProfileError("managed run profile identities disagree")

    recipe = _mapping(plan.get("recipe"), "managed run recipe binding")
    _exact_keys(
        recipe,
        {"recipe_id", "name", "purpose", "canonical_sha256"},
        "managed run recipe binding",
    )
    for key in ("recipe_id", "name", "purpose"):
        text_value(recipe[key], f"managed run recipe {key}")
    digest_value(recipe["canonical_sha256"], "managed run recipe digest")
    if recipe["recipe_id"] != (
        f"workbench-pack:{profile['pack']}:managed-run:{recipe['name']}"
    ):
        raise ManagedRunProfileError("managed run recipe identity is invalid")

    availability = _mapping(plan.get("availability"), "managed run availability")
    if set(availability) != {"state", "reason"} or availability.get("state") not in {"available", "unavailable"}:
        raise ManagedRunProfileError("managed run availability is invalid")
    if not isinstance(availability.get("reason"), str) or not availability["reason"]:
        raise ManagedRunProfileError("managed run availability reason is invalid")
    doctor = _mapping(plan.get("doctor"), "managed run Doctor binding")
    _exact_keys(
        doctor,
        {
            "report_format", "report_schema_version", "report_canonical_sha256",
            "status", "blocker_count", "warning_count", "information_count",
            "blocker_ids",
        },
        "managed run Doctor binding",
    )
    if doctor.get("report_format") != DOCTOR_FORMAT or doctor.get("report_schema_version") != 1:
        raise ManagedRunProfileError("managed run Doctor binding has an unsupported version")
    digest_value(doctor.get("report_canonical_sha256"), "managed run Doctor digest")
    if doctor.get("status") not in {"ready", "attention", "blocked"}:
        raise ManagedRunProfileError("managed run Doctor status is invalid")
    for key in ("blocker_count", "warning_count", "information_count"):
        integer_value(doctor.get(key), f"managed run Doctor {key}", minimum=0)
    blocker_ids = doctor.get("blocker_ids")
    if (
        not isinstance(blocker_ids, list)
        or blocker_ids != sorted(set(blocker_ids))
        or doctor.get("blocker_count") != len(blocker_ids)
    ):
        raise ManagedRunProfileError("managed run Doctor blocker binding is invalid")
    expected_status = (
        "blocked" if availability["state"] == "unavailable" else doctor["status"]
    )
    if plan.get("status") != expected_status:
        raise ManagedRunProfileError("managed run status does not match availability and Doctor")

    target = _mapping(plan.get("target"), "managed run target")
    target_keys = {
        "target_id", "side", "workspace_path", "minecraft_version",
        "platform_profile_id", "platform_profile_canonical_sha256",
        "candidate_lock", "pack_id", "fixture_path", "runtime_template",
        "world_type", "java", "gradle", "strata_root", "disposable_runtime",
        "fresh_world_required",
    }
    _exact_keys(target, target_keys, "managed run target")
    if target.get("side") != "dedicated-server":
        raise ManagedRunProfileError("managed run target is not a dedicated server")
    for key in ("workspace_path", "minecraft_version", "pack_id", "fixture_path", "world_type"):
        text_value(target.get(key), f"managed run target {key}")
    if target["pack_id"] != f"workbench-pack:{profile['pack']}":
        raise ManagedRunProfileError("managed run target pack identity is invalid")
    if target.get("platform_profile_id") is not None:
        text_value(target["platform_profile_id"], "managed run platform profile ID")
    if target.get("platform_profile_canonical_sha256") is not None:
        digest_value(
            target["platform_profile_canonical_sha256"],
            "managed run platform profile digest",
        )
    if target.get("strata_root") is not None:
        text_value(target["strata_root"], "managed run Strata root")
    if target.get("disposable_runtime") is not True or target.get("fresh_world_required") is not True:
        raise ManagedRunProfileError("managed run target must use a disposable runtime and fresh world")

    candidate = target.get("candidate_lock")
    if candidate is not None:
        candidate = _mapping(candidate, "managed run candidate lock")
        _exact_keys(
            candidate,
            {"candidate_id", "path", "file_sha256", "canonical_sha256"},
            "managed run candidate lock",
        )
        text_value(candidate["candidate_id"], "managed run candidate ID")
        text_value(candidate["path"], "managed run candidate path")
        digest_value(candidate["file_sha256"], "managed run candidate file digest")
        digest_value(candidate["canonical_sha256"], "managed run candidate canonical digest")

    runtime = _mapping(target.get("runtime_template"), "managed run runtime template")
    _exact_keys(runtime, {"state", "path", "jar_inventory_sha256", "server_jar"}, "managed run runtime template")
    if runtime.get("state") not in {"resolved", "unresolved", "unavailable"}:
        raise ManagedRunProfileError("managed run runtime-template state is invalid")
    if runtime["state"] == "resolved":
        text_value(runtime.get("path"), "managed run runtime-template path")
        digest_value(runtime.get("jar_inventory_sha256"), "managed run runtime JAR inventory digest")
        server = _mapping(runtime.get("server_jar"), "managed run server JAR")
        _exact_keys(server, {"path", "size", "sha256"}, "managed run server JAR")
        text_value(server["path"], "managed run server JAR path")
        integer_value(server["size"], "managed run server JAR size", minimum=1)
        digest_value(server["sha256"], "managed run server JAR digest")
    elif any(runtime.get(key) is not None for key in ("path", "jar_inventory_sha256", "server_jar")):
        raise ManagedRunProfileError("unresolved runtime-template binding contains resolved facts")

    java = target.get("java")
    if java is not None:
        java = _mapping(java, "managed run Java binding")
        _exact_keys(java, {"path", "sha256", "version_output", "version", "major"}, "managed run Java binding")
        for key in ("path", "version_output", "version"):
            text_value(java[key], f"managed run Java {key}")
        digest_value(java["sha256"], "managed run Java digest")
        integer_value(java["major"], "managed run Java major", minimum=1)
    gradle = target.get("gradle")
    if gradle is not None:
        gradle = _mapping(gradle, "managed run Gradle binding")
        _exact_keys(gradle, {"path", "sha256", "version_output", "version"}, "managed run Gradle binding")
        for key in ("path", "version_output", "version"):
            text_value(gradle[key], f"managed run Gradle {key}")
        digest_value(gradle["sha256"], "managed run Gradle digest")
    if plan.get("status") in {"ready", "attention"}:
        if (
            target.get("platform_profile_id") is None
            or candidate is None
            or target.get("platform_profile_canonical_sha256")
            != candidate.get("canonical_sha256")
            or runtime.get("state") != "resolved"
            or java is None
            or gradle is None
            or target.get("strata_root") is None
        ):
            raise ManagedRunProfileError(
                "executable managed run plan has an unresolved target binding"
            )
        if not candidate["candidate_id"].startswith(
            f"{target['platform_profile_id']}+"
        ):
            raise ManagedRunProfileError(
                "managed run candidate and platform identities disagree"
            )
    expected_target_id = TARGET_ID_PREFIX + _canonical_sha256(
        {key: value for key, value in target.items() if key != "target_id"}
    )
    if target.get("target_id") != expected_target_id:
        raise ManagedRunProfileError("managed run target identity does not match its contents")

    runner = _mapping(plan.get("runner"), "managed run runner")
    _exact_keys(
        runner,
        {
            "runner_id", "implementation_path", "implementation_sha256",
            "working_directory", "command", "arguments", "reproduction_command",
        },
        "managed run runner",
    )
    for key in ("runner_id", "implementation_path", "working_directory"):
        text_value(runner[key], f"managed run runner {key}")
    digest_value(runner["implementation_sha256"], "managed run runner digest")
    if runner["runner_id"] != "workbench-crucible:worldgen-iteration-v1":
        raise ManagedRunProfileError("managed run runner identity is invalid")
    arguments = runner.get("arguments")
    if not isinstance(arguments, list) or not all(isinstance(item, str) and item for item in arguments):
        raise ManagedRunProfileError("managed run runner arguments are invalid")
    command = runner.get("command")
    reproduction = runner.get("reproduction_command")
    effective = _mapping(plan.get("effective"), "managed run effective projection")
    effective_keys = {
        "mode", "seed", "region", "diagnostics", "heap", "plan",
        "open_viewer", "record_jfr", "skip_build", "jvm_arguments",
        "mutations", "stages", "outputs", "retention", "restart_boundary",
    }
    _exact_keys(effective, effective_keys, "managed run effective projection")
    if effective.get("mode") not in {None, "fast", "debug", "performance"}:
        raise ManagedRunProfileError("managed run mode is invalid")
    seed = integer_value(effective.get("seed"), "managed run seed")
    if not -(2**63) <= seed <= 2**63 - 1:
        raise ManagedRunProfileError("managed run seed is outside signed 64-bit range")
    if not isinstance(effective.get("heap"), str) or not HEAP_RE.fullmatch(effective["heap"]):
        raise ManagedRunProfileError("managed run heap is invalid")
    for key in ("open_viewer", "record_jfr", "skip_build"):
        if not isinstance(effective.get(key), bool):
            raise ManagedRunProfileError(f"managed run {key} must be boolean")
    if effective.get("skip_build") is not False:
        raise ManagedRunProfileError("managed run plan V1 does not support skipping the build")
    text_value(effective.get("restart_boundary"), "managed run restart boundary")
    plan_binding = _mapping(effective.get("plan"), "managed run Groovy plan binding")
    _exact_keys(plan_binding, {"path", "sha256"}, "managed run Groovy plan binding")
    text_value(plan_binding["path"], "managed run Groovy plan path")
    digest_value(plan_binding["sha256"], "managed run Groovy plan digest")
    operational_arrays = [
        effective.get(name) for name in ("jvm_arguments", "mutations", "stages", "outputs", "retention")
    ]
    if not all(
        isinstance(items, list)
        and all(isinstance(item, str) and item for item in items)
        and len(items) == len(set(items))
        for items in operational_arrays
    ):
        raise ManagedRunProfileError("managed run operational arrays are invalid")
    if availability["state"] == "unavailable":
        if (
            command is not None
            or reproduction is not None
            or arguments
            or effective.get("mode") is not None
            or effective.get("region") is not None
            or effective.get("diagnostics") is not None
            or any(operational_arrays)
        ):
            raise ManagedRunProfileError("unavailable managed run plan contains executable operations")
    elif plan.get("status") == "blocked":
        if command is not None or reproduction is not None or arguments:
            raise ManagedRunProfileError("Doctor-blocked managed run plan is executable")
    else:
        expected_command = shlex.join(
            ["python3", "tools/workbench.py", "worldgen", "dev", *arguments]
        )
        expected_reproduction = shlex.join(
            [
                "python3",
                "tools/workbench.py",
                "worldgen",
                "dev",
                *_without_value_option(arguments, "--label"),
            ]
        )
        if command != expected_command or reproduction != expected_reproduction:
            raise ManagedRunProfileError("managed run command does not match its argument vector")
        options, flags = _runner_options(arguments)
        region = _mapping(effective.get("region"), "managed run region")
        _exact_keys(
            region,
            {"source", "dimension", "min_x", "min_z", "width", "height", "chunk_count", "halo"},
            "managed run region",
        )
        text_value(region["source"], "managed run region source")
        for key in ("dimension", "min_x", "min_z", "width", "height", "chunk_count", "halo"):
            integer_value(region[key], f"managed run region {key}")
        if (
            region["dimension"] != 0
            or region["width"] < 1
            or region["height"] < 1
            or region["halo"] < 0
            or region["width"] * region["height"] != region["chunk_count"]
            or not 1 <= region["chunk_count"] <= 1024
        ):
            raise ManagedRunProfileError("managed run region arithmetic is invalid")
        diagnostics = _mapping(effective.get("diagnostics"), "managed run diagnostics")
        _exact_keys(diagnostics, {"sample_modulo", "structured_logs", "record_jfr"}, "managed run diagnostics")
        integer_value(diagnostics["sample_modulo"], "managed run diagnostic sample modulo", minimum=1)
        if diagnostics.get("structured_logs") is not True or not isinstance(diagnostics.get("record_jfr"), bool):
            raise ManagedRunProfileError("managed run diagnostic selection is invalid")
        expected_region = (
            f"{region['min_x']},{region['min_z']},{region['width']},{region['height']}"
        )
        expected_options = {
            "--profile": profile["pack"],
            "--runtime-template": runtime["path"],
            "--strata-root": target["strata_root"],
            "--java-cmd": java["path"],
            "--gradle-cmd": gradle["path"],
            "--mode": effective["mode"],
            "--seed": str(effective["seed"]),
            "--region": expected_region,
            "--diagnostic-sample-modulo": str(diagnostics["sample_modulo"]),
            "--plan": plan_binding["path"],
            "--label": options["--label"],
            "--heap": effective["heap"],
        }
        if options != expected_options or not LABEL_RE.fullmatch(options["--label"]):
            raise ManagedRunProfileError(
                "managed run arguments disagree with the displayed effective plan"
            )
        expected_flags = set() if effective["open_viewer"] else {"--no-open"}
        if flags != expected_flags:
            raise ManagedRunProfileError("managed run viewer arguments disagree with the plan")
        record_jfr = effective.get("record_jfr")
        if diagnostics.get("record_jfr") is not record_jfr:
            raise ManagedRunProfileError("managed run JFR selections disagree")
        stages = effective["stages"]
        if ("performance" in stages) != bool(record_jfr):
            raise ManagedRunProfileError("managed run performance stage and JFR selection disagree")
        if ("open_viewer" in stages) != bool(effective.get("open_viewer")):
            raise ManagedRunProfileError("managed run viewer stage and selection disagree")

    for name in ("limitations",):
        items = plan.get(name)
        if not isinstance(items, list) or items != sorted(set(items)):
            raise ManagedRunProfileError(f"managed run {name} must be sorted and unique")


def render_managed_run_plan(plan: Mapping[str, Any]) -> str:
    validate_managed_run_plan(plan)
    profile = plan["profile"]
    recipe = plan["recipe"]
    target = plan["target"]
    runner = plan["runner"]
    effective = plan["effective"]
    lines = [
        "Workbench managed run",
        f"Status: {plan['status']}",
        f"Recipe: {recipe['name']} - {recipe['purpose']}",
        f"Pack: {profile['pack']} ({profile['worldgen_profile_id']})",
        f"Target: {target['side']} / {target['minecraft_version']} / {target['platform_profile_id']}",
    ]
    region = effective["region"]
    if effective["mode"] is not None and isinstance(region, Mapping):
        lines.extend(
            [
                f"Mode: {effective['mode']}",
                "Seed/region: "
                f"{effective['seed']} / {region['min_x']},{region['min_z']},{region['width']},{region['height']}",
                f"Plan: {effective['plan']['path']}",
                "Stages: " + " -> ".join(effective["stages"]),
                "Intended mutations:",
                *[f"  - {item}" for item in effective["mutations"]],
            ]
        )
    if plan["doctor"]["blocker_ids"]:
        lines.append("Blocked by: " + ", ".join(plan["doctor"]["blocker_ids"]))
    if runner["command"]:
        lines.append("Command: " + runner["command"])
    if plan["availability"]["state"] == "unavailable":
        lines.append("Unavailable: " + plan["availability"]["reason"])
    lines.append(
        "Preview resolution was read-only: no build, runtime, world, or report was created."
    )
    return "\n".join(lines) + "\n"


def _require_current_file(path_value: Any, expected_sha256: Any, context: str) -> Path:
    if not isinstance(path_value, str) or not path_value:
        raise ManagedRunProfileError(f"{context} path is invalid")
    if not isinstance(expected_sha256, str) or not SHA256_RE.fullmatch(expected_sha256):
        raise ManagedRunProfileError(f"{context} digest is invalid")
    path = _regular_file(Path(path_value), context)
    if sha256_file(path) != expected_sha256:
        raise ManagedRunProfileError(f"{context} changed after managed-run planning")
    return path


def validate_managed_run_freshness(plan: Mapping[str, Any], *, root: Path) -> None:
    """Recheck byte bindings immediately before the first delegated mutation."""

    validate_managed_run_plan(plan)
    try:
        resolved_root = root.expanduser().resolve(strict=True)
    except OSError as exc:
        raise ManagedRunProfileError(f"Workbench root is unavailable: {root}") from exc
    runner = plan["runner"]
    try:
        working_directory = Path(runner["working_directory"]).resolve(strict=True)
    except OSError as exc:
        raise ManagedRunProfileError("managed run working directory is unavailable") from exc
    if resolved_root != working_directory:
        raise ManagedRunProfileError(
            "managed run root does not match its bound working directory"
        )
    capability = plan["capability"]
    profile = plan["profile"]
    target = plan["target"]
    effective = plan["effective"]
    for path_value, digest, context in (
        (
            capability["definition_path"],
            capability["definition_sha256"],
            "managed run capability definition",
        ),
        (
            profile["catalog_path"],
            profile["catalog_file_sha256"],
            "managed run profile catalog",
        ),
        (
            profile["worldgen_profile_path"],
            profile["worldgen_profile_file_sha256"],
            "managed run worldgen profile",
        ),
        (
            runner["implementation_path"],
            runner["implementation_sha256"],
            "managed run runner implementation",
        ),
        (
            effective["plan"]["path"],
            effective["plan"]["sha256"],
            "managed run Groovy plan",
        ),
    ):
        _require_current_file(path_value, digest, context)
    candidate = target.get("candidate_lock")
    if candidate is not None:
        _require_current_file(
            candidate["path"], candidate["file_sha256"], "managed run candidate lock"
        )
    runtime = target["runtime_template"]
    if runtime["state"] == "resolved":
        try:
            runtime_audit = audit_runtime_template(Path(runtime["path"]), None)
        except (OSError, ValueError, WorldgenIterationError) as exc:
            raise ManagedRunProfileError(
                f"managed run runtime template cannot be freshness-checked: {exc}"
            ) from exc
        current_inventory_sha256 = _canonical_sha256(
            _jar_inventory_projection(runtime_audit["jar_inventory"])
        )
        if (
            runtime_audit.get("unsafe_entries")
            or current_inventory_sha256 != runtime["jar_inventory_sha256"]
        ):
            raise ManagedRunProfileError(
                "managed run runtime JAR inventory changed after planning"
            )
        server = runtime["server_jar"]
        server_path = _require_current_file(
            server["path"], server["sha256"], "managed run server JAR"
        )
        if server_path.stat().st_size != server["size"]:
            raise ManagedRunProfileError("managed run server JAR size changed after planning")
    for key, context in (("java", "managed run Java executable"), ("gradle", "managed run Gradle executable")):
        binding = target.get(key)
        if binding is not None:
            _require_current_file(binding["path"], binding["sha256"], context)


def execute_managed_run_plan(
    plan: Mapping[str, Any],
    *,
    root: Path,
    runner: Callable[..., int],
) -> int:
    validate_managed_run_freshness(plan, root=root)
    if plan.get("status") == "blocked" or plan["availability"]["state"] != "available":
        raise ManagedRunProfileError("blocked or unavailable managed run plan cannot be executed")
    arguments = list(plan["runner"]["arguments"])
    return runner(arguments, root=root.expanduser().resolve())


__all__ = [
    "CANONICALIZATION_ID",
    "CATALOG_FORMAT",
    "ManagedRunProfileError",
    "PLAN_FORMAT",
    "execute_managed_run_plan",
    "load_catalog",
    "render_managed_run_plan",
    "resolve_catalog_path",
    "resolve_managed_run_plan",
    "validate_catalog",
    "validate_managed_run_freshness",
    "validate_managed_run_plan",
]
