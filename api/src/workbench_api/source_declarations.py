"""Independently readable source declaration and navigation V1 contracts.

These validate existing producer records without loading a parser, a profile or
an executing product. V1 retains Pack Program Studio's original authority; it
is not a generic producer admission or a claim that source declarations ran.
"""

from __future__ import annotations

from hashlib import sha256
import json
import re
from typing import Any, Mapping

from .source_locations import validate_location_shape, verify_source_location

DECLARATION_FORMAT = "workbench-pack-source-declarations-v1"
DECLARATION_SCHEMA_VERSION = 1
MAX_DECLARATIONS = 100_000
MAX_RELATIONSHIPS = 250_000


class SourceDeclarationError(RuntimeError):
    """The original static declaration record contract is invalid."""


class SourceNavigationContractError(ValueError):
    """The content-bound navigation record contract is invalid."""


def _content_id(prefix: str, value: Any) -> str:
    # Declaration V1 uses unescaped Unicode; typed navigation keys below do not.
    encoded = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return prefix + sha256(encoded).hexdigest()


def declaration_set_identity(value: Mapping[str, Any]) -> str:
    payload = dict(value)
    payload.pop("declaration_set_id", None)
    return _content_id("workbench-pack-source-declarations:sha256:", payload)


def validate_source_declarations(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SourceDeclarationError("source declaration feed must be an object")
    required = {
        "format",
        "schema_version",
        "declaration_set_id",
        "authority",
        "binding",
        "declarations",
        "summary",
        "limitations",
    }
    if set(value) != required:
        raise SourceDeclarationError("source declaration feed has unexpected keys")
    if value["format"] != DECLARATION_FORMAT or value["schema_version"] != 1:
        raise SourceDeclarationError("unsupported source declaration feed format")
    if value["declaration_set_id"] != declaration_set_identity(value):
        raise SourceDeclarationError("source declaration feed identity does not match content")
    authority = value["authority"]
    if not isinstance(authority, Mapping) or authority.get("owner") != "Pack Program Studio":
        raise SourceDeclarationError("source declaration authority must remain Pack Program Studio")
    if authority.get("runtime_authority") != "none":
        raise SourceDeclarationError("source declaration feed cannot claim runtime authority")
    rows = value["declarations"]
    if not isinstance(rows, list):
        raise SourceDeclarationError("source declarations must be a list")
    identities = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise SourceDeclarationError("source declaration must be an object")
        expected = {
            "source_declaration_id",
            "semantic_descriptor",
            "evidence_state",
            "attributes",
            "lifecycle",
            "provenance",
            "source_effect_id",
            "limitations",
        }
        if set(row) != expected or row.get("evidence_state") != "static-candidate":
            raise SourceDeclarationError("source declaration has an invalid shape or state")
        _semantic_descriptor(row["semantic_descriptor"])
        payload = {key: item for key, item in row.items() if key != "source_declaration_id"}
        expected_id = _content_id("workbench-pack-source-declaration:sha256:", payload)
        if row.get("source_declaration_id") != expected_id:
            raise SourceDeclarationError("source declaration identity does not match content")
        provenance = row.get("provenance")
        if not isinstance(provenance, Mapping) or provenance.get("authority") != "Pack Program Studio":
            raise SourceDeclarationError("source declaration provenance lost its authority")
        identities.append(row["source_declaration_id"])
    if len(identities) != len(set(identities)):
        raise SourceDeclarationError("source declaration identities are duplicate")
    summary = value["summary"]
    if not isinstance(summary, Mapping) or summary.get("declarations") != len(rows):
        raise SourceDeclarationError("source declaration summary is stale")
    return dict(value)


def _semantic_descriptor(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"domain", "kind", "key"}:
        raise SourceDeclarationError("semantic descriptor must contain domain, kind, and key")
    if not isinstance(value["domain"], str) or not value["domain"]:
        raise SourceDeclarationError("semantic descriptor domain is missing")
    if not isinstance(value["kind"], str) or not value["kind"]:
        raise SourceDeclarationError("semantic descriptor kind is missing")
    if not isinstance(value["key"], Mapping) or not value["key"]:
        raise SourceDeclarationError("semantic descriptor key is missing")
    return {"domain": value["domain"], "kind": value["kind"], "key": dict(value["key"])}


def semantic_key(kind: str, key: Mapping[str, Any], *, domain: str) -> dict[str, Any]:
    if (
        not isinstance(kind, str)
        or not isinstance(domain, str)
        or not 1 <= len(kind) <= 128
        or not 1 <= len(domain) <= 128
        or not isinstance(key, Mapping)
        or not key
    ):
        raise SourceNavigationContractError("semantic identity must be typed and nonempty")
    return {"domain": domain, "kind": kind, "key": dict(key)}


def key_identity(key: Mapping[str, Any]) -> str:
    if not isinstance(key, Mapping) or set(key) != {"domain", "kind", "key"}:
        raise SourceNavigationContractError("invalid typed semantic identity")
    semantic_key(key["kind"], key["key"], domain=key["domain"])
    encoded = json.dumps(key, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(encoded) > 64 * 1024:
        raise SourceNavigationContractError("semantic identity exceeds its bound")
    return encoded


def validate_navigation_declarations(
    feed, *, sources: Mapping[str, bytes] | None = None
):
    feed = validate_source_declarations(feed)
    if feed["binding"].get("navigation_contract") != 1:
        raise SourceNavigationContractError("unsupported source navigation contract")
    binding = feed["binding"]
    if (
        not isinstance(binding.get("source_observation"), dict)
        or not isinstance(binding.get("source_interpreter"), dict)
        or not isinstance(binding.get("selected_profile"), dict)
        or re.fullmatch(r"[0-9a-f]{64}", str(binding.get("normalizer_sha256"))) is None
        or binding["source_observation"].get("source_sha256")
        != binding["source_sha256"]
    ):
        raise SourceNavigationContractError("source navigation binding is incomplete")
    rows = feed["declarations"]
    if len(rows) > MAX_DECLARATIONS:
        raise SourceNavigationContractError("source declarations exceed their bound")
    relationships = 0
    for row in rows:
        key_identity(row["semantic_descriptor"])
        nav = row["attributes"].get("navigation")
        if not isinstance(nav, dict) or set(nav) != {
            "kind",
            "label",
            "location",
            "provides",
            "references",
            "issues",
        }:
            raise SourceNavigationContractError("declaration lacks its navigation contract")
        if nav["kind"] not in {
            "recipe",
            "material",
            "quest",
            "quest-line",
            "unsupported",
        }:
            raise SourceNavigationContractError("unsupported navigation declaration kind")
        if (
            not isinstance(nav["label"], str)
            or len(nav["label"]) > 4096
            or not isinstance(nav["issues"], list)
            or len(nav["issues"]) > 1000
            or not isinstance(nav["provides"], list)
            or len(nav["provides"]) != 1
            or nav["provides"] != [row["semantic_descriptor"]]
            or not isinstance(nav["references"], list)
        ):
            raise SourceNavigationContractError("invalid declaration label or issues")
        validate_location_shape(nav["location"])
        if sources is not None:
            path = nav["location"]["path"]
            if path not in sources:
                raise SourceNavigationContractError(
                    "declaration escaped its captured source inputs"
                )
            verify_source_location(sources[path], nav["location"])
        for key in nav["provides"]:
            key_identity(key)
        for reference in nav["references"]:
            if (
                not isinstance(reference, dict)
                or set(reference) != {"relation", "target", "details", "state"}
                or reference["state"] not in {"declared", "unresolved"}
                or not isinstance(reference["relation"], str)
                or re.fullmatch(r"[a-z][a-z-]{0,63}", reference["relation"]) is None
                or not isinstance(reference["details"], dict)
                or (reference["target"] is None) != (reference["state"] == "unresolved")
            ):
                raise SourceNavigationContractError("invalid declared relationship")
            if reference["target"] is not None:
                key_identity(reference["target"])
        relationships += len(nav["references"]) + len(nav["provides"])
        if relationships > MAX_RELATIONSHIPS:
            raise SourceNavigationContractError("source relationships exceed their bound")
    return feed
