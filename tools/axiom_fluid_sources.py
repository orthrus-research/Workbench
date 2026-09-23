"""Audited, hash-bound extraction of native headless fluid construction.

Not an upstream namespace compatibility layer. Excluded world/loader operations
remain unavailable; an isolated producer universe is not a bootstrapped pack.
"""
import re

PACKAGE = "package research.orthrus.axiom;\n"
NOTICE = "// Extracted from pinned GTCEu/Cleanroom source. See spec/native-fluids.md and sources/NOTICE.md.\n"
GT = "src/main/java/gregtech/"
PATHS = {
    "FluidConstants": GT + "api/fluids/FluidConstants.java",
    "FluidState": GT + "api/fluids/FluidState.java",
    "FluidAttribute": GT + "api/fluids/attribute/FluidAttribute.java",
    "AttributedFluid": GT + "api/fluids/attribute/AttributedFluid.java",
    "GTFluid": GT + "api/fluids/GTFluid.java",
    "FluidStorageKey": GT + "api/fluids/store/FluidStorageKey.java",
    "FluidStorageKeys": GT + "api/fluids/store/FluidStorageKeys.java",
    "FluidProperty": GT + "api/unification/material/properties/FluidProperty.java",
    "FluidBuilder": GT + "api/fluids/FluidBuilder.java",
    "FluidUnifier": GT + "api/unification/FluidUnifier.java",
    "MaterialFlag": GT + "api/unification/material/info/MaterialFlag.java",
    "MaterialFlags": GT + "api/unification/material/info/MaterialFlags.java",
    "MaterialIconSet": GT + "api/unification/material/info/MaterialIconSet.java",
    "BlastProperty": GT + "api/unification/material/properties/BlastProperty.java",
    "FluidMaterial": GT + "api/unification/material/Material.java",
    "FluidSupport": GT + "api/util/GTUtility.java",
    "FluidRegistrationService": GT + "api/fluids/GTFluidRegistration.java",
}
FORGE = {
    "NativeFluid": "src/main/java/net/minecraftforge/fluids/Fluid.java",
    "FluidRegistryState": "src/main/java/net/minecraftforge/fluids/FluidRegistry.java",
}


def member(source, marker):
    if source.count(marker) != 1:
        raise ValueError("fluid source member boundary differs: " + marker)
    start = source.index(marker)
    masked = re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//[^\n]*|/\*[\s\S]*?\*/',
                    lambda match: " " * len(match[0]), source)
    opening = masked.index("{", start)
    depth = 1
    end = opening + 1
    while depth and end < len(masked):
        depth += (masked[end] == "{") - (masked[end] == "}")
        end += 1
    if depth:
        raise ValueError("unterminated source member: " + marker)
    return source[start:end]


def clean(text):
    # Preserve every literal, including embedded type names in diagnostics.
    literals = []
    def literal(match):
        literals.append(match[0])
        return "AXIOM_LITERAL_" + str(len(literals) - 1) + "_END"
    text = re.sub(r'"(?:\\.|[^"\\])*"', literal, text)
    text = re.sub(r"^package [^;]+;", PACKAGE.rstrip(), text, count=1, flags=re.MULTILINE)
    text = re.sub(r"^import (?:gregtech\.|net\.minecraft|org\.jetbrains|crafttweaker|stanhebben|com\.google|io\.github)[^;]+;\n", "", text, flags=re.MULTILINE)
    text = re.sub(r"@(?:NotNull|Nullable|UnmodifiableView|Unmodifiable|ZenRegister|ZenMethod|ZenGetter|ZenClass|ZenOperator|SideOnly|ApiStatus\.[A-Za-z]+)\b(?:\([^\n]*?\))?", "", text)
    for old, new in {"ResourceLocation": "NativeLocation", "Material": "FluidMaterial", "Fluid": "NativeFluid"}.items():
        text = re.sub(r"\b" + old + r"\b", new, text)
    for key in ("FLUID", "BLAST"):
        text = re.sub(r"\bPropertyKey\." + key + r"\b", "FluidDomain." + key, text)
    text = text.replace("GTUtility.", "FluidSupport.").replace("Preconditions.checkArgument", "FluidSupport.checkArgument")
    text = text.replace("GTLog.logger", "FluidEnvironment.current().logger()")
    return re.sub(r"AXIOM_LITERAL_(\d+)_END", lambda match: literals[int(match[1])], text)


def finish(text):
    return NOTICE + "\n".join(line.rstrip() for line in text.splitlines()) + "\n"


def extract(name, source):
    if name in FORGE:
        header = source[:source.index("package net.minecraftforge.fluids;")]
        return header + forge(name, source)
    if name not in PATHS:
        raise ValueError("unadmitted fluid source: " + name)
    if name == "FluidSupport":
        methods = [member(source, m) for m in ("public static int convertRGBtoARGB(int colorValue)",
                   "public static int convertRGBtoARGB(int colorValue, int opacity)",
                   "public static String toLowerCaseUnderscore(", "public static String lowerUnderscoreToUpperCamel(")]
        return finish(PACKAGE + "final class FluidSupport {\n" + "\n".join(methods) + '''
    static NativeLocation gregtechId(String path) { return new NativeLocation("gregtech", path); }
    static void checkArgument(boolean condition, String message) { if (!condition) throw new IllegalArgumentException(message); }
}
''')
    if name == "FluidMaterial":
        return material(source)
    if name == "FluidRegistrationService":
        fix = member(source, "private static void fixFluidRegistryName(")
        # Reflection is an implementation mechanism; retain the actual maps it exposes.
        start, end = fix.index("        if (MASTER_FLUID_REFERENCE == null)"), fix.index("        String masterKey")
        fix = fix[:start] + '''        var MASTER_FLUID_REFERENCE = FluidEnvironment.current().registry().masterFluidReference;
        var DEFAULT_FLUID_NAME = FluidEnvironment.current().registry().defaultFluidName;
''' + fix[end:]
        registration = member(source, "public void registerFluid(")
        text = PACKAGE + "final class FluidRegistrationService {\n" + fix + "\n" + registration + "\n}\n"
        text = clean(text).replace("GTValues.MODID", '"gregtech"').replace("FluidRegistry.", "FluidEnvironment.current().registry().")
        return finish(text.replace("fluidSprites.add", "FluidEnvironment.current().sprites().add"))
    text = source
    if name == "GTFluid":
        for marker in ("public @NotNull TextComponentTranslation toTextComponentTranslation()", "public String getLocalizedName(FluidStack stack)"):
            text = text.replace(member(text, marker), "")
        text = text.replace("        @Override\n        @SideOnly(Side.CLIENT)", "")
    if name == "BlastProperty":
        original = member(text, "public static GasTier validateGasTier(")
        text = text.replace(original, '''public static GasTier validateGasTier(String name) {
        throw Failure.unsupported("fluid.blast-script-enum", "GroovyScript/CraftTweaker enum context is not installed");
    }''')
    if name == "FluidProperty":
        text = re.sub(r"    /\*\*\n     \* Obsolete method,[\s\S]*?\*/", "", text)
        text = text.replace(" implements IMaterialProperty, FluidStorage", " implements IMaterialProperty")
        text = text.replace("private final FluidStorageImpl storage = new FluidStorageImpl();", "private final FluidRegistration<FluidStorageKey, NativeFluid> storage = FluidEnvironment.current().newStorage();")
        for marker in ("public @NotNull FluidStorage getStorage()",):
            text = text.replace(member(text, marker), "")
        text = text.replace("@Deprecated", "").replace("@Override", "")
        text = text.replace("return storage.getQueuedBuilder(key);", "return (FluidBuilder) storage.getQueuedBuilder(key);")
        # Existing queue's carrier is MaterialState; this operation requires native fluid material metadata.
    if name == "FluidBuilder":
        start = text.index("        if (hasFluidBlock) {")
        original = member(text[start:], "if (hasFluidBlock)")
        text = text[:start] + text[start:].replace(original, '''if (hasFluidBlock) {
            throw Failure.unsupported("fluid.world-block", "Fluid block construction/update is not implemented; earlier effects are retained");
        }''', 1)
        text = text.replace("FluidRegistry.", "FluidEnvironment.current().registry().")
        text = text.replace("GTFluidRegistration.INSTANCE.", "FluidEnvironment.current().registration().")
        text = text.replace("FluidUnifier.registerFluid", "FluidEnvironment.current().unifier().registerFluid")
        text = text.replace("FluidTooltipUtil.registerTooltip(fluid, FluidTooltipUtil.createFluidTooltip(material, fluid, state));", "FluidEnvironment.current().registerTooltip(fluid, material, state);")
        text = text.replace("Mods.TOPAddons.isModLoaded()", "FluidEnvironment.current().topAddonsLoaded()")
        text = text.replace("Colors.FLUID_NAME_COLOR_MAP", "FluidEnvironment.current().addonColors()")
        text = text.replace("import static gregtech.api.fluids.FluidConstants.*;", "import static research.orthrus.axiom.FluidConstants.*;")
    if name == "FluidUnifier":
        text = text.replace("private static final Map", "private final Map").replace("private FluidUnifier()", "FluidUnifier()")
        text = text.replace("public static ", "public ")
    text = clean(text)
    text = re.sub(r"\bFluidStack\b", "NativeFluidStack", text)
    if name == "FluidStorageKey":
        text = text.replace("public final class FluidStorageKey {", "public final class FluidStorageKey implements FluidRegistration.Key {")
        text = text.replace("private static final Map<NativeLocation, FluidStorageKey> keys = new Object2ObjectOpenHashMap<>();", "// Catalog is owned by the explicit fluid environment, not a process-global registry.")
        text = re.sub(r"\bkeys\.", "FluidEnvironment.current().keys().", text)
    if name == "FluidStorageKeys":
        text = text.replace("import static gregtech.api.util.FluidSupport.gregtechId;", "import static research.orthrus.axiom.FluidSupport.gregtechId;")
        text = text.replace("public static final FluidStorageKey", "public final FluidStorageKey")
        text = text.replace("FluidStorageKeys.LIQUID", "FluidEnvironment.current().storageKeys().LIQUID").replace("FluidStorageKeys.GAS", "FluidEnvironment.current().storageKeys().GAS")
        text = text.replace("private FluidStorageKeys()", "FluidStorageKeys()")
    elif name in ("FluidProperty", "FluidMaterial"):
        text = text.replace("FluidStorageKeys.", "FluidEnvironment.current().storageKeys().")
    if name == "FluidBuilder":
        text = text.replace("public class FluidBuilder {", "public class FluidBuilder implements FluidRegistration.Builder<FluidStorageKey, NativeFluid> {")
        text = text.replace("public  NativeFluid build( String modid,  FluidMaterial material,  FluidStorageKey key)", "public NativeFluid build(String modid, MaterialState carrier, FluidStorageKey key)")
        text = text.replace("        determineName(material, key);", "        FluidMaterial material = carrier == null ? null : FluidMaterial.require(carrier);\n        determineName(material, key);", 1)
    text = re.sub(r"public (final )?(class|interface|enum) " + name + r"\b", lambda m: (m[1] or "") + m[2] + " " + name, text, count=1)
    return finish(text)


def forge(name, source):
    if name == "NativeFluid":
        fields = source[source.index("    public static final int BUCKET_VOLUME"):source.index("    public Fluid(String fluidName")]
        fields = re.sub(r"    private SoundEvent[^;]+;", "", fields)
        fields = fields.replace("protected EnumRarity rarity = EnumRarity.COMMON;", "")
        fields = fields.replace("protected Block block = null;", "")
        markers = ["public Fluid(String fluidName, ResourceLocation still, ResourceLocation flowing)",
                   "public Fluid(String fluidName, ResourceLocation still, ResourceLocation flowing, @Nullable ResourceLocation overlay)",
                   "public Fluid setTranslationKey(", "public Fluid setLuminosity(", "public Fluid setDensity(",
                   "public Fluid setTemperature(", "public Fluid setViscosity(", "public Fluid setGaseous(",
                   "public Fluid setColor(int color)", "public final String getName()", "public String getTranslationKey()",
                   "public String getUnlocalizedName()", "public final int getLuminosity()", "public final int getDensity()",
                   "public final int getTemperature()", "public final int getViscosity()", "public final boolean isGaseous()",
                   "public int getColor()", "public ResourceLocation getStill()", "public ResourceLocation getFlowing()",
                   "public ResourceLocation getOverlay()", "public String getUnlocalizedName(FluidStack stack)",
                   "public String getTranslationKey(FluidStack stack)"]
        text = PACKAGE + "import java.util.Locale;\nclass NativeFluid {\n" + fields + "\n".join(member(source,m) for m in markers) + "\n}\n"
        return finish(re.sub(r"\bFluidStack\b", "NativeFluidStack", clean(text)))
    from axiom_stack_sources import registry
    return registry(source)


def material(source):
    from axiom_construction_sources import material as construction_material
    return construction_material(source)
