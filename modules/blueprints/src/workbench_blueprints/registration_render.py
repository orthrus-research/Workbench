"""Render guarded registration updates from reusable wizard answers."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path, PurePosixPath
import re
from typing import Any, NoReturn

from . import convention_patch


MAX_TARGET_BYTES = 8 * 1024 * 1024
IDENTITY_RE = re.compile(r"^[a-z0-9][a-z0-9_.:/-]*$")
METAITEM_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$")
ORE_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
RECIPE_ALIAS_RE = re.compile(
    r"^\s*static\s+final\s+def\s+(?P<alias>[A-Z][A-Z0-9_]*)\s*=\s*"
    r"recipemap\((?P<quote>['\"])(?P<name>[a-z0-9_]+)(?P=quote)\)\s*$",
    re.MULTILINE,
)


class RegistrationRenderError(ValueError):
    """Raised when wizard answers cannot produce a guarded update."""


def _fail(message: str) -> NoReturn:
    raise RegistrationRenderError(message)


def _safe_relative(value: Any, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        _fail(f"{label} must be a portable relative path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        _fail(f"{label} must be a portable relative path")
    return path


def _target_root(value: Path | str) -> Path:
    root = Path(value).expanduser().resolve()
    if not root.is_dir() or root.is_symlink():
        _fail("registration target must be a regular Minecraft payload")
    return root


def _read_target(root: Path, relative: PurePosixPath) -> tuple[Path, bytes, str]:
    path = root.joinpath(*relative.parts)
    if any(
        parent.is_symlink()
        for parent in (path, *path.parents)
        if parent != root.parent
    ):
        _fail(f"registration target contains a symbolic link: {relative}")
    if not path.is_file() or path.is_symlink():
        _fail(f"registration target is not a regular file: {relative}")
    try:
        if path.stat().st_size > MAX_TARGET_BYTES:
            _fail(f"registration target exceeds the size limit: {relative}")
        content = path.read_bytes()
        text = content.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        _fail(f"cannot read UTF-8 registration target {relative}: {exc}")
    return path, content, text


def _operation(relative: PurePosixPath, before: bytes, after: bytes) -> dict[str, Any]:
    if before == after:
        _fail(f"registration produced no change: {relative}")
    return {
        "operation": "update",
        "path": relative.as_posix(),
        "before_sha256": sha256(before).hexdigest(),
        "content_sha256": sha256(after).hexdigest(),
        "content": after,
    }


def _pattern(catalog: dict[str, Any], pattern_key: str) -> dict[str, Any]:
    matches = [row for row in catalog.get("patterns", []) if row.get("key") == pattern_key]
    if len(matches) != 1:
        _fail(f"unknown registration pattern: {pattern_key}")
    return matches[0]


def _answers(pattern: dict[str, Any], supplied: Any) -> dict[str, Any]:
    if not isinstance(supplied, dict):
        _fail("wizard answers must be an object")
    questions = {row["id"]: row for row in pattern["questions"]}
    unknown = sorted(set(supplied) - set(questions))
    if unknown:
        _fail(f"wizard answers contain unknown fields: {', '.join(unknown)}")
    effective: dict[str, Any] = {}
    for question_id, question in questions.items():
        if question_id in supplied:
            value = supplied[question_id]
        elif "default" in question:
            value = question["default"]
        elif question["required"]:
            _fail(f"wizard answer is required: {question_id}")
        else:
            continue
        question_type = question["type"]
        if question_type in {"string", "color", "path", "choice"}:
            if not isinstance(value, str) or not value:
                _fail(f"wizard answer {question_id} must be a non-empty string")
        elif question_type == "integer":
            if type(value) is not int:
                _fail(f"wizard answer {question_id} must be an integer")
            if value < question.get("minimum", value) or value > question.get("maximum", value):
                _fail(f"wizard answer {question_id} is outside its range")
        elif question_type in {"ingredient-list", "fluid-list"}:
            if not isinstance(value, list):
                _fail(f"wizard answer {question_id} must be an array")
        elif question_type == "ingredient" and not isinstance(value, dict):
            _fail(f"wizard answer {question_id} must be an object")
        if "choices" in question and value not in question["choices"]:
            _fail(f"wizard answer {question_id} is not an admitted choice")
        effective[question_id] = value
    return effective


def _slug(value: str) -> str:
    result = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    if not result:
        _fail("developer name does not produce a registry identity")
    return result


def _symbol(value: str) -> str:
    result = "".join(
        part[:1].upper() + part[1:]
        for part in re.findall(r"[A-Za-z0-9]+", value)
    )
    if not result:
        _fail("developer name does not produce a Groovy symbol")
    return result


def _render_material_fluid(
    pattern: dict[str, Any],
    profile_root: Path,
    root: Path,
    answers: dict[str, Any],
    facts: dict[str, Any],
) -> dict[str, Any]:
    census = facts.get("material_census")
    if not isinstance(census, dict):
        _fail("material-backed fluid requires an Atlas runtime census")
    uncertainties = census.get("uncertainties")
    collisions = census.get("collisions")
    observations = census.get("observations")
    occupied = census.get("occupied_values")
    if not all(isinstance(value, list) for value in (
        uncertainties, collisions, observations, occupied
    )):
        _fail("Atlas runtime material census is malformed")
    if any(row.get("kind") == "material-id" for row in collisions if isinstance(row, dict)):
        _fail("material registration is blocked by existing material-ID collisions")

    name = answers["name"]
    if not isinstance(name, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _-]*", name) is None:
        _fail("material name contains unsupported characters")
    color = answers["color"]
    if not isinstance(color, str) or re.fullmatch(r"0x[0-9a-f]{6}", color) is None:
        _fail("material color must use lowercase 0xrrggbb form")
    registry_name = _slug(name)
    if any(
        isinstance(row, dict) and row.get("registry_name") == registry_name
        for row in observations
    ):
        _fail(f"material registry identity already exists: {registry_name}")
    symbol_name = answers.get("symbol", _symbol(name))
    translation = answers.get("translation", name)
    if not isinstance(symbol_name, str) or re.fullmatch(r"[A-Z][A-Za-z0-9]*", symbol_name) is None:
        _fail("Groovy material symbol must be an upper-camel identifier")
    if (
        not isinstance(translation, str)
        or not translation
        or any(character in translation for character in "\r\n=")
    ):
        _fail("material translation is invalid")
    pattern_path = pattern["authority_spec"]["pattern_path"]
    relative_pattern = _safe_relative(pattern_path, "material convention pattern")
    source = (profile_root / Path(*relative_pattern.parts)).resolve()
    if not source.is_relative_to(profile_root.resolve()):
        _fail("material convention pattern escapes its profile")
    try:
        convention = convention_patch.load_pattern(source)
    except convention_patch.ConventionPatchError as exc:
        _fail(f"material convention cannot be loaded: {exc}")
    allocation = convention["allocation"]
    allocation_owner = allocation["owner_path"]
    blocking_uncertainties: list[dict[str, Any]] = []
    for uncertainty in uncertainties:
        if not isinstance(uncertainty, dict):
            _fail("Atlas runtime material uncertainty is malformed")
        material_id = uncertainty.get("material_id")
        if uncertainty.get("path") == allocation_owner or (
            type(material_id) is int
            and allocation["minimum"] <= material_id <= allocation["maximum"]
        ):
            blocking_uncertainties.append(uncertainty)
    if blocking_uncertainties:
        _fail(
            "material registration is blocked by uncertain builders in its "
            "allocation domain"
        )
    try:
        material_id = convention_patch.allocate_first_free(convention, occupied)
        effective = {
            "color": color,
            "material_id": material_id,
            "name": name,
            "registry_name": registry_name,
            "symbol_name": symbol_name,
            "translation": translation,
        }
        operations = convention_patch.render_updates(convention, root, effective)
    except convention_patch.ConventionPatchError as exc:
        _fail(f"material convention cannot be applied: {exc}")
    return {
        "effective_answers": effective,
        "operations": operations,
        "evidence": {
            "material_census_id": census.get("census_id"),
            "observed_material_count": len(observations),
            "nonblocking_builder_uncertainty_count": len(uncertainties),
            "pattern_id": "sha256:" + convention["pattern_sha256"],
        },
    }


def recipe_map_aliases(
    root: Path | str,
    recipe_maps_path: str,
) -> dict[str, str]:
    """Resolve the exact alias-to-registry-name bindings in the target."""

    target = _target_root(root)
    relative = _safe_relative(recipe_maps_path, "recipe-map binding path")
    _path, _content, text = _read_target(target, relative)
    aliases: dict[str, str] = {}
    for match in RECIPE_ALIAS_RE.finditer(text):
        alias = match.group("alias")
        if alias in aliases:
            _fail(f"recipe-map alias is duplicated: {alias}")
        aliases[alias] = match.group("name")
    if not aliases:
        _fail("target Recipemaps.groovy contains no recognized aliases")
    return dict(sorted(aliases.items()))


def groovy_postinit_scripts(
    root: Path | str,
    owner_root: str,
) -> list[str]:
    """List regular existing scripts under the profile-declared owner."""

    target = _target_root(root)
    relative_root = _safe_relative(owner_root, "Groovy recipe owner root")
    scripts_root = target.joinpath(*relative_root.parts)
    if not scripts_root.is_dir() or scripts_root.is_symlink():
        _fail(
            "registration target lacks its regular Groovy recipe owner root: "
            f"{relative_root}"
        )
    scripts: list[str] = []
    for path in sorted(
        scripts_root.rglob("*.groovy"),
        key=lambda item: item.relative_to(target).as_posix(),
    ):
        if path.is_symlink() or not path.is_file():
            _fail("groovy/postInit contains a non-regular script")
        scripts.append(path.relative_to(target).as_posix())
    return scripts


def runtime_question_options(
    catalog: dict[str, Any],
    pattern_key: str,
    root: Path | str,
) -> dict[str, list[str]]:
    """Resolve target-backed choices declared by one reusable form schema."""

    pattern = _pattern(catalog, pattern_key)
    spec = pattern["authority_spec"]
    resolved: dict[str, list[str]] = {}
    for question in pattern["questions"]:
        source = question.get("runtime_source")
        if source == "recipe-map-aliases":
            resolved[question["id"]] = list(
                recipe_map_aliases(root, spec["recipe_maps_path"])
            )
        elif source == "groovy-postinit-scripts":
            resolved[question["id"]] = groovy_postinit_scripts(
                root, spec["owner_root"]
            )
        elif source is not None:
            _fail(f"unsupported runtime question source: {source}")
    return resolved


def _positive(value: Any, label: str, maximum: int = 2_147_483_647) -> int:
    if type(value) is not int or value < 1 or value > maximum:
        _fail(f"{label} must be an integer from 1 through {maximum}")
    return value


def _ingredient(value: Any, label: str, *, output: bool = False) -> str:
    if not isinstance(value, dict):
        _fail(f"{label} must be an object")
    allowed = {"kind", "name", "amount", "metadata"}
    if set(value) - allowed or not {"kind", "name", "amount"}.issubset(value):
        _fail(f"{label} must contain kind, name, amount, and optional metadata")
    kind = value["kind"]
    name = value["name"]
    amount = _positive(value["amount"], f"{label}.amount")
    if kind not in {"ore", "metaitem", "item"}:
        _fail(f"{label}.kind must be ore, metaitem, or item")
    if output and kind == "ore":
        _fail(f"{label} cannot use an ore dictionary output")
    if not isinstance(name, str):
        _fail(f"{label}.name must be a string")
    if kind == "ore":
        if ORE_NAME_RE.fullmatch(name) is None:
            _fail(f"{label}.name is not a valid ore dictionary identity")
    elif kind == "metaitem":
        if METAITEM_NAME_RE.fullmatch(name) is None:
            _fail(f"{label}.name is not a safe meta-item identity")
    elif IDENTITY_RE.fullmatch(name) is None:
        _fail(f"{label}.name is not a safe registry identity")
    metadata = value.get("metadata")
    if metadata is not None and (
        kind != "item" or type(metadata) is not int or metadata < 0 or metadata > 32767
    ):
        _fail(f"{label}.metadata is valid only for item stacks from 0 through 32767")
    if kind == "item" and ":" not in name:
        _fail(f"{label}.name must be a namespaced item identity")
    call = f"{kind}('{name}'"
    if metadata is not None:
        call += f", {metadata}"
    call += ")"
    return call if amount == 1 else f"{call} * {amount}"


def _fluid(value: Any, label: str) -> str:
    if not isinstance(value, dict) or set(value) != {"name", "amount"}:
        _fail(f"{label} must contain exactly name and amount")
    name = value["name"]
    if not isinstance(name, str) or IDENTITY_RE.fullmatch(name) is None:
        _fail(f"{label}.name is not a safe fluid identity")
    amount = _positive(value["amount"], f"{label}.amount")
    call = f"fluid('{name}')"
    return call if amount == 1 else f"{call} * {amount}"


def _normalized_lines(text: str, label: str) -> tuple[str, str]:
    """Normalize one uniformly LF or CRLF owner while retaining its style."""

    if "\r" not in text:
        return text, "\n"
    without_crlf = text.replace("\r\n", "")
    if "\r" in without_crlf or "\n" in without_crlf:
        _fail(f"{label} has mixed or bare-CR line endings")
    return text.replace("\r\n", "\n"), "\r\n"


def _render_recipe(
    pattern: dict[str, Any],
    root: Path,
    answers: dict[str, Any],
) -> dict[str, Any]:
    spec = pattern["authority_spec"]
    owner_root = _safe_relative(spec["owner_root"], "recipe owner root")
    relative = _safe_relative(answers["script"], "recipe owning script")
    if (
        len(relative.parts) <= len(owner_root.parts)
        or relative.parts[:len(owner_root.parts)] != owner_root.parts
        or relative.suffix != ".groovy"
    ):
        _fail(
            "recipe script must be an existing Groovy file under its "
            f"profile owner: {owner_root}"
        )
    _path, before, raw_text = _read_target(root, relative)
    text, line_ending = _normalized_lines(raw_text, "recipe owning script")
    missing_imports = [
        required
        for required in spec["required_imports"]
        if required not in text
    ]
    if missing_imports:
        _fail(
            "recipe script lacks profile-required imports: "
            + ", ".join(missing_imports)
        )
    aliases = recipe_map_aliases(root, spec["recipe_maps_path"])
    recipe_map = answers["recipe_map"]
    if recipe_map not in aliases:
        _fail(f"recipe-map alias is not defined by the active instance: {recipe_map}")

    item_inputs = answers.get("item_inputs", [])
    fluid_inputs = answers.get("fluid_inputs", [])
    item_outputs = answers.get("item_outputs", [])
    fluid_outputs = answers.get("fluid_outputs", [])
    if any(len(rows) > 16 for rows in (item_inputs, fluid_inputs, item_outputs, fluid_outputs)):
        _fail("recipe input/output lists are limited to 16 entries each")
    if not item_inputs and not fluid_inputs:
        _fail("machine recipe requires at least one item or fluid input")
    if not item_outputs and not fluid_outputs:
        _fail("machine recipe requires at least one item or fluid output")
    indentation = spec["indentation"]
    lines = [f"{recipe_map}.recipeBuilder()"]
    for index, value in enumerate(item_inputs):
        lines.append(
            f"{indentation}.inputs("
            f"{_ingredient(value, f'item_inputs[{index}]')})"
        )
    for index, value in enumerate(fluid_inputs):
        lines.append(
            f"{indentation}.fluidInputs("
            f"{_fluid(value, f'fluid_inputs[{index}]')})"
        )
    for index, value in enumerate(item_outputs):
        lines.append(
            f"{indentation}.outputs("
            f"{_ingredient(value, f'item_outputs[{index}]', output=True)})"
        )
    for index, value in enumerate(fluid_outputs):
        lines.append(
            f"{indentation}.fluidOutputs("
            f"{_fluid(value, f'fluid_outputs[{index}]')})"
        )
    duration = _positive(answers["duration"], "duration")
    voltage = answers["voltage_tier"]
    voltage_questions = [
        row for row in pattern["questions"] if row["id"] == "voltage_tier"
    ]
    if len(voltage_questions) != 1:
        _fail("machine recipe pattern lacks one voltage_tier question")
    voltage_question = voltage_questions[0]
    if voltage not in voltage_question.get("choices", []):
        _fail("voltage_tier is not admitted by the profile pattern")
    lines.extend([
        f"{indentation}.duration({duration})",
        f"{indentation}.EUt(VA[{voltage}])",
        f"{indentation}.buildAndRegister()",
    ])
    block = "\n".join(lines)
    if block in text:
        _fail("the exact machine recipe already exists in its owning script")
    updated = text.rstrip() + "\n\n" + block + "\n"
    operation = _operation(
        relative,
        before,
        updated.replace("\n", line_ending).encode("utf-8"),
    )
    return {
        "effective_answers": answers,
        "operations": [operation],
        "evidence": {
            "recipe_map_alias": recipe_map,
            "recipe_map_registry_name": aliases[recipe_map],
            "owning_script": relative.as_posix(),
        },
    }


def _ore_ingredient(value: Any) -> str:
    if not isinstance(value, dict):
        _fail("ore dictionary ingredient must be an object")
    allowed = {"kind", "name", "metadata"}
    if set(value) - allowed or not {"kind", "name"}.issubset(value):
        _fail("ore dictionary ingredient requires kind, name, and optional metadata")
    stack = {**value, "amount": 1}
    return _ingredient(stack, "ingredient")


def _render_ore_dictionary(
    pattern: dict[str, Any],
    root: Path,
    answers: dict[str, Any],
) -> dict[str, Any]:
    ore_name = answers["ore_name"]
    if not isinstance(ore_name, str) or ORE_NAME_RE.fullmatch(ore_name) is None:
        _fail("ore_name is not a valid Forge ore dictionary identity")
    expression = _ore_ingredient(answers["ingredient"])
    spec = pattern["authority_spec"]
    relative = _safe_relative(spec["owner_path"], "ore dictionary owner path")
    _path, before, raw_text = _read_target(root, relative)
    text, line_ending = _normalized_lines(raw_text, "ore dictionary owner")
    missing_markers = [
        marker for marker in spec["required_markers"] if marker not in text
    ]
    if missing_markers:
        _fail(
            "active-instance ore dictionary owner lacks profile markers: "
            + ", ".join(missing_markers)
        )
    line = f"ore('{ore_name}').add({expression})"
    if line in text:
        _fail("the exact ore dictionary relationship already exists")
    updated = text.rstrip() + "\n\n" + line + "\n"
    return {
        "effective_answers": answers,
        "operations": [
            _operation(
                relative,
                before,
                updated.replace("\n", line_ending).encode("utf-8"),
            )
        ],
        "evidence": {"owner": relative.as_posix()},
    }


def render_registration(
    catalog: dict[str, Any],
    pattern_key: str,
    profile_root: Path | str,
    target_root: Path | str,
    supplied_answers: dict[str, Any],
    *,
    facts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Render deterministic file replacements for one ready profile pattern."""

    pattern = _pattern(catalog, pattern_key)
    answers = _answers(pattern, supplied_answers)
    root = _target_root(target_root)
    renderer = pattern["renderer"]
    if renderer == "gregtech-material-backed-fluid-v1":
        rendered = _render_material_fluid(
            pattern,
            Path(profile_root).resolve(),
            root,
            answers,
            {} if facts is None else facts,
        )
    elif renderer == "gregtech-machine-recipe-v1":
        rendered = _render_recipe(pattern, root, answers)
    elif renderer == "forge-ore-dictionary-entry-v1":
        rendered = _render_ore_dictionary(pattern, root, answers)
    else:  # pragma: no cover - catalog validation closes this set
        _fail(f"unsupported registration renderer: {renderer}")
    rendered["pattern"] = {
        "key": pattern["key"],
        "label": pattern["label"],
        "family_id": pattern["family_id"],
        "lifecycle": pattern["lifecycle"],
        "renderer": renderer,
        "limitations": pattern["limitations"],
    }
    return rendered


def render_material_fluid_recipe(
    catalog: dict[str, Any],
    profile_root: Path | str,
    target_root: Path | str,
    *,
    material_answers: dict[str, Any],
    recipe_answers: dict[str, Any],
    material_census: dict[str, Any],
) -> dict[str, Any]:
    """Compose the native material convention and one real machine recipe.

    This is deliberately an experimental construction result, not an
    admission or support decision.  Both component renderers continue to own
    their existing validation; this function only closes the useful
    cross-component relationship and requires four updates to existing files.
    """

    material = render_registration(
        catalog,
        "material-backed-fluid",
        profile_root,
        target_root,
        material_answers,
        facts={"material_census": material_census},
    )
    effective_material = material["effective_answers"]
    registry_name = effective_material["registry_name"]

    supplied_recipe = dict(recipe_answers)
    output_amount = supplied_recipe.pop("output_amount", None)
    if type(output_amount) is not int or not 1 <= output_amount <= 2_147_483_647:
        _fail("material-fluid recipe output amount must be a positive integer")
    supplied_recipe["fluid_outputs"] = [
        {
            "name": registry_name,
            "amount": output_amount,
        }
    ]
    recipe = render_registration(
        catalog,
        "machine-recipe",
        profile_root,
        target_root,
        supplied_recipe,
    )

    operations = [*material["operations"], *recipe["operations"]]
    paths = [operation.get("path") for operation in operations]
    if (
        len(operations) != 4
        or len(paths) != len(set(paths))
        or any(operation.get("operation") != "update" for operation in operations)
    ):
        _fail(
            "material-fluid recipe composition must update four distinct "
            "existing owner files"
        )
    if recipe["effective_answers"]["fluid_outputs"] != [
        {
            "name": registry_name,
            "amount": supplied_recipe["fluid_outputs"][0]["amount"],
        }
    ]:
        _fail("machine recipe output is not the constructed material fluid")

    return {
        "format": "workbench-blueprints-material-fluid-recipe-render-v1",
        "schema_version": 1,
        "state": "experimental",
        "authority_boundary": {
            "direct_checkout_apply": False,
            "profile_tested_support": False,
            "release_qualified": False,
            "stable_standard_admission": False,
        },
        "effective_request": {
            "material": effective_material,
            "recipe": recipe["effective_answers"],
        },
        "evidence": {
            "material": material["evidence"],
            "recipe": recipe["evidence"],
            "unification": "verified-material-fluid-identity-no-extra-ore-entry",
        },
        "operations": sorted(operations, key=lambda operation: operation["path"]),
        "patterns": [material["pattern"], recipe["pattern"]],
    }


__all__ = [
    "RegistrationRenderError",
    "groovy_postinit_scripts",
    "recipe_map_aliases",
    "render_material_fluid_recipe",
    "render_registration",
    "runtime_question_options",
]
