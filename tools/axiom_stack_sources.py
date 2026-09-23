"""Native fluid-stack and default identity extraction; no vanilla block bootstrap."""
import re
from axiom_fluid_sources import member, clean, finish, PACKAGE

PATHS = {
    "NativeFluidStack": "src/main/java/net/minecraftforge/fluids/FluidStack.java",
    "NativeNbtTypes": "src/main/java/net/minecraftforge/common/util/Constants.java",
    "NativeNbtGuard": "patches/minecraft/net/minecraft/nbt/NBTTagCompound.java.patch",
}

def relocate(text, names):
    literals = []
    def hide(match):
        literals.append(match[0]); return "AXIOM_STRING_" + str(len(literals) - 1) + "_END"
    text = re.sub(r'"(?:\\.|[^"\\])*"', hide, text)
    for before, after in names.items():
        text = re.sub(r"\b" + before + r"\b", after, text)
    return re.sub(r"AXIOM_STRING_(\d+)_END", lambda m: literals[int(m[1])], text)


def extract(name, source):
    if name not in PATHS:
        raise ValueError("unadmitted native fluid-stack source: " + name)
    if name == "NativeNbtGuard":
        guard = '+        if (value == null) throw new IllegalArgumentException("Invalid null NBT value with key " + key);'
        if source.count(guard) != 1:
            raise ValueError("native NBT guard patch boundary differs")
        return finish(PACKAGE + '''// Selected Cleanroom NBTTagCompound patch, Minecraft Forge LGPL-2.1.
// Original license is distributed in sources/licenses/forge/LGPL-2.1.txt.
final class NativeNbtGuard {
    static void requireTag(String key, NativeNbtValue value) {
''' + guard[1:] + '\n    }\n}\n')
    header = source[:source.index("package ")]
    if name == "NativeNbtTypes":
        text = member(source, "public static class NBT").replace("public static class NBT", "final class NativeNbtTypes", 1)
        return header + finish(PACKAGE + text)
    # Item container capabilities and localization remain outside this domain.
    for marker in ("public boolean isFluidEqual(ItemStack other)", "public String getLocalizedName()"):
        source = source.replace(member(source, marker), "")
    source = source.replace("import javax.annotation.Nullable;", "")
    source = clean(source)
    source = relocate(source, {"FluidStack": "NativeFluidStack", "NBTTagCompound": "NativeNbtCompound"})
    source = source.replace("Constants.NBT.", "NativeNbtTypes.")
    source = source.replace("FluidRegistry.", "FluidEnvironment.current().registry().").replace("FMLLog.", "ForgeRegistryLog.")
    source = source.replace("public class NativeFluidStack", "class NativeFluidStack", 1)
    return finish(source)


def registry(source):
    """Retain existing registration and add in-memory default/delegate lifecycle."""
    markers = (
        "public static boolean registerFluid(", "private static String uniqueName(", "public static boolean isFluidDefault(",
        "public static boolean isFluidRegistered(Fluid fluid)", "public static boolean isFluidRegistered(String fluidName)",
        "public static Fluid getFluid(String fluidName)", "public static String getFluidName(Fluid fluid)",
        "public static String getFluidName(FluidStack stack)", "public static FluidStack getFluidStack(",
        "public static Map<String, Fluid> getRegisteredFluids()", "public static Map<Fluid, Integer> getRegisteredFluidIDs()",
        "public static boolean addBucketForFluid(", "public static Set<Fluid> getBucketFluids()",
        "public static boolean hasBucket(", "public static int getMaxID()", "public static String getDefaultFluidName(",
        "public static void initFluidIDs(", "private static void loadFluidDefaults(BiMap<Fluid, Integer>",
        "public static void loadFluidDefaults(NBTTagCompound tag)", "public static void writeDefaultFluidList(",
        "public static void validateFluidRegistry()", "static IRegistryDelegate<Fluid> makeDelegate(",
        "public static String getModId(", "private static class FluidDelegate",
    )
    text = PACKAGE + '''import java.util.*;
import java.util.Map.Entry;
final class FluidRegistryState {
    private int maxID;
    NativeBiMap<String, NativeFluid> fluids;
    final NativeBiMap<String, NativeFluid> masterFluidReference;
    NativeBiMap<NativeFluid, Integer> fluidIDs;
    NativeBiMap<Integer, String> fluidNames;
    final NativeBiMap<String,String> defaultFluidName;
    final Map<NativeFluid, FluidDelegate> delegates = new HashMap<>();
    private final Set<String> bucketFluids = new HashSet<>();
    private Set<NativeFluid> currentBucketFluids;
    private final FluidEnvironment environment;
    FluidRegistryState(FluidEnvironment environment) {
        this.environment = environment;
        var runtime = environment.runtime();
        fluids = new NativeBiMap<>(runtime); masterFluidReference = new NativeBiMap<>(runtime);
        fluidIDs = new NativeBiMap<>(runtime); fluidNames = new NativeBiMap<>(runtime);
        defaultFluidName = new NativeBiMap<>(runtime);
    }
''' + "\n".join(member(source, m) for m in markers) + "\n}\n"
    text = clean(text).replace("public static ", "public ").replace("private static ", "private ")
    text = text.replace("static IRegistryDelegate", "IRegistryDelegate")
    text = re.sub(r"\bFluidStack\b", "NativeFluidStack", text)
    text = re.sub(r"\bNBTTagCompound\b", "NativeNbtCompound", text)
    text = re.sub(r"\bNBTTagList\b", "NativeNbtList", text)
    text = text.replace("new NativeNbtList()", "environment.runtime().newNbtList()")
    text = text.replace("new NBTTagString(getDefaultFluidName(def.getValue()))", "environment.runtime().nbtString(getDefaultFluidName(def.getValue()))")
    text = text.replace("MinecraftForge.EVENT_BUS.post(new FluidRegisterEvent(fluid.getName(), maxID))", "environment.postRegistration(fluid.getName(), maxID)")
    text = text.replace("ModContainer activeModContainer = Loader.instance().activeModContainer();", "RegistryRuntime.ActiveMod activeModContainer = environment.runtime().activeModContainer();")
    text = text.replace("FMLLog.log", "ForgeRegistryLog.log")
    # Keep the existing explicit registration diagnostic port for its admitted API.
    text = text.replace('ForgeRegistryLog.log.error("The fluid registry is corrupted. A fluid', 'environment.logger().error("The fluid registry is corrupted. A fluid')
    text = text.replace("Sets.newHashSet()", "new HashSet<>()").replace("Strings.isNullOrEmpty(name)", "(name == null || name.isEmpty())")
    text = re.sub(r"\bBiMap\b", "NativeBiMap", text)
    text = text.replace("HashBiMap.create(fluids)", "new NativeBiMap<>(environment.runtime(), fluids)")
    text = text.replace("HashBiMap.create(fluidIDs)", "new NativeBiMap<>(environment.runtime(), fluidIDs)")
    text = text.replace("HashBiMap.create()", "new NativeBiMap<>(environment.runtime())")
    text = re.sub(r"Maps.unmodifiableBiMap\((\w+)\)", r"\1.unmodifiableView()", text)
    text = text.replace("        fluidBlocks = null;", "        // Block lookup is not exposed; its cache is not instantiated in this domain.")
    return finish(text)
