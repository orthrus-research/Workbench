"""Current-checkout Supersymmetry machine-recipe change authority.

The profile binds one existing ``groovy/postInit`` owner and its observed
``Recipemaps`` alias.  Blueprints remains the only renderer and byte-
transaction authority.  Atlas remains the only authority that may interpret
the disposable runtime observation contract emitted here.

V1 deliberately implements ADD only.  REPLACE and REMOVE are rejected instead
of being represented by an unproved source matcher.
"""

from __future__ import annotations

PROFILE_API_VERSION = 1

import base64
from collections import Counter
import difflib
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
from typing import Any, Callable, Mapping, NamedTuple, NoReturn, cast
from urllib.parse import urlparse
from urllib.request import url2pathname

from workbench_blueprints import application_transaction
from workbench_blueprints import registration_catalog
from workbench_blueprints import registration_render
from workbench_project_intelligence import inspect_workspace
from workbench_api.profile_extensions import require_profile_extension


CATALOG_PATH = Path("profiles/packs/supersymmetry/registration/catalog-v1.json")
PACK_PROFILE_PATH = Path("profiles/packs/supersymmetry/profile.yaml")
PLATFORM_PROFILE_PATH = Path("profiles/platforms/cleanroom/provisional.yaml")
PACK_PROFILE_ID = "workbench-pack:supersymmetry"
PLATFORM_PROFILE_ID = "workbench-platform:cleanroom:provisional"
PATTERN_KEY = "machine-recipe"

PLAN_FORMAT = "workbench-supersymmetry-recipe-change-plan-v1"
PLAN_KIND = "workbench-supersymmetry-recipe-change-plan"
RECEIPT_FORMAT = "workbench-supersymmetry-recipe-change-receipt-v1"
RECEIPT_KIND = "workbench-supersymmetry-recipe-change-receipt"
ROLLBACK_FORMAT = "workbench-supersymmetry-recipe-change-rollback-v1"
ROLLBACK_KIND = "workbench-supersymmetry-recipe-change-rollback"
RECOVERY_FORMAT = "workbench-supersymmetry-recipe-change-recovery-v1"
RECOVERY_KIND = "workbench-supersymmetry-recipe-change-recovery"
OBSERVATION_FORMAT = "workbench-supersymmetry-recipe-observation-contract-v1"
OBSERVATION_KIND = "workbench-supersymmetry-recipe-observation-contract"

MAXIMUM_SOURCE_BYTES = 4 * 1024 * 1024
MAXIMUM_DIFF_BYTES = 512 * 1024
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_CONTENT_ID = re.compile(r"^(?:sha256:|[a-z][a-z0-9-]*:sha256:)[0-9a-f]{64}$")
_RECIPE_ALIAS = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_REGISTRY_NAME = re.compile(r"^[a-z0-9][a-z0-9_.:/-]{0,255}$")
_REVISION = re.compile(r"^[0-9a-f]{40,64}$")
_VOLTAGE_TIERS = [
    "ULV",
    "LV",
    "MV",
    "HV",
    "EV",
    "IV",
    "LuV",
    "ZPM",
    "UV",
    "UHV",
    "UEV",
    "UIV",
]

AUTHORITY_BOUNDARY = {
    "application_scope": "reversible-local-experiment",
    "atlas_interpretation_required": True,
    "construction_owner": "Blueprints",
    "profile_owner": "Supersymmetry",
    "publication_authorized": False,
    "supported_mutations": ["add"],
    "transaction_owner": "Blueprints",
}

_RUNTIME_OBSERVATION_REQUIREMENT = {
    "contract_format": OBSERVATION_FORMAT,
    "mode": "disposable-cold-start",
    "observation_owner": "Atlas",
    "physical_sides": ["client", "dedicated-server"],
    "state": "required-not-observed",
}


class RecipeChangeError(ValueError):
    """The requested recipe change is unsafe, malformed, or stale."""


def _fail(message: str) -> NoReturn:
    raise RecipeChangeError(message)


def _canonical_bytes(value: Any) -> bytes:
    try:
        return application_transaction.canonical_json_bytes(value)
    except (TypeError, ValueError) as exc:
        raise RecipeChangeError("recipe-change value is not canonical JSON") from exc


def _canonical_copy(value: Any) -> Any:
    return json.loads(_canonical_bytes(value).decode("utf-8"))


def _seal(kind: str, body: Mapping[str, Any]) -> dict[str, Any]:
    return application_transaction.seal(kind, body)


def _safe_relative(value: Any, label: str) -> PurePosixPath:
    if type(value) is not str or not value or "\\" in value:
        _fail(f"{label} must be a portable relative path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        _fail(f"{label} must be a safe normalized relative path")
    return path


def _ordinary_directory(value: Path | str, label: str) -> Path:
    path = Path(os.path.abspath(os.fspath(Path(value).expanduser())))
    try:
        state = path.lstat()
    except OSError as exc:
        raise RecipeChangeError(f"cannot inspect {label}") from exc
    if stat.S_ISLNK(state.st_mode) or not stat.S_ISDIR(state.st_mode):
        _fail(f"{label} must be an ordinary directory")
    return path.resolve()


def _read_regular_descendant(
    root: Path,
    relative: PurePosixPath,
    label: str,
) -> bytes:
    parent = root
    for part in relative.parts[:-1]:
        parent = parent / part
        try:
            state = parent.lstat()
        except OSError as exc:
            raise RecipeChangeError(f"cannot inspect {label} parent") from exc
        if stat.S_ISLNK(state.st_mode) or not stat.S_ISDIR(state.st_mode):
            _fail(f"{label} traverses a symbolic link or non-directory")
    target = root.joinpath(*relative.parts)
    try:
        descriptor = os.open(
            target,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0),
        )
    except OSError as exc:
        raise RecipeChangeError(f"cannot open {label}") from exc
    try:
        before = os.fstat(descriptor)
        if (
            stat.S_ISLNK(before.st_mode)
            or not stat.S_ISREG(before.st_mode)
            or before.st_size > MAXIMUM_SOURCE_BYTES
        ):
            _fail(f"{label} is not a bounded regular file")
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, MAXIMUM_SOURCE_BYTES + 1))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > MAXIMUM_SOURCE_BYTES:
                _fail(f"{label} exceeds its byte bound")
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
        identity = lambda row: (
            row.st_dev,
            row.st_ino,
            row.st_mode,
            row.st_size,
            row.st_mtime_ns,
        )
        if identity(before) != identity(after) or len(raw) != before.st_size:
            _fail(f"{label} changed while being read")
        return raw
    finally:
        os.close(descriptor)


def _source_binding(workspace: Path, relative: str, label: str) -> dict[str, Any]:
    path = _safe_relative(relative, label)
    raw = _read_regular_descendant(workspace, path, label)
    return {
        "path": path.as_posix(),
        "sha256": sha256(raw).hexdigest(),
        "size": len(raw),
    }


def _suite_file(suite: Path, relative: Path, label: str) -> Path:
    path = suite / relative
    try:
        state = path.lstat()
    except OSError as exc:
        raise RecipeChangeError(f"cannot inspect {label}") from exc
    if stat.S_ISLNK(state.st_mode) or not stat.S_ISREG(state.st_mode):
        _fail(f"{label} must be a regular non-symlink file")
    return path


def _profile_context(suite: Path, workspace: Path) -> dict[str, Any]:
    require_profile_extension("workbench.recipe_changes", "supersymmetry")
    platform_path = _suite_file(
        suite, PLATFORM_PROFILE_PATH, "Cleanroom platform profile"
    )
    pack_path = _suite_file(suite, PACK_PROFILE_PATH, "Supersymmetry pack profile")
    try:
        context = inspect_workspace(
            workspace,
            platform_profile_path=platform_path,
            pack_profile_path=pack_path,
            pack_selection="cleanroom-provisional",
        )
    except (OSError, ValueError) as exc:
        raise RecipeChangeError(
            f"cannot establish Supersymmetry workspace context: {exc}"
        ) from exc
    project = context.get("project")
    pack = context.get("pack")
    platform = context.get("platform")
    observed_workspace = context.get("workspace")
    if not all(
        type(value) is dict
        for value in (project, pack, platform, observed_workspace)
    ):
        _fail("workspace inspection lacks its profile context")
    if (
        context.get("format") != "workbench-workspace-context-v2"
        or context.get("schema_version") != 2
        or project.get("name") != "Supersymmetry"
        or project.get("minecraft_version") != "1.12.2"
        or pack.get("profile_family_id") != PACK_PROFILE_ID
        or pack.get("selected_profile") != "cleanroom-provisional"
        or pack.get("maturity") != "experimental"
        or "construct" not in pack.get("permitted_operations", [])
        or pack.get("platform_profile_id") != PLATFORM_PROFILE_ID
        or platform.get("profile_id") != PLATFORM_PROFILE_ID
        or platform.get("minecraft_version") != "1.12.2"
        or observed_workspace.get("root_uri") != workspace.as_uri()
    ):
        _fail(
            "recipe changes require the experimental Supersymmetry Cleanroom "
            "construction profile"
        )
    result = {
        "cleanroom_version": platform.get("cleanroom_version"),
        "minecraft_version": project.get("minecraft_version"),
        "pack_document_sha256": pack.get("document_sha256"),
        "pack_profile_id": pack.get("profile_family_id"),
        "pack_selected_profile": pack.get("selected_profile"),
        "platform_document_sha256": platform.get("document_sha256"),
        "platform_profile_id": platform.get("profile_id"),
        "project_manifest_sha256": project.get("manifest_sha256"),
        "project_name": project.get("name"),
        "project_version": project.get("version"),
        "workspace_revision": observed_workspace.get("revision"),
        "workspace_uri": observed_workspace.get("root_uri"),
    }
    _validate_profile_context(result)
    return result


def _validate_profile_context(value: Any) -> dict[str, Any]:
    expected = {
        "cleanroom_version",
        "minecraft_version",
        "pack_document_sha256",
        "pack_profile_id",
        "pack_selected_profile",
        "platform_document_sha256",
        "platform_profile_id",
        "project_manifest_sha256",
        "project_name",
        "project_version",
        "workspace_revision",
        "workspace_uri",
    }
    if type(value) is not dict or set(value) != expected:
        _fail("recipe-change profile context fields changed")
    context = cast(dict[str, Any], value)
    if (
        context.get("pack_profile_id") != PACK_PROFILE_ID
        or context.get("platform_profile_id") != PLATFORM_PROFILE_ID
        or context.get("pack_selected_profile") != "cleanroom-provisional"
        or context.get("project_name") != "Supersymmetry"
        or context.get("minecraft_version") != "1.12.2"
        or any(
            type(context.get(field)) is not str or not context[field]
            for field in ("cleanroom_version", "project_version")
        )
        or any(
            type(context.get(field)) is not str
            or _DIGEST.fullmatch(context[field]) is None
            for field in (
                "pack_document_sha256",
                "platform_document_sha256",
                "project_manifest_sha256",
            )
        )
        or type(context.get("workspace_revision")) is not str
        or _REVISION.fullmatch(context["workspace_revision"]) is None
    ):
        _fail("recipe-change profile context identity changed")
    _workspace_path_from_uri(context.get("workspace_uri"))
    return context


def _catalog_and_pattern(suite: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    path = _suite_file(suite, CATALOG_PATH, "Supersymmetry registration catalog")
    try:
        catalog = registration_catalog.load_registration_catalog(path)
    except (OSError, ValueError) as exc:
        raise RecipeChangeError(f"cannot load recipe construction authority: {exc}") from exc
    matches = [row for row in catalog["patterns"] if row.get("key") == PATTERN_KEY]
    if len(matches) != 1:
        _fail("registration catalog does not expose one machine-recipe pattern")
    pattern = matches[0]
    if (
        catalog.get("profile_family_id") != PACK_PROFILE_ID
        or pattern.get("renderer") != "gregtech-machine-recipe-v1"
        or pattern.get("family_id") != "machine-recipes"
    ):
        _fail("machine-recipe pattern belongs to a different authority")
    return catalog, pattern


def _resolve_alias(
    aliases: Mapping[str, str], requested: str
) -> tuple[str, str]:
    if type(requested) is not str or not requested:
        _fail("recipe map must name a current alias or registry name")
    matches = [
        (alias, registry)
        for alias, registry in aliases.items()
        if requested in {alias, alias.lower(), registry}
    ]
    if len(matches) != 1:
        _fail("recipe map is not uniquely defined by the current checkout")
    return matches[0]


def _unified_diff(before: bytes, after: bytes, relative: str) -> str:
    try:
        before_lines = before.decode("utf-8", errors="strict").splitlines(
            keepends=True
        )
        after_lines = after.decode("utf-8", errors="strict").splitlines(
            keepends=True
        )
    except UnicodeError as exc:
        raise RecipeChangeError(f"recipe owner is not UTF-8: {relative}") from exc
    for lines in (before_lines, after_lines):
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines[-1] += "\n"
    return "".join(
        difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile=f"a/{relative}",
            tofile=f"b/{relative}",
        )
    )


class _GroovyToken(NamedTuple):
    kind: str
    value: str
    offset: int
    line: int


def _groovy_tokens(text: str) -> list[_GroovyToken]:
    """Tokenize the bounded literal subset needed by the owned renderer.

    This is intentionally not a general Groovy parser.  It is a conservative
    lexical boundary for recipeBuilder chains emitted by the profile's exact
    renderer.  Comments and strings are recognized so recipe-looking text in
    either cannot become evidence.  Unclosed lexical structures fail closed.
    """

    tokens: list[_GroovyToken] = []
    cursor = 0
    line = 1
    length = len(text)

    def advance(end: int) -> None:
        nonlocal cursor, line
        line += text[cursor:end].count("\n")
        cursor = end

    while cursor < length:
        character = text[cursor]
        if character.isspace():
            advance(cursor + 1)
            continue
        if text.startswith("//", cursor):
            end = text.find("\n", cursor + 2)
            advance(length if end < 0 else end)
            continue
        if text.startswith("/*", cursor):
            end = text.find("*/", cursor + 2)
            if end < 0:
                _fail("recipe owner has an unterminated Groovy block comment")
            advance(end + 2)
            continue
        if character in {"'", '"'}:
            start = cursor
            start_line = line
            delimiter = (
                character * 3
                if text.startswith(character * 3, cursor)
                else character
            )
            end = cursor + len(delimiter)
            while end < length and not text.startswith(delimiter, end):
                end += 2 if text[end] == "\\" else 1
            if end >= length:
                _fail("recipe owner has an unterminated Groovy string")
            end += len(delimiter)
            tokens.append(_GroovyToken("string", text[start:end], start, start_line))
            advance(end)
            continue
        if character.isalpha() or character in {"_", "$"}:
            start = cursor
            start_line = line
            end = cursor + 1
            while end < length and (
                text[end].isalnum() or text[end] in {"_", "$"}
            ):
                end += 1
            tokens.append(
                _GroovyToken("identifier", text[start:end], start, start_line)
            )
            advance(end)
            continue
        if character.isdigit():
            start = cursor
            start_line = line
            end = cursor + 1
            while end < length and (
                text[end].isalnum() or text[end] == "_"
            ):
                end += 1
            tokens.append(_GroovyToken("number", text[start:end], start, start_line))
            advance(end)
            continue
        tokens.append(_GroovyToken("symbol", character, cursor, line))
        advance(cursor + 1)
    return tokens


def _groovy_literal(token: _GroovyToken) -> str | None:
    if token.kind != "string" or len(token.value) < 2:
        return None
    delimiter = (
        token.value[:3]
        if token.value.startswith(("'''", '\"\"\"'))
        else token.value[0]
    )
    if (
        not token.value.endswith(delimiter)
        or len(token.value) < len(delimiter) * 2
    ):
        return None
    value = token.value[len(delimiter) : -len(delimiter)]
    if (
        len(delimiter) != 1
        or "\\" in value
        or "\r" in value
        or "\n" in value
        or "\x00" in value
        or delimiter == '"' and "$" in value
    ):
        return None
    return value


def _groovy_positive_integer(tokens: list[_GroovyToken]) -> int | None:
    if len(tokens) != 1 or tokens[0].kind != "number":
        return None
    raw = tokens[0].value.replace("_", "")
    if raw.endswith(("l", "L")):
        raw = raw[:-1]
    if not raw.isdigit():
        return None
    value = int(raw)
    return value if 1 <= value <= 2_147_483_647 else None


def _matching_parenthesis(
    tokens: list[_GroovyToken], opening: int
) -> int | None:
    if opening >= len(tokens) or tokens[opening].value != "(":
        return None
    depth = 0
    for position in range(opening, len(tokens)):
        if tokens[position].value == "(":
            depth += 1
        elif tokens[position].value == ")":
            depth -= 1
            if depth == 0:
                return position
    return None


def _split_groovy_arguments(
    tokens: list[_GroovyToken],
) -> list[list[_GroovyToken]] | None:
    if not tokens:
        return []
    arguments: list[list[_GroovyToken]] = []
    start = 0
    stack: list[str] = []
    closing = {"(": ")", "[": "]", "{": "}"}
    for position, token in enumerate(tokens):
        if token.value in closing:
            stack.append(closing[token.value])
        elif token.value in closing.values():
            if not stack or stack.pop() != token.value:
                return None
        elif token.value == "," and not stack:
            if position == start:
                return None
            arguments.append(tokens[start:position])
            start = position + 1
    if stack or start == len(tokens):
        return None
    arguments.append(tokens[start:])
    return arguments


def _groovy_stack(
    tokens: list[_GroovyToken],
    *,
    fluid: bool,
) -> tuple[Any, ...] | None:
    """Return one renderer-shaped stack without evaluating Groovy."""

    amount = 1
    multiplication = [
        position for position, token in enumerate(tokens) if token.value == "*"
    ]
    expression = tokens
    if multiplication:
        if len(multiplication) != 1:
            return None
        position = multiplication[0]
        left = tokens[:position]
        right = tokens[position + 1 :]
        left_amount = _groovy_positive_integer(left)
        right_amount = _groovy_positive_integer(right)
        if left_amount is not None and right_amount is None:
            amount, expression = left_amount, right
        elif right_amount is not None and left_amount is None:
            amount, expression = right_amount, left
        else:
            return None
    if (
        len(expression) < 4
        or expression[0].kind != "identifier"
        or expression[1].value != "("
        or expression[-1].value != ")"
        or _matching_parenthesis(expression, 1) != len(expression) - 1
    ):
        return None
    kind = expression[0].value
    if kind not in ({"fluid"} if fluid else {"ore", "metaitem", "item"}):
        return None
    arguments = _split_groovy_arguments(expression[2:-1])
    if arguments is None or not arguments or len(arguments[0]) != 1:
        return None
    name = _groovy_literal(arguments[0][0])
    if name is None:
        return None
    if fluid:
        return (name, amount) if len(arguments) == 1 else None
    metadata = -1
    if len(arguments) == 2:
        if kind != "item":
            return None
        parsed_metadata = _groovy_positive_integer(arguments[1])
        if parsed_metadata is None and len(arguments[1]) == 1:
            if arguments[1][0].kind == "number" and arguments[1][0].value == "0":
                parsed_metadata = 0
        if parsed_metadata is None or parsed_metadata > 32767:
            return None
        metadata = parsed_metadata
    elif len(arguments) != 1:
        return None
    return (kind, name, metadata, amount)


def _groovy_voltage(tokens: list[_GroovyToken]) -> str | None:
    values = [token.value for token in tokens]
    if (
        len(values) == 4
        and values[0] == "VA"
        and values[1] == "["
        and values[3] == "]"
    ):
        return values[2] if values[2] in _VOLTAGE_TIERS else None
    if (
        len(values) == 6
        and values[:3] == ["GTValues", ".", "VA"]
        and values[3] == "["
        and values[5] == "]"
    ):
        return values[4] if values[4] in _VOLTAGE_TIERS else None
    if (
        len(values) == 6
        and values[:3] == ["Globals", ".", "voltAmps"]
        and values[3] == "["
        and values[5] == "]"
        and tokens[4].kind == "number"
    ):
        raw = values[4].replace("_", "")
        if raw.isdigit() and int(raw) < len(_VOLTAGE_TIERS):
            return _VOLTAGE_TIERS[int(raw)]
    return None


def _recipe_builder_chains(
    text: str, alias: str
) -> list[dict[str, Any]]:
    tokens = _groovy_tokens(text)
    starts: list[int] = []
    for position in range(len(tokens) - 4):
        if (
            tokens[position].kind == "identifier"
            and tokens[position].value == alias
            and [token.value for token in tokens[position + 1 : position + 5]]
            == [".", "recipeBuilder", "(", ")"]
        ):
            starts.append(position)

    chains: list[dict[str, Any]] = []
    for start in starts:
        cursor = start + 5
        calls: list[tuple[str, list[_GroovyToken]]] = []
        while cursor < len(tokens):
            while cursor < len(tokens) and tokens[cursor].value == ";":
                cursor += 1
            if (
                cursor + 2 >= len(tokens)
                or tokens[cursor].value != "."
                or tokens[cursor + 1].kind != "identifier"
                or tokens[cursor + 2].value != "("
            ):
                _fail(
                    "recipe owner contains an ambiguous "
                    f"{alias}.recipeBuilder chain at line {tokens[start].line}"
                )
            closing = _matching_parenthesis(tokens, cursor + 2)
            if closing is None:
                _fail(
                    "recipe owner contains an unterminated "
                    f"{alias}.recipeBuilder chain at line {tokens[start].line}"
                )
            method = tokens[cursor + 1].value
            arguments = tokens[cursor + 3 : closing]
            calls.append((method, arguments))
            cursor = closing + 1
            if method == "buildAndRegister":
                if arguments:
                    _fail(
                        "recipe owner contains an ambiguous buildAndRegister "
                        f"call at line {tokens[start].line}"
                    )
                break
        else:
            _fail(
                "recipe owner contains an unterminated "
                f"{alias}.recipeBuilder chain at line {tokens[start].line}"
            )
        chains.append(
            {
                "alias": alias,
                "calls": calls,
                "line": tokens[start].line,
            }
        )
    return chains


_RECIPE_COLLECTION_METHODS = {
    "inputs": ("item_inputs", False),
    "fluidInputs": ("fluid_inputs", True),
    "outputs": ("item_outputs", False),
    "fluidOutputs": ("fluid_outputs", True),
}


def _semantic_recipe(chain: Mapping[str, Any]) -> dict[str, Any]:
    collections: dict[str, list[tuple[Any, ...]]] = {
        field: []
        for field in (
            "item_inputs",
            "fluid_inputs",
            "item_outputs",
            "fluid_outputs",
        )
    }
    ambiguous: set[str] = set()
    duration: list[int] = []
    voltage: list[str] = []
    for method, tokens in chain["calls"]:
        if method == "buildAndRegister":
            continue
        collection = _RECIPE_COLLECTION_METHODS.get(method)
        if collection is not None:
            field, fluid = collection
            arguments = _split_groovy_arguments(tokens)
            if arguments is None or not arguments:
                ambiguous.add(field)
                continue
            values = [_groovy_stack(argument, fluid=fluid) for argument in arguments]
            if any(value is None for value in values):
                ambiguous.add(field)
                continue
            collections[field].extend(cast(list[tuple[Any, ...]], values))
            continue
        if method == "duration":
            parsed = _groovy_positive_integer(tokens)
            if parsed is None:
                ambiguous.add("duration")
            else:
                duration.append(parsed)
            continue
        if method == "EUt":
            parsed_voltage = _groovy_voltage(tokens)
            if parsed_voltage is None:
                ambiguous.add("voltage_tier")
            else:
                voltage.append(parsed_voltage)
            continue
        # An unmodeled builder property may or may not affect runtime recipe
        # identity.  Retain that uncertainty instead of silently declaring a
        # same-map, same-I/O proposal distinct.
        ambiguous.add("additional_builder_property")

    if len(duration) != 1:
        ambiguous.add("duration")
    if len(voltage) != 1:
        ambiguous.add("voltage_tier")
    return {
        "alias": chain["alias"],
        "ambiguous": tuple(sorted(ambiguous)),
        "duration": duration[0] if len(duration) == 1 else None,
        "fluid_inputs": tuple(sorted(collections["fluid_inputs"])),
        "fluid_outputs": tuple(sorted(collections["fluid_outputs"])),
        "item_inputs": tuple(sorted(collections["item_inputs"])),
        "item_outputs": tuple(sorted(collections["item_outputs"])),
        "line": chain["line"],
        "voltage_tier": voltage[0] if len(voltage) == 1 else None,
    }


def _recipe_signature(recipe: Mapping[str, Any]) -> tuple[Any, ...] | None:
    if recipe["ambiguous"]:
        return None
    return (
        recipe["alias"],
        recipe["item_inputs"],
        recipe["fluid_inputs"],
        recipe["item_outputs"],
        recipe["fluid_outputs"],
        recipe["duration"],
        recipe["voltage_tier"],
    )


def _could_be_input_collision(
    recipe: Mapping[str, Any], target: Mapping[str, Any]
) -> bool:
    if recipe["alias"] != target["alias"]:
        return False
    ambiguous = set(recipe["ambiguous"])
    for field in ("item_inputs", "fluid_inputs"):
        observed = Counter(recipe[field])
        expected = Counter(target[field])
        if field in ambiguous:
            if any(count > expected[value] for value, count in observed.items()):
                return False
        elif observed != expected:
            return False
    return True


def _reject_semantic_recipe_duplicate(
    before: bytes,
    after: bytes,
    *,
    alias: str,
    owner: str,
) -> None:
    try:
        before_text = before.decode("utf-8", errors="strict")
        after_text = after.decode("utf-8", errors="strict")
    except UnicodeError as exc:  # pragma: no cover - renderer already checks this
        raise RecipeChangeError(f"recipe owner is not UTF-8: {owner}") from exc

    before_chains = _recipe_builder_chains(before_text, alias)
    after_chains = _recipe_builder_chains(after_text, alias)
    before_recipes = [_semantic_recipe(chain) for chain in before_chains]
    after_recipes = [_semantic_recipe(chain) for chain in after_chains]
    if (
        len(after_recipes) != len(before_recipes) + 1
        or after_recipes[:-1] != before_recipes
    ):
        _fail(
            "Blueprints recipe renderer did not append exactly one "
            "semantically inspectable builder chain"
        )
    target = after_recipes[-1]
    target_signature = _recipe_signature(target)
    if target_signature is None:
        _fail("Blueprints recipe renderer emitted an ambiguous recipe signature")

    for existing in before_recipes:
        signature = _recipe_signature(existing)
        if signature == target_signature:
            _fail(
                "machine-recipe ADD is a semantic duplicate of an existing "
                f"recipe in its selected owner {owner}:{existing['line']}"
            )
        if _could_be_input_collision(existing, target):
            _fail(
                "machine-recipe ADD has an input collision with an existing "
                "recipe, or cannot be proven distinct from one, in its selected owner "
                f"{owner}:{existing['line']}"
            )


def _transaction_operation(
    workspace: Path,
    raw_operation: Mapping[str, Any],
) -> dict[str, Any]:
    relative = _safe_relative(raw_operation.get("path"), "recipe owner")
    before = _read_regular_descendant(workspace, relative, "recipe owner")
    after = raw_operation.get("content")
    if type(after) is not bytes:
        _fail("Blueprints recipe renderer did not return exact bytes")
    if (
        raw_operation.get("operation") != "update"
        or raw_operation.get("before_sha256") != sha256(before).hexdigest()
        or raw_operation.get("content_sha256") != sha256(after).hexdigest()
        or before == after
    ):
        _fail("Blueprints recipe operation changed before it was bound")
    diff = _unified_diff(before, after, relative.as_posix())
    if not diff or len(diff.encode("utf-8")) > MAXIMUM_DIFF_BYTES:
        _fail("recipe review diff is empty or exceeds its byte bound")
    return {
        "after_base64": base64.b64encode(after).decode("ascii"),
        "after_sha256": sha256(after).hexdigest(),
        "after_size": len(after),
        "before_base64": base64.b64encode(before).decode("ascii"),
        "before_sha256": sha256(before).hexdigest(),
        "before_size": len(before),
        "diff": diff,
        "operation": "update",
        "ordinal": 0,
        "outcome": "approved-update",
        "path": relative.as_posix(),
        "role": "machine-recipe-owner",
    }


def _review(operation: Mapping[str, Any]) -> dict[str, Any]:
    diff = operation["diff"]
    return {
        "additions": sum(
            1
            for line in diff.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        ),
        "changed_files": [operation["path"]],
        "deletions": sum(
            1
            for line in diff.splitlines()
            if line.startswith("-") and not line.startswith("---")
        ),
        "unified_diff": diff,
    }


def recipe_change_options(
    suite_root: Path | str,
    workspace_root: Path | str,
) -> dict[str, Any]:
    """List live recipe-map aliases and existing profile-owned scripts."""

    suite = _ordinary_directory(suite_root, "Workbench suite root")
    workspace = _ordinary_directory(workspace_root, "Supersymmetry workspace")
    context = _profile_context(suite, workspace)
    catalog, pattern = _catalog_and_pattern(suite)
    try:
        runtime = registration_render.runtime_question_options(
            catalog, PATTERN_KEY, workspace
        )
        aliases = registration_render.recipe_map_aliases(
            workspace, pattern["authority_spec"]["recipe_maps_path"]
        )
    except (OSError, ValueError) as exc:
        raise RecipeChangeError(f"cannot resolve current recipe options: {exc}") from exc
    return {
        "format": "workbench-supersymmetry-recipe-change-options-v1",
        "schema_version": 1,
        "catalog_id": catalog["catalog_id"],
        "limitations": list(pattern["limitations"]),
        "mutation_modes": {
            "supported": ["add"],
            "unsupported": ["replace", "remove"],
        },
        "profile_context": context,
        "recipe_maps": [
            {"alias": alias, "registry_name": registry}
            for alias, registry in aliases.items()
        ],
        "scripts": runtime.get("script", []),
        "state": "experimental",
    }


def build_recipe_change_plan(
    suite_root: Path | str,
    workspace_root: Path | str,
    *,
    mutation: str,
    recipe_script: str,
    recipe_map: str,
    item_inputs: list[dict[str, Any]] | None = None,
    fluid_inputs: list[dict[str, Any]] | None = None,
    item_outputs: list[dict[str, Any]] | None = None,
    fluid_outputs: list[dict[str, Any]] | None = None,
    duration: int,
    voltage_tier: str,
) -> dict[str, Any]:
    """Build one exact-byte ADD plan against the selected live checkout."""

    if mutation != "add":
        _fail("recipe-change V1 supports add only; replace/remove are not implemented")
    suite = _ordinary_directory(suite_root, "Workbench suite root")
    workspace = _ordinary_directory(workspace_root, "Supersymmetry workspace")
    context = _profile_context(suite, workspace)
    catalog, pattern = _catalog_and_pattern(suite)
    spec = pattern["authority_spec"]
    try:
        aliases = registration_render.recipe_map_aliases(
            workspace, spec["recipe_maps_path"]
        )
        alias, registry_name = _resolve_alias(aliases, recipe_map)
        rendered = registration_render.render_registration(
            catalog,
            PATTERN_KEY,
            suite / "profiles/packs/supersymmetry",
            workspace,
            {
                "script": recipe_script,
                "recipe_map": alias,
                "item_inputs": [] if item_inputs is None else item_inputs,
                "fluid_inputs": [] if fluid_inputs is None else fluid_inputs,
                "item_outputs": [] if item_outputs is None else item_outputs,
                "fluid_outputs": [] if fluid_outputs is None else fluid_outputs,
                "duration": duration,
                "voltage_tier": voltage_tier,
            },
        )
    except (OSError, ValueError) as exc:
        if str(exc) == "the exact machine recipe already exists in its owning script":
            raise RecipeChangeError(
                "machine-recipe ADD is a semantic duplicate of an existing "
                f"recipe in its selected owner {recipe_script}"
            ) from exc
        raise RecipeChangeError(f"cannot construct machine-recipe ADD: {exc}") from exc
    raw_operations = rendered.get("operations")
    if (
        type(raw_operations) is not list
        or len(raw_operations) != 1
        or raw_operations[0].get("path") != recipe_script
    ):
        _fail("Blueprints recipe construction escaped its selected owner")
    operation = _transaction_operation(workspace, raw_operations[0])
    _reject_semantic_recipe_duplicate(
        _decoded(operation, "before"),
        _decoded(operation, "after"),
        alias=alias,
        owner=operation["path"],
    )
    dependency = _source_binding(
        workspace, spec["recipe_maps_path"], "recipe-map alias source"
    )
    effective = _canonical_copy(rendered["effective_answers"])
    request = {"mutation": "add", **effective}
    bindings = {
        "catalog": {
            "catalog_id": catalog["catalog_id"],
            "lifecycle": pattern["lifecycle"],
            "pattern_key": pattern["key"],
            "profile_family_id": catalog["profile_family_id"],
            "renderer": pattern["renderer"],
        },
        "owner": {
            "path": operation["path"],
            "required_imports": list(spec["required_imports"]),
            "sha256": operation["before_sha256"],
            "size": operation["before_size"],
        },
        "recipe_map": {
            "alias": alias,
            "registry_name": registry_name,
            "source": dependency,
        },
    }
    body = {
        "action": "add-machine-recipe",
        "authority_bindings": bindings,
        "authority_boundary": dict(AUTHORITY_BOUNDARY),
        "dependencies": [dependency],
        "format": PLAN_FORMAT,
        "kind": PLAN_KIND,
        "limitations": [
            *rendered["pattern"]["limitations"],
            "Replace and remove require a future exact source matcher and are not supported by this plan format.",
        ],
        "operations": [operation],
        "profile_context": context,
        "request": request,
        "review": _review(operation),
        "runtime_observation": dict(_RUNTIME_OBSERVATION_REQUIREMENT),
        "schema_version": 1,
        "state": "experimental-ready",
        "workspace_uri": workspace.as_uri(),
    }
    return validate_recipe_change_plan(_seal(PLAN_KIND, body))


def build_recipe_add_plan(
    suite_root: Path | str,
    workspace_root: Path | str,
    *,
    recipe_script: str,
    recipe_map: str,
    item_inputs: list[dict[str, Any]] | None = None,
    fluid_inputs: list[dict[str, Any]] | None = None,
    item_outputs: list[dict[str, Any]] | None = None,
    fluid_outputs: list[dict[str, Any]] | None = None,
    duration: int,
    voltage_tier: str,
) -> dict[str, Any]:
    """Convenience entry point for the only currently supported mutation."""

    return build_recipe_change_plan(
        suite_root,
        workspace_root,
        mutation="add",
        recipe_script=recipe_script,
        recipe_map=recipe_map,
        item_inputs=item_inputs,
        fluid_inputs=fluid_inputs,
        item_outputs=item_outputs,
        fluid_outputs=fluid_outputs,
        duration=duration,
        voltage_tier=voltage_tier,
    )


def _workspace_path_from_uri(value: Any) -> Path:
    if type(value) is not str:
        _fail("recipe-change plan lacks its workspace URI")
    parsed = urlparse(value)
    if (
        parsed.scheme != "file"
        or parsed.netloc not in {"", "localhost"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        _fail("recipe-change workspace must be a local file URI")
    path = Path(url2pathname(parsed.path))
    if not path.is_absolute():
        _fail("recipe-change workspace URI must contain an absolute path")
    return path


def _workspace_from_uri(value: Any) -> Path:
    return _ordinary_directory(
        _workspace_path_from_uri(value), "recipe-change workspace"
    )


def _decoded(row: Mapping[str, Any], prefix: str) -> bytes:
    encoded = row.get(f"{prefix}_base64")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (TypeError, ValueError) as exc:
        raise RecipeChangeError("recipe operation bytes are invalid") from exc
    if (
        len(raw) > MAXIMUM_SOURCE_BYTES
        or type(row.get(f"{prefix}_size")) is not int
        or row.get(f"{prefix}_size") != len(raw)
        or row.get(f"{prefix}_sha256") != sha256(raw).hexdigest()
    ):
        _fail("recipe operation byte identity changed")
    return raw


def _validate_request(value: Any) -> dict[str, Any]:
    fields = {
        "duration",
        "fluid_inputs",
        "fluid_outputs",
        "item_inputs",
        "item_outputs",
        "mutation",
        "recipe_map",
        "script",
        "voltage_tier",
    }
    if type(value) is not dict or set(value) != fields:
        _fail("recipe-change request fields changed")
    request = cast(dict[str, Any], value)
    script = _safe_relative(request.get("script"), "recipe owner")
    if (
        len(script.parts) < 3
        or script.parts[:2] != ("groovy", "postInit")
        or script.suffix != ".groovy"
        or request.get("mutation") != "add"
        or type(request.get("recipe_map")) is not str
        or _RECIPE_ALIAS.fullmatch(request["recipe_map"]) is None
        or type(request.get("duration")) is not int
        or type(request.get("duration")) is bool
        or not 1 <= request["duration"] <= 2_147_483_647
        or type(request.get("voltage_tier")) is not str
        or not request["voltage_tier"]
    ):
        _fail("recipe-change request identity changed")
    lists = (
        request.get("item_inputs"),
        request.get("fluid_inputs"),
        request.get("item_outputs"),
        request.get("fluid_outputs"),
    )
    if (
        any(type(rows) is not list or len(rows) > 16 for rows in lists)
        or any(
            type(row) is not dict
            for rows in lists
            for row in cast(list[Any], rows)
        )
        or not request["item_inputs"]
        and not request["fluid_inputs"]
        or not request["item_outputs"]
        and not request["fluid_outputs"]
    ):
        _fail("recipe-change input/output structure changed")
    # The catalog renderer owns ingredient semantics.  This check only binds
    # the already-rendered request to ordinary, bounded canonical JSON.
    if len(_canonical_bytes(request)) > 128 * 1024:
        _fail("recipe-change request exceeds its byte bound")
    return request


def _validate_binding(value: Any, label: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != {"path", "sha256", "size"}:
        _fail(f"{label} fields changed")
    binding = cast(dict[str, Any], value)
    _safe_relative(binding.get("path"), label)
    if (
        type(binding.get("sha256")) is not str
        or _DIGEST.fullmatch(binding["sha256"]) is None
        or type(binding.get("size")) is not int
        or type(binding.get("size")) is bool
        or not 1 <= binding["size"] <= MAXIMUM_SOURCE_BYTES
    ):
        _fail(f"{label} identity changed")
    return binding


def _validate_embedded_recipe_render(
    *,
    before: bytes,
    after: bytes,
    request: Mapping[str, Any],
    catalog_binding: Mapping[str, Any],
    owner_binding: Mapping[str, Any],
    map_binding: Mapping[str, Any],
    dependency: Mapping[str, Any],
) -> None:
    """Re-render one operation from retained bytes in an isolated fixture.

    The public renderer is intentionally filesystem-backed because its normal
    job is to guard a live checkout.  Plan validation must not consult that
    mutable checkout, so it supplies the retained owner bytes and the retained
    alias binding through a throwaway fixture and asks the same renderer for
    the authoritative result again.
    """

    owner_relative = _safe_relative(request.get("script"), "recipe owner")
    maps_relative = _safe_relative(dependency.get("path"), "recipe-map dependency")
    if owner_relative == maps_relative:
        _fail("recipe owner and recipe-map dependency must be distinct")

    pattern = {
        "key": PATTERN_KEY,
        "label": "GregTech machine recipe",
        "family_id": "machine-recipes",
        "lifecycle": catalog_binding["lifecycle"],
        "renderer": "gregtech-machine-recipe-v1",
        "summary": "Offline validation fixture for one retained recipe ADD.",
        "authority_spec": {
            "kind": "gregtech-postinit-recipe",
            "owner_root": "groovy/postInit",
            "recipe_maps_path": maps_relative.as_posix(),
            "required_imports": list(owner_binding["required_imports"]),
            "indentation": "    ",
        },
        "questions": [
            {
                "id": "script",
                "type": "path",
                "required": True,
            },
            {
                "id": "recipe_map",
                "type": "choice",
                "required": True,
                "choices": [map_binding["alias"]],
            },
            {
                "id": "item_inputs",
                "type": "ingredient-list",
                "required": False,
                "default": [],
            },
            {
                "id": "fluid_inputs",
                "type": "fluid-list",
                "required": False,
                "default": [],
            },
            {
                "id": "item_outputs",
                "type": "ingredient-list",
                "required": False,
                "default": [],
            },
            {
                "id": "fluid_outputs",
                "type": "fluid-list",
                "required": False,
                "default": [],
            },
            {
                "id": "duration",
                "type": "integer",
                "required": True,
                "minimum": 1,
                "maximum": 2_147_483_647,
            },
            {
                "id": "voltage_tier",
                "type": "choice",
                "required": True,
                "choices": list(_VOLTAGE_TIERS),
            },
        ],
        "limitations": [],
    }
    answers = {
        key: _canonical_copy(value)
        for key, value in request.items()
        if key != "mutation"
    }
    alias_source = (
        f"static final def {map_binding['alias']} = "
        f"recipemap('{map_binding['registry_name']}')\n"
    ).encode("utf-8")

    try:
        with tempfile.TemporaryDirectory(
            prefix="workbench-recipe-plan-validation-"
        ) as temporary:
            fixture = Path(temporary)
            owner_path = fixture.joinpath(*owner_relative.parts)
            maps_path = fixture.joinpath(*maps_relative.parts)
            owner_path.parent.mkdir(parents=True, exist_ok=True)
            maps_path.parent.mkdir(parents=True, exist_ok=True)
            owner_path.write_bytes(before)
            maps_path.write_bytes(alias_source)
            rendered = registration_render.render_registration(
                {"patterns": [pattern]},
                PATTERN_KEY,
                fixture,
                fixture,
                answers,
            )
    except (OSError, ValueError) as exc:
        raise RecipeChangeError(
            f"retained recipe operation cannot be reproduced by Blueprints: {exc}"
        ) from exc

    expected_operations = rendered.get("operations")
    expected_evidence = {
        "recipe_map_alias": map_binding["alias"],
        "recipe_map_registry_name": map_binding["registry_name"],
        "owning_script": owner_relative.as_posix(),
    }
    if (
        rendered.get("effective_answers") != answers
        or rendered.get("evidence") != expected_evidence
        or type(expected_operations) is not list
        or len(expected_operations) != 1
        or expected_operations[0]
        != {
            "operation": "update",
            "path": owner_relative.as_posix(),
            "before_sha256": sha256(before).hexdigest(),
            "content_sha256": sha256(after).hexdigest(),
            "content": after,
        }
    ):
        _fail("recipe operation differs from the exact Blueprints renderer result")


def validate_recipe_change_plan(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one sealed ADD plan without consulting mutable source bytes."""

    if type(value) is not dict:
        _fail("recipe-change plan must be one ordinary object")
    plan = dict(value)
    expected = {
        "action",
        "authority_bindings",
        "authority_boundary",
        "dependencies",
        "format",
        "id",
        "kind",
        "limitations",
        "operations",
        "profile_context",
        "request",
        "review",
        "runtime_observation",
        "schema_version",
        "state",
        "workspace_uri",
    }
    body = dict(plan)
    supplied_id = body.pop("id", None)
    if (
        set(plan) != expected
        or plan.get("format") != PLAN_FORMAT
        or plan.get("kind") != PLAN_KIND
        or plan.get("schema_version") != 1
        or type(plan.get("schema_version")) is bool
        or plan.get("action") != "add-machine-recipe"
        or plan.get("authority_boundary") != AUTHORITY_BOUNDARY
        or plan.get("state") != "experimental-ready"
        or supplied_id != application_transaction.content_id(PLAN_KIND, body)
    ):
        _fail("recipe-change plan identity or authority boundary changed")
    request = _validate_request(plan.get("request"))
    context = _validate_profile_context(plan.get("profile_context"))
    if (
        plan.get("workspace_uri") != context["workspace_uri"]
        or plan.get("runtime_observation") != _RUNTIME_OBSERVATION_REQUIREMENT
    ):
        _fail("recipe-change workspace or runtime requirement changed")
    _workspace_path_from_uri(plan["workspace_uri"])

    limitations = plan.get("limitations")
    if (
        type(limitations) is not list
        or len(limitations) < 2
        or any(type(row) is not str or not row for row in limitations)
    ):
        _fail("recipe-change limitations changed")

    dependencies = plan.get("dependencies")
    if type(dependencies) is not list or len(dependencies) != 1:
        _fail("recipe-change dependency bindings changed")
    dependency = _validate_binding(dependencies[0], "recipe-map dependency")

    operations = plan.get("operations")
    if type(operations) is not list or len(operations) != 1:
        _fail("recipe-change plan must update one existing owner")
    operation = operations[0]
    operation_fields = {
        "after_base64",
        "after_sha256",
        "after_size",
        "before_base64",
        "before_sha256",
        "before_size",
        "diff",
        "operation",
        "ordinal",
        "outcome",
        "path",
        "role",
    }
    if type(operation) is not dict or set(operation) != operation_fields:
        _fail("recipe-change operation fields changed")
    before = _decoded(operation, "before")
    after = _decoded(operation, "after")
    expected_diff = _unified_diff(before, after, operation.get("path"))
    if (
        operation.get("ordinal") != 0
        or type(operation.get("ordinal")) is bool
        or operation.get("operation") != "update"
        or operation.get("outcome") != "approved-update"
        or operation.get("role") != "machine-recipe-owner"
        or operation.get("path") != request["script"]
        or before == after
        or not expected_diff
        or operation.get("diff") != expected_diff
        or len(expected_diff.encode("utf-8")) > MAXIMUM_DIFF_BYTES
    ):
        _fail("recipe-change operation identity changed")
    if plan.get("review") != _review(operation):
        _fail("recipe-change review differs from its exact operation")

    bindings = plan.get("authority_bindings")
    if type(bindings) is not dict or set(bindings) != {
        "catalog",
        "owner",
        "recipe_map",
    }:
        _fail("recipe-change authority bindings changed")
    catalog_binding = bindings["catalog"]
    if (
        type(catalog_binding) is not dict
        or set(catalog_binding)
        != {
            "catalog_id",
            "lifecycle",
            "pattern_key",
            "profile_family_id",
            "renderer",
        }
        or type(catalog_binding.get("catalog_id")) is not str
        or _CONTENT_ID.fullmatch(catalog_binding["catalog_id"]) is None
        or catalog_binding.get("pattern_key") != PATTERN_KEY
        or catalog_binding.get("profile_family_id") != PACK_PROFILE_ID
        or catalog_binding.get("renderer") != "gregtech-machine-recipe-v1"
        or type(catalog_binding.get("lifecycle")) is not str
        or not catalog_binding["lifecycle"]
    ):
        _fail("recipe-change catalog binding changed")
    owner = bindings["owner"]
    if (
        type(owner) is not dict
        or set(owner) != {"path", "required_imports", "sha256", "size"}
        or owner.get("path") != operation["path"]
        or owner.get("sha256") != operation["before_sha256"]
        or owner.get("size") != operation["before_size"]
        or type(owner.get("required_imports")) is not list
        or not owner["required_imports"]
        or any(type(row) is not str or not row for row in owner["required_imports"])
    ):
        _fail("recipe-change owner binding changed")
    map_binding = bindings["recipe_map"]
    if (
        type(map_binding) is not dict
        or set(map_binding) != {"alias", "registry_name", "source"}
        or map_binding.get("alias") != request["recipe_map"]
        or type(map_binding.get("registry_name")) is not str
        or _REGISTRY_NAME.fullmatch(map_binding["registry_name"]) is None
        or map_binding.get("source") != dependency
    ):
        _fail("recipe-map alias binding changed")
    _validate_embedded_recipe_render(
        before=before,
        after=after,
        request=request,
        catalog_binding=catalog_binding,
        owner_binding=owner,
        map_binding=map_binding,
        dependency=dependency,
    )
    return cast(dict[str, Any], plan)


def verify_recipe_change_plan(
    suite_root: Path | str,
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    """Regenerate a plan from current bytes and report whether it is exact."""

    reviewed = validate_recipe_change_plan(plan)
    request = reviewed["request"]
    try:
        fresh = build_recipe_change_plan(
            suite_root,
            _workspace_from_uri(reviewed["workspace_uri"]),
            mutation=request["mutation"],
            recipe_script=request["script"],
            recipe_map=request["recipe_map"],
            item_inputs=request["item_inputs"],
            fluid_inputs=request["fluid_inputs"],
            item_outputs=request["item_outputs"],
            fluid_outputs=request["fluid_outputs"],
            duration=request["duration"],
            voltage_tier=request["voltage_tier"],
        )
    except (OSError, ValueError) as exc:
        return {
            "format": "workbench-supersymmetry-recipe-change-verification-v1",
            "schema_version": 1,
            "plan_id": reviewed["id"],
            "reason": str(exc),
            "state": "stale",
        }
    ready = fresh["id"] == reviewed["id"]
    return {
        "format": "workbench-supersymmetry-recipe-change-verification-v1",
        "schema_version": 1,
        "plan_id": reviewed["id"],
        "reason": None if ready else "plan regenerated differently from current bytes",
        "state": "ready" if ready else "stale",
    }


def recipe_change_workspace(plan: Mapping[str, Any]) -> Path:
    """Resolve the ordinary local workspace bound by a validated plan."""

    reviewed = validate_recipe_change_plan(plan)
    return _workspace_from_uri(reviewed["workspace_uri"])


def _identity_dependencies(request: Mapping[str, Any]) -> list[dict[str, Any]]:
    dependencies: list[dict[str, Any]] = []
    for direction in ("inputs", "outputs"):
        for domain in ("item", "fluid"):
            for ordinal, row in enumerate(request[f"{domain}_{direction}"]):
                dependencies.append(
                    {
                        "direction": direction[:-1],
                        "domain": domain,
                        "ordinal": ordinal,
                        "value": _canonical_copy(row),
                    }
                )
    return dependencies


def build_recipe_observation_contract(
    plan: Mapping[str, Any],
    *,
    physical_side: str,
) -> dict[str, Any]:
    """Derive Atlas's exact disposable-runtime handoff from one plan."""

    reviewed = validate_recipe_change_plan(plan)
    if physical_side not in _RUNTIME_OBSERVATION_REQUIREMENT["physical_sides"]:
        _fail("recipe observation side must be client or dedicated-server")
    request = reviewed["request"]
    map_binding = reviewed["authority_bindings"]["recipe_map"]
    body = {
        "authority": {
            "construction_authority": "none",
            "interpretation_owner": "Atlas",
            "source_profile": PACK_PROFILE_ID,
        },
        "collision_definition": {
            "competing_recipe": (
                "a different registered recipe in the selected map that accepts "
                "a concrete active-runtime expansion of the planned inputs"
            ),
            "expected_competing_input_match_count": 0,
            "expected_exact_recipe_match_count": 1,
            "unresolved_input_expansions_are_failure": True,
        },
        "expected_recipe": {
            "duration": request["duration"],
            "eut_expression": f"VA[{request['voltage_tier']}]",
            "fluid_inputs": _canonical_copy(request["fluid_inputs"]),
            "fluid_outputs": _canonical_copy(request["fluid_outputs"]),
            "item_inputs": _canonical_copy(request["item_inputs"]),
            "item_outputs": _canonical_copy(request["item_outputs"]),
            "recipe_map_alias": request["recipe_map"],
            "recipe_map_registry_name": map_binding["registry_name"],
            "source_owner": request["script"],
            "voltage_tier": request["voltage_tier"],
        },
        "format": OBSERVATION_FORMAT,
        "identity_dependencies": _identity_dependencies(request),
        "kind": OBSERVATION_KIND,
        "limitations": [
            "The result applies only to the exact disposable projection and cold start.",
            "Ore-dictionary collision coverage is limited to expansions present in that active runtime; unresolved expansions fail the observation.",
            "Specialized recipe-builder properties and chanced outputs are outside the V1 construction pattern.",
            "This contract does not authorize source mutation or publication.",
        ],
        "physical_side": physical_side,
        "probe_phase": "postInit-after-pack-recipe-owners",
        "required_checks": {
            "competing_input_match_count": 0,
            "declared_identities_resolve": True,
            "exact_recipe_match_count": 1,
            "groovy_origin": True,
            "lookup_resolves_exact_recipe": True,
            "recipe_map_alias_binding": True,
            "recipe_signature": True,
            "source_owner_compiled": True,
            "unresolved_input_expansion_count": 0,
        },
        "schema_version": 1,
        "source_plan_id": reviewed["id"],
        "state": "observation-required",
    }
    return _seal(OBSERVATION_KIND, body)


def validate_recipe_observation_contract(
    value: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate a contract by deriving it again from its exact source plan."""

    if type(value) is not dict:
        _fail("recipe observation contract must be one ordinary object")
    physical_side = value.get("physical_side")
    expected = build_recipe_observation_contract(
        plan, physical_side=cast(str, physical_side)
    )
    if dict(value) != expected:
        _fail("recipe observation contract differs from its source plan")
    return cast(dict[str, Any], value)


def _validate_application_receipt(
    value: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    reviewed = validate_recipe_change_plan(plan)
    if type(value) is not dict:
        _fail("recipe-change application receipt must be one ordinary object")
    receipt = dict(value)
    body = dict(receipt)
    supplied = body.pop("id", None)
    if (
        receipt.get("format") != RECEIPT_FORMAT
        or receipt.get("kind") != RECEIPT_KIND
        or receipt.get("schema_version") != 1
        or type(receipt.get("schema_version")) is bool
        or receipt.get("plan_id") != reviewed["id"]
        or receipt.get("authority_boundary") != AUTHORITY_BOUNDARY
        or receipt.get("state") not in {"applied", "rejected"}
        or supplied != application_transaction.content_id(RECEIPT_KIND, body)
    ):
        _fail("recipe-change application receipt identity changed")
    if receipt["state"] == "applied":
        try:
            application_transaction.validate_applied_application_receipt(
                receipt,
                reviewed,
                receipt_format=RECEIPT_FORMAT,
                receipt_kind=RECEIPT_KIND,
                receipt_content_kind=RECEIPT_KIND,
                success_mutation_state="applied-experimental-local-edit",
            )
        except ValueError as exc:
            raise RecipeChangeError("recipe-change success receipt is invalid") from exc
        return cast(dict[str, Any], receipt)

    diagnostic = receipt.get("diagnostic_code")
    base_fields = {
        "authority_boundary",
        "diagnostic_code",
        "format",
        "id",
        "kind",
        "mutation_state",
        "plan_id",
        "rollback",
        "schema_version",
        "state",
    }
    if diagnostic in {"BLUEPRINTS_M2_STALE_PLAN", "BLUEPRINTS_M2_TRANSACTION_LOCKED"}:
        if (
            set(receipt) != base_fields
            or receipt.get("mutation_state") != "not-started"
            or receipt.get("rollback") != "not-needed"
        ):
            _fail("recipe-change preflight rejection fields changed")
        return cast(dict[str, Any], receipt)
    if diagnostic not in {
        "BLUEPRINTS_M2_PARTIAL_FAILURE_ROLLED_BACK",
        "BLUEPRINTS_M2_ROLLBACK_REQUIRES_REVIEW",
    }:
        _fail("recipe-change rejection has an unknown diagnostic")
    expected_history = [
        {
            "sha256": operation[f"{prefix}_sha256"],
            "size": operation[f"{prefix}_size"],
        }
        for operation in reviewed["operations"]
        for prefix in ("before", "after")
    ]
    expected_history.sort(key=lambda row: (row["sha256"], row["size"]))
    restored = diagnostic == "BLUEPRINTS_M2_PARTIAL_FAILURE_ROLLED_BACK"
    if (
        set(receipt) != base_fields | {"failure_kind", "history_objects"}
        or type(receipt.get("failure_kind")) is not str
        or not receipt["failure_kind"]
        or receipt.get("history_objects") != expected_history
        or receipt.get("mutation_state")
        != ("restored" if restored else "indeterminate")
        or receipt.get("rollback")
        != ("succeeded" if restored else "blocked-by-later-edit")
    ):
        _fail("recipe-change partial-failure receipt fields changed")
    return cast(dict[str, Any], receipt)


def validate_recipe_change_receipt(
    value: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    """Public validator for an application receipt."""

    return _validate_application_receipt(value, plan)


def apply_recipe_change_plan(
    suite_root: Path | str,
    plan: Mapping[str, Any],
    state_root: Path | str,
    *,
    consent_plan_id: str,
    fail_after_ordinal: int | None = None,
    after_preflight: Callable[[Path], None] | None = None,
    transaction_lock: Path | str | None = None,
    commit_receipt: Callable[[Mapping[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Apply an exact reviewed ADD through the Blueprints transaction owner."""

    reviewed = validate_recipe_change_plan(plan)
    if consent_plan_id != reviewed["id"]:
        _fail("recipe-change apply requires consent to the exact reviewed plan ID")
    verification = verify_recipe_change_plan(suite_root, reviewed)
    if verification["state"] != "ready":
        _fail(f"recipe-change plan is stale: {verification['reason']}")
    workspace = _workspace_from_uri(reviewed["workspace_uri"])

    def source_preflight(root: Path, candidate: Mapping[str, Any]) -> bool:
        if root != workspace or candidate.get("id") != reviewed["id"]:
            return False
        try:
            return verify_recipe_change_plan(suite_root, candidate)["state"] == "ready"
        except (OSError, ValueError):
            return False

    def commit(candidate: Mapping[str, Any]) -> None:
        if commit_receipt is not None:
            commit_receipt(_validate_application_receipt(candidate, reviewed))

    try:
        receipt = application_transaction.apply_application_transaction(
            workspace,
            reviewed,
            state_root,
            receipt_format=RECEIPT_FORMAT,
            receipt_kind=RECEIPT_KIND,
            receipt_content_kind=RECEIPT_KIND,
            success_mutation_state="applied-experimental-local-edit",
            fail_after_ordinal=fail_after_ordinal,
            after_preflight=after_preflight,
            source_preflight=source_preflight,
            transaction_lock=transaction_lock,
            commit_receipt=commit if commit_receipt is not None else None,
        )
    except ValueError as exc:
        raise RecipeChangeError(f"recipe-change transaction failed: {exc}") from exc
    return _validate_application_receipt(receipt, reviewed)


def rollback_recipe_change(
    plan: Mapping[str, Any],
    state_root: Path | str,
    *,
    application_receipt: Mapping[str, Any],
    transaction_lock: Path | str | None = None,
) -> dict[str, Any]:
    """Restore exact before-bytes while preserving any later edit."""

    reviewed = validate_recipe_change_plan(plan)
    applied = _validate_application_receipt(application_receipt, reviewed)
    if applied["state"] != "applied":
        _fail("recipe-change rollback requires a successful application receipt")
    try:
        receipt = application_transaction.rollback_application_transaction(
            _workspace_from_uri(reviewed["workspace_uri"]),
            reviewed,
            state_root,
            applied=applied,
            rollback_format=ROLLBACK_FORMAT,
            rollback_kind=ROLLBACK_KIND,
            rollback_content_kind=ROLLBACK_KIND,
            transaction_lock=transaction_lock,
        )
    except ValueError as exc:
        raise RecipeChangeError(f"recipe-change rollback failed: {exc}") from exc
    return validate_recipe_change_rollback(receipt, reviewed)


def validate_recipe_change_rollback(
    value: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    reviewed = validate_recipe_change_plan(plan)
    if type(value) is not dict:
        _fail("recipe-change rollback receipt must be one ordinary object")
    receipt = dict(value)
    body = dict(receipt)
    supplied = body.pop("id", None)
    if (
        set(receipt)
        != {
            "diagnostic_code",
            "format",
            "id",
            "kind",
            "plan_id",
            "schema_version",
            "state",
            "workspace_mutated",
        }
        or receipt.get("format") != ROLLBACK_FORMAT
        or receipt.get("kind") != ROLLBACK_KIND
        or receipt.get("schema_version") != 1
        or type(receipt.get("schema_version")) is bool
        or receipt.get("plan_id") != reviewed["id"]
        or receipt.get("state") not in {"restored", "rejected"}
        or supplied != application_transaction.content_id(ROLLBACK_KIND, body)
    ):
        _fail("recipe-change rollback receipt identity changed")
    if receipt["state"] == "restored":
        if (
            receipt.get("diagnostic_code") is not None
            or receipt.get("workspace_mutated") is not True
        ):
            _fail("recipe-change rollback success fields changed")
    elif (
        receipt.get("diagnostic_code")
        not in {
            "BLUEPRINTS_M2_LATER_EDIT_PRESERVED",
            "BLUEPRINTS_M2_TRANSACTION_LOCKED",
        }
        or receipt.get("workspace_mutated") is not False
    ):
        _fail("recipe-change rollback rejection fields changed")
    return cast(dict[str, Any], receipt)


def recover_recipe_change(
    plan: Mapping[str, Any],
    state_root: Path | str,
    *,
    transaction_lock: Path | str | None = None,
    commit_receipt: Callable[[Mapping[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Recover a Blueprints transaction from its durable ownership journal."""

    reviewed = validate_recipe_change_plan(plan)

    def commit(candidate: Mapping[str, Any]) -> None:
        if commit_receipt is not None:
            commit_receipt(_validate_application_receipt(candidate, reviewed))

    try:
        result = application_transaction.recover_application_transaction(
            _workspace_from_uri(reviewed["workspace_uri"]),
            reviewed,
            state_root,
            receipt_format=RECEIPT_FORMAT,
            receipt_kind=RECEIPT_KIND,
            receipt_content_kind=RECEIPT_KIND,
            success_mutation_state="applied-experimental-local-edit",
            transaction_lock=transaction_lock,
            commit_receipt=commit if commit_receipt is not None else None,
        )
    except ValueError as exc:
        raise RecipeChangeError(f"recipe-change recovery failed: {exc}") from exc
    application_receipt = result.get("application_receipt")
    if application_receipt is not None:
        application_receipt = _validate_application_receipt(
            application_receipt, reviewed
        )
    body = {
        "application_receipt": application_receipt,
        "attempted_ordinals": result.get("attempted_ordinals"),
        "diagnostic_code": result.get("diagnostic_code"),
        "format": RECOVERY_FORMAT,
        "kind": RECOVERY_KIND,
        "plan_id": reviewed["id"],
        "schema_version": 1,
        "state": result.get("outcome"),
        "workspace_mutated": result.get("workspace_mutated"),
    }
    return validate_recipe_change_recovery(
        _seal(RECOVERY_KIND, body), reviewed
    )


def validate_recipe_change_recovery(
    value: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    reviewed = validate_recipe_change_plan(plan)
    if type(value) is not dict:
        _fail("recipe-change recovery receipt must be one ordinary object")
    receipt = dict(value)
    body = dict(receipt)
    supplied = body.pop("id", None)
    if (
        set(receipt)
        != {
            "application_receipt",
            "attempted_ordinals",
            "diagnostic_code",
            "format",
            "id",
            "kind",
            "plan_id",
            "schema_version",
            "state",
            "workspace_mutated",
        }
        or receipt.get("format") != RECOVERY_FORMAT
        or receipt.get("kind") != RECOVERY_KIND
        or receipt.get("schema_version") != 1
        or type(receipt.get("schema_version")) is bool
        or receipt.get("plan_id") != reviewed["id"]
        or receipt.get("state") not in {"applied", "restored", "review-required"}
        or supplied != application_transaction.content_id(RECOVERY_KIND, body)
    ):
        _fail("recipe-change recovery receipt identity changed")
    attempted = receipt.get("attempted_ordinals")
    if (
        type(attempted) is not list
        or any(type(row) is not int for row in attempted)
        or attempted != list(range(len(attempted)))
        or len(attempted) > 1
        or type(receipt.get("workspace_mutated")) is not bool
    ):
        _fail("recipe-change recovery ownership changed")
    if receipt["state"] == "applied":
        if (
            attempted != [0]
            or receipt.get("diagnostic_code") is not None
            or receipt.get("workspace_mutated") is not False
            or type(receipt.get("application_receipt")) is not dict
        ):
            _fail("recipe-change recovery finalization fields changed")
        nested = _validate_application_receipt(
            receipt["application_receipt"], reviewed
        )
        if nested["state"] != "applied":
            _fail(
                "recipe-change applied recovery requires an applied "
                "application receipt"
            )
    elif receipt["state"] == "restored":
        if (
            receipt.get("diagnostic_code")
            not in {
                "BLUEPRINTS_M2_INTERRUPTED_BEFORE_MUTATION",
                "BLUEPRINTS_M2_INTERRUPTED_TRANSACTION_RESTORED",
            }
            or receipt.get("application_receipt") is not None
        ):
            _fail("recipe-change recovery restoration fields changed")
    elif (
        receipt.get("diagnostic_code")
        != "BLUEPRINTS_M2_RECOVERY_REQUIRES_REVIEW"
        or receipt.get("workspace_mutated") is not False
        or receipt.get("application_receipt") is not None
    ):
        _fail("recipe-change recovery review fields changed")
    return cast(dict[str, Any], receipt)


__all__ = [
    "AUTHORITY_BOUNDARY",
    "OBSERVATION_FORMAT",
    "PLAN_FORMAT",
    "PLAN_KIND",
    "RECEIPT_FORMAT",
    "RECEIPT_KIND",
    "RecipeChangeError",
    "apply_recipe_change_plan",
    "build_recipe_add_plan",
    "build_recipe_change_plan",
    "build_recipe_observation_contract",
    "recipe_change_options",
    "recipe_change_workspace",
    "recover_recipe_change",
    "rollback_recipe_change",
    "validate_recipe_change_plan",
    "validate_recipe_change_receipt",
    "validate_recipe_change_recovery",
    "validate_recipe_change_rollback",
    "validate_recipe_observation_contract",
    "verify_recipe_change_plan",
]
