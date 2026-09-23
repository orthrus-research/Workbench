"""V2 material declarations with import-aware flags and value rules."""

from __future__ import annotations

from copy import deepcopy
import hashlib
from typing import Any, Mapping

from workbench_material_semantics import MaterialSemanticsPolicy

from .declarations import build_source_declarations, source_declaration
from ._material_declarations_core import (
    ExpectedMaterialClosurePolicy,
    MaterialBuilderOperation,
    MaterialDeclarationPolicy,
    _expected_closure,
    _normalize_material_declarations,
)
from .material_expressions import (
    evaluate_conditional_flag_rule,
    parse_import_scope,
    resolve_flag_expression,
)
from .model import canonical_bytes, content_id


MATERIAL_DECLARATION_NORMALIZER_V2 = (
    "workbench-pack-material-declaration-normalizer-v2"
)


def normalize_material_declarations_v2(
    sources: Mapping[str, str | bytes],
    policy: MaterialDeclarationPolicy,
    semantics: MaterialSemanticsPolicy,
) -> dict[str, Any]:
    """Normalize the exact lexical surface with V2 semantic resolution."""

    if policy.expected_closure.policy_id != semantics.runtime_policy_id:
        raise ValueError(
            "material declaration policy does not bind the semantic runtime view"
        )
    if policy.pack_profile_id != semantics.pack_profile_id:
        raise ValueError("material declaration and semantic pack profiles differ")
    decoded_sources = {
        path: value if isinstance(value, str) else value.decode("utf-8")
        for path, value in sources.items()
    }
    import_scopes = {
        path: parse_import_scope(source)
        for path, source in decoded_sources.items()
    }
    semantics_sha256 = semantics.sha256
    normalizer_binding = {
        "format": MATERIAL_DECLARATION_NORMALIZER_V2,
        "policy_id": policy.policy_id,
        "policy_sha256": policy.sha256,
        "material_semantics_policy_id": semantics.policy_id,
        "material_semantics_policy_sha256": semantics_sha256,
        "expected_closure_policy_id": policy.expected_closure.policy_id,
        "expected_closure_policy_sha256": policy.expected_closure.sha256,
    }
    base = _normalize_material_declarations(sources, policy)
    declarations: list[dict[str, Any]] = []
    for source_row in base["declarations"]:
        row = deepcopy(source_row)
        path = row["provenance"]["source"]["path"]
        scope = import_scopes[path]
        attributes = row["attributes"]
        core = attributes["declared_material_core"]
        issues = set(attributes["issues"])
        uncertainties = set(attributes["uncertainties"])

        declared_flags: set[str] = set()
        flag_resolution: list[dict[str, Any]] = []
        unresolved: list[dict[str, Any]] = []
        for operation_index, operation in enumerate(attributes["operations"]):
            if operation["terminal"] != "flags":
                continue
            for argument_index, argument in enumerate(operation["arguments"]):
                resolved = resolve_flag_expression(
                    argument["expression"], semantics, scope
                )
                resolution_row = {
                    "operation_index": operation_index,
                    "argument_index": argument_index,
                    "span": argument["span"],
                    **resolved.to_dict(),
                }
                flag_resolution.append(resolution_row)
                if resolved.state == "exact":
                    declared_flags.update(resolved.flag_names)
                else:
                    unresolved.append(resolution_row)
        if unresolved:
            uncertainties.add("flag:expression-unresolved")
        else:
            uncertainties.discard("flag:expression-unresolved")

        declared_properties = set(core["declared_property_keys"])
        understood = not any(
            uncertainty.startswith("operation:unsupported:")
            for uncertainty in uncertainties
        )
        preliminary = _expected_closure(
            declared_properties,
            declared_flags,
            policy,
            complete=attributes["builder_complete"],
            understood=understood,
            flags_understood=not unresolved,
        )
        issues.update(preliminary.pop("issues"))

        conditional_rows: list[dict[str, Any]] = []
        conditional_flags: set[str] = set()
        conditional_unknown = False
        rules_by_operation: dict[str, list[Any]] = {}
        for rule in semantics.conditional_flag_rules:
            rules_by_operation.setdefault(rule.trigger_operation, []).append(rule)
        for operation_index, operation in enumerate(attributes["operations"]):
            for rule in rules_by_operation.get(operation["terminal"], []):
                evaluated = evaluate_conditional_flag_rule(
                    rule,
                    [argument["expression"] for argument in operation["arguments"]],
                    preliminary["properties"]["keys"],
                    semantics,
                    scope,
                )
                evaluated["operation_index"] = operation_index
                evaluated["operation_span"] = operation["span"]
                conditional_rows.append(evaluated)
                conditional_flags.update(evaluated["added_flags"])
                if evaluated["state"] == "unknown":
                    conditional_unknown = True
                    uncertainties.add(f"flag:value-rule-unresolved:{rule.rule_id}")

        closure = _expected_closure(
            declared_properties,
            declared_flags | conditional_flags,
            policy,
            complete=attributes["builder_complete"],
            understood=understood,
            flags_understood=not unresolved and not conditional_unknown,
        )
        issues.update(closure.pop("issues"))
        existing_additions = {
            row["name"]: row for row in closure["flags"]["added_by_verification"]
        }
        for name in sorted(conditional_flags):
            existing = existing_additions.get(name)
            reason = next(
                f"conditional-rule:{row['rule_id']}"
                for row in conditional_rows
                if row["state"] == "applied" and name in row["added_flags"]
            )
            if existing is None:
                closure["flags"]["added_by_verification"].append(
                    {"name": name, "reasons": [reason]}
                )
            elif reason not in existing["reasons"]:
                existing["reasons"].append(reason)
                existing["reasons"].sort()
        closure["flags"]["added_by_verification"].sort(
            key=lambda value: value["name"].encode("utf-8")
        )

        core["declared_flag_names"] = sorted(declared_flags)
        core["unresolved_flag_expressions"] = unresolved
        core["flag_resolution"] = flag_resolution
        core["conditional_flag_rules"] = conditional_rows
        core["expected_verified_closure"] = closure
        attributes["declared_material_core_sha256"] = hashlib.sha256(
            canonical_bytes(core)
        ).hexdigest()
        attributes["issues"] = sorted(issues)
        attributes["uncertainties"] = sorted(uncertainties)
        attributes["normalization_status"] = (
            "exact-static"
            if not attributes["issues"] and not attributes["uncertainties"]
            else "frontier-static"
        )
        attributes["normalizer"] = normalizer_binding
        declarations.append(
            source_declaration(
                semantic_descriptor=row["semantic_descriptor"],
                attributes=attributes,
                lifecycle=row["lifecycle"],
                provenance=row["provenance"],
                source_effect_id=row["source_effect_id"],
            )
        )

    program_id = content_id(
        "workbench-pack-material-source-program-v2:sha256:",
        {
            "base_program_id": base["binding"]["program_id"],
            "material_semantics_policy_sha256": semantics_sha256,
        },
    )
    return build_source_declarations(
        program_id=program_id,
        pack_profile_id=policy.pack_profile_id,
        platform_profile_id=policy.expected_closure.platform_profile,
        source_sha256=base["binding"]["source_sha256"],
        declarations=declarations,
        source_kind=MATERIAL_DECLARATION_NORMALIZER_V2,
    )


__all__ = [
    "ExpectedMaterialClosurePolicy",
    "MATERIAL_DECLARATION_NORMALIZER_V2",
    "MaterialBuilderOperation",
    "MaterialDeclarationPolicy",
    "normalize_material_declarations_v2",
]
