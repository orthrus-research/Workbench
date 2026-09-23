"""Observe one planned Supersymmetry GregTech recipe before and after staging.

The recipe-change Blueprint owns construction and emits the exact observation
contract.  This module is the pack-owned Atlas interpreter for that contract:
it renders an observation-only Groovy probe, validates its single canonical
marker, and compares baseline and candidate cold starts.  It never edits a
source checkout or grants construction, support, release, or publication
authority.
"""

from __future__ import annotations

PROFILE_API_VERSION = 1

from dataclasses import dataclass
import base64
from hashlib import sha256
import json
import re
from typing import Any, Mapping, NoReturn, cast


MARKER_PREFIX = "[WORKBENCH-RECIPE-CHANGE-RUNTIME-V1]"
CONTRACT_FORMAT = "workbench-supersymmetry-recipe-observation-contract-v1"
CONTRACT_KIND = "workbench-supersymmetry-recipe-observation-contract"
ASSESSMENT_FORMAT = "workbench-supersymmetry-recipe-runtime-assessment-v1"
COMPARISON_FORMAT = "workbench-supersymmetry-recipe-runtime-comparison-v1"
PACK_PROFILE_ID = "workbench-pack:supersymmetry"
PLATFORM_PROFILE_ID = "workbench-platform:cleanroom:provisional"
MAX_LOG_BYTES = 16 * 1024 * 1024
MAX_PROBE_BYTES = 192 * 1024
MAX_CONCRETE_EXPANSIONS = 4096
MAX_MACHINE_INPUT_SLOTS = 64
PROBE_FAILURE_CODES = (
    "probe-phase:recipe-map-binding",
    "probe-phase:physical-side-binding",
    "probe-phase:recipe-map-alias-binding",
    "probe-phase:item-input-resolution",
    "probe-phase:fluid-input-resolution",
    "probe-phase:item-output-resolution",
    "probe-phase:fluid-output-resolution",
    "probe-phase:recipe-list-acquisition",
    "probe-phase:signature-metadata-scan",
    "probe-phase:signature-chance-scan",
    "probe-phase:signature-item-scan",
    "probe-phase:signature-fluid-scan",
    "probe-phase:input-expansion",
    "probe-phase:collision-match-scan",
    "probe-phase:collision-find-recipe",
)
_CONTENT_ID = re.compile(r"^(?:sha256:|[a-z][a-z0-9-]*:sha256:)[0-9a-f]{64}$")
_RECIPE_ALIAS = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_REGISTRY_NAME = re.compile(r"^[a-z0-9][a-z0-9_.:/-]{0,255}$")
_IDENTITY = re.compile(r"^[a-z0-9][a-z0-9_.:/-]*$")
_METAITEM_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$")
_ORE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_BASE64URL = re.compile(r"^[A-Za-z0-9_-]+$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
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
_ROLES = frozenset({"baseline", "candidate"})
_SIDES = frozenset({"client", "dedicated-server"})
_ACTUAL_FIELDS = {
    "actual_physical_side",
    "competing_input_match_count",
    "concrete_input_expansion_count",
    "declared_identity_count",
    "duration",
    "eut",
    "exact_recipe_match_count",
    "fluid_input_count",
    "fluid_output_count",
    "groovy_origin",
    "item_input_count",
    "item_output_count",
    "lookup_exact_match_count",
    "recipe_count",
    "recipe_map_alias_binding",
    "recipe_map_registry_name",
    "resolved_identity_count",
    "source_owner_compiled",
    "unresolved_input_expansion_count",
}
_CAPTURE_FIELDS = {
    "groovy_log_sha256",
    "groovy_log_uri",
    "physical_side",
    "projection_role",
    "runtime_receipt_sha256",
    "runtime_receipt_size",
    "runtime_receipt_uri",
}
_AUTHORITY = {
    "construction_authority": "none",
    "interpretation_owner": "Atlas",
    "source_profile": PACK_PROFILE_ID,
}
_LIMITATIONS = (
    "The result applies only to the exact disposable projection and cold start bound by its runtime receipt.",
    "Collision coverage is limited to at most 4096 concrete item expansions present in that active runtime; unresolved or over-bound expansions fail closed.",
    "Registration and lookup evidence do not prove that a player can execute or reach the recipe.",
    "The result does not authorize source mutation, stable support, release, or publication.",
)
_CONTRACT_LIMITATIONS = [
    "The result applies only to the exact disposable projection and cold start.",
    "Ore-dictionary collision coverage is limited to expansions present in that active runtime; unresolved expansions fail the observation.",
    "Specialized recipe-builder properties and chanced outputs are outside the V1 construction pattern.",
    "This contract does not authorize source mutation or publication.",
]
_COLLISION_DEFINITION = {
    "competing_recipe": (
        "a different registered recipe in the selected map that accepts "
        "a concrete active-runtime expansion of the planned inputs"
    ),
    "expected_competing_input_match_count": 0,
    "expected_exact_recipe_match_count": 1,
    "unresolved_input_expansions_are_failure": True,
}


class RecipeChangeRuntimeObservationError(ValueError):
    """The recipe observation contract, marker, or comparison is invalid."""


def _fail(message: str) -> NoReturn:
    raise RecipeChangeRuntimeObservationError(message)


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RecipeChangeRuntimeObservationError(
            "recipe runtime value is not canonical JSON"
        ) from exc


def _canonical_copy(value: Any) -> Any:
    return json.loads(_canonical_bytes(value).decode("utf-8"))


def _content_id(kind: str, body: Mapping[str, Any]) -> str:
    return f"{kind}:sha256:{sha256(_canonical_bytes(body)).hexdigest()}"


def _groovy_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _groovy_value(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if type(value) is int:
        return str(value)
    if type(value) is str:
        return _groovy_string(value)
    if type(value) is list:
        return "[" + ", ".join(_groovy_value(row) for row in value) + "]"
    if type(value) is dict:
        return "[" + ", ".join(
            f"{_groovy_string(str(key))}: {_groovy_value(value[key])}"
            for key in sorted(value)
        ) + "]"
    _fail("recipe probe value cannot be represented in bounded Groovy")


def _validated_ingredient_rows(
    value: Any,
    *,
    label: str,
    item: bool,
    output: bool,
) -> tuple[dict[str, Any], ...]:
    if type(value) is not list or len(value) > 16:
        _fail(f"{label} must be a bounded list")
    result: list[dict[str, Any]] = []
    for ordinal, raw in enumerate(value):
        if type(raw) is not dict:
            _fail(f"{label}[{ordinal}] must be an object")
        row = dict(raw)
        if item:
            if set(row) not in (
                {"amount", "kind", "name"},
                {"amount", "kind", "metadata", "name"},
            ):
                _fail(f"{label}[{ordinal}] fields changed")
            kind = row.get("kind")
            name = row.get("name")
            metadata = row.get("metadata")
            if (
                kind not in {"ore", "metaitem", "item"}
                or output and kind == "ore"
                or type(name) is not str
                or not name
                or len(name.encode("utf-8")) > 512
                or kind == "ore"
                and _ORE_NAME.fullmatch(name) is None
                or kind == "metaitem"
                and _METAITEM_NAME.fullmatch(name) is None
                or kind == "item"
                and (
                    _IDENTITY.fullmatch(name) is None
                    or ":" not in name
                )
                or metadata is not None
                and (
                    kind != "item"
                    or type(metadata) is not int
                    or type(metadata) is bool
                    or not 0 <= metadata <= 32767
                )
            ):
                _fail(f"{label}[{ordinal}] identity is invalid")
        else:
            if set(row) != {"amount", "name"}:
                _fail(f"{label}[{ordinal}] fields changed")
            name = row.get("name")
            if (
                type(name) is not str
                or _REGISTRY_NAME.fullmatch(name) is None
            ):
                _fail(f"{label}[{ordinal}] fluid identity is invalid")
        if (
            type(row.get("amount")) is not int
            or type(row.get("amount")) is bool
            or not 1 <= row["amount"] <= 2_147_483_647
        ):
            _fail(f"{label}[{ordinal}] amount is invalid")
        result.append(_canonical_copy(row))
    return tuple(result)


@dataclass(frozen=True)
class RecipeChangeProbeSpec:
    """One owner-derived observation contract in one comparison lane."""

    contract_id: str
    source_plan_id: str
    projection_role: str
    physical_side: str
    recipe_map_alias: str
    recipe_map_registry_name: str
    source_owner: str
    item_inputs: tuple[dict[str, Any], ...]
    fluid_inputs: tuple[dict[str, Any], ...]
    item_outputs: tuple[dict[str, Any], ...]
    fluid_outputs: tuple[dict[str, Any], ...]
    duration: int
    voltage_tier: str

    def __post_init__(self) -> None:
        if _CONTENT_ID.fullmatch(self.contract_id) is None:
            _fail("recipe probe contract ID is invalid")
        if _CONTENT_ID.fullmatch(self.source_plan_id) is None:
            _fail("recipe probe source plan ID is invalid")
        if self.projection_role not in _ROLES:
            _fail("recipe probe projection role is invalid")
        if self.physical_side not in _SIDES:
            _fail("recipe probe physical side is invalid")
        if _RECIPE_ALIAS.fullmatch(self.recipe_map_alias) is None:
            _fail("recipe probe map alias is invalid")
        if _REGISTRY_NAME.fullmatch(self.recipe_map_registry_name) is None:
            _fail("recipe probe map registry name is invalid")
        if (
            not isinstance(self.source_owner, str)
            or not self.source_owner.startswith("groovy/postInit/")
            or not self.source_owner.endswith(".groovy")
            or "\\" in self.source_owner
            or any(
                part in {"", ".", ".."}
                for part in self.source_owner.split("/")
            )
        ):
            _fail("recipe probe source owner is invalid")
        if (
            type(self.duration) is not int
            or type(self.duration) is bool
            or not 1 <= self.duration <= 2_147_483_647
            or self.voltage_tier not in _VOLTAGE_EUT
        ):
            _fail("recipe probe numeric signature is invalid")
        # Revalidate detached tuple members; callers must not bypass the
        # Blueprint renderer's supported ingredient vocabulary.
        _validated_ingredient_rows(
            list(self.item_inputs), label="item_inputs", item=True, output=False
        )
        _validated_ingredient_rows(
            list(self.fluid_inputs), label="fluid_inputs", item=False, output=False
        )
        _validated_ingredient_rows(
            list(self.item_outputs), label="item_outputs", item=True, output=True
        )
        _validated_ingredient_rows(
            list(self.fluid_outputs), label="fluid_outputs", item=False, output=True
        )
        if not self.item_inputs and not self.fluid_inputs:
            _fail("recipe probe requires at least one input")
        if not self.item_outputs and not self.fluid_outputs:
            _fail("recipe probe requires at least one output")

    @property
    def expected_eut(self) -> int:
        return _VOLTAGE_EUT[self.voltage_tier]

    @property
    def source_owner_class(self) -> str:
        return self.source_owner.removeprefix("groovy/").removesuffix(
            ".groovy"
        ).replace("/", ".")

    def identity_material(self) -> dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "duration": self.duration,
            "expected_eut": self.expected_eut,
            "fluid_inputs": list(self.fluid_inputs),
            "fluid_outputs": list(self.fluid_outputs),
            "item_inputs": list(self.item_inputs),
            "item_outputs": list(self.item_outputs),
            "physical_side": self.physical_side,
            "projection_role": self.projection_role,
            "recipe_map_alias": self.recipe_map_alias,
            "recipe_map_registry_name": self.recipe_map_registry_name,
            "source_owner": self.source_owner,
            "source_owner_class": self.source_owner_class,
            "source_plan_id": self.source_plan_id,
            "voltage_tier": self.voltage_tier,
        }

    @property
    def probe_id(self) -> str:
        return _content_id(
            "workbench-supersymmetry-recipe-change-probe",
            self.identity_material(),
        )

    @classmethod
    def from_contract(
        cls,
        contract: Mapping[str, Any],
        *,
        projection_role: str,
    ) -> "RecipeChangeProbeSpec":
        """Translate an already owner-validated Blueprint handoff."""

        if type(contract) is not dict:
            _fail("recipe observation contract must be an object")
        expected_fields = {
            "authority",
            "collision_definition",
            "expected_recipe",
            "format",
            "id",
            "identity_dependencies",
            "kind",
            "limitations",
            "physical_side",
            "probe_phase",
            "required_checks",
            "schema_version",
            "source_plan_id",
            "state",
        }
        body = dict(contract)
        supplied_id = body.pop("id", None)
        if (
            set(contract) != expected_fields
            or contract.get("format") != CONTRACT_FORMAT
            or contract.get("kind") != CONTRACT_KIND
            or contract.get("schema_version") != 1
            or contract.get("probe_phase") != "postInit-after-pack-recipe-owners"
            or contract.get("state") != "observation-required"
            or supplied_id != _content_id(CONTRACT_KIND, body)
        ):
            _fail("recipe observation contract identity changed")
        expected = contract.get("expected_recipe")
        if type(expected) is not dict or set(expected) != {
            "duration",
            "eut_expression",
            "fluid_inputs",
            "fluid_outputs",
            "item_inputs",
            "item_outputs",
            "recipe_map_alias",
            "recipe_map_registry_name",
            "source_owner",
            "voltage_tier",
        }:
            _fail("recipe observation expected recipe changed")
        voltage = expected.get("voltage_tier")
        if expected.get("eut_expression") != f"VA[{voltage}]":
            _fail("recipe observation EUT expression changed")
        collision = contract.get("collision_definition")
        required = contract.get("required_checks")
        if (
            contract.get("authority") != _AUTHORITY
            or collision != _COLLISION_DEFINITION
            or contract.get("limitations") != _CONTRACT_LIMITATIONS
            or contract.get("physical_side") not in _SIDES
            or type(contract.get("source_plan_id")) is not str
            or _CONTENT_ID.fullmatch(contract["source_plan_id"]) is None
            or type(required) is not dict
            or required
            != {
                "competing_input_match_count": 0,
                "declared_identities_resolve": True,
                "exact_recipe_match_count": 1,
                "groovy_origin": True,
                "lookup_resolves_exact_recipe": True,
                "recipe_map_alias_binding": True,
                "recipe_signature": True,
                "source_owner_compiled": True,
                "unresolved_input_expansion_count": 0,
            }
        ):
            _fail("recipe observation authority or required checks changed")
        item_inputs = _validated_ingredient_rows(
            expected.get("item_inputs"),
            label="item_inputs",
            item=True,
            output=False,
        )
        fluid_inputs = _validated_ingredient_rows(
            expected.get("fluid_inputs"),
            label="fluid_inputs",
            item=False,
            output=False,
        )
        item_outputs = _validated_ingredient_rows(
            expected.get("item_outputs"),
            label="item_outputs",
            item=True,
            output=True,
        )
        fluid_outputs = _validated_ingredient_rows(
            expected.get("fluid_outputs"),
            label="fluid_outputs",
            item=False,
            output=True,
        )
        expected_dependencies: list[dict[str, Any]] = []
        for direction, domains in (
            ("input", (("item", item_inputs), ("fluid", fluid_inputs))),
            ("output", (("item", item_outputs), ("fluid", fluid_outputs))),
        ):
            for domain, rows in domains:
                for ordinal, row in enumerate(rows):
                    expected_dependencies.append(
                        {
                            "direction": direction,
                            "domain": domain,
                            "ordinal": ordinal,
                            "value": dict(row),
                        }
                    )
        if contract.get("identity_dependencies") != expected_dependencies:
            _fail("recipe observation identity dependencies changed")
        return cls(
            contract_id=cast(str, supplied_id),
            source_plan_id=cast(str, contract.get("source_plan_id")),
            projection_role=projection_role,
            physical_side=cast(str, contract.get("physical_side")),
            recipe_map_alias=cast(str, expected.get("recipe_map_alias")),
            recipe_map_registry_name=cast(
                str, expected.get("recipe_map_registry_name")
            ),
            source_owner=cast(str, expected.get("source_owner")),
            item_inputs=item_inputs,
            fluid_inputs=fluid_inputs,
            item_outputs=item_outputs,
            fluid_outputs=fluid_outputs,
            duration=cast(int, expected.get("duration")),
            voltage_tier=cast(str, voltage),
        )


def derive_recipe_change_probe_spec(
    contract: Mapping[str, Any],
    *,
    projection_role: str,
) -> RecipeChangeProbeSpec:
    """Derive a sealed lane spec from an owner-validated handoff contract."""

    return RecipeChangeProbeSpec.from_contract(
        contract, projection_role=projection_role
    )


def _groovy_probe_body(spec: RecipeChangeProbeSpec) -> str:
    expected_items = _groovy_value(list(spec.item_inputs))
    expected_fluids = _groovy_value(list(spec.fluid_inputs))
    expected_item_outputs = _groovy_value(list(spec.item_outputs))
    expected_fluid_outputs = _groovy_value(list(spec.fluid_outputs))
    # The marker deliberately contains scalar fields only.  Python derives all
    # checks from them, so a probe cannot upgrade its own result by asserting a
    # precomputed success flag.
    return f'''// Generated by Workbench; disposable observation only.
import gregtech.api.recipes.RecipeMap
import gregtech.integration.groovy.GroovyScriptModule
import net.minecraft.item.ItemStack
import net.minecraft.util.ResourceLocation
import net.minecraftforge.fml.common.FMLCommonHandler
import net.minecraftforge.fml.common.registry.ForgeRegistries
import net.minecraftforge.fml.relauncher.Side
import net.minecraftforge.fluids.FluidRegistry
import net.minecraftforge.oredict.OreDictionary
import prePostInit.Recipemaps
import java.nio.charset.StandardCharsets
import java.util.Base64
import java.util.Collections
import java.util.IdentityHashMap
import java.util.TreeMap

def expectedItemInputs = {expected_items}
def expectedFluidInputs = {expected_fluids}
def expectedItemOutputs = {expected_item_outputs}
def expectedFluidOutputs = {expected_fluid_outputs}
def expectedDuration = {spec.duration}
def expectedEut = {spec.expected_eut}
def actual = new TreeMap()
{chr(10).join(f"actual.put({_groovy_string(name)}, null)" for name in sorted(_ACTUAL_FIELDS))}
def errorKind = null
// GroovyScript's sandbox may reject reflection on the caught throwable itself.
// Carry a deterministic code into the catch block instead, so every ordinary
// probe failure can still reach the canonical marker encoder below.
def probeFailureCode = 'probe-phase:recipe-map-binding'
try {{
    def recipeMap = RecipeMap.getByName({_groovy_string(spec.recipe_map_registry_name)})
    actual.put('recipe_map_registry_name', recipeMap == null ? null : recipeMap.getUnlocalizedName())

    probeFailureCode = 'probe-phase:physical-side-binding'
    actual.put('actual_physical_side', FMLCommonHandler.instance().side == Side.CLIENT ? 'client' : 'dedicated-server')

    probeFailureCode = 'probe-phase:recipe-map-alias-binding'
    def aliasMap = Recipemaps.{spec.recipe_map_alias}
    actual.put('recipe_map_alias_binding', recipeMap != null && aliasMap != null && aliasMap.is(recipeMap))

    probeFailureCode = 'probe-phase:item-input-resolution'
    def resolvedItemInputs = []
    def unresolved = 0
    expectedItemInputs.each {{ row ->
        def variants = []
        if (row.kind == 'ore') {{
            OreDictionary.getOres(row.name, false).each {{ raw ->
                if (raw != null && !raw.isEmpty()) {{
                    def stack = raw.copy()
                    stack.setCount(row.amount as int)
                    variants.add(stack)
                }}
            }}
        }} else if (row.kind == 'metaitem') {{
            def raw = GroovyScriptModule.getMetaItem(row.name)
            if (raw != null && !raw.isEmpty()) {{
                def stack = raw.copy()
                stack.setCount(row.amount as int)
                variants.add(stack)
            }}
        }} else {{
            def item = ForgeRegistries.ITEMS.getValue(new ResourceLocation(row.name))
            if (item != null) variants.add(new ItemStack(item, row.amount as int, (row.metadata ?: 0) as int))
        }}
        def unique = new TreeMap()
        variants.each {{ stack ->
            def key = String.valueOf(stack.item.registryName) + '|' + stack.metadata + '|' + stack.count + '|' + String.valueOf(stack.tagCompound)
            unique.put(key, stack)
        }}
        variants = new ArrayList(unique.values())
        if (variants.size() > {MAX_CONCRETE_EXPANSIONS}) throw new IllegalStateException('one item identity exceeds the concrete expansion bound')
        if (variants.isEmpty()) unresolved++
        resolvedItemInputs.add(variants)
    }}

    probeFailureCode = 'probe-phase:fluid-input-resolution'
    def resolvedFluids = []
    expectedFluidInputs.each {{ row ->
        def stack = FluidRegistry.getFluidStack(row.name, row.amount as int)
        if (stack == null) unresolved++
        else resolvedFluids.add(stack)
    }}

    probeFailureCode = 'probe-phase:item-output-resolution'
    def outputIdentitiesResolved = true
    expectedItemOutputs.each {{ row ->
        if (row.kind == 'metaitem') {{
            def stack = GroovyScriptModule.getMetaItem(row.name)
            if (stack == null || stack.isEmpty()) outputIdentitiesResolved = false
        }} else {{
            if (ForgeRegistries.ITEMS.getValue(new ResourceLocation(row.name)) == null) outputIdentitiesResolved = false
        }}
    }}

    probeFailureCode = 'probe-phase:fluid-output-resolution'
    expectedFluidOutputs.each {{ row ->
        if (FluidRegistry.getFluid(row.name) == null) outputIdentitiesResolved = false
    }}
    if (!outputIdentitiesResolved) unresolved++
    def declaredIdentityCount = expectedItemInputs.size() + expectedFluidInputs.size() + expectedItemOutputs.size() + expectedFluidOutputs.size()
    actual.put('declared_identity_count', declaredIdentityCount)
    actual.put('unresolved_input_expansion_count', unresolved)
    actual.put('resolved_identity_count', unresolved == 0 ? declaredIdentityCount : declaredIdentityCount - unresolved)

    def expectedItemStack = {{ row ->
        if (row.kind == 'metaitem') {{
            def raw = GroovyScriptModule.getMetaItem(row.name)
            def value = raw == null ? null : raw.copy()
            if (value != null) value.setCount(row.amount as int)
            return value
        }}
        def item = ForgeRegistries.ITEMS.getValue(new ResourceLocation(row.name))
        return item == null ? null : new ItemStack(item, row.amount as int, (row.metadata ?: 0) as int)
    }}
    def itemInputsMatch = {{ recipe ->
        if (recipe.getInputs().size() != expectedItemInputs.size()) return false
        for (int ordinal = 0; ordinal < expectedItemInputs.size(); ordinal++) {{
            def expected = expectedItemInputs[ordinal]
            def observed = recipe.getInputs()[ordinal]
            if (observed.getAmount() != expected.amount || observed.isNonConsumable() || observed.hasNBTMatchingCondition()) return false
            if (expected.kind == 'ore') {{
                if (!observed.isOreDict() || OreDictionary.getOreName(observed.getOreDict()) != expected.name) return false
            }} else {{
                def stack = expectedItemStack(expected)
                if (stack == null || observed.isOreDict() || !observed.acceptsStack(stack)) return false
                def representatives = observed.getInputStacks()
                if (representatives == null || !representatives.any {{ ItemStack.areItemsEqual(it, stack) && ItemStack.areItemStackTagsEqual(it, stack) }}) return false
            }}
        }}
        return true
    }}
    def fluidInputsMatch = {{ recipe ->
        if (recipe.getFluidInputs().size() != expectedFluidInputs.size()) return false
        for (int ordinal = 0; ordinal < expectedFluidInputs.size(); ordinal++) {{
            def expected = expectedFluidInputs[ordinal]
            def observed = recipe.getFluidInputs()[ordinal]
            def stack = observed.getInputFluidStack()
            if (observed.isNonConsumable() || stack == null || stack.fluid.name != expected.name || stack.amount != expected.amount) return false
        }}
        return true
    }}
    def itemOutputsMatch = {{ recipe ->
        if (recipe.getOutputs().size() != expectedItemOutputs.size()) return false
        for (int ordinal = 0; ordinal < expectedItemOutputs.size(); ordinal++) {{
            def expected = expectedItemOutputs[ordinal]
            def observed = recipe.getOutputs()[ordinal]
            def stack = expectedItemStack(expected)
            if (stack == null || observed.count != expected.amount || !ItemStack.areItemsEqual(observed, stack) || !ItemStack.areItemStackTagsEqual(observed, stack)) return false
        }}
        return true
    }}
    def fluidOutputsMatch = {{ recipe ->
        if (recipe.getFluidOutputs().size() != expectedFluidOutputs.size()) return false
        for (int ordinal = 0; ordinal < expectedFluidOutputs.size(); ordinal++) {{
            def expected = expectedFluidOutputs[ordinal]
            def observed = recipe.getFluidOutputs()[ordinal]
            if (observed == null || observed.fluid.name != expected.name || observed.amount != expected.amount) return false
        }}
        return true
    }}
    def signatureMatches = {{ recipe ->
        probeFailureCode = 'probe-phase:signature-metadata-scan'
        if (recipe.getDuration() != expectedDuration || recipe.getEUt() != expectedEut ||
            recipe.isHidden() || recipe.getIsCTRecipe()) return false
        probeFailureCode = 'probe-phase:signature-chance-scan'
        if (!recipe.getChancedOutputs().getChancedEntries().isEmpty() ||
            !recipe.getChancedFluidOutputs().getChancedEntries().isEmpty()) return false
        probeFailureCode = 'probe-phase:signature-item-scan'
        if (!itemInputsMatch(recipe) || !itemOutputsMatch(recipe)) return false
        probeFailureCode = 'probe-phase:signature-fluid-scan'
        return fluidInputsMatch(recipe) && fluidOutputsMatch(recipe)
    }}

    probeFailureCode = 'probe-phase:recipe-list-acquisition'
    def recipes = Collections.newSetFromMap(new IdentityHashMap())
    if (recipeMap != null) recipeMap.getRecipeList().each {{ recipes.add(it) }}
    def exact = recipes.findAll {{ signatureMatches(it) }}
    def exactRecipe = exact.size() == 1 ? exact[0] : null

    probeFailureCode = 'probe-phase:signature-metadata-scan'
    actual.put('recipe_count', recipes.size())
    actual.put('exact_recipe_match_count', exact.size())
    actual.put('duration', exactRecipe == null ? null : exactRecipe.getDuration())
    actual.put('eut', exactRecipe == null ? null : exactRecipe.getEUt())
    actual.put('groovy_origin', exactRecipe == null ? null : exactRecipe.isGroovyRecipe())

    probeFailureCode = 'probe-phase:signature-item-scan'
    actual.put('item_input_count', exactRecipe == null ? null : exactRecipe.getInputs().size())
    actual.put('item_output_count', exactRecipe == null ? null : exactRecipe.getOutputs().size())

    probeFailureCode = 'probe-phase:signature-fluid-scan'
    actual.put('fluid_input_count', exactRecipe == null ? null : exactRecipe.getFluidInputs().size())
    actual.put('fluid_output_count', exactRecipe == null ? null : exactRecipe.getFluidOutputs().size())

    probeFailureCode = 'probe-phase:input-expansion'
    def combinations = [[]]
    resolvedItemInputs.each {{ variants ->
        def expanded = []
        for (def prefix : combinations) {{
            for (def stack : variants) {{
                expanded.add(prefix + [stack.copy()])
                if (expanded.size() > {MAX_CONCRETE_EXPANSIONS}) break
            }}
            if (expanded.size() > {MAX_CONCRETE_EXPANSIONS}) break
        }}
        combinations = expanded
    }}
    if (combinations.size() > {MAX_CONCRETE_EXPANSIONS}) throw new IllegalStateException('concrete input expansion bound exceeded')
    if (resolvedItemInputs.isEmpty()) combinations = [[]]
    actual.put('concrete_input_expansion_count', combinations.size())

    probeFailureCode = 'probe-phase:collision-match-scan'
    def mapItemSlotCount = recipeMap == null ? 0 : recipeMap.getMaxInputs()
    def mapFluidSlotCount = recipeMap == null ? 0 : recipeMap.getMaxFluidInputs()
    if (mapItemSlotCount < expectedItemInputs.size() ||
        mapFluidSlotCount < expectedFluidInputs.size() ||
        mapItemSlotCount > {MAX_MACHINE_INPUT_SLOTS} ||
        mapFluidSlotCount > {MAX_MACHINE_INPUT_SLOTS}) {{
        throw new IllegalStateException('recipe-map input slot count is outside the probe bound')
    }}
    def queryFluids = new ArrayList(resolvedFluids)
    while (queryFluids.size() < mapFluidSlotCount) queryFluids.add(null)
    def competitors = Collections.newSetFromMap(new IdentityHashMap())
    def lookupExact = 0
    combinations.each {{ items ->
        probeFailureCode = 'probe-phase:collision-match-scan'
        def queryItems = new ArrayList(items)
        while (queryItems.size() < mapItemSlotCount) queryItems.add(ItemStack.EMPTY)
        recipes.each {{ recipe ->
            if (!exact.contains(recipe) && recipe.matches(false, queryItems, queryFluids)) competitors.add(recipe)
        }}
        probeFailureCode = 'probe-phase:collision-find-recipe'
        def found = recipeMap == null ? null : recipeMap.findRecipe(expectedEut, queryItems, queryFluids, true)
        if (exactRecipe != null && found != null && found.is(exactRecipe)) lookupExact++
    }}
    actual.put('competing_input_match_count', competitors.size())
    actual.put('lookup_exact_match_count', lookupExact)
}} catch (Throwable ignored) {{
    errorKind = probeFailureCode
}}

def jsonQuote = {{ Object raw ->
    def out = new StringBuilder('"')
    String.valueOf(raw).each {{ character ->
        int code = (int) character
        if (code == 34) out.append((char) 92).append((char) 34)
        else if (code == 92) out.append((char) 92).append((char) 92)
        else if (code == 8) out.append((char) 92).append('b')
        else if (code == 12) out.append((char) 92).append('f')
        else if (code == 10) out.append((char) 92).append('n')
        else if (code == 13) out.append((char) 92).append('r')
        else if (code == 9) out.append((char) 92).append('t')
        else if (code < 32) out.append((char) 92).append('u').append(String.format('%04x', code))
        else out.append(character)
    }}
    out.append('"').toString()
}}
def jsonScalar = {{ Object value ->
    value == null ? 'null' : value instanceof Boolean ? (value ? 'true' : 'false') :
        value instanceof Number ? String.valueOf(value) : jsonQuote(value)
}}
def jsonMap = {{ Map value -> '{{' + new TreeMap(value).collect {{ key, item -> jsonQuote(key) + ':' + jsonScalar(item) }}.join(',') + '}}' }}
def payload = new TreeMap()
payload.put('actual', actual)
payload.put('contract_id', {_groovy_string(spec.contract_id)})
payload.put('error_kind', errorKind)
payload.put('expected_physical_side', {_groovy_string(spec.physical_side)})
payload.put('format', 'workbench-recipe-change-runtime-marker-v1')
payload.put('probe_id', {_groovy_string(spec.probe_id)})
payload.put('projection_role', {_groovy_string(spec.projection_role)})
payload.put('source_plan_id', {_groovy_string(spec.source_plan_id)})
payload.put('stage', 'postInit')
def payloadJson = '{{' + new TreeMap(payload).collect {{ key, value ->
    jsonQuote(key) + ':' + (key == 'actual' ? jsonMap(value as Map) : jsonScalar(value))
}}.join(',') + '}}'
def encoded = Base64.getUrlEncoder().withoutPadding().encodeToString(payloadJson.getBytes(StandardCharsets.UTF_8))
log.infoMC({_groovy_string(MARKER_PREFIX)} + encoded)
'''


def build_recipe_change_probe(spec: RecipeChangeProbeSpec) -> bytes:
    """Render one bounded observation-only postInit script."""

    raw = _groovy_probe_body(spec).encode("utf-8")
    if len(raw) > MAX_PROBE_BYTES:
        _fail("generated recipe-change probe exceeds its byte bound")
    return raw


def build_recipe_change_probe_overlay(spec: RecipeChangeProbeSpec) -> dict[str, Any]:
    """Describe the probe's fixed create-only path for its physical side."""

    script = build_recipe_change_probe(spec)
    relative = "groovy/postInit/utils/ZzzzWorkbenchRecipeChangeAssertion.groovy"
    target = (".minecraft/" + relative) if spec.physical_side == "client" else relative
    material = {
        "physical_side": spec.physical_side,
        "probe_id": spec.probe_id,
        "projection_role": spec.projection_role,
        "source_plan_id": spec.source_plan_id,
        "source_sha256": sha256(script).hexdigest(),
        "target": target,
    }
    return {
        "format": "workbench-runtime-compatibility-file-overlay-v1",
        "schema_version": 1,
        "patch_id": _content_id(
            "workbench-recipe-change-observation-probe", material
        ),
        "description": (
            "Disposable Supersymmetry recipe-change runtime observation; "
            "it reads registry state and creates no recipe."
        ),
        "target": {"path": target, "must_be_absent": True},
        "source": {
            "path": "RecipeChangeAssertion.groovy",
            "sha256": material["source_sha256"],
        },
    }


def _decode_marker(log_bytes: bytes) -> dict[str, Any]:
    if not isinstance(log_bytes, bytes) or len(log_bytes) > MAX_LOG_BYTES:
        _fail("recipe-change Groovy log is absent or exceeds its byte bound")
    try:
        text = log_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RecipeChangeRuntimeObservationError(
            "recipe-change Groovy log is not UTF-8"
        ) from exc
    tokens: list[str] = []
    for line in text.splitlines():
        index = line.find(MARKER_PREFIX)
        if index < 0:
            continue
        token = line[index + len(MARKER_PREFIX) :].strip()
        if _BASE64URL.fullmatch(token) is None:
            _fail("recipe-change marker payload is malformed")
        tokens.append(token)
    if len(tokens) != 1:
        _fail("Groovy log must contain exactly one recipe-change marker")
    try:
        raw = base64.urlsafe_b64decode(tokens[0] + "=" * (-len(tokens[0]) % 4))
        value = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RecipeChangeRuntimeObservationError(
            "recipe-change marker is not canonical JSON data"
        ) from exc
    if type(value) is not dict or _canonical_bytes(value) != raw:
        _fail("recipe-change marker is not canonical JSON")
    return value


def _source_owner_compiled(
    spec: RecipeChangeProbeSpec,
    log_bytes: bytes,
) -> bool:
    """Derive owner execution only from the retained Groovy engine log.

    GroovyScript writes this line immediately before invoking a script.  The
    probe itself is intentionally unable to claim the result: its marker must
    retain ``source_owner_compiled: null`` and Atlas derives the boolean from
    the exact, custody-bound log bytes instead.
    """

    try:
        text = log_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:  # Kept local for direct helper safety.
        raise RecipeChangeRuntimeObservationError(
            "recipe-change Groovy log is not UTF-8"
        ) from exc
    expected_side = "CLIENT" if spec.physical_side == "client" else "SERVER"
    expected = re.compile(
        rf"\[\d{{2}}:\d{{2}}:\d{{2}}\] "
        rf"\[{expected_side}/INFO\] \[supersymmetry\]:  - running script "
        rf"{re.escape(spec.source_owner_class)}"
    )
    lines = text.splitlines()
    owner_ordinals = [
        ordinal
        for ordinal, line in enumerate(lines)
        if expected.fullmatch(line) is not None
    ]
    marker_ordinals = [
        ordinal
        for ordinal, line in enumerate(lines)
        if MARKER_PREFIX in line
    ]
    return (
        len(owner_ordinals) == 1
        and len(marker_ordinals) == 1
        and owner_ordinals[0] < marker_ordinals[0]
    )


def _validated_capture(
    spec: RecipeChangeProbeSpec,
    capture: Mapping[str, Any],
    log_bytes: bytes | None,
) -> dict[str, Any]:
    if type(capture) is not dict or set(capture) != _CAPTURE_FIELDS:
        _fail("recipe-change runtime capture fields changed")
    if (
        capture.get("physical_side") != spec.physical_side
        or capture.get("projection_role") != spec.projection_role
        or type(capture.get("groovy_log_sha256")) is not str
        or _SHA256.fullmatch(capture["groovy_log_sha256"]) is None
        or log_bytes is not None
        and capture["groovy_log_sha256"] != sha256(log_bytes).hexdigest()
        or type(capture.get("runtime_receipt_sha256")) is not str
        or _SHA256.fullmatch(capture["runtime_receipt_sha256"]) is None
        or type(capture.get("runtime_receipt_size")) is not int
        or type(capture.get("runtime_receipt_size")) is bool
        or not 1 <= capture["runtime_receipt_size"] <= 64 * 1024 * 1024
        or any(
            type(capture.get(field)) is not str
            or not capture[field].startswith("file:")
            for field in ("groovy_log_uri", "runtime_receipt_uri")
        )
    ):
        _fail("recipe-change runtime capture does not bind the probe lane")
    return _canonical_copy(capture)


def _validated_actual(value: Any) -> dict[str, Any]:
    if type(value) is not dict or set(value) != _ACTUAL_FIELDS:
        _fail("recipe-change marker observation fields changed")
    actual = dict(value)
    nullable_nonnegative = (
        "competing_input_match_count",
        "concrete_input_expansion_count",
        "declared_identity_count",
        "exact_recipe_match_count",
        "lookup_exact_match_count",
        "recipe_count",
        "resolved_identity_count",
        "unresolved_input_expansion_count",
        "duration",
        "eut",
        "fluid_input_count",
        "fluid_output_count",
        "item_input_count",
        "item_output_count",
    )
    if (
        actual.get("actual_physical_side") is not None
        and actual["actual_physical_side"] not in _SIDES
        or actual.get("recipe_map_registry_name") is not None
        and (
            type(actual["recipe_map_registry_name"]) is not str
            or _REGISTRY_NAME.fullmatch(actual["recipe_map_registry_name"])
            is None
        )
        or actual.get("recipe_map_alias_binding") is not None
        and type(actual["recipe_map_alias_binding"]) is not bool
        or actual.get("source_owner_compiled") is not None
        and type(actual["source_owner_compiled"]) is not bool
        or actual.get("groovy_origin") is not None
        and type(actual["groovy_origin"]) is not bool
        or any(
            actual.get(field) is not None
            and (
                type(actual[field]) is not int
                or type(actual[field]) is bool
                or not 0 <= actual[field] <= 2_147_483_647
            )
            for field in nullable_nonnegative
        )
        or all(
            type(actual.get(field)) is int
            for field in ("resolved_identity_count", "declared_identity_count")
        )
        and actual["resolved_identity_count"] > actual["declared_identity_count"]
        or all(
            type(actual.get(field)) is int
            for field in ("exact_recipe_match_count", "recipe_count")
        )
        and actual["exact_recipe_match_count"] > actual["recipe_count"]
        or all(
            type(actual.get(field)) is int
            for field in ("competing_input_match_count", "recipe_count")
        )
        and actual["competing_input_match_count"] > actual["recipe_count"]
        or all(
            type(actual.get(field)) is int
            for field in (
                "lookup_exact_match_count",
                "concrete_input_expansion_count",
            )
        )
        and actual["lookup_exact_match_count"]
        > actual["concrete_input_expansion_count"]
    ):
        _fail("recipe-change marker observation values are malformed")
    return _canonical_copy(actual)


def _checks(
    spec: RecipeChangeProbeSpec,
    actual: Mapping[str, Any],
    error_kind: str | None,
) -> dict[str, bool]:
    expected_exact = 0 if spec.projection_role == "baseline" else 1
    exact_present = actual.get("exact_recipe_match_count") == 1
    expansions = actual.get("concrete_input_expansion_count")
    signature = (
        exact_present
        and actual.get("duration") == spec.duration
        and actual.get("eut") == spec.expected_eut
        and actual.get("item_input_count") == len(spec.item_inputs)
        and actual.get("fluid_input_count") == len(spec.fluid_inputs)
        and actual.get("item_output_count") == len(spec.item_outputs)
        and actual.get("fluid_output_count") == len(spec.fluid_outputs)
    )
    return {
        "competing_input_match_count": actual.get("competing_input_match_count") == 0,
        "declared_identities_resolve": (
            type(actual.get("declared_identity_count")) is int
            and type(actual.get("resolved_identity_count")) is int
            and actual.get("declared_identity_count")
            == actual.get("resolved_identity_count")
        ),
        "exact_recipe_match_count": actual.get("exact_recipe_match_count") == expected_exact,
        "groovy_origin": (
            actual.get("groovy_origin") is True
            if spec.projection_role == "candidate"
            else actual.get("groovy_origin") is None
        ),
        "lookup_resolves_exact_recipe": (
            type(expansions) is int
            and type(expansions) is not bool
            and actual.get("lookup_exact_match_count") == expansions
            if spec.projection_role == "candidate"
            else actual.get("lookup_exact_match_count") == 0
        ),
        "physical_side": actual.get("actual_physical_side") == spec.physical_side,
        "probe_execution": error_kind is None,
        "recipe_map_alias_binding": actual.get("recipe_map_alias_binding") is True,
        "recipe_map_count": (
            type(actual.get("recipe_count")) is int
            and type(actual["recipe_count"]) is not bool
        ),
        "recipe_map_registry_name": (
            actual.get("recipe_map_registry_name")
            == spec.recipe_map_registry_name
        ),
        "recipe_signature": signature if spec.projection_role == "candidate" else not exact_present,
        "source_owner_compiled": actual.get("source_owner_compiled") is True,
        "unresolved_input_expansion_count": actual.get("unresolved_input_expansion_count") == 0,
        "bounded_input_expansions": (
            type(expansions) is int
            and type(expansions) is not bool
            and 1 <= expansions <= MAX_CONCRETE_EXPANSIONS
        ),
    }


def _assessment_material(
    spec: RecipeChangeProbeSpec,
    *,
    state: str,
    actual: Mapping[str, Any],
    error_kind: str | None,
    checks: Mapping[str, bool],
    failed: list[str],
    capture: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "capture": dict(capture),
        "checks": dict(checks),
        "contract_id": spec.contract_id,
        "error_kind": error_kind,
        "failed_checks": failed,
        "observed": dict(actual),
        "physical_side": spec.physical_side,
        "probe_id": spec.probe_id,
        "projection_role": spec.projection_role,
        "source_plan_id": spec.source_plan_id,
        "state": state,
    }


def interpret_recipe_change_observation(
    spec: RecipeChangeProbeSpec,
    *,
    groovy_log_bytes: bytes,
    capture: Mapping[str, Any],
) -> dict[str, Any]:
    """Interpret one marker and bind it to retained runtime custody."""

    retained_capture = _validated_capture(spec, capture, groovy_log_bytes)
    marker = _decode_marker(groovy_log_bytes)
    if set(marker) != {
        "actual",
        "contract_id",
        "error_kind",
        "expected_physical_side",
        "format",
        "probe_id",
        "projection_role",
        "source_plan_id",
        "stage",
    }:
        _fail("recipe-change marker fields changed")
    actual = marker.get("actual")
    error_kind = marker.get("error_kind")
    if (
        marker.get("format") != "workbench-recipe-change-runtime-marker-v1"
        or marker.get("probe_id") != spec.probe_id
        or marker.get("contract_id") != spec.contract_id
        or marker.get("source_plan_id") != spec.source_plan_id
        or marker.get("projection_role") != spec.projection_role
        or marker.get("expected_physical_side") != spec.physical_side
        or marker.get("stage") != "postInit"
        or type(actual) is not dict
        or error_kind is not None
        and (
            type(error_kind) is not str
            or error_kind not in PROBE_FAILURE_CODES
        )
    ):
        _fail("recipe-change marker does not bind its probe")
    actual = _validated_actual(actual)
    # This assertion is owned by the retained engine log.  A marker may
    # neither grant nor deny it on its own behalf.  Retained assessments carry
    # the derived boolean and are revalidated separately below.
    if actual["source_owner_compiled"] is not None:
        _fail("recipe-change marker cannot claim source-owner execution")
    actual["source_owner_compiled"] = _source_owner_compiled(
        spec, groovy_log_bytes
    )
    derived = _checks(spec, actual, cast(str | None, error_kind))
    if any(type(value) is not bool for value in derived.values()):
        _fail("recipe-change assessment checks are not boolean")
    failed = sorted(name for name, passed in derived.items() if not passed)
    state = "observed" if not failed else "mismatch"
    material = _assessment_material(
        spec,
        state=state,
        actual=actual,
        error_kind=cast(str | None, error_kind),
        checks=derived,
        failed=failed,
        capture=retained_capture,
    )
    return {
        "assessment_id": _content_id(
            "workbench-atlas-recipe-runtime-assessment", material
        ),
        "authority": dict(_AUTHORITY),
        "capture": retained_capture,
        "checks": derived,
        "contract_id": spec.contract_id,
        "error_kind": error_kind,
        "failed_checks": failed,
        "format": ASSESSMENT_FORMAT,
        "limitations": list(_LIMITATIONS),
        "observed": dict(actual),
        "physical_side": spec.physical_side,
        "probe": {
            **spec.identity_material(),
            "probe_id": spec.probe_id,
            "script_sha256": sha256(build_recipe_change_probe(spec)).hexdigest(),
            "overlay_id": build_recipe_change_probe_overlay(spec)["patch_id"],
        },
        "profile": {
            "pack_profile_id": PACK_PROFILE_ID,
            "platform_profile_id": PLATFORM_PROFILE_ID,
        },
        "projection_role": spec.projection_role,
        "schema_version": 1,
        "source_plan_id": spec.source_plan_id,
        "state": state,
    }


def validate_recipe_change_assessment(
    spec: RecipeChangeProbeSpec,
    assessment: Mapping[str, Any],
) -> dict[str, Any]:
    """Revalidate an assessment without rereading its runtime files."""

    if type(assessment) is not dict or set(assessment) != {
        "assessment_id",
        "authority",
        "capture",
        "checks",
        "contract_id",
        "error_kind",
        "failed_checks",
        "format",
        "limitations",
        "observed",
        "physical_side",
        "probe",
        "profile",
        "projection_role",
        "schema_version",
        "source_plan_id",
        "state",
    }:
        _fail("recipe-change assessment fields changed")
    expected_probe = {
        **spec.identity_material(),
        "probe_id": spec.probe_id,
        "script_sha256": sha256(build_recipe_change_probe(spec)).hexdigest(),
        "overlay_id": build_recipe_change_probe_overlay(spec)["patch_id"],
    }
    if (
        assessment.get("format") != ASSESSMENT_FORMAT
        or assessment.get("schema_version") != 1
        or assessment.get("authority") != _AUTHORITY
        or assessment.get("limitations") != list(_LIMITATIONS)
        or assessment.get("profile")
        != {
            "pack_profile_id": PACK_PROFILE_ID,
            "platform_profile_id": PLATFORM_PROFILE_ID,
        }
        or assessment.get("probe") != expected_probe
        or assessment.get("contract_id") != spec.contract_id
        or assessment.get("source_plan_id") != spec.source_plan_id
        or assessment.get("projection_role") != spec.projection_role
        or assessment.get("physical_side") != spec.physical_side
    ):
        _fail("recipe-change assessment contradicts its probe")
    capture = _validated_capture(
        spec, cast(Mapping[str, Any], assessment.get("capture")), None
    )
    actual = assessment.get("observed")
    error_kind = assessment.get("error_kind")
    if (
        type(actual) is not dict
        or error_kind is not None
        and (
            type(error_kind) is not str
            or error_kind not in PROBE_FAILURE_CODES
        )
    ):
        _fail("recipe-change assessment observation is malformed")
    actual = _validated_actual(actual)
    derived = _checks(spec, actual, cast(str | None, error_kind))
    failed = sorted(name for name, passed in derived.items() if not passed)
    state = "observed" if not failed else "mismatch"
    if (
        assessment.get("checks") != derived
        or assessment.get("failed_checks") != failed
        or assessment.get("state") != state
    ):
        _fail("recipe-change assessment state contradicts its observation")
    material = _assessment_material(
        spec,
        state=state,
        actual=actual,
        error_kind=cast(str | None, error_kind),
        checks=derived,
        failed=failed,
        capture=capture,
    )
    if assessment.get("assessment_id") != _content_id(
        "workbench-atlas-recipe-runtime-assessment", material
    ):
        _fail("recipe-change assessment identity is invalid")
    return dict(assessment)


def _count_or_none(value: Any) -> int | None:
    return value if type(value) is int else None


def _count_view(actual: Mapping[str, Any]) -> dict[str, int | None]:
    return {
        "competitor_count": _count_or_none(
            actual["competing_input_match_count"]
        ),
        "exact_count": _count_or_none(actual["exact_recipe_match_count"]),
        "map_count": _count_or_none(actual["recipe_count"]),
        "unresolved_count": _count_or_none(
            actual["unresolved_input_expansion_count"]
        ),
    }


def compare_recipe_change_observations(
    baseline_spec: RecipeChangeProbeSpec,
    baseline_assessment: Mapping[str, Any],
    candidate_spec: RecipeChangeProbeSpec,
    candidate_assessment: Mapping[str, Any],
) -> dict[str, Any]:
    """Compare one baseline/candidate cold-start pair on one physical side."""

    baseline = validate_recipe_change_assessment(
        baseline_spec, baseline_assessment
    )
    candidate = validate_recipe_change_assessment(
        candidate_spec, candidate_assessment
    )
    if (
        baseline_spec.projection_role != "baseline"
        or candidate_spec.projection_role != "candidate"
        or baseline_spec.contract_id != candidate_spec.contract_id
        or baseline_spec.source_plan_id != candidate_spec.source_plan_id
        or baseline_spec.physical_side != candidate_spec.physical_side
        or {
            key: value
            for key, value in baseline_spec.identity_material().items()
            if key != "projection_role"
        }
        != {
            key: value
            for key, value in candidate_spec.identity_material().items()
            if key != "projection_role"
        }
    ):
        _fail("recipe-change comparison lanes do not share one contract")
    before = baseline["observed"]
    after = candidate["observed"]
    checks = {
        "baseline_observed": baseline["state"] == "observed",
        "candidate_observed": candidate["state"] == "observed",
        "recipe_count_delta_one": (
            type(before.get("recipe_count")) is int
            and type(after.get("recipe_count")) is int
            and after["recipe_count"] - before["recipe_count"] == 1
        ),
        "identity_resolution_stable": (
            before.get("declared_identity_count")
            == after.get("declared_identity_count")
            and before.get("resolved_identity_count")
            == after.get("resolved_identity_count")
            and before.get("concrete_input_expansion_count")
            == after.get("concrete_input_expansion_count")
        ),
        "no_preexisting_exact_recipe": before.get("exact_recipe_match_count") == 0,
        "candidate_exact_recipe_unique": after.get("exact_recipe_match_count") == 1,
        "no_competing_recipe_before_or_after": (
            before.get("competing_input_match_count") == 0
            and after.get("competing_input_match_count") == 0
        ),
        "candidate_lookup_selects_exact_recipe": (
            after.get("lookup_exact_match_count")
            == after.get("concrete_input_expansion_count")
        ),
    }
    failed = sorted(name for name, passed in checks.items() if not passed)
    state = "observed-change" if not failed else "comparison-mismatch"
    material = {
        "baseline_assessment_id": baseline["assessment_id"],
        "candidate_assessment_id": candidate["assessment_id"],
        "observed_counts": {
            "baseline": _count_view(before),
            "candidate": _count_view(after),
        },
        "checks": checks,
        "contract_id": baseline_spec.contract_id,
        "failed_checks": failed,
        "physical_side": baseline_spec.physical_side,
        "source_plan_id": baseline_spec.source_plan_id,
        "state": state,
    }
    return {
        "comparison_id": _content_id(
            "workbench-atlas-recipe-runtime-comparison", material
        ),
        "format": COMPARISON_FORMAT,
        "schema_version": 1,
        "authority": dict(_AUTHORITY),
        "state": state,
        "source_plan_id": baseline_spec.source_plan_id,
        "contract_id": baseline_spec.contract_id,
        "physical_side": baseline_spec.physical_side,
        "baseline_assessment_id": baseline["assessment_id"],
        "candidate_assessment_id": candidate["assessment_id"],
        "observed_counts": {
            "baseline": _count_view(before),
            "candidate": _count_view(after),
        },
        "checks": checks,
        "failed_checks": failed,
        "delta": {
            "recipe_count": (
                after["recipe_count"] - before["recipe_count"]
                if type(before.get("recipe_count")) is int
                and type(after.get("recipe_count")) is int
                else None
            ),
            "exact_recipe_match_count": (
                after.get("exact_recipe_match_count", 0)
                - before.get("exact_recipe_match_count", 0)
                if type(before.get("exact_recipe_match_count")) is int
                and type(after.get("exact_recipe_match_count")) is int
                else None
            ),
            "competing_input_match_count": (
                after.get("competing_input_match_count", 0)
                - before.get("competing_input_match_count", 0)
                if type(before.get("competing_input_match_count")) is int
                and type(after.get("competing_input_match_count")) is int
                else None
            ),
            "unresolved_input_expansion_count": (
                after.get("unresolved_input_expansion_count", 0)
                - before.get("unresolved_input_expansion_count", 0)
                if type(before.get("unresolved_input_expansion_count")) is int
                and type(after.get("unresolved_input_expansion_count")) is int
                else None
            ),
        },
        "claims": {
            "exact_registration_change_observed": not failed,
            "recipe_executed": False,
            "player_progression_reachable": False,
            "stable_support": False,
        },
        "limitations": list(_LIMITATIONS),
    }


def validate_recipe_change_comparison(
    baseline_spec: RecipeChangeProbeSpec,
    baseline_assessment: Mapping[str, Any],
    candidate_spec: RecipeChangeProbeSpec,
    candidate_assessment: Mapping[str, Any],
    comparison: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate a retained comparison by deterministic full re-derivation."""

    if type(comparison) is not dict:
        _fail("recipe-change comparison must be one ordinary object")
    expected = compare_recipe_change_observations(
        baseline_spec,
        baseline_assessment,
        candidate_spec,
        candidate_assessment,
    )
    if dict(comparison) != expected:
        _fail("recipe-change comparison differs from deterministic derivation")
    return dict(comparison)


__all__ = [
    "ASSESSMENT_FORMAT",
    "COMPARISON_FORMAT",
    "MARKER_PREFIX",
    "RecipeChangeProbeSpec",
    "RecipeChangeRuntimeObservationError",
    "build_recipe_change_probe",
    "build_recipe_change_probe_overlay",
    "compare_recipe_change_observations",
    "derive_recipe_change_probe_spec",
    "interpret_recipe_change_observation",
    "validate_recipe_change_assessment",
    "validate_recipe_change_comparison",
]
