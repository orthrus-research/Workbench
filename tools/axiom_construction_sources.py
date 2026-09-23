"""Source-retaining native material construction, not a pack bootstrap.

Every property/builder body is retained. External prefix, enchantment, fluid
identity and presentation dependencies are explicit; no Minecraft namespace
classes or placeholder registry entries are generated.
"""
import re

from axiom_fluid_sources import PACKAGE, clean, finish, member

GT = "src/main/java/gregtech/"
PRODUCER_PATHS = {
    "catalog": GT + "api/unification/material/Materials.java",
    "elements": GT + "api/unification/material/materials/ElementMaterials.java",
}
PATHS = {
    "Element": GT + "api/unification/Element.java",
    "Elements": GT + "api/unification/Elements.java",
    "MaterialStack": GT + "api/unification/stack/MaterialStack.java",
    "SmallDigits": GT + "api/util/SmallDigits.java",
    "EnchantmentLevel": GT + "api/items/toolitem/EnchantmentLevel.java",
    "MarkerMaterial": GT + "api/unification/material/MarkerMaterial.java",
    "MarkerMaterialRegistry": GT + "api/unification/material/registry/MarkerMaterialRegistry.java",
    **{name: GT + "api/unification/material/properties/" + name + ".java" for name in (
        "WoodProperty", "PolymerProperty", "OreProperty", "ToolProperty", "RotorProperty",
        "WireProperties", "ItemPipeProperties", "FluidPipeProperties")},
    "FluidAttributes": GT + "api/fluids/attribute/FluidAttributes.java",
    "MaterialVoltages": GT + "api/GTValues.java",
}


def extract(name, source):
    if name not in PATHS:
        raise ValueError("unadmitted construction source: " + name)
    if name == "MaterialVoltages":
        declarations = []
        for marker in ("public static final long M =", "public static final String[] VN =",
                       "public static final long[] V =", "public static final int[] VA =", "public static final int ULV =",
                       "public static final int LV =", "public static final int MV =",
                       "public static final int HV =", "public static final int EV =",
                       "public static final int IV =", "public static final int LuV =",
                       "public static final int ZPM =", "public static final int UV =",
                       "public static final int UHV =", "public static final int UEV =",
                       "public static final int UIV =", "public static final int UXV =",
                       "public static final int OpV =", "public static final int MAX ="):
            if source.count(marker) != 1:
                raise ValueError("voltage declaration boundary differs: " + marker)
            start = source.index(marker)
            declarations.append(source[start:source.index(";", start) + 1])
        return finish(PACKAGE + "final class MaterialVoltages {\n" + "\n".join(declarations) + "\n}\n")
    text = clean(source)
    # Additional script/client annotations have no role in the native Java body.
    text = re.sub(r"@(?:ZenProperty|UnmodifiableView)(?:\([^\n]*?\))?", "", text)
    if name == "Elements":
        text = text.replace(PACKAGE, PACKAGE + "import com.google.common.base.CaseFormat;\n")
    if name == "MarkerMaterial":
        text = text.replace("GregTechAPI.markerMaterialRegistry", "FluidEnvironment.current().markers()")
    if name == "MarkerMaterialRegistry":
        text = text.replace("    private static MarkerMaterialRegistry INSTANCE;", "")
        text = text.replace(member(text, "public static MarkerMaterialRegistry getInstance()"), "")
        text = text.replace("private MarkerMaterialRegistry()", "MarkerMaterialRegistry()")
    if name == "OreProperty":
        text = text.replace("MathHelper.clamp(index, 0, this.oreByProducts.size() - 1)",
                            "ConstructionDependencies.clamp(index, 0, this.oreByProducts.size() - 1)")
    if name == "ToolProperty":
        # This class only stores keys; it invokes no Enchantment methods. An opaque
        # key is not an invented vanilla enchantment or an enchantment registry.
        text = re.sub(r"\bEnchantment\b", "EnchantmentIdentity", text)
    if name in ("WoodProperty", "PolymerProperty"):
        text = text.replace("properties.getMaterial().addFlags", "FluidMaterial.require(properties.getMaterial()).addFlags")
    if name == "WoodProperty":
        first = "OrePrefix.pipeTinyFluid.setIgnored(properties.getMaterial())"
        if text.count(first) != 1:
            raise ValueError("wood prefix dependency boundary differs")
        text = text.replace(first, "PrefixDependencies.requireInputs();\n            " + first)
        # Preserve the actual static prefix fields, even if its live map entry is
        # removed/replaced. Looking up the same name is not equivalent identity.
        text = text.replace(".setIgnored(properties.getMaterial())", ".setIgnored(FluidMaterial.require(properties.getMaterial()))")
    if name == "WireProperties":
        text = text.replace("properties.getMaterial();", "FluidMaterial.require(properties.getMaterial());")
        text = text.replace("GTValues.", "MaterialVoltages.")
        text = text.replace("import static gregtech.api.unification.material.info.MaterialFlags.GENERATE_FOIL;",
                            "import static research.orthrus.axiom.MaterialFlags.GENERATE_FOIL;")
    if name == "FluidPipeProperties":
        # Only construction/state here. The inherited stack filter is not exposed.
        text = text.replace(", IPropertyFluidFilter", "").replace("@Override", "")
        text = text.replace("FluidAttributes.ACID", "FluidEnvironment.current().attributes().ACID")
    if name == "FluidAttributes":
        text = text.replace("public static final FluidAttribute", "public final FluidAttribute")
        text = text.replace("private FluidAttributes()", "FluidAttributes()")
        text = text.replace("I18n.format(", "ConstructionDependencies.localize(")
        text = text.replace("import static gregtech.api.util.FluidSupport.gregtechId;",
                            "import static research.orthrus.axiom.FluidSupport.gregtechId;")
    text = re.sub(r"public (final )?class " + name + r"\b", lambda m: (m[1] or "") + "class " + name, text, count=1)
    return finish(text)


def material(source):
    """Full builder and MaterialInfo plus construction-relevant instance methods.

    Native fluid-stack creation uses registered delegates. No world/localization API is implied. Marker construction uses
    the actual interning registry, without claiming the full marker-color catalog.
    """
    methods = [member(source, marker) for marker in (
        "private String calculateChemicalFormula()", "public String getChemicalFormula()",
        "public Material setFormula(String formula)", "public Material setFormula(String formula, boolean withFormatting)",
        "public ImmutableList<MaterialStack> getMaterialComponents()", "protected void registerMaterial()",
        "public void addFlags(MaterialFlag... flags)", "public void addFlags(String... names)",
        "public boolean hasFlag(MaterialFlag flag)", "public boolean isElement()", "public Element getElement()",
        "public boolean hasFlags(MaterialFlag... flags)", "public boolean hasAnyOfFlags(MaterialFlag... flags)",
        "protected void calculateDecompositionType()", "public Fluid getFluid()",
        "public Fluid getFluid(@NotNull FluidStorageKey key)", "public FluidStack getFluid(int amount)",
        "public FluidStack getFluid(@NotNull FluidStorageKey key, int amount)", "public FluidStack getPlasma(int amount)",
        "public int getBlockHarvestLevel()",
        "public int getToolHarvestLevel()", "public void setMaterialRGB(int materialRGB)",
        "public int getMaterialRGB()", "public void setMaterialIconSet(MaterialIconSet materialIconSet)",
        "public MaterialIconSet getMaterialIconSet()", "public boolean isRadioactive()", "public long getProtons()",
        "public long getNeutrons()", "public long getMass()", "public int getBlastTemperature()",
        "public String getName()", "public String getModid()", "public ResourceLocation getResourceLocation()",
        "public String toCamelCaseString()", "public String getUnlocalizedName()", "public String getRegistryName()",
        "public int compareTo(Material material)", "public String toString()", "public int getId()",
        "public MaterialStack multiply(long amount)", "public MaterialProperties getProperties()",
        "public <T extends IMaterialProperty> boolean hasProperty(",
        "public <T extends IMaterialProperty> T getProperty(",
        "public <T extends IMaterialProperty> void setProperty(", "public boolean isSolid()",
        "public boolean hasFluid()", "public void verifyMaterial()",
    )]
    text = PACKAGE + """import java.util.*;
import java.util.function.Consumer;
import java.util.function.UnaryOperator;
import com.google.common.collect.ImmutableList;
class FluidMaterial extends MaterialState implements Comparable<FluidMaterial> {
    private final MaterialInfo materialInfo;
    private final MaterialFlags flags;
    private String chemicalFormula;
    private FluidMaterial(MaterialInfo info, MaterialProperties properties, MaterialFlags flags) {
        super(info.metaItemSubId, info.resourceLocation.getNamespace(), info.resourceLocation.getPath(),
              FluidEnvironment.current().runtime().materials()::canModifyMaterials, properties);
        this.materialInfo = info; this.flags = flags;
        registerMaterial();
    }
    // Original marker constructor deliberately leaves the property owner and
    // component list unset and performs no material registration/verification.
    protected FluidMaterial(NativeLocation location) {
        super(0, location.getNamespace(), location.getPath(),
              FluidEnvironment.current().runtime().materials()::canModifyMaterials,
              new MaterialProperties(), false);
        materialInfo = new MaterialInfo(0, location);
        materialInfo.iconSet = MaterialIconSet.DULL;
        flags = new MaterialFlags();
    }
    static FluidMaterial require(MaterialState value) {
        if (!(value instanceof FluidMaterial material)) throw Failure.unsupported("fluid.material", "Native material metadata is required");
        return material;
    }
""" + "\n".join(methods) + "\n" + member(source, "public static class Builder") + "\n" + member(source, "private static class MaterialInfo") + "\n}\n"
    text = clean(text)
    # clean intentionally removes upstream imports; this is the selected native
    # Guava, including its copy/overload semantics, not List.copyOf substitution.
    text = text.replace(PACKAGE, PACKAGE + "import com.google.common.collect.ImmutableList;\n")
    text = text.replace("GregTechAPI.materialManager", "FluidEnvironment.current().runtime().materials()")
    text = text.replace("FluidStorageKeys.", "FluidEnvironment.current().storageKeys().")
    text = text.replace("FluidTooltipUtil.registerTooltip(fluid, FluidTooltipUtil.createFluidTooltip(m, fluid, state))",
                        "FluidEnvironment.current().registerTooltip(fluid, m, state)")
    text = re.sub(r"\bEnchantment\b", "EnchantmentIdentity", text)
    text = re.sub(r"\bFluidStack\b", "NativeFluidStack", text)
    return finish(text)


def producer_sources(originals):
    """Complete ElementMaterials producer, with native static catalog initialization.

    The catalog's register entrypoint is deliberately not compiled: it requires
    all later producers/ore-prefix initialization and is not this probe's scope.
    No declaration within ElementMaterials.register is removed or filtered.
    """
    catalog = originals[PRODUCER_PATHS["catalog"]]
    catalog = catalog.replace(member(catalog, "public static void register()"), "")
    catalog = clean(catalog)
    catalog = catalog.replace("public class Materials", "class ConstructionMaterialCatalog")
    catalog = catalog.replace("import static gregtech.api.unification.material.info.MaterialFlags.*;",
                              "import static research.orthrus.axiom.MaterialFlags.*;")
    producer = clean(originals[PRODUCER_PATHS["elements"]])
    producer = producer.replace("public class ElementMaterials", "class SourceElementProducer")
    for old, new in {
        "import static gregtech.api.GTValues.*;": "import static research.orthrus.axiom.MaterialVoltages.*;",
        "import static gregtech.api.unification.material.Materials.*;": "import static research.orthrus.axiom.ConstructionMaterialCatalog.*;",
        "import static gregtech.api.unification.material.info.MaterialFlags.*;": "import static research.orthrus.axiom.MaterialFlags.*;",
        "import static gregtech.api.unification.material.info.MaterialIconSet.*;": "import static research.orthrus.axiom.MaterialIconSet.*;",
        "import static gregtech.api.util.FluidSupport.gregtechId;": "import static research.orthrus.axiom.FluidSupport.gregtechId;",
        "GTValues.": "MaterialVoltages.",
        "FluidStorageKeys.": "FluidEnvironment.current().storageKeys().",
    }.items():
        producer = producer.replace(old, new)
    producer = producer.replace(PACKAGE, PACKAGE + "import research.orthrus.axiom.BlastProperty.GasTier;\n")
    return {"ConstructionMaterialCatalog": catalog, "SourceElementProducer": producer}
