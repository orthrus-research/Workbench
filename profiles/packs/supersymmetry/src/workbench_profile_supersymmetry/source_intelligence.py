"""Supersymmetry's source-only Groovy, GT material and BetterQuesting policy."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

from workbench_api.profile_extensions import require_profile_extension
from workbench_api.profiles import profiles
from workbench_pack_program_studio.analyzer import AnalysisContext, analyze_program
from workbench_pack_program_studio.declarations import (
    build_source_declarations,
    source_declaration,
)
from workbench_pack_program_studio.lexer import Argument, calls, tokenize
from workbench_pack_program_studio.material_declarations import (
    normalize_material_declarations_v2,
)
from workbench_pack_program_studio.model import content_id
from workbench_pack_program_studio.profile import load_profile
from workbench_pack_program_studio.source_intelligence import semantic_key
from workbench_pack_program_studio.source_locations import (
    JsonSource,
    character_location,
)

from .betterquesting_source_runtime import (
    extract_betterquesting_sources,
    _typed_get,
    _typed_name,
)
from .gtceu_material_declarations import GTCEU_MATERIAL_DECLARATION_POLICY_V2
from .gtceu_material_semantics import GTCEU_SUPERSYMMETRY_MATERIAL_SEMANTICS_V2

PROFILE_API_VERSION = 1
QUEST_PREFIX = "config/betterquesting/DefaultQuests/"


def _key(kind, key):
    return semantic_key(kind, key, domain="supersymmetry")


def _reference(relation, target, **details):
    return {
        "relation": relation,
        "target": target,
        "details": details,
        "state": "declared" if target is not None else "unresolved",
    }


def _declaration(
    kind,
    label,
    key,
    location,
    *,
    attributes=None,
    references=(),
    issues=(),
    lifecycle=None,
):
    def comparison(value):
        # Profile-owned provenance fields are not domain changes. Keep the
        # original attributes for inspection and exact locations separately.
        if isinstance(value, dict):
            return {
                key: comparison(item)
                for key, item in value.items()
                if key not in {"span", "operation_span", "source_sha256"}
            }
        if isinstance(value, list):
            return [comparison(item) for item in value]
        return value

    return source_declaration(
        semantic_descriptor=key,
        attributes={
            **(attributes or {}),
            "comparison": comparison(attributes or {}),
            "navigation": {
                "kind": kind,
                "label": label,
                "location": location,
                "provides": [key],
                "references": list(references),
                "issues": list(issues),
            },
        },
        lifecycle=lifecycle or {"execution": "not-observed"},
        provenance={
            "authority": "Pack Program Studio",
            "profile_interpreter": "supersymmetry",
            "source": location,
        },
    )


def _ingredient(expression):
    """Bounded literal resolver, using the shared lexer; no expression evaluation."""
    tokens = tokenize(expression)
    resolver_calls = calls(tokens)
    if not resolver_calls:
        return None, {
            "expression": expression,
            "reason": "dynamic-or-unsupported-selector",
        }
    call = resolver_calls[0]
    if call.start != 0 or call.callee not in {"fluid", "ore", "item", "metaitem"}:
        return None, {"expression": expression, "reason": "unsupported-resolver"}
    if not call.arguments or (name := call.arguments[0].exact_string()) is None:
        return None, {"expression": expression, "reason": "dynamic-selector-identity"}
    remainder = tuple(token for token in tokens if token.start >= call.end)
    amount = 1
    if remainder:
        if remainder[0].value != "*":
            return None, {
                "expression": expression,
                "reason": "unsupported-selector-transformation",
            }
        amount = Argument(remainder[1:]).integer()
    details = {"expression": expression, "amount": amount}
    if amount is None or amount <= 0:
        details["quantity_state"] = "unresolved"
    if call.callee == "item":
        metadata = 0 if len(call.arguments) == 1 else call.arguments[1].integer()
        if len(call.arguments) > 2 or metadata is None or ":" not in name:
            return None, {**details, "reason": "unsupported-item-selector"}
        return _key("item", {"id": name, "metadata": metadata, "tag": None}), details
    if len(call.arguments) != 1:
        return None, {**details, "reason": "unsupported-resolver-arity"}
    return _key(
        {"fluid": "fluid", "ore": "ore-dictionary", "metaitem": "metaitem"}[
            call.callee
        ],
        {"name": name},
    ), details


def _recipe_declarations(inputs, program, groovy_prefix):
    rows = []
    for effect in program["effects"]:
        if effect["kind"] not in {"machine-recipe", "crafting-recipe"}:
            continue
        source = effect["source"]
        path = groovy_prefix + "/" + source["path"]
        raw = inputs.sources[path]
        location = character_location(raw, path, source["offset"], source["end_offset"])
        recipe = effect.get("recipe") or {}
        references = []
        issues = []
        for method, expressions in recipe.get("properties", {}).items():
            relation = {
                "inputs": "consumes",
                "fluidInputs": "consumes",
                "notConsumable": "requires-catalyst",
                "outputs": "produces",
                "fluidOutputs": "produces",
                "chancedOutput": "may-produce",
            }.get(method)
            if relation is None:
                continue
            for expression in expressions:
                # Split arguments with the existing balanced-call lexer, not text commas.
                wrapper = calls(tokenize("selector(" + expression + ")"))
                arguments = wrapper[0].arguments if wrapper else ()
                if method == "chancedOutput":
                    arguments = arguments[:1]
                for argument in arguments:
                    target, details = _ingredient(argument.expression)
                    if method == "chancedOutput":
                        details["chance_arguments"] = [
                            arg.expression for arg in wrapper[0].arguments[1:]
                        ]
                        details["chance_evaluation"] = "not-performed"
                    references.append(
                        _reference(relation, target, method=method, **details)
                    )
        if effect["kind"] != "machine-recipe":
            issues.append("crafting-relationships-not-normalized")
        if recipe and not recipe.get("complete"):
            issues.append("recipe-builder-incomplete")
        issues.extend(effect.get("limitations", []))
        key = _key("recipe", {"semantic_key": effect["semantic_key"]})
        label = str(
            recipe.get("recipe_map")
            or effect["fields"].get("recipe_id")
            or "unresolved recipe"
        )
        rows.append(
            _declaration(
                "recipe",
                label,
                key,
                location,
                attributes={
                    "recipe": {
                        key: value
                        for key, value in recipe.items()
                        if key not in {"line_start", "line_end"}
                    },
                    "operation": effect["operation"],
                },
                references=references,
                issues=issues,
                lifecycle=effect["lifecycle"],
            )
        )
    return rows


def _material_declarations(inputs, sources):
    feed = normalize_material_declarations_v2(
        sources,
        GTCEU_MATERIAL_DECLARATION_POLICY_V2,
        GTCEU_SUPERSYMMETRY_MATERIAL_SEMANTICS_V2,
    )
    result = []
    for row in feed["declarations"]:
        attributes = deepcopy(row["attributes"])
        identity = attributes["identity"]
        source = row["provenance"]["source"]
        span = source["span"]
        location = character_location(
            inputs.sources[source["path"]], source["path"], span["start"], span["end"]
        )
        key = (
            _key("material", {"registry_name": identity["resource_location"]})
            if identity.get("resource_location")
            else _key(
                "unresolved-material", {"occurrence": row["source_declaration_id"]}
            )
        )
        references = []
        for form in attributes["form_lens"]["fluid_storage_relationships"]:
            # Only the profile's unadorned default liquid convention is admitted.
            # It is an expected source relation, not a captured FluidRegistry entry.
            target = None
            if (
                form["terminal"] == "liquid"
                and not form["arguments"]
                and identity.get("resource_location")
                and attributes["builder_complete"]
            ):
                target = _key("fluid", {"name": identity["registry_name"]})
            references.append(
                _reference(
                    "declares-fluid-form",
                    target,
                    storage_key=form["storage_key"],
                    basis="profile-expected-default-liquid-name"
                    if target
                    else "unresolved-fluid-name",
                )
            )
        issues = [*attributes["issues"], *attributes["uncertainties"]]
        if attributes["collision_candidates"]:
            issues.append("material-identity-collision")
        result.append(
            _declaration(
                "material",
                identity.get("resource_location") or "unresolved material",
                key,
                location,
                attributes=attributes,
                references=references,
                issues=issues,
                lifecycle=row["lifecycle"],
            )
        )
    return result


def _typed_pointer(value, *parts):
    pointer = ""
    for part in parts:
        if isinstance(value, list):
            value = value[part]
            token = str(part)
        else:
            matches = [key for key in value if _typed_name(key) == str(part)]
            if len(matches) != 1:
                raise ValueError("typed JSON source location is missing or ambiguous")
            token = matches[0]
            value = value[token]
        pointer += "/" + token.replace("~", "~0").replace("/", "~1")
    return pointer


def _quest_item(stack):
    if stack.get("ore_dictionary"):
        return _key("ore-dictionary", {"name": stack["ore_dictionary"]})
    return _key(
        "item",
        {"id": stack["item_id"], "metadata": stack["damage"], "tag": stack.get("tag")},
    )


def _quest_declarations(inputs):
    sources = {
        path[len(QUEST_PREFIX) :]: raw
        for path, raw in inputs.files
        if path.startswith(QUEST_PREFIX) and path.endswith(".json")
    }
    if len(sources) > 10_000 or sum(map(len, sources.values())) > 128 * 1024 * 1024:
        raise ValueError("quest sources exceed their input bound")
    # Decode one file at a time. Duplicate IDs retain their own requirements,
    # and catalog construction stays linear instead of joining every task to
    # every quest by a potentially colliding integer.
    rows = []
    for path, raw in sorted(sources.items()):
        rows.extend(
            _quest_file_declarations(
                inputs, extract_betterquesting_sources({path: raw})
            )
        )
    return rows


def _quest_file_declarations(inputs, catalog):
    rows = []
    for quest in catalog["quests"]:
        qid = quest["quest_id"]
        path = QUEST_PREFIX + quest["relative_path"]
        parsed = JsonSource(inputs.sources[path], path)
        location = parsed.location(_typed_pointer(parsed.value, "questID"))
        references = []
        for requirement in catalog["prerequisites"]:
            if requirement["quest_id"] == qid:
                references.append(
                    _reference(
                        "requires-quest",
                        _key("quest", {"id": requirement["required_quest_id"]}),
                        requirement_type=requirement["requirement_type"],
                        ordinal=requirement["ordinal"],
                    )
                )
        for requirement in catalog["item_requirements"]:
            if requirement["quest_id"] == qid:
                references.append(
                    _reference(
                        "requires-item",
                        _quest_item(requirement["item_stack"]),
                        task_id=requirement["task_id"],
                        amount=requirement["item_stack"]["count"],
                    )
                )
        for requirement in catalog["fluid_requirements"]:
            if requirement["quest_id"] == qid:
                references.append(
                    _reference(
                        "requires-fluid",
                        _key(
                            "fluid", {"name": requirement["fluid_stack"]["fluid_name"]}
                        ),
                        task_id=requirement["task_id"],
                        amount=requirement["fluid_stack"]["amount"],
                    )
                )
        properties = _typed_get(
            _typed_get(parsed.value, "properties", {}), "betterquesting", {}
        )
        label = _typed_get(properties, "name", f"Quest {qid}")
        rows.append(
            _declaration(
                "quest",
                str(label),
                _key("quest", {"id": qid}),
                location,
                attributes={
                    "quest": quest,
                    "tasks": [
                        task for task in catalog["tasks"] if task["quest_id"] == qid
                    ],
                    "rewards": [
                        reward
                        for reward in catalog["rewards"]
                        if reward["quest_id"] == qid
                    ],
                },
                references=references,
                issues=[
                    "player-progress-not-observed",
                    "task-completion-semantics-not-evaluated",
                ],
            )
        )
    for line in catalog["lines"]:
        path = QUEST_PREFIX + line["relative_path"]
        parsed = JsonSource(inputs.sources[path], path)
        rows.append(
            _declaration(
                "quest-line",
                f"Quest line {line['line_id']}",
                _key("quest-line", {"id": line["line_id"]}),
                parsed.location(_typed_pointer(parsed.value, "lineID")),
                attributes={"quest_line": line},
                references=[
                    _reference(
                        "contains-quest",
                        _key("quest", {"id": placement["quest_id"]}),
                        placement=placement,
                    )
                    for placement in catalog["placements"]
                    if placement["line_id"] == line["line_id"]
                ],
            )
        )
    for setting in catalog["settings"]:
        path = QUEST_PREFIX + setting["relative_path"]
        parsed = JsonSource(inputs.sources[path], path)
        rows.append(
            _declaration(
                "unsupported",
                setting["relative_path"],
                _key("quest-settings", {"path": setting["relative_path"]}),
                parsed.location(""),
                attributes={"settings": setting},
                issues=["settings-not-interpreted-for-navigation"],
            )
        )
    return rows


def source_declarations(inputs, *, platform_profile, variant):
    require_profile_extension("workbench.source_interpreters", "supersymmetry")
    if (platform_profile, variant) != ("cleanroom", "cleanroom-provisional"):
        raise ValueError(
            "source navigation requires Supersymmetry / Cleanroom provisional"
        )
    admitted = {profile.id: profile for profile in profiles()}
    if platform_profile not in admitted:
        raise ValueError("selected platform is unavailable")
    profile = load_profile(admitted["supersymmetry"].resource("groovy-program"))
    observation = inputs.observation
    root = Path(url2pathname(urlparse(observation["root_uri"]).path))
    groovy_prefix = profile.value["source_layout"]["groovy_root"]
    sources = {
        path: raw for path, raw in inputs.files if path.startswith(groovy_prefix + "/")
    }
    program = analyze_program(
        root,
        profile,
        context=AnalysisContext(side="client"),
        source_bytes=sources,
        git_binding_override={
            "repository_root": str(root),
            "revision": observation["revision"],
            "dirty": observation["dirty"],
        },
    )
    rows = _recipe_declarations(inputs, program, groovy_prefix)
    rows.extend(
        _material_declarations(
            inputs,
            {
                path: raw
                for path, raw in sources.items()
                if path.lower().endswith(".groovy")
            },
        )
    )
    rows.extend(_quest_declarations(inputs))
    rows.sort(key=lambda row: row["source_declaration_id"])
    identity = content_id(
        "workbench-source-navigation-program:sha256:",
        {"groovy": program["program_id"], "source": observation},
    )
    feed = build_source_declarations(
        program_id=identity,
        pack_profile_id=profile.pack_profile_id,
        platform_profile_id=profile.platform_profile_id,
        source_sha256=observation["source_sha256"],
        declarations=rows,
        source_kind="supersymmetry-source-navigation",
    )
    from workbench_pack_program_studio.declarations import declaration_set_identity

    feed["binding"]["static_analysis_context"] = {
        key: program["binding"][key]
        for key in ("side", "physical_side", "packmode", "debug", "installed_mods")
    }
    feed["binding"]["analysis_warnings"] = program["run_config"]["warnings"]
    feed["declaration_set_id"] = declaration_set_identity(feed)
    return feed
