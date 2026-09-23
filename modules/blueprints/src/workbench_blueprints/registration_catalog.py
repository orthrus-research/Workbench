"""Load profile-owned registration families and wizard form schemas."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any, NoReturn


MAX_CATALOG_BYTES = 2 * 1024 * 1024
READY_RENDERERS = frozenset({
    "gregtech-material-backed-fluid-v1",
    "gregtech-machine-recipe-v1",
    "forge-ore-dictionary-entry-v1",
})
QUESTION_TYPES = frozenset({
    "string",
    "color",
    "path",
    "choice",
    "integer",
    "ingredient",
    "ingredient-list",
    "fluid-list",
})
RUNTIME_SOURCES = frozenset({
    "groovy-postinit-scripts",
    "recipe-map-aliases",
})
RENDERER_QUESTION_IDS = {
    "gregtech-material-backed-fluid-v1": {
        "name", "color", "translation", "symbol",
    },
    "gregtech-machine-recipe-v1": {
        "script",
        "recipe_map",
        "item_inputs",
        "fluid_inputs",
        "item_outputs",
        "fluid_outputs",
        "duration",
        "voltage_tier",
    },
    "forge-ore-dictionary-entry-v1": {"ore_name", "ingredient"},
}


class RegistrationCatalogError(ValueError):
    """Raised when profile construction knowledge is malformed."""


def _fail(message: str) -> NoReturn:
    raise RegistrationCatalogError(message)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _closed(
    value: Any,
    required: set[str],
    optional: set[str],
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(f"{label} must be an object")
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - required - optional)
    if missing:
        _fail(f"{label} lacks fields: {', '.join(missing)}")
    if unknown:
        _fail(f"{label} has unexpected fields: {', '.join(unknown)}")
    return value


def _identity(value: Any, label: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[a-z][a-z0-9-]*", value) is None:
        _fail(f"{label} must be a lowercase kebab-case identity")
    return value


def _nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(f"{label} must be a non-empty string")
    return value


def _strings(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        _fail(f"{label} must be a string array")
    return value


def _safe_relative(value: Any, label: str) -> str:
    text = _nonempty(value, label)
    if "\\" in text:
        _fail(f"{label} must be a portable relative path")
    path = PurePosixPath(text)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        _fail(f"{label} must be a portable relative path")
    return path.as_posix()


def _validate_question(value: Any, label: str) -> dict[str, Any]:
    question = _closed(
        value,
        {"id", "label", "type", "required", "description"},
        {"default", "minimum", "maximum", "choices", "runtime_source"},
        label,
    )
    question_id = question["id"]
    if not isinstance(question_id, str) or re.fullmatch(
        r"[a-z][a-z0-9_]*", question_id
    ) is None:
        _fail(f"{label}.id is invalid")
    _nonempty(question["label"], f"{label}.label")
    _nonempty(question["description"], f"{label}.description")
    question_type = question["type"]
    if question_type not in QUESTION_TYPES:
        _fail(f"{label}.type is unsupported")
    if not isinstance(question["required"], bool):
        _fail(f"{label}.required must be boolean")
    if "minimum" in question and type(question["minimum"]) is not int:
        _fail(f"{label}.minimum must be an integer")
    if "maximum" in question and type(question["maximum"]) is not int:
        _fail(f"{label}.maximum must be an integer")
    if (
        "minimum" in question
        and "maximum" in question
        and question["minimum"] > question["maximum"]
    ):
        _fail(f"{label} has an inverted integer range")
    if question_type != "integer" and any(
        field in question for field in ("minimum", "maximum")
    ):
        _fail(f"{label} has an integer range on a non-integer question")
    if "choices" in question:
        choices = _strings(question["choices"], f"{label}.choices")
        if not choices or len(choices) != len(set(choices)):
            _fail(f"{label}.choices must be unique and non-empty")
        if question_type != "choice":
            _fail(f"{label}.choices is valid only for a choice question")
    if question_type == "choice" and not any(
        field in question for field in ("choices", "runtime_source")
    ):
        _fail(f"{label} choice lacks choices or runtime_source")
    if "runtime_source" in question:
        source = _nonempty(
            question["runtime_source"], f"{label}.runtime_source"
        )
        if source not in RUNTIME_SOURCES:
            _fail(f"{label}.runtime_source is unsupported")
        if question_type not in {"choice", "path"}:
            _fail(f"{label}.runtime_source requires a choice or path question")
        if "choices" in question:
            _fail(f"{label} cannot combine choices and runtime_source")
    if "default" in question:
        default = question["default"]
        if question_type in {"string", "color", "path", "choice"}:
            valid_default = isinstance(default, str) and bool(default)
        elif question_type == "integer":
            valid_default = type(default) is int and (
                default >= question.get("minimum", default)
                and default <= question.get("maximum", default)
            )
        elif question_type in {"ingredient-list", "fluid-list"}:
            valid_default = isinstance(default, list)
        else:
            valid_default = isinstance(default, dict)
        if not valid_default:
            _fail(f"{label}.default does not match its question type")
        if "choices" in question and default not in question["choices"]:
            _fail(f"{label}.default is not an admitted choice")
    return question


def _validate_authority_spec(
    value: Any,
    renderer: str,
    label: str,
) -> dict[str, Any]:
    if renderer == "gregtech-material-backed-fluid-v1":
        spec = _closed(value, {"kind", "pattern_path"}, set(), label)
        if spec["kind"] != "convention-patch":
            _fail(f"{label}.kind does not match its renderer")
        _safe_relative(spec["pattern_path"], f"{label}.pattern_path")
        return spec
    if renderer == "gregtech-machine-recipe-v1":
        spec = _closed(
            value,
            {
                "kind",
                "owner_root",
                "recipe_maps_path",
                "required_imports",
                "indentation",
            },
            set(),
            label,
        )
        if spec["kind"] != "gregtech-postinit-recipe":
            _fail(f"{label}.kind does not match its renderer")
        _safe_relative(spec["owner_root"], f"{label}.owner_root")
        _safe_relative(spec["recipe_maps_path"], f"{label}.recipe_maps_path")
        imports = _strings(spec["required_imports"], f"{label}.required_imports")
        if not imports or len(imports) != len(set(imports)):
            _fail(f"{label}.required_imports must be unique and non-empty")
        indentation = spec["indentation"]
        if (
            not isinstance(indentation, str)
            or not indentation
            or len(indentation) > 16
            or indentation.strip(" ")
        ):
            _fail(f"{label}.indentation must contain 1 through 16 spaces")
        return spec
    if renderer == "forge-ore-dictionary-entry-v1":
        spec = _closed(
            value,
            {"kind", "owner_path", "required_markers"},
            set(),
            label,
        )
        if spec["kind"] != "groovyscript-ore-dictionary":
            _fail(f"{label}.kind does not match its renderer")
        _safe_relative(spec["owner_path"], f"{label}.owner_path")
        markers = _strings(spec["required_markers"], f"{label}.required_markers")
        if not markers or len(markers) != len(set(markers)):
            _fail(f"{label}.required_markers must be unique and non-empty")
        return spec
    _fail(f"{label} has no admitted renderer")


def _validate_catalog(value: Any, source: str) -> dict[str, Any]:
    catalog = _closed(
        value,
        {
            "schema_version",
            "format",
            "profile_family_id",
            "authority_order",
            "observations",
            "families",
            "patterns",
        },
        set(),
        source,
    )
    if (
        catalog["schema_version"] != 1
        or catalog["format"] != "workbench-registration-catalog-v1"
    ):
        _fail(f"{source} has an unsupported catalog format")
    _nonempty(catalog["profile_family_id"], f"{source}.profile_family_id")

    authorities = catalog["authority_order"]
    if not isinstance(authorities, list) or not authorities:
        _fail(f"{source}.authority_order must be non-empty")
    authority_ranks: dict[str, int] = {}
    previous_rank = 0
    for index, value in enumerate(authorities):
        row = _closed(
            value,
            {"id", "rank", "description"},
            set(),
            f"{source}.authority_order[{index}]",
        )
        authority_id = _identity(row["id"], f"authority {index} id")
        rank = row["rank"]
        if type(rank) is not int or rank <= previous_rank:
            _fail(f"{source}.authority_order ranks must increase")
        if authority_id in authority_ranks:
            _fail(f"{source} duplicates authority {authority_id}")
        _nonempty(row["description"], f"authority {authority_id} description")
        authority_ranks[authority_id] = rank
        previous_rank = rank

    observations = catalog["observations"]
    if not isinstance(observations, list) or not observations:
        _fail(f"{source}.observations must be non-empty")
    observed_authorities: set[str] = set()
    for index, value in enumerate(observations):
        row = _closed(
            value,
            {"authority", "repository", "revision", "branch", "observed_date"},
            {"census"},
            f"{source}.observations[{index}]",
        )
        authority = row["authority"]
        if authority not in authority_ranks or authority in observed_authorities:
            _fail(f"{source} has an invalid observation authority")
        observed_authorities.add(authority)
        if not isinstance(row["revision"], str) or re.fullmatch(
            r"[0-9a-f]{40}", row["revision"]
        ) is None:
            _fail(f"{source} observation revision is invalid")
        for field in ("repository", "branch", "observed_date"):
            _nonempty(row[field], f"observation {index} {field}")
        if "census" in row and not isinstance(row["census"], dict):
            _fail(f"{source} observation census must be an object")

    families = catalog["families"]
    if not isinstance(families, list) or not families:
        _fail(f"{source}.families must be non-empty")
    family_ids: set[str] = set()
    family_patterns: dict[str, set[str]] = {}
    for index, value in enumerate(families):
        family = _closed(
            value,
            {
                "id",
                "label",
                "description",
                "lifecycle",
                "selected_authority",
                "authority_chain",
                "companion_surfaces",
                "active_instance_support",
                "pattern_keys",
            },
            set(),
            f"{source}.families[{index}]",
        )
        family_id = _identity(family["id"], f"family {index} id")
        if family_id in family_ids:
            _fail(f"{source} duplicates family {family_id}")
        family_ids.add(family_id)
        for field in ("label", "description", "lifecycle"):
            _nonempty(family[field], f"family {family_id} {field}")
        if family["active_instance_support"] not in {
            "ready", "planned", "source-build-required", "observe-only"
        }:
            _fail(f"family {family_id} has invalid active-instance support")
        _strings(family["companion_surfaces"], f"family {family_id} companions")
        pattern_keys = {
            _identity(item, f"family {family_id} pattern key")
            for item in _strings(family["pattern_keys"], f"family {family_id} patterns")
        }
        if len(pattern_keys) != len(family["pattern_keys"]):
            _fail(f"family {family_id} duplicates pattern keys")
        family_patterns[family_id] = pattern_keys

        chain = family["authority_chain"]
        if not isinstance(chain, list) or not chain:
            _fail(f"family {family_id} authority chain must be non-empty")
        chain_ids: set[str] = set()
        last_rank = 0
        constructor_authority: str | None = None
        for chain_index, chain_value in enumerate(chain):
            row = _closed(
                chain_value,
                {"authority", "role", "paths", "note"},
                set(),
                f"family {family_id} authority_chain[{chain_index}]",
            )
            authority = row["authority"]
            rank = authority_ranks.get(authority)
            if rank is None or rank <= last_rank or authority in chain_ids:
                _fail(f"family {family_id} authority chain violates priority order")
            chain_ids.add(authority)
            last_rank = rank
            if row["role"] not in {
                "constructor", "data-definition", "binding-only", "fallback", "absent"
            }:
                _fail(f"family {family_id} has invalid authority role")
            paths = _strings(row["paths"], f"family {family_id} authority paths")
            if row["role"] in {"constructor", "data-definition", "binding-only"} and not paths:
                _fail(f"family {family_id} authority role requires source paths")
            _nonempty(row["note"], f"family {family_id} authority note")
            if constructor_authority is None and row["role"] in {
                "constructor", "data-definition"
            }:
                constructor_authority = authority
        if family["selected_authority"] != constructor_authority:
            _fail(
                f"family {family_id} selected authority is not the first "
                "constructor in priority order"
            )

    patterns = catalog["patterns"]
    if not isinstance(patterns, list) or not patterns:
        _fail(f"{source}.patterns must be non-empty")
    pattern_ids: set[str] = set()
    patterns_by_family: dict[str, set[str]] = {item: set() for item in family_ids}
    for index, value in enumerate(patterns):
        pattern = _closed(
            value,
            {
                "key", "label", "family_id", "lifecycle", "renderer",
                "summary", "authority_spec", "questions", "limitations",
            },
            set(),
            f"{source}.patterns[{index}]",
        )
        key = _identity(pattern["key"], f"pattern {index} key")
        if key in pattern_ids:
            _fail(f"{source} duplicates pattern {key}")
        pattern_ids.add(key)
        family_id = pattern["family_id"]
        if family_id not in family_ids:
            _fail(f"pattern {key} references unknown family")
        patterns_by_family[family_id].add(key)
        if pattern["renderer"] not in READY_RENDERERS:
            _fail(f"pattern {key} references an unsupported renderer")
        _validate_authority_spec(
            pattern["authority_spec"],
            pattern["renderer"],
            f"pattern {key} authority_spec",
        )
        if pattern["lifecycle"] not in {"experimental", "stable"}:
            _fail(f"pattern {key} has invalid lifecycle")
        for field in ("label", "summary"):
            _nonempty(pattern[field], f"pattern {key} {field}")
        questions = pattern["questions"]
        if not isinstance(questions, list) or not questions:
            _fail(f"pattern {key} requires questions")
        question_ids: set[str] = set()
        for question_index, question_value in enumerate(questions):
            question = _validate_question(
                question_value,
                f"pattern {key} questions[{question_index}]",
            )
            if question["id"] in question_ids:
                _fail(f"pattern {key} duplicates question {question['id']}")
            question_ids.add(question["id"])
        if question_ids != RENDERER_QUESTION_IDS[pattern["renderer"]]:
            _fail(f"pattern {key} questions do not match its renderer")
        _strings(pattern["limitations"], f"pattern {key} limitations")

    for family_id in family_ids:
        if family_patterns[family_id] != patterns_by_family[family_id]:
            _fail(f"family {family_id} pattern index differs from patterns")
    return catalog


def load_registration_catalog(path: Path | str) -> dict[str, Any]:
    """Load, validate, and content-address one profile registration catalog."""

    source = Path(path).resolve()
    if source.is_symlink() or not source.is_file():
        _fail("registration catalog must be a regular file")
    try:
        if source.stat().st_size > MAX_CATALOG_BYTES:
            _fail("registration catalog exceeds the size limit")
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _fail(f"cannot decode registration catalog: {exc}")
    catalog = _validate_catalog(value, str(source))
    return {
        **catalog,
        "catalog_id": "sha256:" + sha256(_canonical_bytes(catalog)).hexdigest(),
    }


__all__ = [
    "RegistrationCatalogError",
    "load_registration_catalog",
]
