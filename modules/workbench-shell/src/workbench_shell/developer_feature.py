"""Current-checkout, reversible developer feature flow.

This module composes Atlas observations and Blueprints renderers against the
selected checkout.  It intentionally does not consume release admissions,
support decisions, compositions, proof bundles, or freeze records.
"""

from __future__ import annotations

from workbench_api.resources import module_root as _module_resource_root, repository_root as _repository_resource_root

import base64
import difflib
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
from typing import Any, Callable, Mapping, NoReturn, cast
from urllib.parse import urlparse
from urllib.request import url2pathname

from .bootstrap import inspect_project
from workbench_api.state_paths import default_feature_state_root


CATALOG_PATH = Path("profiles/packs/supersymmetry/registration/catalog-v1.json")
PROFILE_ROOT = Path("profiles/packs/supersymmetry")
PLAN_FORMAT = "workbench-developer-material-fluid-recipe-plan-v1"
RECEIPT_FORMAT = "workbench-developer-material-fluid-recipe-receipt-v1"
ROLLBACK_FORMAT = "workbench-developer-material-fluid-recipe-rollback-v1"
RECOVERY_FORMAT = "workbench-developer-material-fluid-recipe-recovery-v1"
RUN_FORMAT = "workbench-developer-material-fluid-recipe-run-v1"
PLAN_KIND = "workbench-developer-material-fluid-recipe-plan"
RECEIPT_KIND = "workbench-developer-material-fluid-recipe-receipt"
ROLLBACK_KIND = "workbench-developer-material-fluid-recipe-rollback"
RECOVERY_KIND = "workbench-developer-material-fluid-recipe-recovery"
RUN_KIND = "workbench-developer-material-fluid-recipe-run"
MAXIMUM_OPERATION_BYTES = 4 * 1024 * 1024
MAXIMUM_RECORD_BYTES = 48 * 1024 * 1024
MAXIMUM_DIFF_BYTES = 512 * 1024
AUTHORITY_BOUNDARY = {
    "application_scope": "reversible-local-experiment",
    "atlas_semantics_admitted": False,
    "profile_tested_support": False,
    "release_or_publication_authorized": False,
    "stable_standard_admission": False,
    "transaction_owner": "blueprints",
}
_CONTENT_ID = re.compile(r"[a-z][a-z0-9-]*:sha256:[0-9a-f]{64}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_FLUID_ID = re.compile(r"[a-z0-9][a-z0-9_.:/-]*\Z")
_RECIPE_ALIAS = re.compile(r"[A-Z][A-Z0-9_]*\Z")
_SYMBOL = re.compile(r"[A-Z][A-Za-z0-9]*\Z")
_VOLTAGE_TIERS = frozenset(
    {"ULV", "LV", "MV", "HV", "EV", "IV", "LuV", "ZPM", "UV", "UHV", "UEV", "UIV"}
)
_STATIC_ROLES = {
    "groovy/material/PetrochemistryMaterials.groovy": "material-fluid-registration",
    "groovy/material/SuSyMaterials.groovy": "material-declaration",
    "resources/langfiles/lang/en_us.lang": "localization",
}
_RECORD_COLLECTIONS = frozenset(
    {"plans", "receipts", "recoveries", "rollbacks", "runs"}
)
_RECORD_KINDS = {
    "plans": frozenset(
        {
            PLAN_KIND,
            "workbench-developer-source-feature-plan",
            "workbench-supersymmetry-recipe-change-plan",
        }
    ),
    "receipts": frozenset(
        {
            RECEIPT_KIND,
            "workbench-developer-source-feature-receipt",
            "workbench-supersymmetry-recipe-change-receipt",
        }
    ),
    "recoveries": frozenset(
        {
            RECOVERY_KIND,
            "workbench-developer-source-feature-recovery",
            "workbench-supersymmetry-recipe-change-recovery",
        }
    ),
    "rollbacks": frozenset(
        {
            ROLLBACK_KIND,
            "workbench-developer-source-feature-rollback",
            "workbench-supersymmetry-recipe-change-rollback",
        }
    ),
    "runs": frozenset(
        {
            RUN_KIND,
            "workbench-developer-recipe-change-runtime-comparison",
        }
    ),
}


class DeveloperFeatureError(ValueError):
    """A current-checkout experimental feature request is unsafe or stale."""


def _fail(message: str) -> NoReturn:
    raise DeveloperFeatureError(message)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _content_id(kind: str, body: Mapping[str, Any]) -> str:
    return f"{kind}:sha256:{sha256(_canonical_bytes(dict(body))).hexdigest()}"


def _seal(kind: str, body: Mapping[str, Any]) -> dict[str, Any]:
    return {**body, "id": _content_id(kind, body)}


def _authorities() -> tuple[Any, Any, Any, Any]:
    try:
        from workbench_atlas import material_census
        from workbench_blueprints import application_transaction
        from workbench_blueprints import registration_catalog
        from workbench_blueprints import registration_render
    except ImportError as exc:  # pragma: no cover - packaging regression
        _fail(f"Workbench feature authorities are unavailable: {exc}")
    return (
        material_census,
        registration_catalog,
        registration_render,
        application_transaction,
    )


def _construction_suite_root() -> Path:
    """Resolve packaged or source construction inputs without target access."""

    candidates = [_repository_resource_root(__file__)]
    for candidate in candidates:
        if (
            (candidate / CATALOG_PATH).is_file()
            and (candidate / PROFILE_ROOT).is_dir()
        ):
            return candidate
    _fail("feature construction authority is unavailable in this installation")


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


def _read_workspace_file(
    workspace: Path,
    relative: PurePosixPath,
    label: str,
    *,
    maximum: int = MAXIMUM_OPERATION_BYTES,
) -> bytes:
    """Read one bounded regular descendant without following workspace links."""

    parent = workspace
    for part in relative.parts[:-1]:
        parent = parent / part
        try:
            state = parent.lstat()
        except OSError as exc:
            raise DeveloperFeatureError(f"cannot inspect {label} parent") from exc
        if stat.S_ISLNK(state.st_mode) or not stat.S_ISDIR(state.st_mode):
            _fail(f"{label} traverses a symbolic link or non-directory")
    path = workspace.joinpath(*relative.parts)
    try:
        target_state = path.lstat()
    except OSError as exc:
        raise DeveloperFeatureError(f"cannot inspect {label}") from exc
    if stat.S_ISLNK(target_state.st_mode) or not stat.S_ISREG(
        target_state.st_mode
    ):
        _fail(f"{label} is not a regular non-symlink file")
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0),
        )
    except OSError as exc:
        raise DeveloperFeatureError(f"cannot open {label}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
            _fail(f"{label} is not a bounded regular file")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, maximum + 1))
            if not chunk:
                break
            chunks.append(chunk)
            if sum(len(value) for value in chunks) > maximum:
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


def _workspace_path_from_uri(value: Any) -> Path:
    if type(value) is not str:
        _fail("feature plan lacks its workspace URI")
    parsed = urlparse(value)
    if (
        parsed.scheme != "file"
        or parsed.netloc not in {"", "localhost"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        _fail("feature workspace must be a local file URI")
    path = Path(url2pathname(parsed.path))
    if not path.is_absolute():
        _fail("feature workspace URI must contain an absolute path")
    return path


def _workspace_from_uri(value: Any) -> Path:
    path = _workspace_path_from_uri(value)
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise DeveloperFeatureError(f"feature workspace is unavailable: {exc}") from exc
    if not resolved.is_dir() or resolved.is_symlink():
        _fail("feature workspace is not a regular directory")
    return resolved


def _normalize_color(value: str) -> str:
    if type(value) is not str:
        _fail("material color must be six hexadecimal digits")
    normalized = value.lower().removeprefix("#").removeprefix("0x")
    if re.fullmatch(r"[0-9a-f]{6}", normalized) is None:
        _fail("material color must be six hexadecimal digits")
    return "0x" + normalized


def _slug(value: str) -> str:
    if type(value) is not str:
        _fail("material name must be text")
    result = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    if not result:
        _fail("material name does not produce a registry identity")
    return result


def _require_context(inspection: Mapping[str, Any]) -> None:
    context = inspection.get("workspace_context")
    if type(context) is not dict:
        _fail("project inspection lacks a workspace context")
    project = context.get("project")
    workspace = context.get("workspace")
    pack = context.get("pack")
    platform = context.get("platform")
    if not all(type(value) is dict for value in (project, workspace, pack, platform)):
        _fail("project inspection is incomplete")
    if (
        project.get("name") != "Supersymmetry"
        or project.get("minecraft_version") != "1.12.2"
        or pack.get("profile_family_id") != "workbench-pack:supersymmetry"
        or pack.get("maturity") != "experimental"
        or "construct" not in pack.get("permitted_operations", [])
        or platform.get("profile_id")
        != "workbench-platform:cleanroom:provisional"
    ):
        _fail(
            "material-fluid recipe construction requires the experimental "
            "Supersymmetry Cleanroom profile with construct permission"
        )
    if any(
        type(value) is not str or not value
        for value in (
            platform.get("cleanroom_version"),
            pack.get("document_sha256"),
            project.get("version"),
            platform.get("document_sha256"),
            pack.get("selected_profile"),
            workspace.get("revision"),
        )
    ):
        _fail("project inspection lacks an exact construction binding")


def _read_lifecycle_file(
    workspace: Path,
    relative: str,
    marker: str | None,
) -> dict[str, Any]:
    path = _safe_relative(relative, "lifecycle source path")
    try:
        raw = _read_workspace_file(
            workspace,
            path,
            f"lifecycle source {relative}",
        )
        text = raw.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise DeveloperFeatureError(f"cannot read lifecycle source {relative}: {exc}") from exc
    if marker is not None and text.count(marker) != 1:
        _fail(f"current checkout does not expose one exact lifecycle marker: {marker}")
    return {"path": relative, "sha256": sha256(raw).hexdigest(), "size": len(raw)}


def _recipe_alias(
    aliases: Mapping[str, str],
    requested: str,
) -> tuple[str, str]:
    if type(requested) is not str or not requested:
        _fail("recipe map must name a current Recipemaps alias or registry name")
    candidates = [
        (alias, registry)
        for alias, registry in aliases.items()
        if requested in {alias, alias.lower(), registry}
    ]
    if len(candidates) != 1:
        available = ", ".join(
            f"{alias} ({registry})" for alias, registry in list(aliases.items())[:12]
        )
        _fail(
            "recipe map is not uniquely defined by the current checkout; "
            f"examples: {available}"
        )
    return candidates[0]


def _transaction_operation(
    workspace: Path,
    raw_operation: Mapping[str, Any],
    *,
    ordinal: int,
    role: str,
) -> dict[str, Any]:
    relative = _safe_relative(raw_operation.get("path"), "feature operation path")
    before = _read_workspace_file(
        workspace,
        relative,
        f"feature operation target {relative}",
    )
    after = raw_operation.get("content")
    if type(after) is not bytes:
        _fail(f"Blueprint operation lacks rendered bytes: {relative}")
    if (
        raw_operation.get("operation") != "update"
        or raw_operation.get("before_sha256") != sha256(before).hexdigest()
        or raw_operation.get("content_sha256") != sha256(after).hexdigest()
        or before == after
    ):
        _fail(f"Blueprint operation identity changed: {relative}")
    diff = _unified_diff(before, after, relative.as_posix())
    if not diff or len(diff.encode("utf-8")) > MAXIMUM_DIFF_BYTES:
        _fail(f"feature diff is empty or exceeds its bound: {relative}")
    return {
        "after_base64": base64.b64encode(after).decode("ascii"),
        "after_sha256": sha256(after).hexdigest(),
        "after_size": len(after),
        "before_base64": base64.b64encode(before).decode("ascii"),
        "before_sha256": sha256(before).hexdigest(),
        "before_size": len(before),
        "diff": diff,
        "operation": "update",
        "ordinal": ordinal,
        "outcome": "approved-update",
        "path": relative.as_posix(),
        "role": role,
    }


def _unified_diff(before: bytes, after: bytes, relative: str) -> str:
    """Render readable review text even when an owner lacks a final newline."""

    try:
        before_lines = before.decode("utf-8", errors="strict").splitlines(
            keepends=True
        )
        after_lines = after.decode("utf-8", errors="strict").splitlines(
            keepends=True
        )
    except UnicodeError as exc:
        raise DeveloperFeatureError(
            f"feature target is not UTF-8: {relative}"
        ) from exc

    # ``difflib`` concatenates its next marker onto a final line that has no
    # terminator. The exact bytes remain in the plan; this normalization is
    # only for a reviewable line-oriented presentation.
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


def _review(operations: list[dict[str, Any]]) -> dict[str, Any]:
    unified = "".join(operation["diff"] for operation in operations)
    additions = sum(
        1
        for line in unified.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    deletions = sum(
        1
        for line in unified.splitlines()
        if line.startswith("-") and not line.startswith("---")
    )
    return {
        "additions": additions,
        "changed_files": [operation["path"] for operation in operations],
        "deletions": deletions,
        "unified_diff": unified,
    }


def material_fluid_recipe_options(
    suite_root: Path | str,
    workspace_root: Path | str,
) -> dict[str, Any]:
    """List current-checkout recipe maps and existing owner scripts."""

    suite = Path(suite_root).resolve()
    workspace = Path(workspace_root).expanduser().resolve()
    _require_context(inspect_project(suite, workspace))
    _material_census, registration_catalog, registration_render, _transaction = (
        _authorities()
    )
    try:
        catalog = registration_catalog.load_registration_catalog(suite / CATALOG_PATH)
        runtime = registration_render.runtime_question_options(
            catalog,
            "machine-recipe",
            workspace,
        )
        aliases = registration_render.recipe_map_aliases(
            workspace,
            "groovy/prePostInit/Recipemaps.groovy",
        )
    except (ValueError, OSError) as exc:
        raise DeveloperFeatureError(f"cannot resolve current recipe options: {exc}") from exc
    return {
        "format": "workbench-developer-material-fluid-recipe-options-v1",
        "schema_version": 1,
        "state": "experimental",
        "recipe_maps": [
            {"alias": alias, "registry_name": registry}
            for alias, registry in aliases.items()
        ],
        "scripts": runtime.get("script", []),
    }


def build_material_fluid_recipe_plan(
    suite_root: Path | str,
    workspace_root: Path | str,
    *,
    name: str,
    color: str,
    recipe_script: str,
    recipe_map: str,
    input_fluid: str,
    input_amount: int = 1000,
    output_amount: int = 1000,
    duration: int = 200,
    voltage_tier: str = "LV",
    translation: str | None = None,
    symbol: str | None = None,
) -> dict[str, Any]:
    """Build one current-checkout four-file plan without mutating source."""

    suite = Path(suite_root).resolve()
    workspace = Path(workspace_root).expanduser().resolve()
    inspection = inspect_project(suite, workspace)
    _require_context(inspection)
    if type(input_fluid) is not str or _FLUID_ID.fullmatch(input_fluid) is None:
        _fail("input fluid is not a safe registry identity")
    if input_fluid == _slug(name):
        _fail("input fluid cannot be the material fluid being created")
    for value, label in (
        (input_amount, "input amount"),
        (output_amount, "output amount"),
        (duration, "duration"),
    ):
        if type(value) is not int or value < 1 or value > 2_147_483_647:
            _fail(f"{label} must be a positive integer")

    material_census, registration_catalog, registration_render, _transaction = (
        _authorities()
    )
    try:
        catalog = registration_catalog.load_registration_catalog(suite / CATALOG_PATH)
        census = material_census.census_material_directory(workspace)
        aliases = registration_render.recipe_map_aliases(
            workspace,
            "groovy/prePostInit/Recipemaps.groovy",
        )
        resolved_alias, resolved_registry = _recipe_alias(aliases, recipe_map)
        rendered = registration_render.render_material_fluid_recipe(
            catalog,
            suite / PROFILE_ROOT,
            workspace,
            material_answers={
                "name": name,
                "color": _normalize_color(color),
                **({} if translation is None else {"translation": translation}),
                **({} if symbol is None else {"symbol": symbol}),
            },
            recipe_answers={
                "script": recipe_script,
                "recipe_map": resolved_alias,
                "item_inputs": [],
                "fluid_inputs": [{"name": input_fluid, "amount": input_amount}],
                "item_outputs": [],
                "output_amount": output_amount,
                "duration": duration,
                "voltage_tier": voltage_tier,
            },
            material_census=census,
        )
    except (ValueError, OSError) as exc:
        raise DeveloperFeatureError(f"cannot construct material-fluid recipe: {exc}") from exc

    dependencies = [
        _read_lifecycle_file(
            workspace,
            "groovy/preInit/MaterialChanges.groovy",
            "SuSyMaterials.init()",
        ),
        _read_lifecycle_file(
            workspace,
            "groovy/prePostInit/Recipemaps.groovy",
            None,
        ),
    ]
    _read_lifecycle_file(
        workspace,
        "groovy/material/SuSyMaterials.groovy",
        "PetrochemistryMaterials.register()",
    )

    role_by_path = {
        "groovy/material/SuSyMaterials.groovy": "material-declaration",
        "groovy/material/PetrochemistryMaterials.groovy": "material-fluid-registration",
        "resources/langfiles/lang/en_us.lang": "localization",
        recipe_script: "machine-recipe",
    }
    raw_operations = rendered["operations"]
    if {operation.get("path") for operation in raw_operations} != set(role_by_path):
        _fail("Blueprint composition changed outside its four reviewed owners")
    operations = [
        _transaction_operation(
            workspace,
            operation,
            ordinal=index,
            role=role_by_path[operation["path"]],
        )
        for index, operation in enumerate(raw_operations)
    ]
    effective = rendered["effective_request"]
    request = {
        "color": effective["material"]["color"],
        "duration": effective["recipe"]["duration"],
        "input_amount": effective["recipe"]["fluid_inputs"][0]["amount"],
        "input_fluid": effective["recipe"]["fluid_inputs"][0]["name"],
        "material_id": effective["material"]["material_id"],
        "name": effective["material"]["name"],
        "output_amount": effective["recipe"]["fluid_outputs"][0]["amount"],
        "recipe_map": effective["recipe"]["recipe_map"],
        "recipe_map_registry_name": resolved_registry,
        "recipe_script": effective["recipe"]["script"],
        "registry_name": effective["material"]["registry_name"],
        "symbol": effective["material"]["symbol_name"],
        "translation": effective["material"]["translation"],
        "voltage_tier": effective["recipe"]["voltage_tier"],
    }
    body = {
        "action": "apply-experimental-reversible-edit",
        "authority_boundary": dict(AUTHORITY_BOUNDARY),
        "dependencies": dependencies,
        "format": PLAN_FORMAT,
        "kind": PLAN_KIND,
        "operations": operations,
        "request": request,
        "review": _review(operations),
        "schema_version": 1,
        "state": "experimental-ready",
        "workspace_uri": workspace.as_uri(),
    }
    return validate_material_fluid_recipe_plan(_seal(PLAN_KIND, body))


def _decoded(row: Mapping[str, Any], prefix: str) -> bytes:
    try:
        raw = base64.b64decode(row[f"{prefix}_base64"], validate=True)
    except (KeyError, TypeError, ValueError) as exc:
        raise DeveloperFeatureError("feature operation bytes are invalid") from exc
    if (
        len(raw) > MAXIMUM_OPERATION_BYTES
        or type(row.get(f"{prefix}_size")) is not int
        or row.get(f"{prefix}_size") != len(raw)
        or row.get(f"{prefix}_sha256") != sha256(raw).hexdigest()
    ):
        _fail("feature operation byte identity changed")
    return raw


def _validate_material_fluid_recipe_blueprint_semantics(
    plan: Mapping[str, Any],
    before_by_path: Mapping[str, bytes],
    after_by_path: Mapping[str, bytes],
) -> None:
    """Replay Blueprints from plan-owned bytes without reading the checkout.

    A content ID only proves that a record is internally self-consistent.  It
    does not prove that a resealed ``after`` payload is the construction the
    request authorizes.  Replaying the owner renderer here closes that gap for
    validation paths such as rollback and interrupted-transaction recovery,
    where consulting the mutable live checkout would be both unnecessary and
    unsafe.

    The renderer currently accepts a filesystem target, so this adapter gives
    it an isolated, disposable target made solely from the plan's authenticated
    before-bytes.  The synthetic recipe-map source contains only the alias and
    registry binding already carried by the request; no live project state is
    observed.
    """

    request = cast(dict[str, Any], plan["request"])
    suite = _construction_suite_root()
    _material_census, registration_catalog, registration_render, _transaction = (
        _authorities()
    )
    try:
        catalog = registration_catalog.load_registration_catalog(
            suite / CATALOG_PATH
        )
        with tempfile.TemporaryDirectory(
            prefix="workbench-material-plan-validation-"
        ) as temporary:
            target = Path(temporary)
            for raw_path, content in before_by_path.items():
                relative = _safe_relative(raw_path, "feature operation path")
                destination = target.joinpath(*relative.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)

            recipe_maps = target / "groovy/prePostInit/Recipemaps.groovy"
            recipe_maps.parent.mkdir(parents=True, exist_ok=True)
            recipe_maps.write_text(
                "static final def "
                f"{request['recipe_map']} = "
                f"recipemap('{request['recipe_map_registry_name']}')\n",
                encoding="utf-8",
            )
            # The requested material ID was allocated from the live Atlas
            # census when the plan was built.  Marking all earlier IDs occupied
            # makes the owner renderer reproduce that already-bound effective
            # request without pretending the four embedded owner files are a
            # complete material census.
            rendered = registration_render.render_material_fluid_recipe(
                catalog,
                suite / PROFILE_ROOT,
                target,
                material_answers={
                    "name": request["name"],
                    "color": request["color"],
                    "translation": request["translation"],
                    "symbol": request["symbol"],
                },
                recipe_answers={
                    "script": request["recipe_script"],
                    "recipe_map": request["recipe_map"],
                    "item_inputs": [],
                    "fluid_inputs": [
                        {
                            "name": request["input_fluid"],
                            "amount": request["input_amount"],
                        }
                    ],
                    "item_outputs": [],
                    "output_amount": request["output_amount"],
                    "duration": request["duration"],
                    "voltage_tier": request["voltage_tier"],
                },
                material_census={
                    "census_id": "offline-plan-reconstruction",
                    "collisions": [],
                    "observations": [],
                    "occupied_values": list(
                        range(20_000, request["material_id"])
                    ),
                    "uncertainties": [],
                },
            )
    except (OSError, ValueError) as exc:
        raise DeveloperFeatureError(
            f"feature plan does not reproduce its Blueprints construction: {exc}"
        ) from exc

    expected_effective = {
        "material": {
            "color": request["color"],
            "material_id": request["material_id"],
            "name": request["name"],
            "registry_name": request["registry_name"],
            "symbol_name": request["symbol"],
            "translation": request["translation"],
        },
        "recipe": {
            "duration": request["duration"],
            "fluid_inputs": [
                {
                    "amount": request["input_amount"],
                    "name": request["input_fluid"],
                }
            ],
            "fluid_outputs": [
                {
                    "amount": request["output_amount"],
                    "name": request["registry_name"],
                }
            ],
            "item_inputs": [],
            "item_outputs": [],
            "recipe_map": request["recipe_map"],
            "script": request["recipe_script"],
            "voltage_tier": request["voltage_tier"],
        },
    }
    raw_operations = rendered.get("operations")
    if (
        rendered.get("format")
        != "workbench-blueprints-material-fluid-recipe-render-v1"
        or rendered.get("schema_version") != 1
        or rendered.get("state") != "experimental"
        or _canonical_bytes(rendered.get("effective_request"))
        != _canonical_bytes(expected_effective)
        or not isinstance(raw_operations, list)
        or [row.get("path") for row in raw_operations if isinstance(row, dict)]
        != [row["path"] for row in plan["operations"]]
        or rendered.get("evidence", {}).get("recipe", {}).get(
            "recipe_map_registry_name"
        )
        != request["recipe_map_registry_name"]
    ):
        _fail("feature plan differs from its Blueprints construction")

    expected_by_path = {
        row.get("path"): row for row in raw_operations if isinstance(row, dict)
    }
    if set(expected_by_path) != set(before_by_path) or set(after_by_path) != set(
        before_by_path
    ):
        _fail("feature plan differs from its Blueprints construction")
    for path, before in before_by_path.items():
        expected = expected_by_path[path]
        if (
            expected.get("operation") != "update"
            or expected.get("before_sha256") != sha256(before).hexdigest()
            or expected.get("content_sha256")
            != sha256(after_by_path[path]).hexdigest()
            or expected.get("content") != after_by_path[path]
        ):
            _fail("feature plan differs from its Blueprints construction")


def validate_material_fluid_recipe_plan(value: Mapping[str, Any]) -> dict[str, Any]:
    if type(value) is not dict:
        _fail("feature plan must be one ordinary object")
    plan = dict(value)
    expected = {
        "action",
        "authority_boundary",
        "dependencies",
        "format",
        "id",
        "kind",
        "operations",
        "request",
        "review",
        "schema_version",
        "state",
        "workspace_uri",
    }
    body = dict(plan)
    supplied = body.pop("id", None)
    if (
        set(plan) != expected
        or plan.get("format") != PLAN_FORMAT
        or plan.get("kind") != PLAN_KIND
        or type(plan.get("schema_version")) is not int
        or plan.get("schema_version") != 1
        or plan.get("action") != "apply-experimental-reversible-edit"
        or _canonical_bytes(plan.get("authority_boundary"))
        != _canonical_bytes(AUTHORITY_BOUNDARY)
        or plan.get("state") != "experimental-ready"
        or supplied != _content_id(PLAN_KIND, body)
    ):
        _fail("feature plan identity or authority boundary changed")
    _workspace_path_from_uri(plan.get("workspace_uri"))

    dependencies = plan.get("dependencies")
    expected_dependencies = [
        "groovy/preInit/MaterialChanges.groovy",
        "groovy/prePostInit/Recipemaps.groovy",
    ]
    if (
        type(dependencies) is not list
        or len(dependencies) != len(expected_dependencies)
        or [row.get("path") for row in dependencies if type(row) is dict]
        != expected_dependencies
    ):
        _fail("feature plan dependency bindings changed")
    for row in dependencies:
        if (
            type(row) is not dict
            or set(row) != {"path", "sha256", "size"}
            or type(row.get("sha256")) is not str
            or _DIGEST.fullmatch(row["sha256"]) is None
            or type(row.get("size")) is not int
            or not 1 <= row["size"] <= MAXIMUM_OPERATION_BYTES
        ):
            _fail("feature plan dependency binding changed")

    operations = plan.get("operations")
    if type(operations) is not list or len(operations) != 4:
        _fail("feature plan must contain four existing-file updates")
    roles = {
        "localization",
        "machine-recipe",
        "material-declaration",
        "material-fluid-registration",
    }
    request = plan.get("request")
    expected_request = {
        "color",
        "duration",
        "input_amount",
        "input_fluid",
        "material_id",
        "name",
        "output_amount",
        "recipe_map",
        "recipe_map_registry_name",
        "recipe_script",
        "registry_name",
        "symbol",
        "translation",
        "voltage_tier",
    }
    if type(request) is not dict or set(request) != expected_request:
        _fail("feature plan request fields changed")
    recipe_path = _safe_relative(request["recipe_script"], "recipe script")
    if (
        len(recipe_path.parts) < 3
        or recipe_path.parts[:2] != ("groovy", "postInit")
        or recipe_path.suffix != ".groovy"
    ):
        _fail("feature recipe must update one existing groovy/postInit script")
    expected_roles = {**_STATIC_ROLES, recipe_path.as_posix(): "machine-recipe"}
    if (
        any(
            type(row) is not dict or type(row.get("ordinal")) is not int
            for row in operations
        )
        or [row.get("ordinal") for row in operations if type(row) is dict]
        != list(range(4))
        or {row.get("role") for row in operations if type(row) is dict} != roles
        or len({row.get("path") for row in operations if type(row) is dict}) != 4
        or {
            row.get("path"): row.get("role")
            for row in operations
            if type(row) is dict
        }
        != expected_roles
    ):
        _fail("feature plan operation order, roles, or paths changed")
    before_by_path: dict[str, bytes] = {}
    after_by_path: dict[str, bytes] = {}
    for row in operations:
        if type(row) is not dict or set(row) != {
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
        }:
            _fail("feature plan operation fields changed")
        _safe_relative(row["path"], "feature operation path")
        before = _decoded(row, "before")
        after = _decoded(row, "after")
        before_by_path[row["path"]] = before
        after_by_path[row["path"]] = after
        expected_diff = _unified_diff(before, after, row["path"])
        if (
            row.get("operation") != "update"
            or row.get("outcome") != "approved-update"
            or not expected_diff
            or row.get("diff") != expected_diff
            or len(expected_diff.encode("utf-8")) > MAXIMUM_DIFF_BYTES
        ):
            _fail("feature plan operation diff changed")
    expected_review = _review(cast(list[dict[str, Any]], operations))
    review = plan.get("review")
    if (
        type(review) is not dict
        or set(review)
        != {"additions", "changed_files", "deletions", "unified_diff"}
        or type(review.get("additions")) is not int
        or type(review.get("deletions")) is not int
        or _canonical_bytes(review) != _canonical_bytes(expected_review)
    ):
        _fail("feature plan review differs from its operations")
    if (
        request.get("registry_name") != _slug(request.get("name"))
        or request.get("color") != _normalize_color(request.get("color"))
        or type(request.get("symbol")) is not str
        or _SYMBOL.fullmatch(request["symbol"]) is None
        or type(request.get("translation")) is not str
        or not request["translation"]
        or any(character in request["translation"] for character in "\r\n=")
        or type(request.get("material_id")) is not int
        or not 20_000 <= request["material_id"] <= 20_999
        or type(request.get("input_fluid")) is not str
        or _FLUID_ID.fullmatch(request["input_fluid"]) is None
        or request.get("input_fluid") == request.get("registry_name")
        or type(request.get("recipe_map")) is not str
        or _RECIPE_ALIAS.fullmatch(request["recipe_map"]) is None
        or type(request.get("recipe_map_registry_name")) is not str
        or _FLUID_ID.fullmatch(request["recipe_map_registry_name"]) is None
        or request.get("voltage_tier") not in _VOLTAGE_TIERS
        or any(
            type(request.get(field)) is not int
            or not 1 <= request[field] <= 2_147_483_647
            for field in ("duration", "input_amount", "output_amount")
        )
    ):
        _fail("feature plan request identity changed")
    _validate_material_fluid_recipe_blueprint_semantics(
        plan,
        before_by_path,
        after_by_path,
    )
    return cast(dict[str, Any], plan)


def verify_material_fluid_recipe_plan(
    suite_root: Path | str,
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    """Regenerate a reviewed plan from current bytes and report staleness."""

    value = validate_material_fluid_recipe_plan(plan)
    request = value["request"]
    try:
        workspace = _workspace_from_uri(value["workspace_uri"])
        fresh = build_material_fluid_recipe_plan(
            suite_root,
            workspace,
            name=request["name"],
            color=request["color"],
            recipe_script=request["recipe_script"],
            recipe_map=request["recipe_map"],
            input_fluid=request["input_fluid"],
            input_amount=request["input_amount"],
            output_amount=request["output_amount"],
            duration=request["duration"],
            voltage_tier=request["voltage_tier"],
            translation=request["translation"],
            symbol=request["symbol"],
        )
    except (OSError, ValueError) as exc:
        return {
            "format": "workbench-developer-feature-verification-v1",
            "schema_version": 1,
            "plan_id": value["id"],
            "state": "stale",
            "reason": str(exc),
        }
    return {
        "format": "workbench-developer-feature-verification-v1",
        "schema_version": 1,
        "plan_id": value["id"],
        "state": "ready" if fresh["id"] == value["id"] else "stale",
        "reason": None if fresh["id"] == value["id"] else "plan regenerated differently",
    }


def material_fluid_recipe_workspace(plan: Mapping[str, Any]) -> Path:
    """Resolve the ordinary local workspace bound by one validated plan."""

    reviewed = validate_material_fluid_recipe_plan(plan)
    return _workspace_from_uri(reviewed["workspace_uri"])


def apply_material_fluid_recipe_plan(
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
    """Apply one reviewed experimental plan through Blueprints transaction safety."""

    value = validate_material_fluid_recipe_plan(plan)
    if consent_plan_id != value["id"]:
        _fail("apply requires explicit consent to the exact reviewed plan ID")
    verification = verify_material_fluid_recipe_plan(suite_root, value)
    if verification["state"] != "ready":
        _fail(f"feature plan is stale: {verification['reason']}")
    workspace = _workspace_from_uri(value["workspace_uri"])
    _material_census, _registration_catalog, _registration_render, transaction = (
        _authorities()
    )

    def current_plan_matches(
        root: Path,
        candidate: Mapping[str, Any],
    ) -> bool:
        if root != workspace or candidate.get("id") != value["id"]:
            return False
        try:
            return (
                verify_material_fluid_recipe_plan(suite_root, candidate)["state"]
                == "ready"
            )
        except (OSError, ValueError):
            return False

    def commit_validated_receipt(candidate: Mapping[str, Any]) -> None:
        if commit_receipt is not None:
            commit_receipt(
                validate_material_fluid_recipe_receipt(candidate, value)
            )

    try:
        receipt = transaction.apply_application_transaction(
            workspace,
            value,
            state_root,
            receipt_format=RECEIPT_FORMAT,
            receipt_kind=RECEIPT_KIND,
            receipt_content_kind=RECEIPT_KIND,
            success_mutation_state="applied-experimental-local-edit",
            fail_after_ordinal=fail_after_ordinal,
            after_preflight=after_preflight,
            source_preflight=current_plan_matches,
            transaction_lock=transaction_lock,
            commit_receipt=(
                commit_validated_receipt if commit_receipt is not None else None
            ),
        )
    except ValueError as exc:
        raise DeveloperFeatureError(f"feature transaction failed: {exc}") from exc
    return validate_material_fluid_recipe_receipt(receipt, value)


def validate_material_fluid_recipe_receipt(
    value: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    reviewed = validate_material_fluid_recipe_plan(plan)
    if type(value) is not dict:
        _fail("feature application receipt must be one ordinary object")
    receipt = dict(value)
    body = dict(receipt)
    supplied = body.pop("id", None)
    if (
        receipt.get("format") != RECEIPT_FORMAT
        or receipt.get("kind") != RECEIPT_KIND
        or type(receipt.get("schema_version")) is not int
        or receipt.get("schema_version") != 1
        or receipt.get("plan_id") != reviewed["id"]
        or _canonical_bytes(receipt.get("authority_boundary"))
        != _canonical_bytes(AUTHORITY_BOUNDARY)
        or receipt.get("state") not in {"applied", "rejected"}
        or supplied != _content_id(RECEIPT_KIND, body)
    ):
        _fail("feature application receipt identity changed")
    _material_census, _registration_catalog, _registration_render, transaction = (
        _authorities()
    )
    if receipt["state"] == "applied":
        try:
            transaction.validate_applied_application_receipt(
                receipt,
                reviewed,
                receipt_format=RECEIPT_FORMAT,
                receipt_kind=RECEIPT_KIND,
                receipt_content_kind=RECEIPT_KIND,
                success_mutation_state="applied-experimental-local-edit",
            )
        except ValueError as exc:
            raise DeveloperFeatureError("feature success receipt is invalid") from exc
    else:
        diagnostic = receipt.get("diagnostic_code")
        simple = {
            "BLUEPRINTS_M2_STALE_PLAN",
            "BLUEPRINTS_M2_TRANSACTION_LOCKED",
        }
        partial = {
            "BLUEPRINTS_M2_PARTIAL_FAILURE_ROLLED_BACK",
            "BLUEPRINTS_M2_ROLLBACK_REQUIRES_REVIEW",
        }
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
        if diagnostic in simple:
            if (
                set(receipt) != base_fields
                or receipt.get("mutation_state") != "not-started"
                or receipt.get("rollback") != "not-needed"
            ):
                _fail("feature preflight rejection fields changed")
        elif diagnostic in partial:
            expected_history = [
                {
                    "sha256": operation[f"{prefix}_sha256"],
                    "size": operation[f"{prefix}_size"],
                }
                for operation in reviewed["operations"]
                for prefix in ("before", "after")
            ]
            expected_history.sort(key=lambda row: (row["sha256"], row["size"]))
            succeeded = (
                diagnostic == "BLUEPRINTS_M2_PARTIAL_FAILURE_ROLLED_BACK"
            )
            if (
                set(receipt) != base_fields | {"failure_kind", "history_objects"}
                or type(receipt.get("failure_kind")) is not str
                or not receipt["failure_kind"]
                or receipt.get("history_objects") != expected_history
                or receipt.get("mutation_state")
                != ("restored" if succeeded else "indeterminate")
                or receipt.get("rollback")
                != ("succeeded" if succeeded else "blocked-by-later-edit")
            ):
                _fail("feature failure-recovery fields changed")
        else:
            _fail("feature rejection has an unknown diagnostic")
    return cast(dict[str, Any], receipt)


def rollback_material_fluid_recipe(
    plan: Mapping[str, Any],
    state_root: Path | str,
    *,
    application_receipt: Mapping[str, Any],
    transaction_lock: Path | str | None = None,
) -> dict[str, Any]:
    """Restore reviewed before-bytes without rechecking changed authorities."""

    reviewed = validate_material_fluid_recipe_plan(plan)
    applied = validate_material_fluid_recipe_receipt(application_receipt, reviewed)
    if applied["state"] != "applied":
        _fail("rollback requires a successful feature application receipt")
    workspace = _workspace_from_uri(reviewed["workspace_uri"])
    _material_census, _registration_catalog, _registration_render, transaction = (
        _authorities()
    )
    try:
        receipt = transaction.rollback_application_transaction(
            workspace,
            reviewed,
            state_root,
            applied=applied,
            rollback_format=ROLLBACK_FORMAT,
            rollback_kind=ROLLBACK_KIND,
            rollback_content_kind=ROLLBACK_KIND,
            transaction_lock=transaction_lock,
        )
    except ValueError as exc:
        raise DeveloperFeatureError(f"feature rollback failed: {exc}") from exc
    return validate_material_fluid_recipe_rollback(receipt, reviewed)


def validate_material_fluid_recipe_rollback(
    value: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    reviewed = validate_material_fluid_recipe_plan(plan)
    if type(value) is not dict:
        _fail("feature rollback receipt must be one ordinary object")
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
        or type(receipt.get("schema_version")) is not int
        or receipt.get("schema_version") != 1
        or receipt.get("plan_id") != reviewed["id"]
        or receipt.get("state") not in {"restored", "rejected"}
        or supplied != _content_id(ROLLBACK_KIND, body)
    ):
        _fail("feature rollback receipt identity changed")
    if receipt["state"] == "restored":
        if receipt.get("diagnostic_code") is not None or receipt.get(
            "workspace_mutated"
        ) is not True:
            _fail("feature rollback success fields changed")
    elif (
        receipt.get("diagnostic_code")
        not in {
            "BLUEPRINTS_M2_LATER_EDIT_PRESERVED",
            "BLUEPRINTS_M2_TRANSACTION_LOCKED",
        }
        or receipt.get("workspace_mutated") is not False
    ):
        _fail("feature rollback rejection fields changed")
    return cast(dict[str, Any], receipt)


def recover_material_fluid_recipe(
    plan: Mapping[str, Any],
    state_root: Path | str,
    *,
    transaction_lock: Path | str | None = None,
    commit_receipt: Callable[[Mapping[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Recover an interrupted local edit from its durable attempt journal."""

    reviewed = validate_material_fluid_recipe_plan(plan)
    workspace = _workspace_from_uri(reviewed["workspace_uri"])
    _material_census, _registration_catalog, _registration_render, transaction = (
        _authorities()
    )

    def commit_validated_receipt(candidate: Mapping[str, Any]) -> None:
        if commit_receipt is not None:
            commit_receipt(
                validate_material_fluid_recipe_receipt(candidate, reviewed)
            )

    try:
        result = transaction.recover_application_transaction(
            workspace,
            reviewed,
            state_root,
            receipt_format=RECEIPT_FORMAT,
            receipt_kind=RECEIPT_KIND,
            receipt_content_kind=RECEIPT_KIND,
            success_mutation_state="applied-experimental-local-edit",
            transaction_lock=transaction_lock,
            commit_receipt=(
                commit_validated_receipt if commit_receipt is not None else None
            ),
        )
    except ValueError as exc:
        raise DeveloperFeatureError(f"feature recovery failed: {exc}") from exc
    application_receipt = result.get("application_receipt")
    if application_receipt is not None:
        application_receipt = validate_material_fluid_recipe_receipt(
            application_receipt,
            reviewed,
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
    return validate_material_fluid_recipe_recovery(
        _seal(RECOVERY_KIND, body),
        reviewed,
    )


def validate_material_fluid_recipe_recovery(
    value: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    reviewed = validate_material_fluid_recipe_plan(plan)
    if type(value) is not dict:
        _fail("feature recovery receipt must be one ordinary object")
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
        or type(receipt.get("schema_version")) is not int
        or receipt.get("schema_version") != 1
        or receipt.get("plan_id") != reviewed["id"]
        or receipt.get("state")
        not in {"applied", "restored", "review-required"}
        or supplied != _content_id(RECOVERY_KIND, body)
    ):
        _fail("feature recovery receipt identity changed")
    attempted = receipt.get("attempted_ordinals")
    if (
        type(attempted) is not list
        or any(type(row) is not int for row in attempted)
        or attempted != list(range(len(attempted)))
        or len(attempted) > len(reviewed["operations"])
        or type(receipt.get("workspace_mutated")) is not bool
    ):
        _fail("feature recovery attempt ownership changed")
    if receipt["state"] == "applied":
        if (
            attempted != list(range(len(reviewed["operations"])))
            or receipt.get("diagnostic_code") is not None
            or receipt.get("workspace_mutated") is not False
            or type(receipt.get("application_receipt")) is not dict
        ):
            _fail("feature recovery finalization fields changed")
        application_receipt = validate_material_fluid_recipe_receipt(
            receipt["application_receipt"],
            reviewed,
        )
        if application_receipt["state"] != "applied":
            _fail(
                "feature recovery applied state requires a successful "
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
            _fail("feature recovery restoration fields changed")
    elif (
        receipt.get("diagnostic_code")
        != "BLUEPRINTS_M2_RECOVERY_REQUIRES_REVIEW"
        or receipt.get("workspace_mutated") is not False
        or receipt.get("application_receipt") is not None
    ):
        _fail("feature recovery review fields changed")
    return cast(dict[str, Any], receipt)


def _state_root(
    value: Path | str,
    *,
    create: bool,
) -> Path:
    root = Path(os.path.abspath(os.fspath(Path(value).expanduser())))
    if root.is_symlink():
        _fail("feature state root cannot be a symbolic link")
    if create:
        root.mkdir(parents=True, exist_ok=True)
    try:
        state = root.lstat()
    except OSError as exc:
        raise DeveloperFeatureError("feature state root is unavailable") from exc
    if stat.S_ISLNK(state.st_mode) or not stat.S_ISDIR(state.st_mode):
        _fail("feature state root is not an ordinary directory")
    return root


def _state_directory(parent: Path, name: str, *, create: bool) -> Path:
    path = parent / name
    try:
        state = path.lstat()
    except FileNotFoundError:
        if not create:
            _fail("retained feature directory is unavailable")
        try:
            path.mkdir(mode=0o700)
            state = path.lstat()
        except OSError as exc:
            raise DeveloperFeatureError(
                "cannot create retained feature directory"
            ) from exc
    except OSError as exc:
        raise DeveloperFeatureError(
            "cannot inspect retained feature directory"
        ) from exc
    if stat.S_ISLNK(state.st_mode) or not stat.S_ISDIR(state.st_mode):
        _fail("retained feature path traverses a symbolic link")
    return path


def _record_path(
    root: Path,
    collection: str,
    identity: str,
    *,
    create: bool,
) -> Path:
    if collection not in _RECORD_COLLECTIONS:
        _fail("unknown feature record collection")
    if _CONTENT_ID.fullmatch(identity) is None:
        _fail("retained feature identity is invalid")
    collection_root = _state_directory(root, collection, create=create)
    identity_root = _state_directory(
        collection_root,
        identity.rsplit(":", 1)[1],
        create=create,
    )
    return identity_root / "record.json"


def _atomic_record(path: Path, value: Mapping[str, Any]) -> Path:
    raw = json.dumps(
        dict(value), ensure_ascii=False, indent=2, sort_keys=True
    ).encode("utf-8") + b"\n"
    if len(raw) > MAXIMUM_RECORD_BYTES:
        _fail("retained feature record exceeds its size bound")
    if path.is_symlink():
        _fail("retained feature path traverses a symbolic link")
    if path.exists():
        if not path.is_file() or path.read_bytes() != raw:
            _fail("retained feature identity collides with different bytes")
        return path
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        try:
            os.link(temporary_name, path)
        except FileExistsError:
            if not path.is_file() or path.read_bytes() != raw:
                _fail("retained feature record changed concurrently")
    finally:
        Path(temporary_name).unlink(missing_ok=True)
    return path


def retain_feature_record(
    state_root: Path | str,
    collection: str,
    value: Mapping[str, Any],
) -> Path:
    root = _state_root(state_root, create=True)
    if collection not in _RECORD_KINDS:
        _fail("unknown feature record collection")
    body = dict(value)
    identity = body.pop("id", None)
    kind = body.get("kind")
    if (
        type(identity) is not str
        or kind not in _RECORD_KINDS[collection]
        or identity != _content_id(kind, body)
    ):
        _fail("feature record identity does not match its bytes")
    return _atomic_record(
        _record_path(root, collection, identity, create=True),
        value,
    )


def _read_record(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0),
        )
    except OSError as exc:
        raise DeveloperFeatureError(f"cannot read {label}: {exc}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAXIMUM_RECORD_BYTES:
            _fail(f"{label} is not a bounded regular file")
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, MAXIMUM_RECORD_BYTES + 1))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > MAXIMUM_RECORD_BYTES:
                _fail(f"{label} exceeds its byte bound")
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
        if (
            (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            or len(raw) != before.st_size
        ):
            _fail(f"{label} changed while being read")
    finally:
        os.close(descriptor)
    try:
        value = json.loads(raw.decode("utf-8", errors="strict"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise DeveloperFeatureError(f"cannot decode {label}: {exc}") from exc
    if type(value) is not dict:
        _fail(f"{label} is malformed")
    return cast(dict[str, Any], value), raw


def _load_record(path: Path, label: str) -> dict[str, Any]:
    return _read_record(path, label)[0]


def resolve_feature_record(
    state_root: Path | str,
    collection: str,
    reference: Path | str,
) -> dict[str, Any]:
    text = os.fspath(reference)
    if _CONTENT_ID.fullmatch(text):
        root = _state_root(state_root, create=False)
        path = _record_path(root, collection, text, create=False)
    else:
        if collection not in _RECORD_COLLECTIONS:
            _fail("unknown feature record collection")
        path = Path(reference).expanduser().resolve()
    return _load_record(path, f"feature {collection[:-1]} record")


def reference_feature_record(state_root: Path, collection: str, identity: str) -> dict[str, Any]:
    """Return a byte-verified reference for navigation without copying custody."""
    root = _state_root(state_root, create=False)
    path = _record_path(root, collection, identity, create=False)
    value, raw = _read_record(path, "retained feature reference")
    if value.get("id") != identity:
        _fail("retained feature reference identity differs")
    return {"owner_id": "blueprints", "record_id": identity, "record_kind": value["kind"],
            "uri": path.as_uri(), "digest": "sha256:" + sha256(raw).hexdigest(),
            "last_verified_state": None, "verified_at": None}


def transaction_state_root(
    state_root: Path | str,
    plan_id: str,
    *,
    create: bool = True,
) -> Path:
    if _CONTENT_ID.fullmatch(plan_id) is None:
        _fail("transaction plan identity is invalid")
    root = _state_root(state_root, create=create)
    transactions = _state_directory(root, "transactions", create=create)
    return _state_directory(
        transactions,
        plan_id.rsplit(":", 1)[1],
        create=create,
    )


def workspace_transaction_lock_path(
    plan: Mapping[str, Any],
    *,
    lock_root: Path | str | None = None,
) -> Path:
    """Return the stable per-user lock shared by every plan for one workspace."""

    reviewed = validate_material_fluid_recipe_plan(plan)
    return workspace_transaction_lock_path_for_uri(
        reviewed["workspace_uri"], lock_root=lock_root
    )


def workspace_transaction_lock_path_for_uri(
    workspace_uri: str,
    *,
    lock_root: Path | str | None = None,
) -> Path:
    """Return the stable per-user transaction lock for one workspace URI."""

    workspace = _workspace_from_uri(workspace_uri)
    root = _state_root(
        default_feature_state_root() if lock_root is None else lock_root,
        create=True,
    )
    locks = _state_directory(root, "workspace-locks", create=True)
    owner = _state_directory(
        locks,
        sha256(workspace.as_uri().encode("utf-8")).hexdigest(),
        create=True,
    )
    return owner / "active-transaction.lock"


__all__ = [
    "AUTHORITY_BOUNDARY",
    "DeveloperFeatureError",
    "RUN_FORMAT",
    "RUN_KIND",
    "apply_material_fluid_recipe_plan",
    "build_material_fluid_recipe_plan",
    "default_feature_state_root",
    "material_fluid_recipe_options",
    "material_fluid_recipe_workspace",
    "recover_material_fluid_recipe",
    "resolve_feature_record",
    "retain_feature_record",
    "rollback_material_fluid_recipe",
    "transaction_state_root",
    "validate_material_fluid_recipe_plan",
    "validate_material_fluid_recipe_receipt",
    "validate_material_fluid_recipe_recovery",
    "validate_material_fluid_recipe_rollback",
    "verify_material_fluid_recipe_plan",
    "workspace_transaction_lock_path",
    "workspace_transaction_lock_path_for_uri",
]
