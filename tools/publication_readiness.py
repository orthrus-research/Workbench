#!/usr/bin/env python3
"""Read-only, local publication gates; bound operator evidence is not proof of approval.

This tool never runs supplied commands, contacts GitHub, alters repository settings,
or publishes. It verifies local file identities and conservative evidence bindings.
Operator attestations remain assertions: their authenticity and actual execution
must be reviewed by the responsible maintainer, not inferred from a JSON document.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

from jsonschema import Draft202012Validator, FormatChecker

from component_versions import load_authority
from prepare_public_export import PublicExportError, public_export_plan
from repository_policy import _strict_json, PublicRepositoryError
from verify_wheelhouse import verify as verify_wheelhouse


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = Path("packaging/release/schemas/workbench-publication-evidence-v1.schema.json")
MAX_EVIDENCE_BYTES = 1024 * 1024
MAX_LOG_BYTES = 64 * 1024 * 1024
MAX_ARTIFACT_BYTES = 512 * 1024 * 1024
SOURCE_CONTROLS = (
    "default_branch_protected", "required_checks", "eligible_approval_path",
    "secret_scanning", "push_protection", "private_vulnerability_reporting",
)
RELEASE_CONTROLS = (
    "tag_namespaces_protected", "release_environment_protected",
    "reviewed_publication_mechanism",
)


class ReadinessError(ValueError):
    """Invalid or mismatched local publication evidence."""


def _file(reference: dict, base: Path, *, limit: int = MAX_LOG_BYTES) -> tuple[Path, bytes]:
    path = Path(reference["path"])
    if not path.is_absolute():
        path = base / path
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= limit:
        raise ReadinessError(f"evidence is not one bounded regular file: {path}")
    raw = path.read_bytes()
    if len(raw) > limit or hashlib.sha256(raw).hexdigest() != reference["sha256"]:
        raise ReadinessError(f"evidence file digest differs: {path}")
    return path.resolve(), raw


def _load_evidence(path: Path, root: Path) -> tuple[dict, Path]:
    path = path.absolute()
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_EVIDENCE_BYTES:
        raise ReadinessError("publication evidence must be a bounded regular file")
    resolved = path.resolve()
    if resolved.is_relative_to(root.resolve()) and not resolved.is_relative_to(root.resolve() / ".workbench"):
        raise ReadinessError("local publication evidence belongs in ignored .workbench storage or outside the repository")
    value = _strict_json(path, label="publication evidence")
    schema = _strict_json(root / SCHEMA, label="publication evidence schema")
    errors = sorted(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value), key=lambda error: str(error.path))
    if errors:
        raise ReadinessError(f"invalid publication evidence: {errors[0].message}")
    # Python/JSON Schema number equivalence is not an exact identity contract.
    if type(value["schema_version"]) is not int:
        raise ReadinessError("schema_version must be an integer")
    for field in ("source_revision", "git_tree_oid"):
        if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", value[field]) is None:
            raise ReadinessError(f"invalid exact {field}")
    return value, resolved.parent


def readiness(revision: str, evidence_path: Path | None = None, *, components: list[str] | None = None, root: Path = ROOT) -> dict:
    plan = public_export_plan(revision, root=root)
    _authority, inventory = load_authority(root)
    selected = sorted(set(components) if components else inventory)
    if not selected or any(name not in inventory for name in selected):
        raise ReadinessError("requested component is not in the native release inventory")
    evidence, base = _load_evidence(evidence_path, root) if evidence_path else ({}, root)
    if evidence and (evidence["source_revision"] != plan["source_revision"] or evidence["git_tree_oid"] != plan["git_tree_oid"]):
        raise ReadinessError("publication evidence belongs to another source revision or tree")
    gates: dict[str, dict] = {}

    def gate(identifier: str, passed: bool, reason: str, *, kind: str = "operator-attestation", details: dict | None = None) -> bool:
        gates[identifier] = {"state": "satisfied" if passed else "pending", "evidence_kind": kind, "reason": reason, **(details or {})}
        return passed

    def check(identifier: str, value: dict | None, *, full: bool = False) -> bool:
        if value is None:
            return gate(identifier, False, "No bound command result supplied.")
        raw = _file(value["log"], base)[1] if value["log"] is not None else b""
        passed = value["result"] == "pass" and type(value["exit_code"]) is int and value["exit_code"] == 0 and bool(value["command"]) and bool(raw)
        if full:
            passed = passed and "--full" in value["command"] and any(arg.replace("\\", "/").endswith("validation/validate.py") for arg in value["command"])
            passed = passed and b"Workbench full validation passed; run artifacts:" in raw
            passed = passed and raw.rfind(b"VALIDATION FAILED:") < raw.rfind(b"Workbench full validation passed; run artifacts:")
        return gate(identifier, passed, "File identity verified; command/result remain operator-attested. A Python-only run.json cannot prove full IDE validation.", details={"declared_result": value["result"], "limitations": value["limitations"]})

    gate("source-clean", plan["source_clean"] and plan["reviewed_revision_is_head"], "Exact reviewed HEAD, ordinary index, and clean working tree required.", kind="local-observation")
    if evidence.get("source_scan"):
        scan, _raw = _file(evidence["source_scan"], base, limit=64 * 1024)
        scanned_plan = public_export_plan(revision, scan, root=root)
        gate("source-secret-scan", scanned_plan["secret_scan"]["verified"], "Exact public-tree scan receipt verified; scanner execution remains operator-attested.")
    else:
        gate("source-secret-scan", False, "Exact public-tree secret-scan receipt required.")
    check("full-validation", evidence.get("full_validation"), full=True)

    qualified: set[str] = set()
    artifact_digests: set[str] = set()
    targets: list[dict] = []
    seen: set[tuple[str, str]] = set()
    selected_results: list[bool] = []
    source_identity = hashlib.sha256()
    for row in sorted(plan["files"], key=lambda item: item["path"].encode("utf-8")):
        source_identity.update(row["path"].encode("utf-8") + b"\0" + bytes.fromhex(row["sha256"]))
    assemblies: dict[tuple[Path, str], dict] = {}
    for artifact in evidence.get("artifacts", []):
        name, target = artifact["component"], artifact["target"]
        if name not in inventory or (name, target) in seen:
            raise ReadinessError("unknown component or repeated component/target artifact")
        seen.add((name, target))
        path, raw = _file(artifact["file"], base, limit=MAX_ARTIFACT_BYTES)
        expected = {item["filename_template"].format(version=inventory[name]["version"]) for item in inventory[name]["artifacts"]}
        if path.name not in expected:
            raise ReadinessError(f"artifact name/version differs from native owner: {name}")
        keys = ["ownership", "functional", "secret_scan"]
        assembly_passed = True
        if inventory[name]["kind"] == "client":
            keys.append("host")
        else:
            assembly_passed = False
            reference = artifact.get("assembly_manifest")
            if reference:
                assembly_path, _ = _file(reference, base, limit=MAX_EVIDENCE_BYTES)
                if assembly_path.name != "wheelhouse.json":
                    raise ReadinessError("native assembly reference must identify wheelhouse.json")
                cache_key = (assembly_path, reference["sha256"])
                if cache_key not in assemblies:
                    assemblies[cache_key] = verify_wheelhouse(assembly_path.parent)
                    _file(reference, base, limit=MAX_EVIDENCE_BYTES)
                assembly = assemblies[cache_key]
                native_target = assembly["target"]
                expected_target = f"{native_target['platform']}/{native_target['machine']}/python-{native_target['python']}"
                record = {"filename": path.name, "name": name, "version": inventory[name]["version"], "size": len(raw), "sha256": artifact["file"]["sha256"]}
                if assembly["source_sha256"] != source_identity.hexdigest() or record not in assembly["wheels"] or target != expected_target:
                    raise ReadinessError("native artifact source, wheel record or target differs from bound assembly")
                assembly_passed = True
            gate(f"artifact:{name}:{target}:assembly", assembly_passed, "Native wheelhouse must verify and bind the exact reviewed source digest, wheel record and target.", kind="local-observation")
        outcomes = [check(f"artifact:{name}:{target}:{key}", artifact.get(key)) for key in keys]
        outcomes.append(assembly_passed)
        if all(outcomes):
            qualified.add(name)
        if name in selected:
            artifact_digests.add(artifact["file"]["sha256"])
            selected_results.append(all(outcomes))
        targets.append({"component": name, "target": target, "sha256": artifact["file"]["sha256"], "size": len(raw), "checks_satisfied": all(outcomes)})
    gate("selected-artifacts", set(selected).issubset(qualified) and all(selected_results), "Each selected native owner needs exact artifact, target, ownership, functional and secret-scan results; IDE clients also need a host result.")

    hosted = evidence.get("hosted")
    controls = hosted["checks"] if hosted else {}
    if hosted:
        _file(hosted["evidence"], base)
    for name in (*SOURCE_CONTROLS, *RELEASE_CONTROLS):
        gate(f"hosted:{name}", controls.get(name) is True, "Requires a reviewed hosted observation, not checked-in policy or configured CI matrix.")

    def reporting(name: str) -> bool:
        value = evidence.get(name)
        if value:
            _file(value["evidence"], base)
        passed = bool(value and value["owner"].strip() and value["route"].strip() and value["confidential"])
        return gate(name, passed, "Responsible owner and tested confidential route must be explicitly attested; no report is sent by this tool.")

    reporting("security_reporting")
    conduct = reporting("conduct_reporting")
    if evidence.get("conduct_reporting") and evidence.get("security_reporting") and evidence["conduct_reporting"]["route"].strip().casefold() == evidence["security_reporting"]["route"].strip().casefold():
        conduct = gate("conduct_reporting", False, "Conduct and vulnerability reports require separate routes.")
    policy = False
    if evidence.get("conduct_policy"):
        path, _raw = _file(evidence["conduct_policy"], base)
        reviewed_policy = next((row for row in plan["files"] if row["path"] == "CODE_OF_CONDUCT.md"), None)
        policy = bool(path == (root / "CODE_OF_CONDUCT.md").resolve()
                      and reviewed_policy
                      and evidence["conduct_policy"]["sha256"] == reviewed_policy["sha256"])
    gate("conduct-policy", policy, "A reviewed governing CODE_OF_CONDUCT.md is required; no contact is invented.", kind="local-observation")
    community_policy = policy and conduct
    gate("community-safe-state", community_policy or controls.get("community_intake_disabled") is True, "Community intake must remain closed until governing policy, owner and confidential conduct route exist.")

    approval = evidence.get("approval")
    approval_valid = bool(approval)
    if approval:
        _file(approval["evidence"], base)
        actor = approval["actor"].strip().casefold()
        author = approval["source_author"].strip().casefold()
        if approval["mode"] == "bootstrap-same-author":
            approval_valid = actor == author == "zestehl" and controls.get("independent_review_required") is False
        else:
            approval_valid = bool(actor and author and actor != author and controls.get("eligible_approval_path") is True)
    for action in ("publish-source", "publish-artifacts", "enable-community"):
        passed = bool(approval_valid and action in approval["actions"])
        if action == "publish-artifacts":
            passed = passed and set(approval["artifact_sha256"]) == artifact_digests
        gate(f"approval:{action}", passed, "Exact-source, action-scoped maintainer decision required. Same-author bootstrap never counts as independent review.")

    migration = evidence.get("migration_decision")
    migration_passed = False
    if migration:
        _file(migration["evidence"], base)
        migration_passed = bool(hosted and migration["destination_head"] == hosted["destination_head"])
        if migration["strategy"] == "new-root-empty-destination":
            migration_passed = migration_passed and migration["destination_head"] is None
        if migration["strategy"] == "replace-existing-public-history":
            migration_passed = migration_passed and bool(approval_valid and "replace-public-history" in approval["actions"])
    gate("migration-decision", migration_passed, "Reviewed strategy must bind the observed destination HEAD. Source publication approval alone never authorizes replacing existing history.")

    license_decision = evidence.get("license_decision")
    license_passed = False
    if license_decision:
        _file(license_decision["evidence"], base)
        source_license = next((row["sha256"] for row in plan["files"] if row["path"] == "LICENSE"), None)
        license_passed = bool(hosted and source_license and license_decision["source_license_sha256"] == source_license
                              and license_decision["destination_license_sha256"] == hosted["destination_license_sha256"]
                              and license_decision["intended_spdx"] == "LGPL-3.0-only")
    gate("license-decision", license_passed, "Explicit maintainer decision must bind both license observations and agree with the currently validated native LGPL-3.0-only authority. This is not a legal conclusion or license change.")

    local = ["source-clean", "source-secret-scan", "full-validation"]
    source = [*local, *(f"hosted:{name}" for name in SOURCE_CONTROLS), "security_reporting", "community-safe-state", "migration-decision", "license-decision", "approval:publish-source"]
    release = [*source, "selected-artifacts", *(f"hosted:{name}" for name in RELEASE_CONTROLS), "approval:publish-artifacts"]
    community = ["source-clean", *(f"hosted:{name}" for name in SOURCE_CONTROLS), "security_reporting", "conduct-policy", "conduct_reporting", "approval:enable-community"]
    scopes = {}
    for name, required in (("local", local), ("source", source), ("release", release), ("community", community)):
        blockers = [key for key in required if gates[key]["state"] != "satisfied"]
        scopes[name] = {"ready": not blockers, "blockers": blockers}
    return {"format": "workbench-publication-readiness-v1", "schema_version": 1, "source_revision": plan["source_revision"], "git_tree_oid": plan["git_tree_oid"], "destination": plan["destination"], "selected_components": selected, "qualified_targets": targets, "hosted_observed_at": hosted["observed_at"] if hosted else None, "gates": gates, "scopes": scopes, "mutations_performed": False, "limitations": ["Readiness verifies local bindings and operator assertions, not their independent authenticity or authorization.", "Hosted observations may become stale; reobserve immediately before the exact approved action.", "No support is implied for unlisted targets; this tool never changes builder qualified:false."]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--component", action="append")
    parser.add_argument("--require-ready", choices=("local", "source", "release", "community"))
    args = parser.parse_args(argv)
    try:
        result = readiness(args.revision, args.evidence, components=args.component)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 1 if args.require_ready and not result["scopes"][args.require_ready]["ready"] else 0
    except (ReadinessError, PublicExportError, PublicRepositoryError, OSError, ValueError) as exc:
        print(f"publication readiness failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
