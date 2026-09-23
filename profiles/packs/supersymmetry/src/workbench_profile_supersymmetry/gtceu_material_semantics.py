#!/usr/bin/env python3

"""Composed source/runtime material semantics for the pinned legacy profile."""

from __future__ import annotations

from workbench_material_semantics import (
    ConditionalFlagRule,
    FlagDefinition,
    FlagPresetDefinition,
    MaterialSemanticLayer,
    MaterialSymbolDefinition,
    MaterialSemanticsPolicy,
    SemanticConstant,
    SemanticSourceFile,
    ValueCondition,
)

from workbench_profile_supersymmetry.gtceu_material_classification import GTCEU_MATERIAL_CLASSIFICATION_POLICY


GT_FLAGS = "gregtech.api.unification.material.info.MaterialFlags"
GT_MATERIALS = "gregtech.api.unification.material.Materials"
GT_VALUES = "gregtech.api.GTValues"
GT_MATERIAL_TYPE = "gregtech.api.unification.material.Materials"
SUSY_FLAGS = (
    "supersymmetry.api.unification.material.info.SuSyMaterialFlags"
)
GCYM_FLAGS = "gregicality.multiblocks.api.unification.GCYMMaterialFlags"


def _base_flag_definitions() -> tuple[FlagDefinition, ...]:
    policy = GTCEU_MATERIAL_CLASSIFICATION_POLICY
    categories = dict(policy.flag_categories)
    dependencies = dict(policy.flag_dependencies)
    requirements = dict(policy.flag_property_requirements)
    names = sorted(
        set(categories)
        | set(dependencies)
        | {name for values in dependencies.values() for name in values}
        | set(requirements)
        | {
            name
            for _, values in policy.property_implied_flags
            for name in values
        }
    )
    return tuple(
        FlagDefinition(
            name=name,
            declaring_type=GT_FLAGS,
            source_symbol=name.upper(),
            categories=tuple(categories.get(name, ("profile_extension",))),
            required_flags=tuple(dependencies.get(name, ())),
            required_properties=tuple(requirements.get(name, ())),
        )
        for name in names
    )


_GT_SOURCE_FILES = (
    SemanticSourceFile(
        "src/main/java/gregtech/api/GTValues.java",
        "f2861c31315f5e556705927a63e7649ef0c6ca1c0a420170af38a99146b15675",
    ),
    SemanticSourceFile(
        "src/main/java/gregtech/api/unification/material/Material.java",
        "46b7cf9c92c06fb3c6db97b06e71f3b826d30d2889f417909418bd406245425e",
    ),
    SemanticSourceFile(
        "src/main/java/gregtech/api/unification/material/Materials.java",
        "aa8d1403441faef12ded7cc6127e322107f9c6cfef52ba27e1c9f3480ca7e6a0",
    ),
    SemanticSourceFile(
        "src/main/java/gregtech/api/unification/material/info/MaterialFlags.java",
        "bc61f3d88dd284288e1a95c8847146409fbc08d6db9027e2c5173e483cdaa660",
    ),
    SemanticSourceFile(
        "src/main/java/gregtech/api/unification/material/materials/ElementMaterials.java",
        "be3b532e40e87d05af4324b320367c89fd128c7bdd0f286c6d2e36db23594ac9",
    ),
    SemanticSourceFile(
        "src/main/java/gregtech/api/unification/material/materials/FirstDegreeMaterials.java",
        "5a0cf3112434988131dc0d28811440047719bc5c937ac050744da49be6f9fb3c",
    ),
    SemanticSourceFile(
        "src/main/java/gregtech/api/unification/material/materials/HigherDegreeMaterials.java",
        "7b86e33fe22da4830d228d1e78477a23d78af6138fcd14a76eb00ac6d59b7c8e",
    ),
    SemanticSourceFile(
        "src/main/java/gregtech/api/unification/material/materials/MaterialFlagAddition.java",
        "94c7943eefb3986c40c7eeed1a92970330ef5742ea90eabdd6cdd50c895c3e0e",
    ),
    SemanticSourceFile(
        "src/main/java/gregtech/api/unification/material/materials/OrganicChemistryMaterials.java",
        "bca7a0876ff7a1fdbd768da92cdbda7c686a1e4b398c256d6df66c4b7179c075",
    ),
    SemanticSourceFile(
        "src/main/java/gregtech/api/unification/material/materials/SecondDegreeMaterials.java",
        "e6c2604bc8d6d6827fe336b7dc88da736f2b2ed37918bdddca7e154d66755eed",
    ),
    SemanticSourceFile(
        "src/main/java/gregtech/api/unification/material/materials/UnknownCompositionMaterials.java",
        "2a70b558408731e7777662347932d8e42202e1e4e8ea7373adb0a175a185fbfe",
    ),
    SemanticSourceFile(
        "src/main/java/gregtech/api/unification/material/properties/WireProperties.java",
        "707ba6cef1c0f3d12182305737a5b6f6861c0a373b718fd84621e15c4b6abcb7",
    ),
)


_GT_MATERIAL_SYMBOL_NAMES = tuple(sorted("""
Actinium Air Aluminium Americium Ammonia Antimony AntimonyTrifluoride AquaRegia Argon Arsenic
Asbestos Barium Barite Bastnasite Benzene Beryllium Bismuth BisphenolA BlackSteel Borax Boron
Brass Bromine Butadiene Butane Butene Cadmium Caesium Calcium Carbon
CarbonDioxide CarbonMonoxide Cassiterite Cerium Chlorine Chrome Cinnabar
ChromiumTrioxide CoalGas Cobalt Copper CupricOxide Diatomite
Diaminobenzidine Dichlorobenzene Dichlorobenzidine DilutedHydrochloricAcid
DilutedSulfuricAcid DiphenylIsophtalate Dysprosium Erbium Ethane Ethanol
Ethylene Europium Fluorine Gadolinium Gallium Germanium Gold Graphite Gypsum
Galena Glass Hafnium Helium Holmium Hydrogen Ilmenite Indium IndiumGalliumPhosphide
IndiumTinBariumTitaniumCuprate Iodine Iridium Iron Iron3Chloride IronMagnetic
Krypton Lanthanum Lead Lepidolite Lithium LithiumChloride Lubricant Lutetium Magnesia Magnalium
Magnesium MagnesiumDiboride Manganese ManganesePhosphide Mercury
Malachite MercuryBariumCalciumCuprate Methane Methanol Mica Molybdenite Molybdenum
Monazite Naphtha NaturalGas Neodymium Neon NetherAir Nickel Niobium Nitrogen
Nitrochlorobenzene Osmium OsmiumTetroxide Oxygen Palladium Pentlandite Phenol Phosphorus PhthalicAcid
Pitchblende Platinum Pollucite Polonium Polybenzimidazole Polycaprolactam
Polydimethylsiloxane Polyethylene PolyphenyleneSulfide Polytetrafluoroethylene
PolychlorinatedBiphenyl PolyvinylAcetate PolyvinylButyral PolyvinylChloride Potassium Praseodymium
Propane Propene Pyrochlore RefineryGas ReinforcedEpoxyResin Rhenium Rhodium
Rubber Rubidium Ruthenium RutheniumTriniumAmericiumNeutronate Rutile SaltWater
Samarium SamariumIronArsenicOxide Scandium Scheelite Selenium Silicon SiliconDioxide
SiliconeRubber Silver Sodium SodiumPotassium StainlessSteel Steel SteelMagnetic
SodiumHydroxide Spodumene Sphalerite Stibnite Strontium StyreneButadieneRubber Sulfur Tantalite Tantalum Technetium Tellurium
Terbium Tetrahedrite Thallium Thorium Thulium Tin Titanium Toluene Tungsten
TungstenCarbide TungstenSteel UraniumRhodiumDinaquadide UraniumTriplatinum
Vanadium VanadiumSteel Water WoodGas Xenon Ytterbium Yttrium Zinc Zirconium
""".split()))


# These are source-declared registry names, not naming-convention guesses.
_GT_MATERIAL_REGISTRY_NAME_OVERRIDES = {
    "DiphenylIsophtalate": "diphenyl_isophthalate",
    "Iron3Chloride": "iron_iii_chloride",
    "Polyethylene": "plastic",
}


def _lower_underscore(symbol: str) -> str:
    output: list[str] = []
    for index, character in enumerate(symbol):
        if character.isupper() and index and (
            symbol[index - 1].islower()
            or (index + 1 < len(symbol) and symbol[index + 1].islower())
        ):
            output.append("_")
        output.append(character.lower())
    return "".join(output)


_GT_LAYER = MaterialSemanticLayer(
    layer_id="gregtech-ce-unofficial-2.8.10",
    owner="GregTechCEu",
    source_id="SRC-GTCEU",
    revision="9fe140febe8747bbe2f06dfd570421331ec06f4b",
    tree="a2e4c580930c853c7211ec8fcab4031365464ce9",
    source_files=_GT_SOURCE_FILES,
    flags=_base_flag_definitions(),
    presets=(
        FlagPresetDefinition(GT_MATERIALS, "EXT2_METAL", (
            "generate_plate",
            "generate_rod",
            "generate_long_rod",
            "generate_bolt_screw",
        )),
        FlagPresetDefinition(GT_MATERIALS, "EXT_METAL", (
            "generate_plate",
            "generate_rod",
        )),
        FlagPresetDefinition(GT_MATERIALS, "STD_METAL", ("generate_plate",)),
    ),
    material_symbols=tuple(
        MaterialSymbolDefinition(
            GT_MATERIAL_TYPE,
            symbol,
            "gregtech:"
            + _GT_MATERIAL_REGISTRY_NAME_OVERRIDES.get(
                symbol, _lower_underscore(symbol)
            ),
        )
        for symbol in _GT_MATERIAL_SYMBOL_NAMES
    ),
)


_GCYM_LAYER = MaterialSemanticLayer(
    layer_id="gregicality-multiblocks-supersymmetry-lock",
    owner="Gregicality Multiblocks",
    source_id="SRC-GREGICALITY-MULTIBLOCKS",
    revision="54168e0dc6d55089469be43eb4ab7517776972b2",
    tree="34191d70832f70b26f4c736ecc0a52435ceb9b04",
    flags=(
        FlagDefinition(
            "no_alloy_blast_recipes",
            GCYM_FLAGS,
            "NO_ALLOY_BLAST_RECIPES",
            ("profile_extension", "restriction"),
        ),
    ),
)


_SUSY_LAYER = MaterialSemanticLayer(
    layer_id="susy-core-b5ee1120-material-flags",
    owner="SuSy Core",
    source_id="SRC-SUSYCORE",
    revision="b5ee1120df93c95b66e6a4dfff35582928f2d348",
    tree="1f5877f5acad37fe1a6e0aaa7270d579f570ff92",
    source_files=(
        SemanticSourceFile(
            "src/main/java/supersymmetry/api/unification/material/info/SuSyMaterialFlags.java",
            "9094b1610abd8e819ea016c03c471b88f04896af0ac55386a3a74ac960b98594",
        ),
    ),
    flags=tuple(sorted((
        FlagDefinition(
            "GENERATE_SPUTTERING_TARGET",
            SUSY_FLAGS,
            "GENERATE_SPUTTERING_TARGET",
            ("form_generation", "profile_extension"),
            required_properties=("dust",),
        ),
        FlagDefinition(
            "continuously_cast",
            SUSY_FLAGS,
            "CONTINUOUSLY_CAST",
            ("process_policy", "profile_extension"),
            required_flags=("no_alloy_blast_recipes",),
            required_properties=("blast_alloy", "dust", "fluid"),
        ),
        FlagDefinition(
            "generate_catalyst_bed",
            SUSY_FLAGS,
            "GENERATE_CATALYST_BED",
            ("form_generation", "profile_extension"),
            required_flags=("generate_catalyst_pellet",),
            required_properties=("dust",),
        ),
        FlagDefinition(
            "generate_catalyst_pellet",
            SUSY_FLAGS,
            "GENERATE_CATALYST_PELLET",
            ("form_generation", "profile_extension"),
            required_properties=("dust",),
        ),
        FlagDefinition(
            "generate_concentrate",
            SUSY_FLAGS,
            "GENERATE_CONCENTRATE",
            ("form_generation", "profile_extension"),
            required_properties=("ore",),
        ),
        FlagDefinition(
            "generate_fiber",
            SUSY_FLAGS,
            "GENERATE_FIBER",
            ("form_generation", "profile_extension"),
            required_properties=("fiber",),
        ),
        FlagDefinition(
            "generate_flotated",
            SUSY_FLAGS,
            "GENERATE_FLOTATED",
            ("form_generation", "profile_extension"),
            required_properties=("ore",),
        ),
        FlagDefinition(
            "generate_pins",
            SUSY_FLAGS,
            "GENERATE_PINS",
            ("form_generation", "profile_extension"),
            required_flags=("generate_plate",),
            required_properties=("dust",),
        ),
        FlagDefinition(
            "generate_sifted",
            SUSY_FLAGS,
            "GENERATE_SIFTED",
            ("form_generation", "profile_extension"),
            required_properties=("ore",),
        ),
        FlagDefinition(
            "generate_thread",
            SUSY_FLAGS,
            "GENERATE_THREAD",
            ("form_generation", "profile_extension"),
            required_properties=("fiber",),
        ),
        FlagDefinition(
            "generate_wet_dust",
            SUSY_FLAGS,
            "GENERATE_WET_DUST",
            ("form_generation", "profile_extension"),
            required_properties=("dust",),
        ),
        FlagDefinition(
            "generate_wet_fiber",
            SUSY_FLAGS,
            "GENERATE_WET_FIBER",
            ("form_generation", "profile_extension"),
            required_properties=("fiber",),
        ),
        FlagDefinition(
            "hip_pressed",
            SUSY_FLAGS,
            "HIP_PRESSED",
            ("process_policy", "profile_extension"),
            required_flags=("no_smelting", "no_working"),
            required_properties=("dust",),
        ),
        FlagDefinition(
            "superalloy",
            SUSY_FLAGS,
            "SUPERALLOY",
            ("process_policy", "profile_extension"),
            required_flags=("hip_pressed",),
            required_properties=("dust",),
        ),
    ), key=lambda flag: flag.name.encode("utf-8"))),
)


GTCEU_SUPERSYMMETRY_MATERIAL_SEMANTICS_V2 = MaterialSemanticsPolicy(
    policy_id=(
        "workbench://profiles/supersymmetry/material-semantics/"
        "gtceu-2.8.10-susycore-b5ee1120-v2"
    ),
    pack_profile_id="workbench-pack:supersymmetry",
    runtime_policy_id=(
        "workbench://profiles/supersymmetry/atlas/material-classification/"
        "gtceu-2.8.10-susycore-b5ee1120-v2"
    ),
    base_runtime_policy=GTCEU_MATERIAL_CLASSIFICATION_POLICY,
    layers=tuple(sorted(
        (_GCYM_LAYER, _GT_LAYER, _SUSY_LAYER),
        key=lambda layer: layer.layer_id.encode("utf-8"),
    )),
    constants=(
        SemanticConstant(GT_VALUES, "IV", "int", 5),
        SemanticConstant(
            GT_VALUES,
            "V",
            "long-array",
            (
                8,
                32,
                128,
                512,
                2048,
                8192,
                32768,
                131072,
                524288,
                2097152,
                8388608,
                33554432,
                134217728,
                536870912,
                2147483647,
            ),
        ),
    ),
    conditional_flag_rules=(
        ConditionalFlagRule(
            rule_id=(
                "workbench://profiles/supersymmetry/material-rules/"
                "gtceu-2.8.10-wire-foil"
            ),
            trigger_operation="cableProperties",
            required_properties=("ingot", "wire"),
            conditions=(
                ValueCondition(
                    "integer-greater-than-or-equal",
                    argument_index=0,
                    expected=8192,
                    java_coercion="signed-int32",
                ),
                ValueCondition(
                    "boolean-equals",
                    argument_index=3,
                    expected=False,
                    default=False,
                ),
            ),
            added_flags=("generate_foil",),
            source_files=tuple(
                source
                for source in _GT_SOURCE_FILES
                if source.path.endswith("WireProperties.java")
                or source.path.endswith("GTValues.java")
                or source.path.endswith("material/Material.java")
            ),
        ),
    ),
)


GTCEU_SUPERSYMMETRY_MATERIAL_CLASSIFICATION_POLICY_V2 = (
    GTCEU_SUPERSYMMETRY_MATERIAL_SEMANTICS_V2.runtime_policy()
)


__all__ = [
    "GTCEU_SUPERSYMMETRY_MATERIAL_CLASSIFICATION_POLICY_V2",
    "GTCEU_SUPERSYMMETRY_MATERIAL_SEMANTICS_V2",
]
