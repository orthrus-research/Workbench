"""Observe one planned Supersymmetry material, fluid, and machine recipe.

The generated Groovy source is installed only in a disposable runtime
projection.  It reads postInit registry state and emits one bounded marker; it
does not create materials, fluids, recipes, or localization entries.
"""

from __future__ import annotations

PROFILE_API_VERSION = 1

from dataclasses import dataclass
import base64
from hashlib import sha256
import json
import re
from typing import Any, Mapping, NoReturn


MARKER_PREFIX = "[WORKBENCH-MATERIAL-FLUID-RECIPE-V1]"
PACK_PROFILE_ID = "workbench-pack:supersymmetry"
PLATFORM_PROFILE_ID = "workbench-platform:cleanroom:provisional"
MAX_LOG_BYTES = 16 * 1024 * 1024
_REGISTRY_NAME = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
_FLUID_ID = re.compile(r"^[a-z0-9][a-z0-9_.:/-]{0,255}$")
_RECIPE_ALIAS = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_SYMBOL = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_PLAN_ID = re.compile(
    r"^(?:sha256:|[a-z][a-z0-9-]*:sha256:)[0-9a-f]{64}$"
)
_BASE64URL = re.compile(r"^[A-Za-z0-9_-]+$")
_VOLTAGE_EUT = {
    "ULV": 7,
    "LV": 30,
    "MV": 120,
    "HV": 480,
    "EV": 1920,
    "IV": 7680,
    "LuV": 30720,
    "ZPM": 122880,
    "UV": 491520,
    "UHV": 1966080,
    "UEV": 7864320,
    "UIV": 31457280,
}
_SOURCE_NAME = "MaterialFluidRecipeAssertion.groovy"
# GroovyScript executes postInit owners in lexical path order.  Keep the
# observation after the pack's recipe owners so it inspects the finished
# registry instead of a partially populated postInit phase.
_TARGET = (
    ".minecraft/groovy/postInit/utils/"
    "ZzzzWorkbenchMaterialFluidRecipeAssertion.groovy"
)
_ASSESSMENT_AUTHORITY = {
    "owner": "Atlas",
    "claim": (
        "interpretation of one combined Supersymmetry material-fluid-recipe "
        "runtime marker"
    ),
    "construction_authority": "none",
}
_ASSESSMENT_LIMITATIONS = (
    "The observation applies only to the exact disposable projection and cold-start capture.",
    "It does not authorize a source edit, stable pattern, release, or installed-instance mutation.",
)


class MaterialFluidRecipeObservationError(ValueError):
    """The recipe probe specification or retained observation is invalid."""


def _fail(message: str) -> NoReturn:
    raise MaterialFluidRecipeObservationError(message)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _canonical_copy(value: Any) -> Any:
    return json.loads(_canonical_bytes(value).decode("utf-8"))


@dataclass(frozen=True)
class MaterialFluidRecipeProbeSpec:
    """Exact reviewed plan fields required by the combined runtime probe."""

    registry_name: str
    symbol_name: str
    material_id: int
    color_rgb: int
    translation: str
    recipe_map_alias: str
    recipe_map_registry_name: str
    input_fluid: str
    input_amount: int
    output_amount: int
    duration: int
    voltage_tier: str
    source_plan_id: str

    def __post_init__(self) -> None:
        if _REGISTRY_NAME.fullmatch(self.registry_name) is None:
            _fail("probe registry_name is not a safe Supersymmetry name")
        if _SYMBOL.fullmatch(self.symbol_name) is None:
            _fail("probe symbol_name is not a safe Groovy symbol")
        if type(self.material_id) is not int or not (0 <= self.material_id <= 32767):
            _fail("probe material_id is outside the supported registry range")
        if type(self.color_rgb) is not int or not (0 <= self.color_rgb <= 0xFFFFFF):
            _fail("probe color_rgb is not a 24-bit color")
        if not isinstance(self.translation, str) or not self.translation.strip():
            _fail("probe translation is missing")
        if len(self.translation.encode("utf-8")) > 1024:
            _fail("probe translation exceeds the byte limit")
        if _RECIPE_ALIAS.fullmatch(self.recipe_map_alias) is None:
            _fail("probe recipe_map_alias is not a safe Supersymmetry alias")
        if _REGISTRY_NAME.fullmatch(self.recipe_map_registry_name) is None:
            _fail("probe recipe_map_registry_name is not a safe recipe-map name")
        if _FLUID_ID.fullmatch(self.input_fluid) is None:
            _fail("probe input_fluid is not a safe fluid identity")
        if self.input_fluid == self.registry_name:
            _fail("probe input fluid cannot be its material output fluid")
        for value, label in (
            (self.input_amount, "input_amount"),
            (self.output_amount, "output_amount"),
            (self.duration, "duration"),
        ):
            if type(value) is not int or not (1 <= value <= 2_147_483_647):
                _fail(f"probe {label} must be a positive 32-bit integer")
        if self.voltage_tier not in _VOLTAGE_EUT:
            _fail("probe voltage_tier is not supported by the profile recipe pattern")
        if (
            not isinstance(self.source_plan_id, str)
            or _SOURCE_PLAN_ID.fullmatch(self.source_plan_id) is None
        ):
            _fail("probe source_plan_id must be an exact content identity")

    @property
    def material_resource(self) -> str:
        return f"susy:{self.registry_name}"

    @property
    def expected_eut(self) -> int:
        return _VOLTAGE_EUT[self.voltage_tier]

    def identity_material(self) -> dict[str, Any]:
        return {
            "color_rgb": self.color_rgb,
            "duration": self.duration,
            "expected_eut": self.expected_eut,
            "input_amount": self.input_amount,
            "input_fluid": self.input_fluid,
            "material_id": self.material_id,
            "material_resource": self.material_resource,
            "output_amount": self.output_amount,
            "output_fluid": self.registry_name,
            "recipe_map_alias": self.recipe_map_alias,
            "recipe_map_registry_name": self.recipe_map_registry_name,
            "registry_name": self.registry_name,
            "source_plan_id": self.source_plan_id,
            "symbol_name": self.symbol_name,
            "translation": self.translation,
            "voltage_tier": self.voltage_tier,
        }

    @property
    def probe_id(self) -> str:
        return "workbench-material-fluid-recipe-probe:sha256:" + sha256(
            _canonical_bytes(self.identity_material())
        ).hexdigest()


def _groovy_string(value: str) -> str:
    # JSON strings are valid non-interpolating Groovy string literals.
    return json.dumps(value, ensure_ascii=False)


def build_material_fluid_recipe_probe(spec: MaterialFluidRecipeProbeSpec) -> bytes:
    """Render one observation-only postInit probe for the reviewed plan."""

    script = f'''// Generated by Workbench; disposable observation only.
import gregtech.api.GregTechAPI
import gregtech.api.fluids.store.FluidStorageKeys
import gregtech.api.recipes.RecipeMap
import gregtech.api.unification.material.info.MaterialFlags
import gregtech.api.unification.material.properties.PropertyKey
import net.minecraftforge.fluids.FluidRegistry
import prePostInit.Recipemaps
import java.nio.charset.StandardCharsets
import java.util.Base64
import java.util.Collections
import java.util.TreeMap
import static gregtech.api.GTValues.*

def expected = new TreeMap()
expected.put('color_rgb', {spec.color_rgb})
expected.put('duration', {spec.duration})
expected.put('eut', VA[{spec.voltage_tier}])
expected.put('input_amount', {spec.input_amount})
expected.put('input_fluid', {_groovy_string(spec.input_fluid)})
expected.put('material_id', {spec.material_id})
expected.put('material_resource', {_groovy_string(spec.material_resource)})
expected.put('output_amount', {spec.output_amount})
expected.put('output_fluid', {_groovy_string(spec.registry_name)})
expected.put('recipe_map_alias', {_groovy_string(spec.recipe_map_alias)})
expected.put('recipe_map_registry_name', {_groovy_string(spec.recipe_map_registry_name)})
expected.put('translation', {_groovy_string(spec.translation)})

def actual = new TreeMap()
[
    'chanced_fluid_output_count', 'chanced_item_output_count', 'color_rgb',
    'duration', 'eut', 'exact_recipe_match_count', 'find_recipe_identity',
    'fluid_input_count', 'fluid_name', 'fluid_output_count',
    'forge_registry_roundtrip', 'groovy_recipe', 'groovy_target_count',
    'has_flammable_flag', 'has_fluid_property', 'input_amount',
    'input_fluid', 'item_input_count', 'item_output_count',
    'localized_name', 'manager_phase', 'material_id', 'material_resource',
    'output_amount', 'output_fluid', 'recipe_map_alias_identity',
    'recipe_map_alias_registry_name', 'recipe_map_registry_name'
].each {{ key -> actual.put(key, null) }}
def errorKind = null
try {{
    def manager = GregTechAPI.materialManager
    def material = manager == null ? null : manager.getMaterial(expected.material_resource)
    def fluid = material == null ? null : material.getFluid(FluidStorageKeys.LIQUID)
    def aliasMap = Recipemaps.{spec.recipe_map_alias}
    def recipeMap = RecipeMap.getByName(expected.recipe_map_registry_name)
    def targetRecipes = recipeMap == null ? [] : recipeMap.getRecipeList().findAll {{ recipe ->
        def outputs = recipe.getFluidOutputs()
        recipe.isGroovyRecipe() && outputs.size() == 1 &&
                outputs[0]?.fluid?.name == expected.output_fluid
    }}
    def exactRecipes = targetRecipes.findAll {{ recipe ->
        def inputs = recipe.getFluidInputs()
        def outputs = recipe.getFluidOutputs()
        recipe.getInputs().isEmpty() && recipe.getOutputs().isEmpty() &&
                recipe.getChancedOutputs().getChancedEntries().isEmpty() &&
                recipe.getChancedFluidOutputs().getChancedEntries().isEmpty() &&
                inputs.size() == 1 && outputs.size() == 1 &&
                inputs[0].getInputFluidStack()?.fluid?.name == expected.input_fluid &&
                inputs[0].getInputFluidStack()?.amount == expected.input_amount &&
                outputs[0]?.fluid?.name == expected.output_fluid &&
                outputs[0]?.amount == expected.output_amount &&
                recipe.getDuration() == expected.duration &&
                recipe.getEUt() == expected.eut
    }}
    def inspected = targetRecipes.size() == 1 ? targetRecipes[0] :
            (exactRecipes.size() == 1 ? exactRecipes[0] : null)
    def queryFluid = FluidRegistry.getFluidStack(expected.input_fluid, expected.input_amount)
    def foundRecipe = recipeMap == null || queryFluid == null ? null : recipeMap.findRecipe(
            expected.eut, Collections.emptyList(), Collections.singletonList(queryFluid), false)

    actual.put('color_rgb', material == null ? null : material.getMaterialRGB())
    actual.put('fluid_name', fluid == null ? null : fluid.getName())
    actual.put('forge_registry_roundtrip', fluid != null && FluidRegistry.getFluid(fluid.getName())?.is(fluid))
    actual.put('has_flammable_flag', material != null && material.hasFlag(MaterialFlags.FLAMMABLE))
    actual.put('has_fluid_property', material != null && material.hasProperty(PropertyKey.FLUID))
    actual.put('localized_name', material == null ? null : material.getLocalizedName())
    actual.put('manager_phase', manager == null ? null : String.valueOf(manager.getPhase()))
    actual.put('material_id', material == null ? null : material.getId())
    actual.put('material_resource', material == null ? null : String.valueOf(material.getResourceLocation()))
    actual.put('recipe_map_alias_identity', aliasMap != null && recipeMap != null && aliasMap.is(recipeMap))
    actual.put('recipe_map_alias_registry_name', aliasMap == null ? null : aliasMap.getUnlocalizedName())
    actual.put('recipe_map_registry_name', recipeMap == null ? null : recipeMap.getUnlocalizedName())
    actual.put('groovy_target_count', targetRecipes.size())
    actual.put('exact_recipe_match_count', exactRecipes.size())
    actual.put('find_recipe_identity', exactRecipes.size() == 1 && foundRecipe != null && foundRecipe.is(exactRecipes[0]))
    if (inspected != null) {{
        def inputs = inspected.getFluidInputs()
        def outputs = inspected.getFluidOutputs()
        actual.put('chanced_fluid_output_count', inspected.getChancedFluidOutputs().getChancedEntries().size())
        actual.put('chanced_item_output_count', inspected.getChancedOutputs().getChancedEntries().size())
        actual.put('duration', inspected.getDuration())
        actual.put('eut', inspected.getEUt())
        actual.put('fluid_input_count', inputs.size())
        actual.put('fluid_output_count', outputs.size())
        actual.put('groovy_recipe', inspected.isGroovyRecipe())
        actual.put('input_amount', inputs.size() == 1 ? inputs[0].getInputFluidStack()?.amount : null)
        actual.put('input_fluid', inputs.size() == 1 ? inputs[0].getInputFluidStack()?.fluid?.name : null)
        actual.put('item_input_count', inspected.getInputs().size())
        actual.put('item_output_count', inspected.getOutputs().size())
        actual.put('output_amount', outputs.size() == 1 ? outputs[0]?.amount : null)
        actual.put('output_fluid', outputs.size() == 1 ? outputs[0]?.fluid?.name : null)
    }}
}} catch (Throwable failure) {{
    errorKind = failure.getClass().getName()
}}

def checks = new TreeMap()
checks.put('color_rgb', actual.color_rgb == expected.color_rgb)
checks.put('duration', actual.duration == expected.duration)
checks.put('eut', actual.eut == expected.eut)
checks.put('find_recipe_identity', actual.find_recipe_identity == true)
checks.put('fluid_input', actual.fluid_input_count == 1 &&
        actual.input_fluid == expected.input_fluid && actual.input_amount == expected.input_amount)
checks.put('fluid_name', actual.fluid_name == expected.output_fluid)
checks.put('fluid_output', actual.fluid_output_count == 1 &&
        actual.chanced_fluid_output_count == 0 &&
        actual.output_fluid == expected.output_fluid && actual.output_amount == expected.output_amount)
checks.put('forge_registry_roundtrip', actual.forge_registry_roundtrip == true)
checks.put('groovy_origin', actual.groovy_recipe == true)
checks.put('has_flammable_flag', actual.has_flammable_flag == true)
checks.put('has_fluid_property', actual.has_fluid_property == true)
checks.put('localized_name', actual.localized_name == expected.translation)
checks.put('manager_frozen', actual.manager_phase == 'FROZEN')
checks.put('material_id', actual.material_id == expected.material_id)
checks.put('material_resource', actual.material_resource == expected.material_resource)
checks.put('no_item_io', actual.item_input_count == 0 && actual.item_output_count == 0 &&
        actual.chanced_item_output_count == 0)
checks.put('probe_execution', errorKind == null)
checks.put('recipe_map_alias_binding', actual.recipe_map_alias_identity == true &&
        actual.recipe_map_alias_registry_name == expected.recipe_map_registry_name)
checks.put('recipe_map_registry_name', actual.recipe_map_registry_name == expected.recipe_map_registry_name)
checks.put('unique_exact_groovy_recipe', actual.exact_recipe_match_count == 1)

def success = checks.values().every {{ it == true }}

// GroovyScript's sandbox does not expose groovy-json.  This encoder is
// deterministic and deliberately limited to the scalar/map marker shape.
def jsonQuote = {{ Object raw ->
    def out = new StringBuilder('"')
    String.valueOf(raw).each {{ character ->
        int code = (int) character
        if (code == 34) {{
            out.append((char) 92).append((char) 34)
        }} else if (code == 92) {{
            out.append((char) 92).append((char) 92)
        }} else if (code == 8) {{
            out.append((char) 92).append('b')
        }} else if (code == 12) {{
            out.append((char) 92).append('f')
        }} else if (code == 10) {{
            out.append((char) 92).append('n')
        }} else if (code == 13) {{
            out.append((char) 92).append('r')
        }} else if (code == 9) {{
            out.append((char) 92).append('t')
        }} else if (code < 32) {{
            out.append((char) 92).append('u').append(String.format('%04x', code))
        }} else {{
            out.append(character)
        }}
    }}
    out.append('"').toString()
}}
def jsonScalar = {{ Object value ->
    value == null ? 'null' :
            value instanceof Boolean ? (value ? 'true' : 'false') :
            value instanceof Number ? String.valueOf(value) : jsonQuote(value)
}}
def jsonMap = {{ Map value ->
    '{{' + new TreeMap(value).collect {{ key, item ->
        jsonQuote(key) + ':' + jsonScalar(item)
    }}.join(',') + '}}'
}}
def payloadJson = '{{' + [
        jsonQuote('actual') + ':' + jsonMap(actual),
        jsonQuote('checks') + ':' + jsonMap(checks),
        jsonQuote('error_kind') + ':' + jsonScalar(errorKind),
        jsonQuote('format') + ':' + jsonQuote('workbench-material-fluid-recipe-observation-v1'),
        jsonQuote('probe_id') + ':' + jsonQuote({_groovy_string(spec.probe_id)}),
        jsonQuote('source_plan_id') + ':' + jsonQuote({_groovy_string(spec.source_plan_id)}),
        jsonQuote('stage') + ':' + jsonQuote('postInit'),
        jsonQuote('state') + ':' + jsonQuote(success ? 'observed' : 'mismatch'),
].join(',') + '}}'
def encoded = Base64.getUrlEncoder().withoutPadding().encodeToString(
        payloadJson.getBytes(StandardCharsets.UTF_8))
log.infoMC({_groovy_string(MARKER_PREFIX)} + encoded)
'''
    encoded = script.encode("utf-8")
    if len(encoded) > 96 * 1024:
        _fail("generated material-fluid-recipe probe exceeds the byte limit")
    return encoded


def build_material_fluid_recipe_probe_overlay(
    spec: MaterialFluidRecipeProbeSpec,
    source_name: str = _SOURCE_NAME,
) -> dict[str, Any]:
    """Describe the probe's one fixed create-only disposable overlay."""

    if source_name != _SOURCE_NAME:
        _fail("material-fluid-recipe probe uses its fixed source name")
    script = build_material_fluid_recipe_probe(spec)
    material = {
        "probe_id": spec.probe_id,
        "source_plan_id": spec.source_plan_id,
        "source_sha256": sha256(script).hexdigest(),
        "target": _TARGET,
    }
    return {
        "format": "workbench-runtime-compatibility-file-overlay-v1",
        "schema_version": 1,
        "patch_id": "workbench-observation-probe:sha256:"
        + sha256(_canonical_bytes(material)).hexdigest(),
        "description": (
            "Disposable Supersymmetry postInit material-fluid-recipe observation; "
            "it does not alter source or construct registry state."
        ),
        "target": {"path": _TARGET, "must_be_absent": True},
        "source": {"path": source_name, "sha256": material["source_sha256"]},
    }


def _decode_marker(log_bytes: bytes) -> dict[str, Any]:
    if not isinstance(log_bytes, bytes) or len(log_bytes) > MAX_LOG_BYTES:
        _fail("Groovy log is absent or exceeds the observation byte limit")
    try:
        text = log_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MaterialFluidRecipeObservationError("Groovy log is not UTF-8") from exc
    tokens: list[str] = []
    for line in text.splitlines():
        index = line.find(MARKER_PREFIX)
        if index < 0:
            continue
        token = line[index + len(MARKER_PREFIX) :].strip()
        if _BASE64URL.fullmatch(token) is None:
            _fail("material-fluid-recipe marker payload is malformed")
        tokens.append(token)
    if len(tokens) != 1:
        _fail("Groovy log must contain exactly one material-fluid-recipe marker")
    try:
        token = tokens[0]
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        value = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MaterialFluidRecipeObservationError(
            "material-fluid-recipe marker is not canonical JSON data"
        ) from exc
    if not isinstance(value, dict) or _canonical_bytes(value) != raw:
        _fail("material-fluid-recipe marker is not canonical JSON")
    return value


_ACTUAL_NAMES = {
    "chanced_fluid_output_count",
    "chanced_item_output_count",
    "color_rgb",
    "duration",
    "eut",
    "exact_recipe_match_count",
    "find_recipe_identity",
    "fluid_input_count",
    "fluid_name",
    "fluid_output_count",
    "forge_registry_roundtrip",
    "groovy_recipe",
    "groovy_target_count",
    "has_flammable_flag",
    "has_fluid_property",
    "input_amount",
    "input_fluid",
    "item_input_count",
    "item_output_count",
    "localized_name",
    "manager_phase",
    "material_id",
    "material_resource",
    "output_amount",
    "output_fluid",
    "recipe_map_alias_identity",
    "recipe_map_alias_registry_name",
    "recipe_map_registry_name",
}


def _derived_checks(
    actual: Mapping[str, Any],
    error_kind: str | None,
    spec: MaterialFluidRecipeProbeSpec,
) -> dict[str, bool]:
    return {
        "color_rgb": actual.get("color_rgb") == spec.color_rgb,
        "duration": actual.get("duration") == spec.duration,
        "eut": actual.get("eut") == spec.expected_eut,
        "find_recipe_identity": actual.get("find_recipe_identity") is True,
        "fluid_input": (
            actual.get("fluid_input_count") == 1
            and actual.get("input_fluid") == spec.input_fluid
            and actual.get("input_amount") == spec.input_amount
        ),
        "fluid_name": actual.get("fluid_name") == spec.registry_name,
        "fluid_output": (
            actual.get("fluid_output_count") == 1
            and actual.get("chanced_fluid_output_count") == 0
            and actual.get("output_fluid") == spec.registry_name
            and actual.get("output_amount") == spec.output_amount
        ),
        "forge_registry_roundtrip": actual.get("forge_registry_roundtrip") is True,
        "groovy_origin": actual.get("groovy_recipe") is True,
        "has_flammable_flag": actual.get("has_flammable_flag") is True,
        "has_fluid_property": actual.get("has_fluid_property") is True,
        "localized_name": actual.get("localized_name") == spec.translation,
        "manager_frozen": actual.get("manager_phase") == "FROZEN",
        "material_id": actual.get("material_id") == spec.material_id,
        "material_resource": actual.get("material_resource") == spec.material_resource,
        "no_item_io": (
            actual.get("item_input_count") == 0
            and actual.get("item_output_count") == 0
            and actual.get("chanced_item_output_count") == 0
        ),
        "probe_execution": error_kind is None,
        "recipe_map_alias_binding": (
            actual.get("recipe_map_alias_identity") is True
            and actual.get("recipe_map_alias_registry_name")
            == spec.recipe_map_registry_name
        ),
        "recipe_map_registry_name": (
            actual.get("recipe_map_registry_name")
            == spec.recipe_map_registry_name
        ),
        "unique_exact_groovy_recipe": actual.get("exact_recipe_match_count") == 1,
    }


def _developer_assertions(checks: Mapping[str, bool]) -> dict[str, str]:
    groups = {
        "material_registration": {
            "color_rgb",
            "has_flammable_flag",
            "has_fluid_property",
            "manager_frozen",
            "material_id",
            "material_resource",
            "probe_execution",
        },
        "fluid_registration": {
            "fluid_name",
            "forge_registry_roundtrip",
            "has_fluid_property",
            "manager_frozen",
            "probe_execution",
        },
        "localization": {"localized_name", "probe_execution"},
        "recipe_registration": {
            "duration",
            "eut",
            "find_recipe_identity",
            "fluid_input",
            "fluid_output",
            "groovy_origin",
            "no_item_io",
            "probe_execution",
            "recipe_map_alias_binding",
            "recipe_map_registry_name",
            "unique_exact_groovy_recipe",
        },
    }
    return {
        "groovy_compilation": "observed",
        **{
            name: (
                "observed"
                if all(checks.get(check) is True for check in required)
                else "failed"
            )
            for name, required in groups.items()
        },
    }


_CAPTURE_FIELDS = {
    "groovy_log_sha256",
    "groovy_log_uri",
    "launch_id",
    "launch_receipt_sha256",
    "launch_receipt_uri",
    "materialization_id",
    "materialization_receipt_sha256",
    "materialization_receipt_size",
    "materialization_receipt_uri",
    "payload",
    "probe_id",
    "probe_overlay_id",
    "probe_script_sha256",
    "session_outcome",
    "session_receipt_sha256",
    "session_receipt_size",
    "session_receipt_uri",
}


def _validated_capture(
    spec: MaterialFluidRecipeProbeSpec,
    capture: Mapping[str, Any],
    *,
    groovy_log_bytes: bytes | None,
) -> dict[str, Any]:
    if not isinstance(capture, Mapping) or set(capture) != _CAPTURE_FIELDS:
        _fail("material-fluid-recipe capture binding has unexpected fields")
    expected_script = sha256(build_material_fluid_recipe_probe(spec)).hexdigest()
    expected_overlay = build_material_fluid_recipe_probe_overlay(spec)["patch_id"]
    payload = capture.get("payload")
    if (
        not isinstance(capture.get("launch_id"), str)
        or not capture["launch_id"].startswith("sha256:")
        or _SHA256.fullmatch(capture["launch_id"][7:]) is None
        or not isinstance(capture.get("materialization_id"), str)
        or not capture["materialization_id"].startswith("sha256:")
        or _SHA256.fullmatch(capture["materialization_id"][7:]) is None
        or any(
            _SHA256.fullmatch(str(capture.get(field))) is None
            for field in (
                "groovy_log_sha256",
                "launch_receipt_sha256",
                "materialization_receipt_sha256",
                "session_receipt_sha256",
            )
        )
        or groovy_log_bytes is not None
        and capture.get("groovy_log_sha256")
        != sha256(groovy_log_bytes).hexdigest()
        or capture.get("probe_id") != spec.probe_id
        or capture.get("probe_script_sha256") != expected_script
        or capture.get("probe_overlay_id") != expected_overlay
        or capture.get("session_outcome") not in {"completed", "analysis-incomplete"}
        or any(
            not isinstance(capture.get(field), str)
            or not capture[field].startswith("file:")
            for field in (
                "groovy_log_uri",
                "launch_receipt_uri",
                "materialization_receipt_uri",
                "session_receipt_uri",
            )
        )
        or type(capture.get("materialization_receipt_size")) is not int
        or not (0 < capture["materialization_receipt_size"] <= 4 * 1024 * 1024)
        or type(capture.get("session_receipt_size")) is not int
        or not (0 < capture["session_receipt_size"] <= 4 * 1024 * 1024)
        or not isinstance(payload, Mapping)
        or set(payload) != {"file_count", "total_bytes", "tree_sha256"}
        or type(payload.get("file_count")) is not int
        or payload["file_count"] < 1
        or type(payload.get("total_bytes")) is not int
        or payload["total_bytes"] < 1
        or not isinstance(payload.get("tree_sha256"), str)
        or not payload["tree_sha256"].startswith("sha256:")
        or _SHA256.fullmatch(payload["tree_sha256"][7:]) is None
    ):
        _fail("material-fluid-recipe capture does not bind the probe run")
    return _canonical_copy(capture)


def _probe_record(spec: MaterialFluidRecipeProbeSpec) -> dict[str, Any]:
    return {
        **spec.identity_material(),
        "probe_id": spec.probe_id,
        "script_sha256": sha256(build_material_fluid_recipe_probe(spec)).hexdigest(),
        "overlay_id": build_material_fluid_recipe_probe_overlay(spec)["patch_id"],
    }


def _assessment_id_material(
    spec: MaterialFluidRecipeProbeSpec,
    *,
    state: str,
    error_kind: str | None,
    checks: Mapping[str, bool],
    assertions: Mapping[str, str],
    failed: list[str],
    observed: Mapping[str, Any],
    capture: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "capture": dict(capture),
        "checks": dict(checks),
        "developer_assertions": dict(assertions),
        "error_kind": error_kind,
        "failed_checks": list(failed),
        "observed": dict(observed),
        "probe_id": spec.probe_id,
        "source_plan_id": spec.source_plan_id,
        "state": state,
    }


def interpret_material_fluid_recipe_observation(
    spec: MaterialFluidRecipeProbeSpec,
    groovy_log_bytes: bytes,
    capture: Mapping[str, Any],
) -> dict[str, Any]:
    """Interpret one combined marker without granting construction authority."""

    validated_capture = _validated_capture(
        spec, capture, groovy_log_bytes=groovy_log_bytes
    )
    marker = _decode_marker(groovy_log_bytes)
    if set(marker) != {
        "actual",
        "checks",
        "error_kind",
        "format",
        "probe_id",
        "source_plan_id",
        "stage",
        "state",
    }:
        _fail("material-fluid-recipe marker has unexpected fields")
    actual = marker.get("actual")
    checks = marker.get("checks")
    error_kind = marker.get("error_kind")
    if (
        marker.get("format")
        != "workbench-material-fluid-recipe-observation-v1"
        or marker.get("probe_id") != spec.probe_id
        or marker.get("source_plan_id") != spec.source_plan_id
        or marker.get("stage") != "postInit"
        or marker.get("state") not in {"observed", "mismatch"}
        or not isinstance(actual, Mapping)
        or set(actual) != _ACTUAL_NAMES
        or not isinstance(checks, Mapping)
        or error_kind is not None
        and (not isinstance(error_kind, str) or not error_kind or len(error_kind) > 1024)
    ):
        _fail("material-fluid-recipe marker does not bind the reviewed probe")
    derived = _derived_checks(actual, error_kind, spec)
    if checks != derived or any(type(value) is not bool for value in checks.values()):
        _fail("material-fluid-recipe marker checks contradict actual state")
    state = "observed" if all(derived.values()) else "mismatch"
    if marker["state"] != state:
        _fail("material-fluid-recipe marker state contradicts its checks")
    failed = sorted(name for name, passed in derived.items() if not passed)
    assertions = _developer_assertions(derived)
    material = _assessment_id_material(
        spec,
        state=state,
        error_kind=error_kind,
        checks=derived,
        assertions=assertions,
        failed=failed,
        observed=actual,
        capture=validated_capture,
    )
    return {
        "format": "workbench-supersymmetry-material-fluid-recipe-assessment-v1",
        "schema_version": 1,
        "assessment_id": "workbench-atlas-material-fluid-recipe-assessment:sha256:"
        + sha256(_canonical_bytes(material)).hexdigest(),
        "authority": dict(_ASSESSMENT_AUTHORITY),
        "profile": {
            "pack_profile_id": PACK_PROFILE_ID,
            "platform_profile_id": PLATFORM_PROFILE_ID,
        },
        "probe": _probe_record(spec),
        "state": state,
        "checks": derived,
        "developer_assertions": assertions,
        "failed_checks": failed,
        "error_kind": error_kind,
        "observed": dict(actual),
        "capture": validated_capture,
        "limitations": list(_ASSESSMENT_LIMITATIONS),
    }


def validate_material_fluid_recipe_assessment(
    spec: MaterialFluidRecipeProbeSpec,
    assessment: Mapping[str, Any],
) -> dict[str, Any]:
    """Revalidate a retained combined assessment without upgrading its claim."""

    if not isinstance(assessment, Mapping) or set(assessment) != {
        "assessment_id",
        "authority",
        "capture",
        "checks",
        "developer_assertions",
        "error_kind",
        "failed_checks",
        "format",
        "limitations",
        "observed",
        "probe",
        "profile",
        "schema_version",
        "state",
    }:
        _fail("material-fluid-recipe assessment has unexpected fields")
    if (
        assessment.get("format")
        != "workbench-supersymmetry-material-fluid-recipe-assessment-v1"
        or assessment.get("schema_version") != 1
        or assessment.get("authority") != _ASSESSMENT_AUTHORITY
        or assessment.get("limitations") != list(_ASSESSMENT_LIMITATIONS)
        or assessment.get("profile")
        != {
            "pack_profile_id": PACK_PROFILE_ID,
            "platform_profile_id": PLATFORM_PROFILE_ID,
        }
        or assessment.get("probe") != _probe_record(spec)
    ):
        _fail("material-fluid-recipe assessment contradicts its bound probe")
    capture = _validated_capture(
        spec,
        assessment.get("capture"),
        groovy_log_bytes=None,
    )
    observed = assessment.get("observed")
    checks = assessment.get("checks")
    error_kind = assessment.get("error_kind")
    if (
        not isinstance(observed, Mapping)
        or set(observed) != _ACTUAL_NAMES
        or not isinstance(checks, Mapping)
        or error_kind is not None
        and (not isinstance(error_kind, str) or not error_kind or len(error_kind) > 1024)
    ):
        _fail("material-fluid-recipe assessment observation is malformed")
    derived = _derived_checks(observed, error_kind, spec)
    if checks != derived or any(type(value) is not bool for value in checks.values()):
        _fail("material-fluid-recipe assessment checks contradict observation")
    state = "observed" if all(derived.values()) else "mismatch"
    failed = sorted(name for name, passed in derived.items() if not passed)
    assertions = _developer_assertions(derived)
    if (
        assessment.get("state") != state
        or assessment.get("failed_checks") != failed
        or assessment.get("developer_assertions") != assertions
    ):
        _fail("material-fluid-recipe assessment state contradicts its checks")
    material = _assessment_id_material(
        spec,
        state=state,
        error_kind=error_kind,
        checks=derived,
        assertions=assertions,
        failed=failed,
        observed=observed,
        capture=capture,
    )
    expected_id = "workbench-atlas-material-fluid-recipe-assessment:sha256:" + sha256(
        _canonical_bytes(material)
    ).hexdigest()
    if assessment.get("assessment_id") != expected_id:
        _fail("material-fluid-recipe assessment identity is invalid")
    return dict(assessment)


__all__ = [
    "MARKER_PREFIX",
    "MaterialFluidRecipeObservationError",
    "MaterialFluidRecipeProbeSpec",
    "build_material_fluid_recipe_probe",
    "build_material_fluid_recipe_probe_overlay",
    "interpret_material_fluid_recipe_observation",
    "validate_material_fluid_recipe_assessment",
]
