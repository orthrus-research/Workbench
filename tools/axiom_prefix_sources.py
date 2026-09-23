"""Retain GT prefix/marker metadata and state, with explicit catalog/config ports.

No vanilla enums, synthetic materials, game registry or client renderer are
generated. Minecraft dye metadata executes separately supplied native bytecode.
"""
import re

from axiom_fluid_sources import PACKAGE, clean, finish, member

GT = "src/main/java/gregtech/"
PATHS = {
    "OrePrefix": GT + "api/unification/ore/OrePrefix.java",
    "IOreRegistrationHandler": GT + "api/unification/ore/IOreRegistrationHandler.java",
    "TriConsumer": GT + "api/util/function/TriConsumer.java",
    "MarkerMaterials": GT + "api/unification/material/MarkerMaterials.java",
    "MaterialIconType": GT + "api/unification/material/info/MaterialIconType.java",
}


def extract(name, source):
    if name not in PATHS:
        raise ValueError("unadmitted prefix source: " + name)
    if name == "MaterialIconType":
        # Metadata only. No resource-pack caches or client asset lookup API.
        start = source.index("    public static final Map<String, MaterialIconType>")
        end = source.index("    private static final Table<MaterialIconType")
        source = PACKAGE + "import java.util.*;\nclass MaterialIconType {\n" + source[start:end] + "\n" + \
            "public final String name;\npublic final int id;\n" + \
            member(source, "public MaterialIconType(String name)") + "\n" + \
            member(source, "public String toString()") + '''
    // Existing server-side identifier projection; not client resource-pack fallback.
    NativeLocation getBlockTexturePath(MaterialIconSet iconSet) {
        return FluidSupport.gregtechId(String.format("blocks/material_sets/%s/%s", iconSet.name, name));
    }
}
'''
    if name == "OrePrefix":
        # The source's rendering methods are outside the metadata/state surface.
        for marker in ("public String getLocalNameForItem(", "private String findUnlocalizedName("):
            source = source.replace(member(source, marker), "")
        source = re.sub(r"\bMaterials\.(\w+)", r'PrefixDependencies.material("\1")', source)
        source = source.replace("ConfigHolder.recipes.generateLowQualityGems", "PrefixDependencies.generateLowQualityGems()")
        source = source.replace("ConfigHolder.worldgen.allUniqueStoneTypes", "PrefixDependencies.allUniqueStoneTypes()")
        source = source.replace("I18n.format(", "ConstructionDependencies.localize(")
    text = clean(source)
    if name in ("OrePrefix", "MaterialIconType"):
        text = text.replace(PACKAGE, PACKAGE + "import com.google.common.base.Preconditions;\nimport com.google.common.base.CaseFormat;\n")
    if name == "OrePrefix":
        for old, new in {
            "import static gregtech.api.GTValues.M;": "import static research.orthrus.axiom.MaterialVoltages.M;",
            "import static gregtech.api.unification.material.info.MaterialFlags.*;": "import static research.orthrus.axiom.MaterialFlags.*;",
            "import static gregtech.api.unification.ore.OrePrefix.": "import static research.orthrus.axiom.OrePrefix.",
        }.items():
            text = text.replace(old, new)
    if name == "MarkerMaterials":
        text = text.replace(PACKAGE, PACKAGE + "import com.google.common.collect.HashBiMap;\n")
        text = re.sub(r"\bEnumDyeColor\b", "NativeDyeColor", text).replace("GTValues.", "MaterialVoltages.")
    text = re.sub(r"public (class|interface) " + name + r"\b", r"\1 " + name, text, count=1)
    return finish(text)
