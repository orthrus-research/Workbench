"""Atlas normalization and derivation for retained worldgen evidence."""

from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re
from typing import Any, Iterable
import zipfile

MAX_SCRIPT_BYTES = 4 * 1024 * 1024
MAX_REPORT_BYTES = 8 * 1024 * 1024
MAX_LOG_BYTES = 32 * 1024 * 1024
MAX_CONFIG_BYTES = 4 * 1024 * 1024
MAX_ARTIFACT_BYTES = 512 * 1024 * 1024
MAX_EVIDENCE_TOTAL_BYTES = 512 * 1024 * 1024
MAX_CLASS_ENTRIES = 100_000
MAX_CLASS_TOTAL_BYTES = 512 * 1024 * 1024
MAX_SCRIPTS = 256
MAX_BIOME_REPORTS = 8192
MAX_LOGS = 32
CLIMATES = ("ICY", "COOL", "WARM", "DESERT")

ASSIGNMENT_RE = re.compile(
    r"^\s*(?P<variable>[A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*"
    r"(?P<selector>forBiomes|forAllBiomesExcept)\s*"
    r"\((?P<arguments>[^)]*)\)\s*(?:#.*)?$"
)
GENERATION_RE = re.compile(
    r"^\s*(?P<variable>[A-Za-z_$][A-Za-z0-9_$]*)"
    r"\.addToGeneration\(\s*['\"](?P<climate>[A-Za-z]+)['\"]\s*,"
    r"\s*(?P<weight>-?\d+)\s*\)\s*(?:#.*)?$"
)
RTG_UNSUPPORTED_RE = re.compile(
    r"\|\|\s*(?P<biome_id>\d+)\s*\|\s*"
    r"(?P<biome_class>[^|]+?)\s*\|\s*"
    r"(?P<resource_location>[a-z0-9_.-]+:[a-z0-9_./-]+)\s*\|\|"
)
MISSING_EVENT_RE = re.compile(
    r"chunk generator (?P<generator>[A-Za-z0-9_.$/]+) did not trigger "
    r"(?P<event>[A-Za-z0-9_.$/]+) during initialization!",
    re.IGNORECASE,
)
LOG_CONTEXT_RE = re.compile(
    r"^\[[^]]+\]\s+\[[^]/]+/(?P<severity>[A-Z]+)\]\s+"
    r"\[(?P<reporter>[^]]+)\]:"
)
NOISE_STATE_RE = re.compile(
    r"Could not provide native noise fields and RNG for dimension id "
    r"(?P<dimension_id>-?\d+): (?P<dimension_name>[^!]+)!"
)
NOISE_CONSEQUENCE = (
    "Terrain features that depend on these won't work correctly."
)
SERVER_READY_RE = re.compile(r"\]: Done \([^)]+\)! For help, type")
WORLD_SEED_RE = re.compile(r"\[RTG\]: World Seed: (?P<seed>-?\d+)\s*$")
FORGE_CONFIG_RE = re.compile(
    r"^\s*(?P<type>[BIDS]):(?P<key>[^=\s]+)=(?P<value>.*)$"
)


class AtlasWorldgenObservationError(ValueError):
    """Raised when retained world-generation evidence cannot be trusted."""


def _read_regular(
    path: Path,
    label: str,
    maximum: int,
    *,
    aggregate_remaining: int | None = None,
) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise AtlasWorldgenObservationError(
            f"{label} is not a regular file: {path}"
        )
    try:
        size = path.stat().st_size
        if size > maximum:
            raise AtlasWorldgenObservationError(
                f"{label} exceeds the {maximum}-byte audit limit"
            )
        if aggregate_remaining is not None and size > aggregate_remaining:
            raise AtlasWorldgenObservationError(
                "runtime evidence exceeds the aggregate byte audit limit"
            )
        read_limit = maximum
        if aggregate_remaining is not None:
            read_limit = min(read_limit, aggregate_remaining)
        with path.open("rb") as source:
            raw = source.read(read_limit + 1)
        if len(raw) > maximum:
            raise AtlasWorldgenObservationError(
                f"{label} exceeds the {maximum}-byte audit limit"
            )
        if (
            aggregate_remaining is not None
            and len(raw) > aggregate_remaining
        ):
            raise AtlasWorldgenObservationError(
                "runtime evidence exceeds the aggregate byte audit limit"
            )
        return raw
    except OSError as exc:
        raise AtlasWorldgenObservationError(
            f"{label} cannot be read: {path}"
        ) from exc


def _utf8(raw: bytes, label: str) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AtlasWorldgenObservationError(
            f"{label} is not valid UTF-8"
        ) from exc


def _safe_relative(value: Any, label: str, *, glob: bool = False) -> str:
    if not isinstance(value, str) or not value:
        raise AtlasWorldgenObservationError(f"{label} must be a non-empty string")
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise AtlasWorldgenObservationError(f"{label} escapes the runtime root")
    if not glob and any(character in value for character in "*?["):
        raise AtlasWorldgenObservationError(f"{label} cannot contain a glob")
    return value


def _glob_files(
    runtime_root: Path,
    patterns: Iterable[Any],
    *,
    label: str,
    maximum: int,
) -> list[Path]:
    found: dict[str, Path] = {}
    for index, raw_pattern in enumerate(patterns):
        pattern = _safe_relative(
            raw_pattern,
            f"{label} pattern {index}",
            glob=True,
        )
        for candidate in runtime_root.glob(pattern):
            resolved = candidate.resolve()
            if not resolved.is_relative_to(runtime_root):
                raise AtlasWorldgenObservationError(
                    f"{label} match escapes the runtime root: {candidate}"
                )
            if candidate.is_symlink() or not candidate.is_file():
                raise AtlasWorldgenObservationError(
                    f"{label} match is not a regular file: {candidate}"
                )
            found[resolved.as_posix()] = resolved
    result = [found[key] for key in sorted(found)]
    if len(result) > maximum:
        raise AtlasWorldgenObservationError(
            f"{label} exceeds the {maximum}-file audit limit"
        )
    return result


class _EvidenceIndex:
    def __init__(
        self,
        *,
        maximum_total_bytes: int = MAX_EVIDENCE_TOTAL_BYTES,
    ) -> None:
        self.records: list[dict[str, Any]] = []
        self._ids: dict[Path, str] = {}
        self._raw: dict[Path, bytes] = {}
        self._maximum_total_bytes = maximum_total_bytes
        self._total_bytes = 0

    def add(self, path: Path, kind: str, maximum: int) -> tuple[str, bytes]:
        resolved = path.resolve()
        existing = self._ids.get(resolved)
        if existing is not None:
            return existing, self._raw[resolved]
        raw = _read_regular(
            resolved,
            kind,
            maximum,
            aggregate_remaining=(
                self._maximum_total_bytes - self._total_bytes
            ),
        )
        evidence_id = f"evidence:{len(self.records) + 1}"
        self.records.append({
            "evidence_id": evidence_id,
            "kind": kind,
            "uri": resolved.as_uri(),
            "sha256": sha256(raw).hexdigest(),
            "size": len(raw),
        })
        self._ids[resolved] = evidence_id
        self._raw[resolved] = raw
        self._total_bytes += len(raw)
        return evidence_id, raw


def _script_bindings(
    paths: list[Path],
    evidence: _EvidenceIndex,
) -> list[dict[str, Any]]:
    assignments: dict[tuple[Path, str], dict[str, Any]] = {}
    generations: list[tuple[Path, dict[str, Any]]] = []
    for path in paths:
        evidence_id, raw = evidence.add(path, "biometweaker-script", MAX_SCRIPT_BYTES)
        for line_number, line in enumerate(
            _utf8(raw, "BiomeTweaker script").splitlines(),
            start=1,
        ):
            assignment = ASSIGNMENT_RE.match(line)
            if assignment:
                arguments = [
                    value.strip()
                    for value in assignment.group("arguments").split(",")
                    if value.strip()
                ]
                variable = assignment.group("variable")
                if (path, variable) in assignments:
                    raise AtlasWorldgenObservationError(
                        f"duplicate BiomeTweaker selector definition for "
                        f"{variable} in {path}"
                    )
                assignments[(path, variable)] = {
                    "evidence_id": evidence_id,
                    "line": line_number,
                    "variable": variable,
                    "selector": assignment.group("selector"),
                    "arguments": arguments,
                }
                continue
            generation = GENERATION_RE.match(line)
            if generation:
                climate = generation.group("climate").upper()
                if climate not in CLIMATES:
                    continue
                generations.append((path, {
                    "evidence_id": evidence_id,
                    "line": line_number,
                    "variable": generation.group("variable"),
                    "climate": climate,
                    "weight": int(generation.group("weight")),
                }))
    bindings: list[dict[str, Any]] = []
    for path, generation in generations:
        assignment = assignments.get((path, generation["variable"]))
        if assignment is None:
            continue
        bindings.append({
            **assignment,
            "generation_line": generation["line"],
            "climate": generation["climate"],
            "weight": generation["weight"],
        })
    return sorted(
        bindings,
        key=lambda item: (
            item["evidence_id"],
            item["generation_line"],
            item["variable"],
        ),
    )


def _biome_registry(
    paths: list[Path],
    evidence: _EvidenceIndex,
    profile_non_overworld: set[str],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    reports: list[dict[str, Any]] = []
    membership_index: dict[str, dict[str, Any]] = {}
    climate_biomes: dict[str, int] = defaultdict(int)
    climate_entries: dict[str, int] = defaultdict(int)
    duplicates: list[dict[str, Any]] = []
    cross_climate: list[dict[str, Any]] = []
    non_overworld: list[dict[str, Any]] = []
    for path in paths:
        evidence_id, raw = evidence.add(
            path,
            "biometweaker-biome-report",
            MAX_REPORT_BYTES,
        )
        try:
            payload = json.loads(_utf8(raw, "BiomeTweaker biome report"))
        except json.JSONDecodeError as exc:
            raise AtlasWorldgenObservationError(
                f"BiomeTweaker biome report is invalid JSON: {path}"
            ) from exc
        if not isinstance(payload, dict):
            raise AtlasWorldgenObservationError(
                f"BiomeTweaker biome report must be an object: {path}"
            )
        resource = payload.get("Resource Location")
        raw_entries = payload.get("BiomeManager Entries", {})
        dictionary = payload.get("Dictionary Types", [])
        if (
            not isinstance(resource, str)
            or not resource
            or not isinstance(raw_entries, dict)
            or not isinstance(dictionary, list)
            or not all(
                isinstance(item, str) and bool(item)
                for item in dictionary
            )
            or len(set(dictionary)) != len(dictionary)
        ):
            raise AtlasWorldgenObservationError(
                f"BiomeTweaker biome report has an unsupported shape: {path}"
            )
        entries: dict[str, list[int]] = {}
        for key, raw_weights in raw_entries.items():
            if not isinstance(key, str) or not key.endswith(" Weights"):
                continue
            climate = key[:-8].upper()
            if climate not in CLIMATES:
                continue
            if (
                not isinstance(raw_weights, list)
                or not all(type(weight) is int for weight in raw_weights)
                or any(weight < 0 for weight in raw_weights)
            ):
                raise AtlasWorldgenObservationError(
                    f"BiomeTweaker weights are invalid in {path}"
                )
            positive_weights = [
                weight for weight in raw_weights if weight > 0
            ]
            if positive_weights:
                entries[climate] = positive_weights
                climate_biomes[climate] += 1
                climate_entries[climate] += len(positive_weights)
                if len(positive_weights) > 1:
                    duplicates.append({
                        "resource_location": resource,
                        "climate": climate,
                        "weights": positive_weights,
                        "evidence_id": evidence_id,
                    })
        if resource in membership_index:
            raise AtlasWorldgenObservationError(
                f"duplicate BiomeTweaker biome report for {resource}"
            )
        membership_index[resource] = {
            "climates": entries,
            "evidence_id": evidence_id,
        }
        if len(entries) > 1:
            cross_climate.append({
                "resource_location": resource,
                "climates": entries,
                "evidence_id": evidence_id,
            })
        profile_match = resource in profile_non_overworld
        if entries and profile_match:
            non_overworld.append({
                "resource_location": resource,
                "climates": entries,
                "dictionary_types": sorted(dictionary),
                "basis": "profile",
                "evidence_id": evidence_id,
            })
        reports.append({
            "resource_location": resource,
            "evidence_id": evidence_id,
        })
    state = "observed" if paths else "unavailable"
    return {
        "state": state,
        "report_count": len(reports),
        "climate_totals": [
            {
                "climate": climate,
                "biome_count": climate_biomes.get(climate, 0),
                "entry_count": climate_entries.get(climate, 0),
            }
            for climate in CLIMATES
        ],
        "duplicate_memberships": sorted(
            duplicates,
            key=lambda item: (item["resource_location"], item["climate"]),
        ),
        "cross_climate_memberships": sorted(
            cross_climate,
            key=lambda item: item["resource_location"],
        ),
        "non_overworld_memberships": sorted(
            non_overworld,
            key=lambda item: item["resource_location"],
        ),
    }, membership_index


def _log_observations(
    paths: list[Path],
    evidence: _EvidenceIndex,
) -> dict[str, Any]:
    unsupported: list[dict[str, Any]] = []
    missing_events: list[dict[str, Any]] = []
    noise_warnings: list[dict[str, Any]] = []
    server_ready: list[dict[str, Any]] = []
    seeds: list[dict[str, Any]] = []
    for path in paths:
        evidence_id, raw = evidence.add(path, "runtime-log", MAX_LOG_BYTES)
        in_unsupported_table = False
        pending_noise: dict[str, Any] | None = None
        for line_number, line in enumerate(
            _utf8(raw, "runtime log").splitlines(),
            start=1,
        ):
            if (
                pending_noise is not None
                and line.strip() == NOISE_CONSEQUENCE
            ):
                pending_noise["consequence_observed"] = True
                pending_noise["consequence_line"] = line_number
            pending_noise = None
            if "could not find realistic versions of the following biomes" in line:
                in_unsupported_table = True
                continue
            if in_unsupported_table:
                match = RTG_UNSUPPORTED_RE.search(line)
                if match:
                    unsupported.append({
                        "biome_id": int(match.group("biome_id")),
                        "biome_class": match.group("biome_class").strip(),
                        "resource_location": match.group("resource_location"),
                        "evidence_id": evidence_id,
                        "line": line_number,
                    })
                elif unsupported and "`= ===" in line:
                    in_unsupported_table = False
            event = MISSING_EVENT_RE.search(line)
            if event:
                context = LOG_CONTEXT_RE.match(line)
                missing_events.append({
                    "generator": event.group("generator").replace("/", "."),
                    "event": event.group("event").replace("/", "."),
                    "reporter": (
                        context.group("reporter")
                        if context is not None
                        else "unresolved"
                    ),
                    "severity": (
                        context.group("severity")
                        if context is not None
                        else "unresolved"
                    ),
                    "evidence_id": evidence_id,
                    "line": line_number,
                })
            noise = NOISE_STATE_RE.search(line)
            if noise:
                context = LOG_CONTEXT_RE.match(line)
                pending_noise = {
                    "dimension_id": int(noise.group("dimension_id")),
                    "dimension_name": noise.group("dimension_name"),
                    "reporter": (
                        context.group("reporter")
                        if context is not None
                        else "unresolved"
                    ),
                    "severity": (
                        context.group("severity")
                        if context is not None
                        else "unresolved"
                    ),
                    "consequence_observed": False,
                    "consequence_line": None,
                    "evidence_id": evidence_id,
                    "line": line_number,
                }
                noise_warnings.append(pending_noise)
            if SERVER_READY_RE.search(line):
                context = LOG_CONTEXT_RE.match(line)
                server_ready.append({
                    "reporter": (
                        context.group("reporter")
                        if context is not None
                        else "unresolved"
                    ),
                    "evidence_id": evidence_id,
                    "line": line_number,
                })
            seed = WORLD_SEED_RE.search(line)
            if seed:
                seeds.append({
                    "seed": int(seed.group("seed")),
                    "evidence_id": evidence_id,
                    "line": line_number,
                })
    return {
        "state": "observed" if paths else "unavailable",
        "unsupported_biomes": sorted(
            unsupported,
            key=lambda item: (
                item["evidence_id"],
                item["line"],
            ),
        ),
        "missing_events": sorted(
            missing_events,
            key=lambda item: (
                item["evidence_id"],
                item["line"],
            ),
        ),
        "noise_state_warnings": sorted(
            noise_warnings,
            key=lambda item: (
                item["evidence_id"],
                item["line"],
            ),
        ),
        "server_ready_markers": sorted(
            server_ready,
            key=lambda item: (
                item["evidence_id"],
                item["line"],
            ),
        ),
        "world_seeds": sorted(
            seeds,
            key=lambda item: (
                item["evidence_id"],
                item["line"],
            ),
        ),
    }


def _forge_config_values(text: str) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for line_number, line in enumerate(text.splitlines(), start=1):
        match = FORGE_CONFIG_RE.match(line)
        if match:
            result[match.group("key")] = {
                "type": match.group("type"),
                "value": match.group("value"),
                "line": str(line_number),
            }
    return result


def _class_literal_states(
    artifact: Path,
    raw: bytes,
    keys: list[str],
) -> dict[str, str]:
    states = {key: "not-observed" for key in keys}
    try:
        with zipfile.ZipFile(BytesIO(raw)) as archive:
            infos = [
                info for info in archive.infolist()
                if not info.is_dir() and info.filename.endswith(".class")
            ]
            if len(infos) > MAX_CLASS_ENTRIES:
                raise AtlasWorldgenObservationError(
                    "runtime artifact exceeds the class-entry audit limit"
                )
            if sum(info.file_size for info in infos) > MAX_CLASS_TOTAL_BYTES:
                raise AtlasWorldgenObservationError(
                    "runtime artifact exceeds the uncompressed class-byte audit limit"
                )
            for info in infos:
                if info.file_size > MAX_REPORT_BYTES:
                    raise AtlasWorldgenObservationError(
                        f"class entry exceeds audit limit: {info.filename}"
                    )
                payload = archive.read(info)
                constants = _class_utf8_constants(payload, info.filename)
                for key in keys:
                    if (
                        states[key] == "not-observed"
                        and key.encode("ascii") in constants
                    ):
                        states[key] = "observed"
    except (
        EOFError,
        NotImplementedError,
        OSError,
        RuntimeError,
        UnicodeEncodeError,
        zipfile.BadZipFile,
    ) as exc:
        raise AtlasWorldgenObservationError(
            f"cannot inspect runtime artifact classes: {artifact}"
        ) from exc
    return states


def _class_utf8_constants(payload: bytes, entry_name: str) -> set[bytes]:
    """Return exact CONSTANT_Utf8 payloads from one Java class file."""

    malformed = AtlasWorldgenObservationError(
        f"class entry has a malformed constant pool: {entry_name}"
    )
    if len(payload) < 10 or payload[:4] != b"\xca\xfe\xba\xbe":
        raise malformed
    constant_pool_count = int.from_bytes(payload[8:10], "big")
    if constant_pool_count < 1:
        raise malformed
    offset = 10
    index = 1
    constants: set[bytes] = set()
    fixed_sizes = {
        3: 4,   # Integer
        4: 4,   # Float
        5: 8,   # Long
        6: 8,   # Double
        7: 2,   # Class
        8: 2,   # String
        9: 4,   # Fieldref
        10: 4,  # Methodref
        11: 4,  # InterfaceMethodref
        12: 4,  # NameAndType
        15: 3,  # MethodHandle
        16: 2,  # MethodType
        17: 4,  # Dynamic
        18: 4,  # InvokeDynamic
        19: 2,  # Module
        20: 2,  # Package
    }
    while index < constant_pool_count:
        if offset >= len(payload):
            raise malformed
        tag = payload[offset]
        offset += 1
        if tag == 1:
            if offset + 2 > len(payload):
                raise malformed
            length = int.from_bytes(payload[offset:offset + 2], "big")
            offset += 2
            if offset + length > len(payload):
                raise malformed
            constants.add(payload[offset:offset + length])
            offset += length
        else:
            size = fixed_sizes.get(tag)
            if size is None or offset + size > len(payload):
                raise malformed
            offset += size
            if tag in (5, 6):
                index += 1
                if index >= constant_pool_count:
                    raise malformed
        index += 1
    return constants


def _configuration_observations(
    runtime_root: Path,
    declarations: list[Any],
    evidence: _EvidenceIndex,
) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for index, raw_declaration in enumerate(declarations):
        if not isinstance(raw_declaration, dict):
            raise AtlasWorldgenObservationError(
                f"configuration audit {index} must be an object"
            )
        config_relative = _safe_relative(
            raw_declaration.get("config_path"),
            f"configuration audit {index} config_path",
        )
        artifact_glob = _safe_relative(
            raw_declaration.get("artifact_glob"),
            f"configuration audit {index} artifact_glob",
            glob=True,
        )
        keys = raw_declaration.get("keys")
        if (
            not isinstance(keys, list)
            or not keys
            or not all(isinstance(key, str) and key for key in keys)
            or len(set(keys)) != len(keys)
        ):
            raise AtlasWorldgenObservationError(
                f"configuration audit {index} keys are invalid"
            )
        config_input = runtime_root / config_relative
        if config_input.is_symlink():
            raise AtlasWorldgenObservationError(
                f"configuration audit {index} config_path is a symbolic link"
            )
        config_path = config_input.resolve()
        if not config_path.is_relative_to(runtime_root):
            raise AtlasWorldgenObservationError(
                f"configuration audit {index} config_path escapes the runtime root"
            )
        if not config_path.exists():
            observations.extend({
                "key": key,
                "state": "unavailable",
                "configured": False,
                "config_evidence_id": None,
                "artifact_evidence_id": None,
                "configured_type": None,
                "configured_value": None,
            } for key in keys)
            continue
        config_id, config_raw = evidence.add(
            config_path,
            "forge-configuration",
            MAX_CONFIG_BYTES,
        )
        configured = _forge_config_values(
            _utf8(config_raw, "Forge configuration")
        )
        artifacts = _glob_files(
            runtime_root,
            [artifact_glob],
            label=f"configuration audit {index} artifact",
            maximum=2,
        )
        if len(artifacts) != 1:
            observations.extend({
                "key": key,
                "state": "binding-unresolved",
                "configured": key in configured,
                "config_evidence_id": config_id,
                "artifact_evidence_id": None,
                "configured_type": configured.get(key, {}).get("type"),
                "configured_value": configured.get(key, {}).get("value"),
            } for key in keys)
            continue
        artifact = artifacts[0]
        artifact_id, artifact_raw = evidence.add(
            artifact,
            "runtime-artifact",
            MAX_ARTIFACT_BYTES,
        )
        states = _class_literal_states(artifact, artifact_raw, keys)
        for key in keys:
            value = configured.get(key)
            observations.append({
                "key": key,
                "state": (
                    "class-literal-observed"
                    if states[key] == "observed"
                    else "class-literal-not-observed"
                ),
                "configured": value is not None,
                "config_evidence_id": config_id,
                "artifact_evidence_id": artifact_id,
                "configured_type": value.get("type") if value else None,
                "configured_value": value.get("value") if value else None,
            })
    return sorted(observations, key=lambda item: item["key"])


def _artifact_observations(
    runtime_root: Path,
    declarations: list[Any],
    evidence: _EvidenceIndex,
) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, declaration in enumerate(declarations):
        if not isinstance(declaration, dict):
            raise AtlasWorldgenObservationError(
                f"artifact inventory declaration {index} must be an object"
            )
        artifact_id = declaration.get("artifact_id")
        expected_sha256 = declaration.get("expected_sha256")
        required = declaration.get("required")
        if (
            not isinstance(artifact_id, str)
            or not artifact_id
            or artifact_id in seen_ids
            or not isinstance(expected_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None
            or type(required) is not bool
        ):
            raise AtlasWorldgenObservationError(
                f"artifact inventory declaration {index} is invalid"
            )
        seen_ids.add(artifact_id)
        matches = _glob_files(
            runtime_root,
            [declaration.get("artifact_glob")],
            label=f"artifact inventory {artifact_id}",
            maximum=8,
        )
        evidence_ids: list[str] = []
        observed_sha256: list[str] = []
        for match in matches:
            evidence_id, raw = evidence.add(
                match,
                "runtime-artifact",
                MAX_ARTIFACT_BYTES,
            )
            evidence_ids.append(evidence_id)
            observed_sha256.append(sha256(raw).hexdigest())
        if not matches:
            state = "unavailable"
        elif len(matches) > 1:
            state = "ambiguous"
        elif observed_sha256[0] == expected_sha256:
            state = "verified"
        else:
            state = "drifted"
        observations.append({
            "artifact_id": artifact_id,
            "required": required,
            "state": state,
            "expected_sha256": expected_sha256,
            "observed_sha256": observed_sha256,
            "evidence_ids": evidence_ids,
        })
    return observations


def _source_alignments(
    workspace_root: Path,
    runtime_root: Path,
    paths: list[Any],
    evidence: _EvidenceIndex,
) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for index, raw_path in enumerate(paths):
        relative = _safe_relative(
            raw_path,
            f"source alignment path {index}",
        )
        source_input = workspace_root / relative
        runtime_input = runtime_root / relative
        if source_input.is_symlink() or runtime_input.is_symlink():
            raise AtlasWorldgenObservationError(
                f"source alignment path {index} is a symbolic link"
            )
        source_path = source_input.resolve()
        runtime_path = runtime_input.resolve()
        if (
            not source_path.is_relative_to(workspace_root)
            or not runtime_path.is_relative_to(runtime_root)
        ):
            raise AtlasWorldgenObservationError(
                f"source alignment path {index} escapes its root"
            )
        source_id: str | None = None
        runtime_id: str | None = None
        source_sha: str | None = None
        runtime_sha: str | None = None
        if source_path.exists():
            source_id, source_raw = evidence.add(
                source_path,
                "project-source",
                MAX_SCRIPT_BYTES,
            )
            source_sha = sha256(source_raw).hexdigest()
        if runtime_path.exists():
            runtime_id, runtime_raw = evidence.add(
                runtime_path,
                "biometweaker-script",
                MAX_SCRIPT_BYTES,
            )
            runtime_sha = sha256(runtime_raw).hexdigest()
        state = (
            "unavailable"
            if source_sha is None or runtime_sha is None
            else "matched"
            if source_sha == runtime_sha
            else "drifted"
        )
        observations.append({
            "path": relative,
            "state": state,
            "source_evidence_id": source_id,
            "runtime_evidence_id": runtime_id,
        })
    return observations


def _guidance(
    spec: dict[str, Any],
    category: str,
    artifact_states: dict[str, str],
) -> dict[str, Any] | None:
    all_guidance = spec.get("guidance", {})
    if not isinstance(all_guidance, dict):
        raise AtlasWorldgenObservationError("worldgen audit guidance must be an object")
    item = all_guidance.get(category)
    if item is None:
        return None
    requirements = item.get("requires_artifacts", [])
    if (
        not isinstance(item, dict)
        or not isinstance(item.get("interpretation"), str)
        or not isinstance(item.get("developer_actions"), list)
        or not isinstance(requirements, list)
        or not all(
            isinstance(artifact_id, str) and artifact_id
            for artifact_id in requirements
        )
        or not all(
            isinstance(action, str) and action
            for action in item["developer_actions"]
        )
    ):
        raise AtlasWorldgenObservationError(
            f"worldgen audit guidance is invalid for {category}"
        )
    if any(
        artifact_states.get(artifact_id) != "verified"
        for artifact_id in requirements
    ):
        return None
    return {
        "profile_id": spec["profile_id"],
        "interpretation": item["interpretation"],
        "developer_actions": list(item["developer_actions"]),
    }


def _selector_expectation_observations(
    spec: dict[str, Any],
    bindings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    expectations = spec.get("selector_expectations", [])
    if not isinstance(expectations, list):
        raise AtlasWorldgenObservationError("selector_expectations must be a list")
    observations: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, expectation in enumerate(expectations):
        if not isinstance(expectation, dict):
            raise AtlasWorldgenObservationError(
                f"selector expectation {index} must be an object"
            )
        variable = expectation.get("variable")
        climate = expectation.get("climate")
        expected = expectation.get("selector")
        if (
            not isinstance(variable, str)
            or not variable
            or climate not in CLIMATES
            or expected not in {"forBiomes", "forAllBiomesExcept"}
        ):
            raise AtlasWorldgenObservationError(
                f"selector expectation {index} is invalid"
            )
        identity = (variable, climate)
        if identity in seen:
            raise AtlasWorldgenObservationError(
                f"duplicate selector expectation for {variable}/{climate}"
            )
        seen.add(identity)
        matches = [
            binding for binding in bindings
            if binding["variable"] == variable
            and binding["climate"] == climate
        ]
        if len(matches) > 1:
            raise AtlasWorldgenObservationError(
                f"multiple selector bindings observed for {variable}/{climate}"
            )
        if not matches:
            observations.append({
                "kind": "biometweaker-generation-selector",
                "variable": variable,
                "climate": climate,
                "expected_selector": expected,
                "state": "unobserved",
                "observed_selector": None,
                "evidence_id": None,
                "assignment_line": None,
                "generation_line": None,
            })
            continue
        binding = matches[0]
        observations.append({
            "kind": "biometweaker-generation-selector",
            "variable": variable,
            "climate": climate,
            "expected_selector": expected,
            "state": (
                "matched"
                if binding["selector"] == expected
                else "mismatched"
            ),
            "observed_selector": binding["selector"],
            "evidence_id": binding["evidence_id"],
            "assignment_line": binding["line"],
            "generation_line": binding["generation_line"],
        })
    return observations


def _finding(
    spec: dict[str, Any],
    category: str,
    confidence: str,
    summary: str,
    facts: dict[str, Any],
    evidence_ids: Iterable[str | None],
    artifact_observations: list[dict[str, Any]],
) -> dict[str, Any]:
    artifact_states = {
        item["artifact_id"]: item["state"]
        for item in artifact_observations
    }
    all_guidance = spec.get("guidance", {})
    guidance_spec = (
        all_guidance.get(category, {})
        if isinstance(all_guidance, dict)
        else {}
    )
    requirements = (
        guidance_spec.get("requires_artifacts", [])
        if isinstance(guidance_spec, dict)
        else []
    )
    binding_state = (
        "verified"
        if requirements and all(
            artifact_states.get(artifact_id) == "verified"
            for artifact_id in requirements
        )
        else "unverified"
        if requirements
        else "not-required"
    )
    finding_facts = dict(facts)
    if requirements:
        finding_facts["profile_artifact_binding"] = {
            "state": binding_state,
            "required_artifacts": list(requirements),
        }
    if binding_state == "unverified" and confidence == "confirmed":
        confidence = "observed"
    result: dict[str, Any] = {
        "category": category,
        "confidence": confidence,
        "summary": summary,
        "facts": finding_facts,
        "evidence_ids": sorted({
            evidence_id for evidence_id in evidence_ids
            if isinstance(evidence_id, str)
        }),
    }
    guidance = _guidance(spec, category, artifact_states)
    if guidance is not None:
        result["guidance"] = guidance
    return result


def _findings(
    spec: dict[str, Any],
    bindings: list[dict[str, Any]],
    selector_expectations: list[dict[str, Any]],
    registry: dict[str, Any],
    membership_index: dict[str, dict[str, Any]],
    log: dict[str, Any],
    configuration: list[dict[str, Any]],
    artifact_observations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    binding_index = {
        (binding["variable"], binding["climate"]): binding
        for binding in bindings
    }
    for expectation in selector_expectations:
        if expectation["state"] != "mismatched":
            continue
        binding = binding_index[
            (expectation["variable"], expectation["climate"])
        ]
        result.append(_finding(
            spec,
            "generation-selector-mismatch",
            "confirmed",
            (
                f"{binding['variable']} uses {binding['selector']} "
                f"before adding biomes to {binding['climate']}; "
                f"the profile expects {expectation['expected_selector']}."
            ),
            {
                "variable": binding["variable"],
                "climate": binding["climate"],
                "observed_selector": binding["selector"],
                "expected_selector": expectation["expected_selector"],
                "argument_count": len(binding["arguments"]),
                "assignment_line": binding["line"],
                "generation_line": binding["generation_line"],
            },
            [binding["evidence_id"]],
            artifact_observations,
        ))
    duplicates = registry["duplicate_memberships"]
    if duplicates:
        result.append(_finding(
            spec,
            "duplicate-biome-manager-membership",
            "confirmed",
            f"{len(duplicates)} biome/climate pairs contain duplicate entries.",
            {"count": len(duplicates)},
            (item["evidence_id"] for item in duplicates),
            artifact_observations,
        ))
    cross = registry["cross_climate_memberships"]
    if cross:
        result.append(_finding(
            spec,
            "cross-climate-biome-manager-membership",
            "observed",
            f"{len(cross)} biomes appear in more than one climate pool.",
            {"count": len(cross)},
            (item["evidence_id"] for item in cross),
            artifact_observations,
        ))
    non_overworld = registry["non_overworld_memberships"]
    if non_overworld:
        result.append(_finding(
            spec,
            "non-overworld-biome-climate-membership",
            "confirmed",
            (
                f"{len(non_overworld)} known non-Overworld biomes appear in "
                "Overworld climate pools."
            ),
            {
                "count": len(non_overworld),
                "resource_locations": [
                    item["resource_location"] for item in non_overworld
                ],
            },
            (item["evidence_id"] for item in non_overworld),
            artifact_observations,
        ))
    overlaps = []
    for unsupported in log["unsupported_biomes"]:
        membership = membership_index.get(unsupported["resource_location"])
        if membership and membership["climates"]:
            overlaps.append({
                "resource_location": unsupported["resource_location"],
                "climates": membership["climates"],
                "log_evidence_id": unsupported["evidence_id"],
                "report_evidence_id": membership["evidence_id"],
            })
    if overlaps:
        patch_observation = next(
            (
                item
                for item in configuration
                if item["key"] == "patchBiome" and item["configured"]
            ),
            None,
        )
        patch_biome = (
            patch_observation["configured_value"]
            if patch_observation is not None
            else None
        )
        result.append(_finding(
            spec,
            "rtg-unsupported-biome-climate-overlap",
            "confirmed",
            (
                f"{len(overlaps)} biomes logged as RTG-unsupported are also "
                "present in generation climate pools."
            ),
            {
                "count": len(overlaps),
                "configured_patch_biome": patch_biome,
                "resource_locations": [
                    item["resource_location"] for item in overlaps
                ],
            },
            (
                evidence_id
                for item in overlaps
                for evidence_id in (
                    item["log_evidence_id"],
                    item["report_evidence_id"],
                    (
                        patch_observation["config_evidence_id"]
                        if patch_observation is not None
                        else None
                    ),
                )
            ),
            artifact_observations,
        ))
    expected_event_reports = spec.get("expected_event_reports", [])
    if not isinstance(expected_event_reports, list):
        raise AtlasWorldgenObservationError(
            "expected_event_reports must be a list"
        )
    for index, expected in enumerate(expected_event_reports):
        if (
            not isinstance(expected, dict)
            or not all(
                isinstance(expected.get(field), str)
                and expected.get(field)
                for field in ("reporter", "generator", "event")
            )
        ):
            raise AtlasWorldgenObservationError(
                f"expected event report {index} is invalid"
            )
    for event in log["missing_events"]:
        exact_profile_match = any(
            isinstance(expected, dict)
            and expected.get("reporter") == event["reporter"]
            and expected.get("generator") == event["generator"]
            and expected.get("event") == event["event"]
            for expected in expected_event_reports
        )
        category = (
            "reported-missing-worldgen-event"
            if exact_profile_match
            else "unclassified-reported-missing-worldgen-event"
        )
        associated_noise = [
            warning for warning in log["noise_state_warnings"]
            if warning["evidence_id"] == event["evidence_id"]
            and event["line"] < warning["line"] <= event["line"] + 5
        ]
        later_ready = next(
            (
                marker for marker in log["server_ready_markers"]
                if marker["evidence_id"] == event["evidence_id"]
                and marker["line"] > event["line"]
            ),
            None,
        )
        result.append(_finding(
            spec,
            category,
            "confirmed",
            (
                f"{event['reporter']} reported that {event['generator']} had "
                f"not triggered {event['event']} during initialization."
            ),
            {
                "reporter": event["reporter"],
                "severity": event["severity"],
                "generator": event["generator"],
                "event": event["event"],
                "line": event["line"],
                "associated_noise_state_warnings": [
                    {
                        "dimension_id": warning["dimension_id"],
                        "dimension_name": warning["dimension_name"],
                        "severity": warning["severity"],
                        "line": warning["line"],
                        "consequence_observed": warning[
                            "consequence_observed"
                        ],
                        "consequence_line": warning["consequence_line"],
                    }
                    for warning in associated_noise
                ],
                "later_server_ready_line": (
                    later_ready["line"]
                    if later_ready is not None
                    else None
                ),
            },
            [event["evidence_id"]],
            artifact_observations,
        ))
    absent_keys = [
        item for item in configuration
        if item["configured"]
        and item["state"] == "class-literal-not-observed"
    ]
    if absent_keys:
        result.append(_finding(
            spec,
            "configuration-key-class-literal-not-observed",
            "observed",
            (
                f"{len(absent_keys)} configured keys have no exact literal in "
                "the installed artifact's class entries."
            ),
            {"keys": [item["key"] for item in absent_keys]},
            (
                evidence_id
                for item in absent_keys
                for evidence_id in (
                    item["config_evidence_id"],
                    item["artifact_evidence_id"],
                )
            ),
            artifact_observations,
        ))
    return result


def observe_runtime_worldgen(
    workspace_root: Path | str,
    *,
    runtime_root: Path | str,
    profile: dict[str, Any],
) -> dict[str, Any]:
    """Normalize and derive retained worldgen evidence for one profile."""

    workspace = Path(workspace_root).expanduser().resolve()
    runtime_input = Path(runtime_root).expanduser()
    if runtime_input.is_symlink():
        raise AtlasWorldgenObservationError(
            f"runtime root cannot be a symbolic link: {runtime_root}"
        )
    runtime = runtime_input.resolve()
    if not runtime.is_dir():
        raise AtlasWorldgenObservationError(
            f"runtime root is not a directory: {runtime_root}"
        )
    if (
        not isinstance(profile, dict)
        or profile.get("format")
        != "workbench-runtime-worldgen-audit-profile-v1"
        or profile.get("schema_version") != 1
        or not isinstance(profile.get("profile_id"), str)
    ):
        raise AtlasWorldgenObservationError(
            "worldgen audit profile has an unsupported shape"
        )
    spec = profile
    discovery = spec.get("discovery")
    if not isinstance(discovery, dict):
        raise AtlasWorldgenObservationError(
            "worldgen audit profile lacks discovery rules"
        )
    for key in ("script_globs", "biome_report_globs", "log_globs"):
        if not isinstance(discovery.get(key), list):
            raise AtlasWorldgenObservationError(
                f"worldgen audit discovery {key} must be a list"
            )
    scripts = _glob_files(
        runtime,
        discovery.get("script_globs", []),
        label="BiomeTweaker script",
        maximum=MAX_SCRIPTS,
    )
    reports = _glob_files(
        runtime,
        discovery.get("biome_report_globs", []),
        label="BiomeTweaker biome report",
        maximum=MAX_BIOME_REPORTS,
    )
    logs = _glob_files(
        runtime,
        discovery.get("log_globs", []),
        label="runtime log",
        maximum=MAX_LOGS,
    )
    non_overworld_values = spec.get("known_non_overworld_biomes", [])
    if (
        not isinstance(non_overworld_values, list)
        or not all(isinstance(value, str) and value for value in non_overworld_values)
        or len(set(non_overworld_values)) != len(non_overworld_values)
    ):
        raise AtlasWorldgenObservationError(
            "known_non_overworld_biomes must be a string list"
        )
    evidence = _EvidenceIndex()
    bindings = _script_bindings(scripts, evidence)
    selector_expectations = _selector_expectation_observations(
        spec,
        bindings,
    )
    registry, membership_index = _biome_registry(
        reports,
        evidence,
        set(non_overworld_values),
    )
    log = _log_observations(logs, evidence)
    raw_config_audits = discovery.get("configuration_literal_audits", [])
    if not isinstance(raw_config_audits, list):
        raise AtlasWorldgenObservationError(
            "configuration_literal_audits must be a list"
        )
    configuration = _configuration_observations(
        runtime,
        raw_config_audits,
        evidence,
    )
    raw_artifact_inventory = discovery.get("artifact_inventory", [])
    if not isinstance(raw_artifact_inventory, list):
        raise AtlasWorldgenObservationError("artifact_inventory must be a list")
    artifacts = _artifact_observations(
        runtime,
        raw_artifact_inventory,
        evidence,
    )
    raw_alignment_paths = discovery.get("source_alignment_paths", [])
    if not isinstance(raw_alignment_paths, list):
        raise AtlasWorldgenObservationError(
            "source_alignment_paths must be a list"
        )
    source_alignments = _source_alignments(
        workspace,
        runtime,
        raw_alignment_paths,
        evidence,
    )
    findings = _findings(
        spec,
        bindings,
        selector_expectations,
        registry,
        membership_index,
        log,
        configuration,
        artifacts,
    )
    limitations = [
        (
            "The user-selected runtime root is not bound to a Workbench launch "
            "receipt or materialization identity; consumed files are exact, but "
            "their relationship to the selected project is unverified."
        ),
        (
            "Files discovered in a mutable runtime root are not proven to be "
            "contemporaneous with one another; cross-file joins are retained "
            "correlations unless a capture receipt binds them."
        ),
        (
            "This offline audit observes configuration, registry reports, logs, "
            "and class literals; it does not inspect generated chunks or prove "
            "which finding caused a reported visual symptom."
        ),
        (
            "A missing class-file literal is evidence of configuration drift, "
            "not proof that a key is unbound; reflection or transformers may "
            "supply an indirect binding."
        ),
        (
            "Log severity and wording do not establish process failure or "
            "capture causality; later server-ready markers are preserved when "
            "observed."
        ),
    ]
    if not reports:
        limitations.append(
            "No BiomeTweaker biome reports were available, so effective climate membership was not observed."
        )
    if not logs:
        limitations.append(
            "No runtime log was available, so RTG warnings and event-contract reports were not observed."
        )
    expected_event_report_configured = bool(
        spec.get("expected_event_reports", [])
    )
    expected_event_report_observed = any(
        finding["category"] == "reported-missing-worldgen-event"
        for finding in findings
    )
    if (
        expected_event_report_configured
        and logs
        and not expected_event_report_observed
    ):
        limitations.append(
            "No profile-matching missing-event report was observed; absence of "
            "that log message is not proof that the expected event fired."
        )
        if not log["world_seeds"] and not log["server_ready_markers"]:
            limitations.append(
                "The retained logs contain neither an RTG world-seed marker "
                "nor a server-ready marker, so they do not evidence RTG world "
                "entry for evaluating the expected event boundary."
            )
    unobserved_expectations = [
        item for item in selector_expectations
        if item["state"] == "unobserved"
    ]
    if unobserved_expectations:
        limitations.append(
            "One or more required profile selector expectations were not observed; the supported V1 parser may not cover the retained script shape."
        )
    unverified_required_artifacts = [
        item for item in artifacts
        if item["required"] and item["state"] != "verified"
    ]
    if unverified_required_artifacts:
        limitations.append(
            "One or more required profile artifacts were unavailable, ambiguous, or drifted; dependent profile guidance was suppressed and confidence was downgraded."
        )
    unresolved_configuration_audits = [
        item for item in configuration
        if item["state"] in ("unavailable", "binding-unresolved")
    ]
    if unresolved_configuration_audits:
        limitations.append(
            "One or more declared configuration literal audits were unavailable "
            "or lacked exactly one matching artifact; configuration binding "
            "could not be evaluated."
        )
    if any(item["state"] == "drifted" for item in source_alignments):
        limitations.append(
            "At least one audited runtime input differs from the selected project source; findings describe the runtime evidence, not the current source file."
        )
    if findings:
        state = "findings-observed"
    elif not evidence.records:
        state = "insufficient-evidence"
    elif (
        unobserved_expectations
        or not reports
        or not logs
        or unverified_required_artifacts
        or unresolved_configuration_audits
    ):
        state = "inconclusive"
    else:
        state = "no-findings-observed"
    return {
        "authority": {
            "classification": "atlas-derived-observation",
            "normative": False,
            "atlas_publication": False,
            "sentinel_policy_finding": False,
        },
        "state": state,
        "evidence": evidence.records,
        "script_bindings": bindings,
        "profile_expectations": selector_expectations,
        "artifact_observations": artifacts,
        "source_alignments": source_alignments,
        "biome_registry": registry,
        "log_observations": log,
        "configuration_observations": configuration,
        "findings": findings,
        "limitations": limitations,
    }
