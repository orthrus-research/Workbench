"""Projection of static archive facts into the topology receipt contract."""

from __future__ import annotations

import hashlib
from typing import Any, Iterable, Mapping, Sequence

from .mixin_topology import (
    ArtifactScanError,
    RECEIPT_FORMAT,
    RECEIPT_ID_PREFIX,
    _embedded_package_overlaps,
    _owner_id_collisions,
    canonical_json_bytes,
)


CONTRACT_ID = (
    "WORKBENCH-PROJECT-INTELLIGENCE-MIXIN-COMPONENT-TOPOLOGY-RECEIPT-V1"
)
CANONICALIZATION_ID = "workbench-canonical-json-v1"

_ID_PREFIXES = {
    "artifact_id": "workbench-mixin-artifact:sha256:",
    "binding_id": "workbench-mixin-binding:sha256:",
    "compatibility_id": "workbench-mixin-compatibility:sha256:",
    "component_id": "workbench-mixin-component:sha256:",
    "configuration_id": "workbench-mixin-configuration:sha256:",
    "evidence_id": "workbench-mixin-evidence:sha256:",
    "finding_id": "workbench-mixin-finding:sha256:",
    "registration_id": "workbench-mixin-registration:sha256:",
    "scope_id": "workbench-mixin-scope:sha256:",
}

_POLICY = {
    "archive_execution": False,
    "candidate_binding": "not-requested",
    "class_analysis": "registration-interface-header-only",
    "compatibility_resource": "cleanmix_version_compatibility.json",
    "manifest_continuations": True,
    "policy": "offline-mixin-artifact-topology-v1",
    "runtime_observation": False,
    "unresolved_count": "finding-records-by-kind",
    "unresolved_finding_kinds": [
        "compatibility",
        "conflict",
        "packaging",
        "unresolved",
    ],
}

_SUMMARY_LIMITATIONS = [
    "Static archive declarations do not prove runtime preparation or application.",
    "Mixin target and operation annotations are outside this bounded scanner policy.",
    "Owner IDs are collision inputs and require runtime inventory corroboration.",
]

_BOUNDARIES = {
    "apply_begin_proves_apply_completion": False,
    "atlas_remains_interpretation_authority": True,
    "candidate_lock_v1_mutated": False,
    "candidate_lock_v1_role": "exact-external-binding",
    "content_address_is_signature": False,
    "intermediate_bytecode_is_final_truth": False,
    "logs_are_final_truth": False,
    "static_declaration_proves_runtime_application": False,
}

_ENDPOINT_ID_PREFIXES = {
    "artifact": _ID_PREFIXES["artifact_id"],
    "component": _ID_PREFIXES["component_id"],
    "configuration": _ID_PREFIXES["configuration_id"],
    "mixin-binding": _ID_PREFIXES["binding_id"],
}

_ALL_CONTENT_ID_PREFIXES = tuple(_ID_PREFIXES.values()) + (RECEIPT_ID_PREFIX,)


def _digest(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _identified(id_field: str, material: Mapping[str, Any]) -> dict[str, Any]:
    record = dict(material)
    record[id_field] = _ID_PREFIXES[id_field] + _digest(material)
    return record


def _entry_index(scan: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["path"]: row for row in scan["member_inventory"]}


def _manifest_lookup(scan: Mapping[str, Any]) -> dict[str, str]:
    return {
        row["name"].casefold(): row["value"]
        for row in scan["manifest"]["main_attributes"]
    }


class _EvidenceCatalog:
    def __init__(self) -> None:
        self._rows: dict[str, dict[str, Any]] = {}

    def add(
        self,
        *,
        kind: str,
        collection: str,
        admission: str,
        truth_scope: str,
        locator: str,
        content_sha256: str | None,
        size: int | None,
        source_ids: Iterable[str] = (),
        limitations: Iterable[str] = (),
    ) -> str:
        material = {
            "admission_state": admission,
            "collection_state": collection,
            "content_sha256": content_sha256,
            "evidence_kind": kind,
            "limitations": sorted(set(limitations)),
            "locator": locator,
            "size": size,
            "source_ids": sorted(set(source_ids)),
            "truth_scope": truth_scope,
        }
        row = _identified("evidence_id", material)
        self._rows[row["evidence_id"]] = row
        return row["evidence_id"]

    def rows(self) -> list[dict[str, Any]]:
        return [self._rows[key] for key in sorted(self._rows)]


class _ComponentCatalog:
    def __init__(self) -> None:
        self._rows: dict[str, dict[str, Any]] = {}
        self._keys: dict[tuple[str, str, str], str] = {}

    def add(
        self,
        *,
        key: tuple[str, str, str],
        artifact_id: str,
        kind: str,
        logical_name: str,
        identity_state: str,
        class_name: str | None,
        entry_path: str | None,
        entry_sha256: str | None,
        service_interface: str | None,
        capabilities: Iterable[str],
        evidence_ids: Iterable[str],
    ) -> str:
        material = {
            "artifact_id": artifact_id,
            "capabilities": sorted(set(capabilities)),
            "component_kind": kind,
            "evidence_ids": sorted(set(evidence_ids)),
            "identity_state": identity_state,
            "implementation": {
                "class_name": class_name,
                "entry_path": entry_path,
                "entry_sha256": entry_sha256,
                "service_interface": service_interface,
            },
            "logical_name": logical_name,
            "version_claims": [],
        }
        previous_id = self._keys.get(key)
        if previous_id is not None:
            previous = self._rows[previous_id]
            previous_material = {
                field: value
                for field, value in previous.items()
                if field != "component_id"
            }
            for field in (
                "artifact_id",
                "component_kind",
                "identity_state",
                "implementation",
                "logical_name",
                "version_claims",
            ):
                if previous_material[field] != material[field]:
                    raise ValueError(
                        f"component key {key!r} has conflicting {field}"
                    )
            material["capabilities"] = sorted(
                set(previous_material["capabilities"]) | set(material["capabilities"])
            )
            material["evidence_ids"] = sorted(
                set(previous_material["evidence_ids"]) | set(material["evidence_ids"])
            )
            del self._rows[previous_id]
        row = _identified("component_id", material)
        identity = row["component_id"]
        self._rows[identity] = row
        self._keys[key] = identity
        return identity

    def get(self, key: tuple[str, str, str]) -> str:
        return self._keys[key]

    def maybe(self, key: tuple[str, str, str]) -> str | None:
        return self._keys.get(key)

    def rows(self) -> list[dict[str, Any]]:
        return [self._rows[key] for key in sorted(self._rows)]


class _FindingCatalog:
    def __init__(self) -> None:
        self._rows: dict[str, dict[str, Any]] = {}

    def add(
        self,
        *,
        kind: str,
        severity: str,
        state: str,
        subjects: Iterable[str],
        statement: str,
        evidence_ids: Iterable[str],
        limitations: Iterable[str] = (),
    ) -> str:
        material = {
            "evidence_ids": sorted(set(evidence_ids)),
            "finding_kind": kind,
            "limitations": sorted(set(limitations)),
            "severity": severity,
            "state": state,
            "statement": statement,
            "subject_ids": sorted(set(subjects)),
        }
        row = _identified("finding_id", material)
        self._rows[row["finding_id"]] = row
        return row["finding_id"]

    def rows(self) -> list[dict[str, Any]]:
        return [self._rows[key] for key in sorted(self._rows)]


def _entry_evidence(
    evidence: _EvidenceCatalog,
    scan: Mapping[str, Any],
    path: str,
    kind: str,
    *,
    missing_ok: bool = False,
) -> str:
    entries = _entry_index(scan)
    entry = entries.get(path)
    source = f"artifact-bytes:sha256:{scan['identity']['sha256']}"
    if entry is None:
        if not missing_ok:
            raise KeyError(path)
        return evidence.add(
            kind=kind,
            collection="missing",
            admission="supporting-only",
            truth_scope="static-declaration",
            locator=f"{scan['label']}!/{path}",
            content_sha256=None,
            size=None,
            source_ids=[source],
            limitations=[
                "Absence is bounded to the exact scanned archive bytes."
            ],
        )
    return evidence.add(
        kind=kind,
        collection="exact",
        admission="admitted",
        truth_scope="static-declaration",
        locator=f"{scan['label']}!/{path}",
        content_sha256=entry["sha256"],
        size=entry["size_bytes"],
        source_ids=[source],
    )


def _component_for_entry(
    *,
    components: _ComponentCatalog,
    evidence: _EvidenceCatalog,
    scan: Mapping[str, Any],
    artifact_id: str,
    key_kind: str,
    logical_name: str,
    component_kind: str,
    entry_path: str,
    class_name: str | None,
    evidence_kind: str = "jar-entry-bytes",
    service_interface: str | None = None,
    capabilities: Iterable[str] = (),
    declaring_evidence: Iterable[str] = (),
) -> str:
    entry = _entry_index(scan).get(entry_path)
    if entry is None:
        return components.add(
            key=(scan["identity"]["sha256"], key_kind, logical_name),
            artifact_id=artifact_id,
            kind=component_kind,
            logical_name=logical_name,
            identity_state="declared-only",
            class_name=class_name,
            entry_path=entry_path,
            entry_sha256=None,
            service_interface=service_interface,
            capabilities=capabilities,
            evidence_ids=declaring_evidence,
        )
    entry_evidence = _entry_evidence(evidence, scan, entry_path, evidence_kind)
    return components.add(
        key=(scan["identity"]["sha256"], key_kind, logical_name),
        artifact_id=artifact_id,
        kind=component_kind,
        logical_name=logical_name,
        identity_state="exact-entry",
        class_name=class_name,
        entry_path=entry_path,
        entry_sha256=entry["sha256"],
        service_interface=service_interface,
        capabilities=capabilities,
        evidence_ids=[*declaring_evidence, entry_evidence],
    )


def _phase(value: str | None) -> tuple[str, str | None]:
    if value is None:
        return "UNSPECIFIED", None
    normalized = value.strip().upper()
    for known in ("PREINIT", "INIT", "DEFAULT"):
        if normalized in {known, f"@ENV({known})"}:
            return known, None
    return "CUSTOM", value


def _artifact_records(
    scans: Sequence[Mapping[str, Any]], evidence: _EvidenceCatalog
) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, str]]:
    records: list[dict[str, Any]] = []
    artifact_ids: dict[str, str] = {}
    artifact_evidence: dict[str, str] = {}
    version_attributes = (
        "implementation-version",
        "specification-version",
        "bundle-version",
        "automatic-module-name",
    )
    for scan in scans:
        digest = scan["identity"]["sha256"]
        exact_evidence = evidence.add(
            kind="artifact-bytes",
            collection="exact",
            admission="admitted",
            truth_scope="artifact-identity",
            locator=f"artifact:{scan['label']}",
            content_sha256=digest,
            size=scan["identity"]["size_bytes"],
            limitations=["A content address is not a signature."],
        )
        artifact_evidence[digest] = exact_evidence
        manifest_evidence: str | None = None
        if scan["manifest"]["present"]:
            manifest_evidence = _entry_evidence(
                evidence, scan, scan["manifest"]["path"], "manifest"
            )
        manifest = _manifest_lookup(scan)
        claims = []
        for attribute in version_attributes:
            if attribute not in manifest:
                continue
            claims.append(
                {
                    "evidence_ids": [manifest_evidence],
                    "namespace": f"jar-manifest:{attribute}",
                    "state": "observed-manifest",
                    "value": manifest[attribute],
                }
            )
        material = {
            "artifact_kind": (
                "jar" if scan["label"].casefold().endswith(".jar") else "zip"
            ),
            "coordinate": None,
            "coordinate_state": "not-applicable",
            "evidence_ids": [exact_evidence],
            "file_name": scan["label"],
            "sha256": digest,
            "size": scan["identity"]["size_bytes"],
            "source_binding": {
                "repository": None,
                "revision": None,
                "state": "not-applicable",
                "tree": None,
            },
            "version_claims": sorted(
                claims,
                key=lambda row: (
                    row["namespace"],
                    "" if row["value"] is None else row["value"],
                    row["state"],
                ),
            ),
        }
        row = _identified("artifact_id", material)
        records.append(row)
        artifact_ids[digest] = row["artifact_id"]
    return (
        sorted(records, key=lambda row: row["artifact_id"]),
        artifact_ids,
        artifact_evidence,
    )


def _create_components(
    scans: Sequence[Mapping[str, Any]],
    artifact_ids: Mapping[str, str],
    artifact_evidence: Mapping[str, str],
    evidence: _EvidenceCatalog,
) -> _ComponentCatalog:
    components = _ComponentCatalog()
    for scan in scans:
        digest = scan["identity"]["sha256"]
        artifact_id = artifact_ids[digest]
        exact_artifact_evidence = artifact_evidence[digest]
        components.add(
            key=(digest, "artifact-container", scan["label"]),
            artifact_id=artifact_id,
            kind="other",
            logical_name=f"artifact-container:{scan['label']}",
            identity_state="artifact-bound",
            class_name=None,
            entry_path=None,
            entry_sha256=None,
            service_interface=None,
            capabilities=["offline-artifact-inspection"],
            evidence_ids=[exact_artifact_evidence],
        )
        manifest_evidence = (
            [
                _entry_evidence(
                    evidence, scan, scan["manifest"]["path"], "manifest"
                )
            ]
            if scan["manifest"]["present"]
            else [exact_artifact_evidence]
        )
        for config in scan["mixin_configs"]:
            config_evidence = _entry_evidence(
                evidence, scan, config["path"], "mixin-config-resource"
            )
            _component_for_entry(
                components=components,
                evidence=evidence,
                scan=scan,
                artifact_id=artifact_id,
                key_kind="mixin-config",
                logical_name=config["path"],
                component_kind="mixin-config",
                entry_path=config["path"],
                class_name=None,
                evidence_kind="mixin-config-resource",
                declaring_evidence=[config_evidence],
                capabilities=["declares-mixins"],
            )
            for mixin in config["mixins"]:
                _component_for_entry(
                    components=components,
                    evidence=evidence,
                    scan=scan,
                    artifact_id=artifact_id,
                    key_kind="mixin-class",
                    logical_name=mixin["class_name"],
                    component_kind="mixin-class",
                    entry_path=mixin["class_member"],
                    class_name=mixin["class_name"],
                    declaring_evidence=[config_evidence],
                    capabilities=[f"declared-environment:{mixin['environment']}"],
                )
            plugin = config["plugin"]
            if plugin is not None:
                _component_for_entry(
                    components=components,
                    evidence=evidence,
                    scan=scan,
                    artifact_id=artifact_id,
                    key_kind="config-plugin",
                    logical_name=plugin["class_name"],
                    component_kind="config-plugin",
                    entry_path=plugin["class_member"],
                    class_name=plugin["class_name"],
                    declaring_evidence=[config_evidence],
                    capabilities=["mixin-config-plugin"],
                )
        for refmap in scan["refmaps"]:
            declaring = [
                _entry_evidence(
                    evidence,
                    scan,
                    path,
                    "mixin-config-resource",
                )
                for path in refmap["declared_by"]
            ]
            _component_for_entry(
                components=components,
                evidence=evidence,
                scan=scan,
                artifact_id=artifact_id,
                key_kind="reference-mapper",
                logical_name=refmap["path"],
                component_kind="reference-mapper",
                entry_path=refmap["path"],
                class_name=None,
                evidence_kind="refmap",
                declaring_evidence=declaring or [exact_artifact_evidence],
                capabilities=["mixin-refmap-resource"],
            )
        for package in scan["embedded_packages"]:
            if not package["present"]:
                continue
            kind = "mixin-runtime" if package["component"] == "mixin" else "other"
            components.add(
                key=(digest, "embedded-package", package["component"]),
                artifact_id=artifact_id,
                kind=kind,
                logical_name=f"embedded-package:{package['component']}",
                identity_state="artifact-bound",
                class_name=None,
                entry_path=None,
                entry_sha256=None,
                service_interface=None,
                capabilities=[
                    f"class-count:{package['class_count']}",
                    f"package-prefix:{package['prefix']}",
                ],
                evidence_ids=[exact_artifact_evidence],
            )
        for route in scan["registration_routes"]:
            if route["kind"] == "manifest-mixin-configs":
                continue
            for value in route["values"]:
                if route["kind"] in {
                    "forge-core-plugin",
                    "mixinbooter-early-loader",
                    "mixinbooter-late-loader",
                }:
                    key_kind = "host-loader"
                    component_kind = "host-loader"
                    service_interface = None
                elif route["kind"] == "manifest-mixin-connector":
                    key_kind = "connector"
                    component_kind = "connector"
                    service_interface = None
                elif route["kind"] == "java-service-provider":
                    service_interface = route["source"].split("/", 2)[-1]
                    key_kind = f"service-provider:{service_interface}"
                    component_kind = _service_component_kind(service_interface)
                else:
                    key_kind = "registration-class"
                    component_kind = "other"
                    service_interface = None
                entry_path = value.replace(".", "/") + ".class"
                _component_for_entry(
                    components=components,
                    evidence=evidence,
                    scan=scan,
                    artifact_id=artifact_id,
                    key_kind=key_kind,
                    logical_name=value,
                    component_kind=component_kind,
                    entry_path=entry_path,
                    class_name=value,
                    service_interface=service_interface,
                    declaring_evidence=manifest_evidence,
                    capabilities=[f"registration-route:{route['kind']}"],
                )
    return components


def _service_component_kind(service: str) -> str:
    if service.endswith("IMixinService"):
        return "mixin-service"
    if service.endswith("IGlobalPropertyService"):
        return "global-property-service"
    if service.endswith("Processor") or "processing" in service.casefold():
        return "annotation-processor"
    return "other"


def _registration_record(
    *,
    kind: str,
    source_kind: str,
    source_id: str,
    target_kind: str,
    target_id: str,
    declaration_state: str,
    phase: str,
    evidence_ids: Iterable[str],
) -> dict[str, Any]:
    return _identified(
        "registration_id",
        {
            "declaration_state": declaration_state,
            "evidence_ids": sorted(set(evidence_ids)),
            "ordinal": None,
            "phase": phase,
            "registration_kind": kind,
            "runtime_state": "not-observed",
            "source": {"id": source_id, "kind": source_kind},
            "target": {"id": target_id, "kind": target_kind},
        },
    )


def _create_registrations(
    scans: Sequence[Mapping[str, Any]],
    artifact_ids: Mapping[str, str],
    components: _ComponentCatalog,
    evidence: _EvidenceCatalog,
) -> tuple[list[dict[str, Any]], dict[tuple[str, str], list[str]]]:
    rows: dict[str, dict[str, Any]] = {}
    config_routes: dict[tuple[str, str], list[str]] = {}
    for scan in scans:
        digest = scan["identity"]["sha256"]
        artifact_id = artifact_ids[digest]
        manifest_evidence = (
            _entry_evidence(
                evidence, scan, scan["manifest"]["path"], "manifest"
            )
            if scan["manifest"]["present"]
            else None
        )
        configs_by_path = {
            config["path"]: config for config in scan["mixin_configs"]
        }
        for route in scan["registration_routes"]:
            for value in route["values"]:
                if route["kind"] == "manifest-mixin-configs":
                    target = components.get((digest, "mixin-config", value))
                    phase, _ = _phase(configs_by_path[value]["target_phase"])
                    registration_kind = "manifest-config"
                    source_kind = "artifact"
                    source_id = artifact_id
                    target_kind = "component"
                    target_id = target
                    route_evidence = [manifest_evidence]
                elif route["kind"] == "manifest-mixin-connector":
                    target_id = components.get((digest, "connector", value))
                    phase = "UNRESOLVED"
                    registration_kind = "manifest-connector"
                    source_kind = "artifact"
                    source_id = artifact_id
                    target_kind = "component"
                    route_evidence = [manifest_evidence]
                elif route["kind"] == "java-service-provider":
                    service = route["source"].split("/", 2)[-1]
                    target_id = components.get(
                        (digest, f"service-provider:{service}", value)
                    )
                    phase = "UNRESOLVED"
                    registration_kind = "service-provider"
                    source_kind = "artifact"
                    source_id = artifact_id
                    target_kind = "component"
                    route_evidence = [
                        _entry_evidence(
                            evidence,
                            scan,
                            route["source"],
                            "service-provider-file",
                        )
                    ]
                elif route["kind"] in {
                    "mixinbooter-early-loader",
                    "mixinbooter-late-loader",
                }:
                    source_id = components.get((digest, "host-loader", value))
                    target_id = "UNRESOLVED"
                    phase = (
                        "PREINIT"
                        if route["kind"] == "mixinbooter-early-loader"
                        else "DEFAULT"
                    )
                    registration_kind = "programmatic-config"
                    source_kind = "component"
                    target_kind = "runtime-phase"
                    class_path = value.replace(".", "/") + ".class"
                    route_evidence = [
                        _entry_evidence(
                            evidence, scan, class_path, "jar-entry-bytes"
                        )
                    ]
                else:
                    key_kind = (
                        "host-loader"
                        if route["kind"] == "forge-core-plugin"
                        else "registration-class"
                    )
                    target_id = components.get((digest, key_kind, value))
                    phase = "UNRESOLVED"
                    registration_kind = "other"
                    source_kind = "artifact"
                    source_id = artifact_id
                    target_kind = "component"
                    route_evidence = [manifest_evidence]
                row = _registration_record(
                    kind=registration_kind,
                    source_kind=source_kind,
                    source_id=source_id,
                    target_kind=target_kind,
                    target_id=target_id,
                    declaration_state="declared-exact",
                    phase=phase,
                    evidence_ids=[item for item in route_evidence if item is not None],
                )
                rows[row["registration_id"]] = row
                if route["kind"] == "manifest-mixin-configs":
                    config_routes.setdefault((digest, value), []).append(
                        row["registration_id"]
                    )
        for config in scan["mixin_configs"]:
            config_component = components.get(
                (digest, "mixin-config", config["path"])
            )
            config_evidence = _entry_evidence(
                evidence, scan, config["path"], "mixin-config-resource"
            )
            phase, _ = _phase(config["target_phase"])
            for mixin in config["mixins"]:
                mixin_component = components.get(
                    (digest, "mixin-class", mixin["class_name"])
                )
                row = _registration_record(
                    kind="config-mixin",
                    source_kind="component",
                    source_id=config_component,
                    target_kind="component",
                    target_id=mixin_component,
                    declaration_state="declared-exact",
                    phase=phase,
                    evidence_ids=[config_evidence],
                )
                rows[row["registration_id"]] = row
    return [rows[key] for key in sorted(rows)], config_routes


def _owner(scan: Mapping[str, Any]) -> dict[str, Any]:
    owners = scan["owner_id_inputs"]
    if len(owners) != 1:
        return {"canonical_id": None, "raw_id": None, "state": "unresolved"}
    owner = owners[0]
    if owner["source"] == "artifact-file-name-fallback":
        state = "fallback-container-name"
    else:
        state = "declared-only"
    return {
        "canonical_id": owner["normalized_owner_id"],
        "raw_id": owner["raw_owner_id"],
        "state": state,
    }


def _create_configurations(
    scans: Sequence[Mapping[str, Any]],
    components: _ComponentCatalog,
    config_routes: Mapping[tuple[str, str], Sequence[str]],
    evidence: _EvidenceCatalog,
) -> tuple[list[dict[str, Any]], dict[tuple[str, str], str]]:
    rows: list[dict[str, Any]] = []
    ids: dict[tuple[str, str], str] = {}
    for scan in scans:
        digest = scan["identity"]["sha256"]
        refmaps = {row["path"]: row for row in scan["refmaps"]}
        entries = _entry_index(scan)
        for config in scan["mixin_configs"]:
            component_id = components.get(
                (digest, "mixin-config", config["path"])
            )
            config_evidence = _entry_evidence(
                evidence, scan, config["path"], "mixin-config-resource"
            )
            plugin_id = None
            if config["plugin"] is not None:
                plugin_id = components.get(
                    (digest, "config-plugin", config["plugin"]["class_name"])
                )
            refmap_path = config["refmap_path"]
            if refmap_path is None:
                refmap = {
                    "resource": None,
                    "resource_sha256": None,
                    "state": "not-applicable",
                    "wrapper_component_id": None,
                }
            else:
                refmap_scan = refmaps[refmap_path]
                refmap = {
                    "resource": refmap_path,
                    "resource_sha256": refmap_scan["sha256"],
                    "state": "exact" if refmap_scan["present"] else "missing",
                    "wrapper_component_id": None,
                }
            phase, custom_phase = _phase(config["target_phase"])
            entry = entries[config["path"]]
            material = {
                "component_id": component_id,
                "custom_phase_name": custom_phase,
                "declared_phase": phase,
                "evidence_ids": [config_evidence],
                "minimum_runtime_version": config["min_version"],
                "mixin_package": config["package"],
                "mixin_priority": config["mixin_priority"],
                "owner": _owner(scan),
                "plugin_component_id": plugin_id,
                "priority": config["priority"],
                "refmap": refmap,
                "registration_ids": sorted(
                    set(config_routes.get((digest, config["path"]), []))
                ),
                "required": config["required"],
                "required_features": config["required_features"],
                "resource": {
                    "path": config["path"],
                    "sha256": entry["sha256"],
                    "size": entry["size_bytes"],
                    "state": "exact",
                },
                "semantic_options_sha256": config["semantic_options_sha256"],
            }
            row = _identified("configuration_id", material)
            rows.append(row)
            ids[(digest, config["path"])] = row["configuration_id"]
    return sorted(rows, key=lambda row: row["configuration_id"]), ids


def _resource_identity(
    scan: Mapping[str, Any], *, present: bool
) -> dict[str, Any]:
    path = scan["compatibility_metadata"]["path"]
    entry = _entry_index(scan).get(path)
    return {
        "path": path,
        "sha256": entry["sha256"] if entry is not None else None,
        "size": entry["size_bytes"] if entry is not None else None,
        "state": "exact" if present else "missing",
    }


def _create_compatibility_epochs(
    scans: Sequence[Mapping[str, Any]],
    artifact_ids: Mapping[str, str],
    components: _ComponentCatalog,
    evidence: _EvidenceCatalog,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for scan in scans:
        if not scan["mixin_configs"]:
            continue
        digest = scan["identity"]["sha256"]
        metadata = scan["compatibility_metadata"]
        metadata_evidence = _entry_evidence(
            evidence,
            scan,
            metadata["path"],
            "compatibility-metadata",
            missing_ok=True,
        )
        resource = _resource_identity(scan, present=metadata["present"])
        if not metadata["present"]:
            material = {
                "declared_epoch": None,
                "effective_epoch": None,
                "evidence_ids": [metadata_evidence],
                "fallback_epoch": None,
                "latest_supported_epoch": None,
                "metadata_resource": resource,
                "resolution_state": "absent-metadata",
                "scope_key": metadata["path"],
                "scope_kind": "artifact-default",
                "subject_id": artifact_ids[digest],
                "subject_kind": "artifact",
            }
            rows.append(_identified("compatibility_id", material))
            continue
        if not metadata["entries"]:
            material = {
                "declared_epoch": None,
                "effective_epoch": None,
                "evidence_ids": [metadata_evidence],
                "fallback_epoch": None,
                "latest_supported_epoch": None,
                "metadata_resource": resource,
                "resolution_state": "unresolved",
                "scope_key": metadata["path"],
                "scope_kind": "artifact-default",
                "subject_id": artifact_ids[digest],
                "subject_kind": "artifact",
            }
            rows.append(_identified("compatibility_id", material))
            continue
        for scope_key, epoch in sorted(metadata["entries"].items()):
            class_name = scope_key.split("::", 1)[0]
            component_id = components.maybe(
                (digest, "mixin-class", class_name)
            )
            member = "::" in scope_key
            material = {
                "declared_epoch": epoch,
                "effective_epoch": None,
                "evidence_ids": [metadata_evidence],
                "fallback_epoch": None,
                "latest_supported_epoch": None,
                "metadata_resource": resource,
                "resolution_state": (
                    "member-override" if member else "exact-metadata"
                ),
                "scope_key": scope_key,
                "scope_kind": "member" if member else "mixin-class",
                "subject_id": component_id or artifact_ids[digest],
                "subject_kind": "component" if component_id else "artifact",
            }
            rows.append(_identified("compatibility_id", material))
    return sorted(rows, key=lambda row: row["compatibility_id"])


def _create_findings(
    scans: Sequence[Mapping[str, Any]],
    artifact_ids: Mapping[str, str],
    artifact_evidence: Mapping[str, str],
    components: _ComponentCatalog,
    evidence: _EvidenceCatalog,
) -> tuple[list[dict[str, Any]], int]:
    findings = _FindingCatalog()
    unresolved = 0
    for scan in scans:
        digest = scan["identity"]["sha256"]
        artifact_id = artifact_ids[digest]
        exact_artifact = artifact_evidence[digest]
        config_evidence = [
            _entry_evidence(
                evidence, scan, config["path"], "mixin-config-resource"
            )
            for config in scan["mixin_configs"]
        ]
        if scan["mixin_configs"] and not scan["compatibility_metadata"]["present"]:
            missing = _entry_evidence(
                evidence,
                scan,
                scan["compatibility_metadata"]["path"],
                "compatibility-metadata",
                missing_ok=True,
            )
            findings.add(
                kind="compatibility",
                severity="warning",
                state="observed-exact",
                subjects=[artifact_id],
                statement=(
                    f"{scan['label']} does not contain "
                    "cleanmix_version_compatibility.json."
                ),
                evidence_ids=[missing, exact_artifact],
                limitations=[
                    "Effective fallback behavior is platform-profile knowledge and was not inferred."
                ],
            )
            unresolved += 1
        invalid_compatibility = scan["compatibility_metadata"]["invalid_entries"]
        if invalid_compatibility:
            metadata_evidence = _entry_evidence(
                evidence,
                scan,
                scan["compatibility_metadata"]["path"],
                "compatibility-metadata",
            )
            findings.add(
                kind="compatibility",
                severity="error",
                state="contradicted",
                subjects=[artifact_id],
                statement=(
                    f"{scan['label']} contains {len(invalid_compatibility)} "
                    "invalid CleanMix compatibility metadata entry or entries."
                ),
                evidence_ids=[metadata_evidence, exact_artifact],
            )
            unresolved += len(invalid_compatibility)
        if len(scan["owner_id_inputs"]) > 1:
            owner_evidence = exact_artifact
            if "mcmod.info" in _entry_index(scan):
                owner_evidence = _entry_evidence(
                    evidence, scan, "mcmod.info", "other"
                )
            findings.add(
                kind="unresolved",
                severity="warning",
                state="unresolved",
                subjects=[artifact_id],
                statement=(
                    f"{scan['label']} supplies multiple static owner-ID inputs; "
                    "the runtime owner cannot be selected offline."
                ),
                evidence_ids=[owner_evidence],
            )
            unresolved += 1
        route_kinds = {route["kind"] for route in scan["registration_routes"]}
        if "manifest-mixin-configs" in route_kinds and route_kinds.intersection(
            {"mixinbooter-early-loader", "mixinbooter-late-loader"}
        ):
            findings.add(
                kind="topology",
                severity="warning",
                state="derived-bounded",
                subjects=[artifact_id],
                statement=(
                    f"{scan['label']} exposes manifest config registration and "
                    "a MixinBooter loader interface."
                ),
                evidence_ids=[exact_artifact, *config_evidence],
                limitations=[
                    "Static bytecode does not prove that both routes register the same config at runtime."
                ],
            )
        for config in scan["mixin_configs"]:
            declared = _entry_evidence(
                evidence, scan, config["path"], "mixin-config-resource"
            )
            if config["mixins"] and config["package"] is None:
                config_subject = components.get(
                    (digest, "mixin-config", config["path"])
                )
                findings.add(
                    kind="packaging",
                    severity="error",
                    state="observed-exact",
                    subjects=[artifact_id, config_subject],
                    statement=(
                        f"Mixin config {config['path']} declares mixin classes "
                        "without a package; CleanMix will leave them orphaned."
                    ),
                    evidence_ids=[declared, exact_artifact],
                )
                unresolved += 1
            for mixin in config["mixins"]:
                if mixin["present"]:
                    continue
                subject = components.get(
                    (digest, "mixin-class", mixin["class_name"])
                )
                findings.add(
                    kind="packaging",
                    severity="error",
                    state="observed-exact",
                    subjects=[artifact_id, subject],
                    statement=(
                        f"Configured mixin class {mixin['class_name']} is absent "
                        f"from {scan['label']}."
                    ),
                    evidence_ids=[declared, exact_artifact],
                )
                unresolved += 1
            plugin = config["plugin"]
            if plugin is not None and not plugin["present"]:
                subject = components.get(
                    (digest, "config-plugin", plugin["class_name"])
                )
                findings.add(
                    kind="packaging",
                    severity="error",
                    state="observed-exact",
                    subjects=[artifact_id, subject],
                    statement=(
                        f"Configured Mixin plugin {plugin['class_name']} is "
                        f"absent from {scan['label']}."
                    ),
                    evidence_ids=[declared, exact_artifact],
                )
                unresolved += 1
        for refmap in scan["refmaps"]:
            if refmap["present"]:
                continue
            missing = _entry_evidence(
                evidence,
                scan,
                refmap["path"],
                "refmap",
                missing_ok=True,
            )
            component_id = components.get(
                (digest, "reference-mapper", refmap["path"])
            )
            findings.add(
                kind="packaging",
                severity="warning",
                state="observed-exact",
                subjects=[artifact_id, component_id],
                statement=(
                    f"Declared refmap {refmap['path']} is absent from "
                    f"{scan['label']}."
                ),
                evidence_ids=[missing, *config_evidence],
            )
            unresolved += 1

    for collision in _owner_id_collisions(scans):
        subjects = [
            artifact_ids[item["artifact_sha256"]]
            for item in collision["inputs"]
        ]
        evidence_ids = [
            artifact_evidence[item["artifact_sha256"]]
            for item in collision["inputs"]
        ]
        findings.add(
            kind="conflict",
            severity="warning",
            state="derived-bounded",
            subjects=subjects,
            statement=(
                "Multiple artifacts normalize static owner-ID inputs to "
                f"{collision['normalized_owner_id']!r}."
            ),
            evidence_ids=evidence_ids,
            limitations=[
                "Static owner inputs do not establish the owner selected by the runtime loader."
            ],
        )
        unresolved += 1
    for overlap in _embedded_package_overlaps(scans):
        subjects = [
            artifact_ids[item["artifact_sha256"]]
            for item in overlap["artifacts"]
        ]
        evidence_ids = [
            artifact_evidence[item["artifact_sha256"]]
            for item in overlap["artifacts"]
        ]
        findings.add(
            kind="conflict",
            severity="warning",
            state="observed-exact",
            subjects=subjects,
            statement=(
                f"Multiple artifacts embed the {overlap['component']} package "
                f"prefix {overlap['prefix']}."
            ),
            evidence_ids=evidence_ids,
            limitations=[
                "Package overlap does not prove that every embedded class is runtime-active."
            ],
        )
        unresolved += 1
    return findings.rows(), unresolved


def _compatibility_state(scans: Sequence[Mapping[str, Any]]) -> str:
    mixins = [
        mixin
        for scan in scans
        for config in scan["mixin_configs"]
        for mixin in config["mixins"]
    ]
    invalid_count = sum(
        len(scan["compatibility_metadata"]["invalid_entries"])
        for scan in scans
    )
    if not mixins:
        if invalid_count:
            return "unresolved"
        return "not-applicable"
    resolved = sum(mixin["compatibility_version"] is not None for mixin in mixins)
    if resolved == len(mixins) and not invalid_count:
        return "complete"
    if resolved or invalid_count:
        return "partial"
    return "unresolved"


def _referenced_ids(record: Mapping[str, Any], field: str) -> list[str]:
    value = record.get(field)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ArtifactScanError(f"{field} must be an array of IDs")
    if value != sorted(set(value)):
        raise ArtifactScanError(f"{field} must be sorted and unique")
    return value


def _policy_unresolved_count(findings: Sequence[Mapping[str, Any]]) -> int:
    unresolved_kinds = set(_POLICY["unresolved_finding_kinds"])
    return sum(row.get("finding_kind") in unresolved_kinds for row in findings)


def _receipt_compatibility_state(
    components: Sequence[Mapping[str, Any]],
    compatibility_epochs: Sequence[Mapping[str, Any]],
    findings: Sequence[Mapping[str, Any]],
) -> str:
    mixin_component_ids = {
        row.get("component_id")
        for row in components
        if row.get("component_kind") == "mixin-class"
    }
    contradicted = any(
        row.get("finding_kind") == "compatibility"
        and row.get("state") == "contradicted"
        for row in findings
    )
    if not mixin_component_ids:
        return "unresolved" if contradicted else "not-applicable"
    resolved_component_ids = {
        row.get("subject_id")
        for row in compatibility_epochs
        if row.get("subject_kind") == "component"
        and row.get("scope_kind") == "mixin-class"
        and row.get("resolution_state") == "exact-metadata"
        and row.get("subject_id") in mixin_component_ids
    }
    if resolved_component_ids == mixin_component_ids and not contradicted:
        return "complete"
    if resolved_component_ids or contradicted:
        return "partial"
    return "unresolved"


def validate_topology_receipt(receipt: Mapping[str, Any]) -> None:
    """Enforce normative content IDs, ordering, counts, and references."""

    if not isinstance(receipt, Mapping):
        raise ArtifactScanError("topology receipt must be an object")
    expected_top_level = {
        "artifacts",
        "boundaries",
        "canonicalization_id",
        "compatibility_epochs",
        "components",
        "configurations",
        "contract_id",
        "evidence",
        "findings",
        "format",
        "mixin_bindings",
        "producer",
        "receipt_id",
        "registrations",
        "schema_version",
        "scope",
        "summary",
    }
    if set(receipt) != expected_top_level:
        raise ArtifactScanError("topology receipt has unexpected or missing fields")
    if receipt.get("format") != RECEIPT_FORMAT:
        raise ArtifactScanError("topology receipt has the wrong format")
    if receipt.get("schema_version") != 1:
        raise ArtifactScanError("topology receipt has the wrong schema version")
    if receipt.get("contract_id") != CONTRACT_ID:
        raise ArtifactScanError("topology receipt has the wrong contract ID")
    if receipt.get("canonicalization_id") != CANONICALIZATION_ID:
        raise ArtifactScanError("topology receipt has the wrong canonicalization ID")
    if receipt.get("boundaries") != _BOUNDARIES:
        raise ArtifactScanError("topology receipt boundaries do not match policy")

    producer = receipt.get("producer")
    expected_producer = {
        "input_set_sha256": None,
        "policy_id": "workbench-project-intelligence:offline-mixin-topology-v1",
        "policy_sha256": _digest(_POLICY),
        "tool_id": "workbench-project-intelligence:mixin-artifact-scanner",
        "tool_version": "1",
    }
    if not isinstance(producer, Mapping):
        raise ArtifactScanError("topology receipt producer must be an object")
    if set(producer) != set(expected_producer):
        raise ArtifactScanError("topology receipt producer fields do not match policy")
    for field in ("policy_id", "policy_sha256", "tool_id", "tool_version"):
        if producer.get(field) != expected_producer[field]:
            raise ArtifactScanError(f"topology receipt producer {field} is wrong")

    def check_numbers(value: Any, context: str) -> None:
        if isinstance(value, float):
            raise ArtifactScanError(f"{context} contains a non-integer number")
        if isinstance(value, Mapping):
            for key, child in value.items():
                check_numbers(child, f"{context}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                check_numbers(child, f"{context}[{index}]")

    check_numbers(receipt, "receipt")
    expected_receipt_id = RECEIPT_ID_PREFIX + _digest(
        {key: value for key, value in receipt.items() if key != "receipt_id"}
    )
    if receipt.get("receipt_id") != expected_receipt_id:
        raise ArtifactScanError("topology receipt content ID does not match")

    scope = receipt.get("scope")
    if not isinstance(scope, Mapping):
        raise ArtifactScanError("topology receipt scope must be an object")
    expected_scope_fields = {
        "artifact_set_sha256",
        "candidate_lock_v1",
        "configuration_set_sha256",
        "mapping_namespace",
        "physical_side",
        "runtime_java",
        "scope_id",
        "scope_kind",
    }
    if set(scope) != expected_scope_fields:
        raise ArtifactScanError("topology receipt scope fields do not match policy")
    if scope.get("scope_kind") != "offline-artifact-set":
        raise ArtifactScanError("topology receipt is not an offline artifact scope")
    if scope.get("candidate_lock_v1") is not None:
        raise ArtifactScanError("offline topology receipt must not bind a candidate lock")
    if scope.get("physical_side") != "OFFLINE":
        raise ArtifactScanError("offline topology receipt has the wrong physical side")
    if scope.get("runtime_java") is not None or scope.get("mapping_namespace") is not None:
        raise ArtifactScanError("offline topology receipt must not claim runtime context")
    expected_scope_id = _ID_PREFIXES["scope_id"] + _digest(
        {key: value for key, value in scope.items() if key != "scope_id"}
    )
    if scope.get("scope_id") != expected_scope_id:
        raise ArtifactScanError("topology receipt scope ID does not match")

    arrays = {
        "artifacts": "artifact_id",
        "components": "component_id",
        "configurations": "configuration_id",
        "registrations": "registration_id",
        "mixin_bindings": "binding_id",
        "compatibility_epochs": "compatibility_id",
        "evidence": "evidence_id",
        "findings": "finding_id",
    }
    rows_by_array: dict[str, list[Mapping[str, Any]]] = {}
    ids_by_array: dict[str, set[str]] = {}
    all_ids: set[str] = set()
    for array_name, id_field in arrays.items():
        raw_rows = receipt.get(array_name)
        if not isinstance(raw_rows, list) or any(
            not isinstance(row, Mapping) for row in raw_rows
        ):
            raise ArtifactScanError(f"topology receipt {array_name} must be an array")
        rows = list(raw_rows)
        identities = [row.get(id_field) for row in rows]
        if any(not isinstance(identity, str) for identity in identities):
            raise ArtifactScanError(f"topology receipt {array_name} has a missing ID")
        if identities != sorted(identities):
            raise ArtifactScanError(f"topology receipt {array_name} is not ID-sorted")
        if len(identities) != len(set(identities)):
            raise ArtifactScanError(f"topology receipt {array_name} repeats an ID")
        for row in rows:
            material = {key: value for key, value in row.items() if key != id_field}
            expected = _ID_PREFIXES[id_field] + _digest(material)
            if row[id_field] != expected:
                raise ArtifactScanError(
                    f"topology receipt {array_name} content ID does not match"
                )
        rows_by_array[array_name] = rows
        ids_by_array[array_name] = set(identities)
        all_ids.update(identities)

    if not rows_by_array["artifacts"]:
        raise ArtifactScanError("topology receipt must contain an artifact")
    if not rows_by_array["components"]:
        raise ArtifactScanError("topology receipt must contain a component")
    if rows_by_array["mixin_bindings"]:
        raise ArtifactScanError(
            "offline scanner V1 cannot claim resolved mixin bindings"
        )

    artifact_material: list[dict[str, Any]] = []
    for row in rows_by_array["artifacts"]:
        file_name = row.get("file_name")
        digest = row.get("sha256")
        size = row.get("size")
        if not isinstance(file_name, str) or not file_name:
            raise ArtifactScanError("topology artifact has an invalid file name")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise ArtifactScanError("topology artifact has an invalid SHA-256")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ArtifactScanError("topology artifact has an invalid size")
        artifact_material.append(
            {"file_name": file_name, "sha256": digest, "size": size}
        )
    artifact_material.sort(key=lambda row: (row["sha256"], row["file_name"]))
    expected_artifact_set_sha256 = _digest(artifact_material)
    if scope.get("artifact_set_sha256") != expected_artifact_set_sha256:
        raise ArtifactScanError("topology receipt artifact-set digest is wrong")
    if producer.get("input_set_sha256") != expected_artifact_set_sha256:
        raise ArtifactScanError("topology receipt producer input-set digest is wrong")

    configuration_material: list[dict[str, Any]] = []
    for row in rows_by_array["configurations"]:
        resource = row.get("resource")
        if not isinstance(resource, Mapping) or resource.get("state") != "exact":
            raise ArtifactScanError(
                "offline topology configuration must bind an exact resource"
            )
        path = resource.get("path")
        digest = resource.get("sha256")
        size = resource.get("size")
        if not isinstance(path, str) or not path or not isinstance(digest, str):
            raise ArtifactScanError("topology configuration resource is invalid")
        if len(digest) != 64 or any(
            character not in "0123456789abcdef" for character in digest
        ):
            raise ArtifactScanError("topology configuration digest is invalid")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ArtifactScanError("topology configuration size is invalid")
        configuration_material.append({"path": path, "sha256": digest})
    configuration_material.sort(key=lambda row: (row["sha256"], row["path"]))
    expected_configuration_set_sha256 = (
        _digest(configuration_material) if configuration_material else None
    )
    if scope.get("configuration_set_sha256") != expected_configuration_set_sha256:
        raise ArtifactScanError("topology receipt configuration-set digest is wrong")

    summary = receipt.get("summary")
    if not isinstance(summary, Mapping):
        raise ArtifactScanError("topology receipt summary must be an object")
    count_fields = {
        "artifact_count": "artifacts",
        "component_count": "components",
        "configuration_count": "configurations",
        "registration_count": "registrations",
        "mixin_binding_count": "mixin_bindings",
        "compatibility_epoch_count": "compatibility_epochs",
        "evidence_count": "evidence",
        "finding_count": "findings",
    }
    for field, array_name in count_fields.items():
        if summary.get(field) != len(rows_by_array[array_name]):
            raise ArtifactScanError(f"topology receipt summary {field} is wrong")
    contradiction_count = sum(
        row.get("state") == "contradicted" for row in rows_by_array["findings"]
    )
    if summary.get("contradiction_count") != contradiction_count:
        raise ArtifactScanError(
            "topology receipt summary contradiction_count is wrong"
        )
    unresolved_count = _policy_unresolved_count(rows_by_array["findings"])
    if summary.get("unresolved_count") != unresolved_count:
        raise ArtifactScanError("topology receipt summary unresolved_count is wrong")
    expected_receipt_state = "complete" if unresolved_count == 0 else "incomplete"
    if summary.get("receipt_state") != expected_receipt_state:
        raise ArtifactScanError("topology receipt summary receipt_state is wrong")
    expected_topology_state = (
        "partial"
        if rows_by_array["configurations"] or unresolved_count
        else "complete"
    )
    if summary.get("topology_state") != expected_topology_state:
        raise ArtifactScanError("topology receipt summary topology_state is wrong")
    if summary.get("runtime_observation_state") != "not-observed":
        raise ArtifactScanError(
            "offline topology receipt cannot claim runtime observation"
        )
    expected_compatibility_state = _receipt_compatibility_state(
        rows_by_array["components"],
        rows_by_array["compatibility_epochs"],
        rows_by_array["findings"],
    )
    if summary.get("compatibility_state") != expected_compatibility_state:
        raise ArtifactScanError(
            "topology receipt summary compatibility_state is wrong"
        )
    if summary.get("limitations") != _SUMMARY_LIMITATIONS:
        raise ArtifactScanError("topology receipt summary limitations are wrong")

    artifact_ids = ids_by_array["artifacts"]
    component_ids = ids_by_array["components"]
    configuration_ids = ids_by_array["configurations"]
    registration_ids = ids_by_array["registrations"]
    binding_ids = ids_by_array["mixin_bindings"]
    evidence_ids = ids_by_array["evidence"]
    artifacts_by_id = {
        row["artifact_id"]: row for row in rows_by_array["artifacts"]
    }
    components_by_id = {
        row["component_id"]: row for row in rows_by_array["components"]
    }
    registrations_by_id = {
        row["registration_id"]: row for row in rows_by_array["registrations"]
    }
    evidence_by_id = {
        row["evidence_id"]: row for row in rows_by_array["evidence"]
    }

    artifact_source_ids = {
        f"artifact-bytes:sha256:{row['sha256']}"
        for row in rows_by_array["artifacts"]
    }
    static_evidence_kinds = {
        "artifact-bytes",
        "jar-entry-bytes",
        "manifest",
        "service-provider-file",
        "mixin-config-resource",
        "compatibility-metadata",
        "refmap",
        "other",
    }
    for row in rows_by_array["evidence"]:
        identity = row["evidence_id"]
        if row.get("evidence_kind") not in static_evidence_kinds:
            raise ArtifactScanError(
                f"offline topology evidence {identity} has a non-static kind"
            )
        collection_state = row.get("collection_state")
        admission_state = row.get("admission_state")
        truth_scope = row.get("truth_scope")
        content_sha256 = row.get("content_sha256")
        size = row.get("size")
        source_ids = row.get("source_ids")
        limitations = row.get("limitations")
        if (
            not isinstance(source_ids, list)
            or any(not isinstance(item, str) for item in source_ids)
            or source_ids != sorted(set(source_ids))
        ):
            raise ArtifactScanError(
                f"topology evidence {identity} source_ids are not sorted and unique"
            )
        if (
            not isinstance(limitations, list)
            or any(not isinstance(item, str) or not item for item in limitations)
            or limitations != sorted(set(limitations))
        ):
            raise ArtifactScanError(
                f"topology evidence {identity} limitations are invalid"
            )
        if collection_state == "exact":
            if admission_state != "admitted":
                raise ArtifactScanError(
                    f"exact topology evidence {identity} is not admitted"
                )
            if (
                not isinstance(content_sha256, str)
                or len(content_sha256) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in content_sha256
                )
                or isinstance(size, bool)
                or not isinstance(size, int)
                or size < 0
            ):
                raise ArtifactScanError(
                    f"exact topology evidence {identity} has invalid byte identity"
                )
        elif collection_state == "missing":
            if admission_state != "supporting-only":
                raise ArtifactScanError(
                    f"missing topology evidence {identity} cannot be admitted"
                )
            if content_sha256 is not None or size is not None:
                raise ArtifactScanError(
                    f"missing topology evidence {identity} claims byte identity"
                )
        else:
            raise ArtifactScanError(
                f"offline topology evidence {identity} has an invalid collection state"
            )
        if row.get("evidence_kind") == "artifact-bytes":
            if truth_scope != "artifact-identity" or source_ids:
                raise ArtifactScanError(
                    f"artifact evidence {identity} has the wrong truth scope or source"
                )
        else:
            if truth_scope != "static-declaration":
                raise ArtifactScanError(
                    f"static topology evidence {identity} has the wrong truth scope"
                )
            if len(source_ids) != 1 or source_ids[0] not in artifact_source_ids:
                raise ArtifactScanError(
                    f"static topology evidence {identity} lacks an exact artifact source"
                )

    def matching_evidence(
        values: Iterable[str],
        *,
        kind: str | None = None,
        collection: str | None = None,
        admission: str | None = None,
        truth_scope: str | None = None,
        content_sha256: str | None = None,
        size: int | None = None,
        source_id: str | None = None,
    ) -> bool:
        for identity in values:
            row = evidence_by_id[identity]
            if kind is not None and row.get("evidence_kind") != kind:
                continue
            if collection is not None and row.get("collection_state") != collection:
                continue
            if admission is not None and row.get("admission_state") != admission:
                continue
            if truth_scope is not None and row.get("truth_scope") != truth_scope:
                continue
            if content_sha256 is not None and row.get("content_sha256") != content_sha256:
                continue
            if size is not None and row.get("size") != size:
                continue
            if source_id is not None and source_id not in row.get("source_ids", []):
                continue
            return True
        return False

    def require_subset(values: Iterable[str], known: set[str], context: str) -> None:
        missing = sorted(set(values) - known)
        if missing:
            raise ArtifactScanError(f"{context} references unknown IDs: {missing}")

    for row in rows_by_array["artifacts"]:
        artifact_record_evidence = _referenced_ids(row, "evidence_ids")
        require_subset(
            artifact_record_evidence,
            evidence_ids,
            f"artifact {row['artifact_id']}",
        )
        if not matching_evidence(
            artifact_record_evidence,
            kind="artifact-bytes",
            collection="exact",
            admission="admitted",
            truth_scope="artifact-identity",
            content_sha256=row.get("sha256"),
            size=row.get("size"),
        ):
            raise ArtifactScanError(
                f"artifact {row['artifact_id']} lacks matching exact-byte evidence"
            )
        claims = row.get("version_claims")
        if not isinstance(claims, list) or any(
            not isinstance(claim, Mapping) for claim in claims
        ):
            raise ArtifactScanError(
                f"artifact {row['artifact_id']} version claims must be an array"
            )
        expected_claim_order = sorted(
            claims,
            key=lambda claim: (
                claim.get("namespace"),
                "" if claim.get("value") is None else claim.get("value"),
                claim.get("state"),
            ),
        )
        if claims != expected_claim_order:
            raise ArtifactScanError(
                f"artifact {row['artifact_id']} version claims are not sorted"
            )
        for claim in claims:
            claim_evidence = _referenced_ids(claim, "evidence_ids")
            require_subset(
                claim_evidence,
                evidence_ids,
                f"artifact version claim {row['artifact_id']}",
            )
            if not matching_evidence(
                claim_evidence,
                collection="exact",
                admission="admitted",
                truth_scope="static-declaration",
            ):
                raise ArtifactScanError(
                    f"artifact version claim {row['artifact_id']} lacks admitted static evidence"
                )
    for row in rows_by_array["components"]:
        require_subset(
            [row.get("artifact_id")],
            artifact_ids,
            f"component {row['component_id']}",
        )
        component_evidence = _referenced_ids(row, "evidence_ids")
        require_subset(
            component_evidence,
            evidence_ids,
            f"component {row['component_id']}",
        )
        implementation = row.get("implementation")
        if not isinstance(implementation, Mapping):
            raise ArtifactScanError(
                f"component {row['component_id']} implementation must be an object"
            )
        identity_state = row.get("identity_state")
        if identity_state == "exact-entry":
            entry_sha256 = implementation.get("entry_sha256")
            if not matching_evidence(
                component_evidence,
                collection="exact",
                admission="admitted",
                truth_scope="static-declaration",
                content_sha256=entry_sha256,
            ):
                raise ArtifactScanError(
                    f"component {row['component_id']} lacks matching exact-entry evidence"
                )
        elif identity_state == "artifact-bound":
            artifact = artifacts_by_id[row["artifact_id"]]
            if not matching_evidence(
                component_evidence,
                kind="artifact-bytes",
                collection="exact",
                admission="admitted",
                truth_scope="artifact-identity",
                content_sha256=artifact["sha256"],
                size=artifact["size"],
            ):
                raise ArtifactScanError(
                    f"component {row['component_id']} lacks artifact-binding evidence"
                )
        elif identity_state == "declared-only":
            if not matching_evidence(
                component_evidence,
                collection="exact",
                admission="admitted",
            ):
                raise ArtifactScanError(
                    f"component {row['component_id']} lacks admitted declaration evidence"
                )
        else:
            raise ArtifactScanError(
                f"offline component {row['component_id']} has an invalid identity state"
            )
    for row in rows_by_array["configurations"]:
        require_subset(
            [row.get("component_id")],
            component_ids,
            f"configuration {row['configuration_id']}",
        )
        if components_by_id[row["component_id"]].get("component_kind") != "mixin-config":
            raise ArtifactScanError(
                f"configuration {row['configuration_id']} does not reference a mixin-config component"
            )
        plugin_id = row.get("plugin_component_id")
        if plugin_id is not None:
            require_subset(
                [plugin_id], component_ids, f"configuration {row['configuration_id']}"
            )
            if components_by_id[plugin_id].get("component_kind") != "config-plugin":
                raise ArtifactScanError(
                    f"configuration {row['configuration_id']} plugin has the wrong component kind"
                )
        refmap = row.get("refmap")
        if not isinstance(refmap, Mapping):
            raise ArtifactScanError(
                f"configuration {row['configuration_id']} refmap must be an object"
            )
        wrapper_id = refmap.get("wrapper_component_id")
        if wrapper_id is not None:
            require_subset(
                [wrapper_id], component_ids, f"configuration {row['configuration_id']}"
            )
            if components_by_id[wrapper_id].get("component_kind") != "reference-mapper":
                raise ArtifactScanError(
                    f"configuration {row['configuration_id']} refmap wrapper has the wrong component kind"
                )
        configuration_registrations = _referenced_ids(row, "registration_ids")
        require_subset(
            configuration_registrations,
            registration_ids,
            f"configuration {row['configuration_id']}",
        )
        for registration_id in configuration_registrations:
            registration = registrations_by_id[registration_id]
            if registration.get("target", {}).get("id") != row.get("component_id"):
                raise ArtifactScanError(
                    f"configuration {row['configuration_id']} registration does not target its component"
                )
        configuration_evidence = _referenced_ids(row, "evidence_ids")
        require_subset(
            configuration_evidence,
            evidence_ids,
            f"configuration {row['configuration_id']}",
        )
        resource = row["resource"]
        configuration_artifact = artifacts_by_id[
            components_by_id[row["component_id"]]["artifact_id"]
        ]
        configuration_artifact_source = (
            f"artifact-bytes:sha256:{configuration_artifact['sha256']}"
        )
        if not matching_evidence(
            configuration_evidence,
            kind="mixin-config-resource",
            collection="exact",
            admission="admitted",
            truth_scope="static-declaration",
            content_sha256=resource["sha256"],
            size=resource["size"],
            source_id=configuration_artifact_source,
        ):
            raise ArtifactScanError(
                f"configuration {row['configuration_id']} lacks exact resource evidence"
            )
        required_features = row.get("required_features")
        if (
            not isinstance(required_features, list)
            or any(not isinstance(item, str) or not item for item in required_features)
            or required_features != sorted(set(required_features))
        ):
            raise ArtifactScanError(
                f"configuration {row['configuration_id']} required features are invalid"
            )
        refmap_state = refmap.get("state")
        if refmap_state == "exact":
            if not matching_evidence(
                evidence_ids,
                kind="refmap",
                collection="exact",
                admission="admitted",
                truth_scope="static-declaration",
                content_sha256=refmap.get("resource_sha256"),
                source_id=configuration_artifact_source,
            ):
                raise ArtifactScanError(
                    f"configuration {row['configuration_id']} lacks exact refmap evidence"
                )
        elif refmap_state == "missing":
            if not matching_evidence(
                evidence_ids,
                kind="refmap",
                collection="missing",
                admission="supporting-only",
                truth_scope="static-declaration",
                source_id=configuration_artifact_source,
            ):
                raise ArtifactScanError(
                    f"configuration {row['configuration_id']} lacks bounded missing-refmap evidence"
                )
        elif refmap_state != "not-applicable":
            raise ArtifactScanError(
                f"offline configuration {row['configuration_id']} has an invalid refmap state"
            )
    endpoint_ids = {
        "artifact": artifact_ids,
        "component": component_ids,
        "configuration": configuration_ids,
        "mixin-binding": binding_ids,
    }
    endpoint_kinds = set(endpoint_ids) | {
        "runtime-phase",
        "transformer-chain",
        "other",
    }
    phases = {"PREINIT", "INIT", "DEFAULT", "CUSTOM", "UNSPECIFIED", "UNRESOLVED"}
    for row in rows_by_array["registrations"]:
        for endpoint_name in ("source", "target"):
            endpoint = row.get(endpoint_name)
            if not isinstance(endpoint, Mapping):
                raise ArtifactScanError(
                    f"registration {row['registration_id']} {endpoint_name} must be an endpoint"
                )
            kind = endpoint.get("kind")
            identity = endpoint.get("id")
            if kind not in endpoint_kinds or not isinstance(identity, str) or not identity:
                raise ArtifactScanError(
                    f"registration {row['registration_id']} {endpoint_name} has an invalid endpoint"
                )
            expected_prefix = _ENDPOINT_ID_PREFIXES.get(kind)
            if expected_prefix is not None and not identity.startswith(expected_prefix):
                raise ArtifactScanError(
                    f"registration {row['registration_id']} {endpoint_name} ID has the wrong kind prefix"
                )
            for known_kind, prefix in _ENDPOINT_ID_PREFIXES.items():
                if identity.startswith(prefix) and kind != known_kind:
                    raise ArtifactScanError(
                        f"registration {row['registration_id']} {endpoint_name} endpoint kind contradicts its ID"
                    )
            known = endpoint_ids.get(kind)
            if known is not None:
                require_subset(
                    [identity],
                    known,
                    f"registration {row['registration_id']} {endpoint_name}",
                )
            elif kind == "runtime-phase" and identity not in phases:
                raise ArtifactScanError(
                    f"registration {row['registration_id']} {endpoint_name} has an unknown runtime phase"
                )
            elif any(identity.startswith(prefix) for prefix in _ALL_CONTENT_ID_PREFIXES):
                raise ArtifactScanError(
                    f"registration {row['registration_id']} {endpoint_name} hides a typed content ID"
                )
        source = row["source"]
        target = row["target"]
        registration_kind = row.get("registration_kind")
        endpoint_pattern = (source["kind"], target["kind"])
        expected_patterns = {
            "manifest-config": ("artifact", "component"),
            "manifest-connector": ("artifact", "component"),
            "service-provider": ("artifact", "component"),
            "programmatic-config": ("component", "runtime-phase"),
            "config-mixin": ("component", "component"),
            "other": ("artifact", "component"),
        }
        if registration_kind not in expected_patterns:
            raise ArtifactScanError(
                f"offline scanner registration {row['registration_id']} has an unsupported kind"
            )
        if endpoint_pattern != expected_patterns[registration_kind]:
            raise ArtifactScanError(
                f"registration {row['registration_id']} endpoint types contradict its kind"
            )
        expected_component_kinds = {
            "manifest-config": (None, "mixin-config"),
            "manifest-connector": (None, "connector"),
            "programmatic-config": ("host-loader", None),
            "config-mixin": ("mixin-config", "mixin-class"),
        }
        source_component_kind, target_component_kind = expected_component_kinds.get(
            registration_kind, (None, None)
        )
        if source_component_kind is not None and components_by_id[
            source["id"]
        ].get("component_kind") != source_component_kind:
            raise ArtifactScanError(
                f"registration {row['registration_id']} source component has the wrong kind"
            )
        if target_component_kind is not None and components_by_id[
            target["id"]
        ].get("component_kind") != target_component_kind:
            raise ArtifactScanError(
                f"registration {row['registration_id']} target component has the wrong kind"
            )
        if row.get("runtime_state") != "not-observed":
            raise ArtifactScanError(
                f"offline registration {row['registration_id']} claims runtime state"
            )
        if row.get("declaration_state") != "declared-exact":
            raise ArtifactScanError(
                f"offline registration {row['registration_id']} lacks an exact declaration"
            )
        if row.get("phase") not in phases:
            raise ArtifactScanError(
                f"registration {row['registration_id']} has an invalid phase"
            )
        registration_evidence = _referenced_ids(row, "evidence_ids")
        require_subset(
            registration_evidence,
            evidence_ids,
            f"registration {row['registration_id']}",
        )
        if not matching_evidence(
            registration_evidence,
            collection="exact",
            admission="admitted",
            truth_scope="static-declaration",
        ):
            raise ArtifactScanError(
                f"registration {row['registration_id']} lacks admitted static evidence"
            )
    for row in rows_by_array["mixin_bindings"]:
        require_subset(
            [row.get("configuration_id")],
            configuration_ids,
            f"mixin binding {row['binding_id']}",
        )
        require_subset(
            [row.get("mixin_component_id")],
            component_ids,
            f"mixin binding {row['binding_id']}",
        )
        require_subset(
            _referenced_ids(row, "evidence_ids"),
            evidence_ids,
            f"mixin binding {row['binding_id']}",
        )
    subject_ids = {
        "artifact": artifact_ids,
        "component": component_ids,
        "configuration": configuration_ids,
        "mixin-binding": binding_ids,
    }
    for row in rows_by_array["compatibility_epochs"]:
        require_subset(
            [row.get("subject_id")],
            subject_ids.get(row.get("subject_kind"), set()),
            f"compatibility epoch {row['compatibility_id']}",
        )
        compatibility_evidence = _referenced_ids(row, "evidence_ids")
        require_subset(
            compatibility_evidence,
            evidence_ids,
            f"compatibility epoch {row['compatibility_id']}",
        )
        metadata_resource = row.get("metadata_resource")
        if not isinstance(metadata_resource, Mapping):
            raise ArtifactScanError(
                f"compatibility epoch {row['compatibility_id']} resource must be an object"
            )
        resource_state = metadata_resource.get("state")
        resolution_state = row.get("resolution_state")
        if resolution_state in {
            "exact-metadata",
            "member-override",
            "unresolved",
        }:
            if resource_state != "exact" or not matching_evidence(
                compatibility_evidence,
                kind="compatibility-metadata",
                collection="exact",
                admission="admitted",
                truth_scope="static-declaration",
                content_sha256=metadata_resource.get("sha256"),
                size=metadata_resource.get("size"),
            ):
                raise ArtifactScanError(
                    f"compatibility epoch {row['compatibility_id']} lacks exact metadata evidence"
                )
        elif resolution_state == "absent-metadata":
            if resource_state != "missing" or not matching_evidence(
                compatibility_evidence,
                kind="compatibility-metadata",
                collection="missing",
                admission="supporting-only",
                truth_scope="static-declaration",
            ):
                raise ArtifactScanError(
                    f"compatibility epoch {row['compatibility_id']} lacks bounded absence evidence"
                )
        else:
            raise ArtifactScanError(
                f"offline compatibility epoch {row['compatibility_id']} has an invalid resolution state"
            )
    for row in rows_by_array["findings"]:
        finding_subjects = _referenced_ids(row, "subject_ids")
        require_subset(
            finding_subjects,
            artifact_ids
            | component_ids
            | configuration_ids
            | registration_ids
            | binding_ids,
            f"finding {row['finding_id']}",
        )
        finding_evidence = _referenced_ids(row, "evidence_ids")
        require_subset(
            finding_evidence,
            evidence_ids,
            f"finding {row['finding_id']}",
        )
        if not matching_evidence(finding_evidence, admission="admitted"):
            raise ArtifactScanError(
                f"finding {row['finding_id']} has no admitted evidence"
            )
        if row.get("state") == "observed-exact" and not matching_evidence(
            finding_evidence,
            collection="exact",
            admission="admitted",
        ):
            raise ArtifactScanError(
                f"exact finding {row['finding_id']} lacks exact admitted evidence"
            )
        if row.get("state") == "derived-bounded":
            limitations = row.get("limitations")
            if not isinstance(limitations, list) or not limitations:
                raise ArtifactScanError(
                    f"derived finding {row['finding_id']} lacks limitations"
                )
        if row.get("state") in {"declared-only", "rejected"}:
            raise ArtifactScanError(
                f"offline scanner finding {row['finding_id']} has an invalid state"
            )


def build_contract_receipt(
    scans: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Map validated static scans into the authoritative experimental V1."""

    evidence = _EvidenceCatalog()
    artifacts, artifact_ids, artifact_evidence = _artifact_records(scans, evidence)
    components = _create_components(
        scans, artifact_ids, artifact_evidence, evidence
    )
    registrations, config_routes = _create_registrations(
        scans, artifact_ids, components, evidence
    )
    configurations, _ = _create_configurations(
        scans, components, config_routes, evidence
    )
    compatibility_epochs = _create_compatibility_epochs(
        scans, artifact_ids, components, evidence
    )
    findings, _ = _create_findings(
        scans,
        artifact_ids,
        artifact_evidence,
        components,
        evidence,
    )
    unresolved = _policy_unresolved_count(findings)
    evidence_rows = evidence.rows()
    component_rows = components.rows()

    input_material = [
        {
            "file_name": scan["label"],
            "sha256": scan["identity"]["sha256"],
            "size": scan["identity"]["size_bytes"],
        }
        for scan in scans
    ]
    input_material.sort(key=lambda row: (row["sha256"], row["file_name"]))
    artifact_set_sha256 = _digest(input_material)
    configuration_material = [
        {"path": config["path"], "sha256": config["sha256"]}
        for scan in scans
        for config in scan["mixin_configs"]
    ]
    configuration_material.sort(key=lambda row: (row["sha256"], row["path"]))
    configuration_set_sha256 = (
        _digest(configuration_material) if configuration_material else None
    )
    scope_material = {
        "artifact_set_sha256": artifact_set_sha256,
        "candidate_lock_v1": None,
        "configuration_set_sha256": configuration_set_sha256,
        "mapping_namespace": None,
        "physical_side": "OFFLINE",
        "runtime_java": None,
        "scope_kind": "offline-artifact-set",
    }
    scope = _identified("scope_id", scope_material)

    receipt_complete = unresolved == 0
    material = {
        "artifacts": artifacts,
        "boundaries": dict(_BOUNDARIES),
        "canonicalization_id": CANONICALIZATION_ID,
        "compatibility_epochs": compatibility_epochs,
        "components": component_rows,
        "configurations": configurations,
        "contract_id": CONTRACT_ID,
        "evidence": evidence_rows,
        "findings": findings,
        "format": RECEIPT_FORMAT,
        "mixin_bindings": [],
        "producer": {
            "input_set_sha256": artifact_set_sha256,
            "policy_id": "workbench-project-intelligence:offline-mixin-topology-v1",
            "policy_sha256": _digest(_POLICY),
            "tool_id": "workbench-project-intelligence:mixin-artifact-scanner",
            "tool_version": "1",
        },
        "registrations": registrations,
        "schema_version": 1,
        "scope": scope,
        "summary": {
            "artifact_count": len(artifacts),
            "compatibility_epoch_count": len(compatibility_epochs),
            "compatibility_state": _compatibility_state(scans),
            "component_count": len(component_rows),
            "configuration_count": len(configurations),
            "contradiction_count": sum(
                finding["state"] == "contradicted" for finding in findings
            ),
            "evidence_count": len(evidence_rows),
            "finding_count": len(findings),
            "limitations": list(_SUMMARY_LIMITATIONS),
            "mixin_binding_count": 0,
            "receipt_state": "complete" if receipt_complete else "incomplete",
            "registration_count": len(registrations),
            "runtime_observation_state": "not-observed",
            "topology_state": (
                "partial"
                if configurations or not receipt_complete
                else "complete"
            ),
            "unresolved_count": unresolved,
        },
    }
    receipt = dict(material)
    receipt["receipt_id"] = RECEIPT_ID_PREFIX + _digest(material)
    validate_topology_receipt(receipt)
    return receipt
