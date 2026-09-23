"""Native Cleanroom registry construction/state extraction, not GameData bootstrap.

Registry ownership is supplied by the existing active-runtime port. Persistence,
missing-world mapping and registry-event construction are not exposed. No game
classes, namespace compatibility stubs or fabricated registry entries are emitted.
"""
import re

from axiom_fluid_sources import member

PACKAGE = "package research.orthrus.axiom;"
BASE = "src/main/java/net/minecraftforge/"
PATHS = {
    **{name: BASE + "registries/" + name + ".java" for name in (
        "IForgeRegistryEntry", "IRegistryDelegate", "RegistryDelegate", "IForgeRegistry",
        "IForgeRegistryInternal", "IForgeRegistryModifiable", "ILockableRegistry", "ForgeRegistry")},
    "ForgeRegistryManager": BASE + "registries/RegistryManager.java",
    "ForgeRegistryBuilder": BASE + "registries/RegistryBuilder.java",
    "ForgeNamespacedRegistry": BASE + "registries/NamespacedWrapper.java",
    "ForgeDefaultedRegistry": BASE + "registries/NamespacedDefaultedWrapper.java",
    "ForgeEntryNames": BASE + "registries/GameData.java",
    "ForgeRegistryLog": BASE + "fml/common/FMLLog.java",
    "ForgeRegistryLookup": BASE + "fml/common/registry/GameRegistry.java",
}
RENAMES = {"RegistryManager": "ForgeRegistryManager", "RegistryBuilder": "ForgeRegistryBuilder",
           "NamespacedWrapper": "ForgeNamespacedRegistry", "NamespacedDefaultedWrapper": "ForgeDefaultedRegistry", "FMLLog": "ForgeRegistryLog",
           "ResourceLocation": "NativeLocation"}


def extract(name, source):
    if name not in PATHS:
        raise ValueError("unadmitted Forge registry source: " + name)
    header = source[:source.index("package ")]
    if name == "ForgeRegistry":
        # Preserve the entire construction/query/mutation/sync region, including
        # fields, callback order and failure behavior. No save/network API stubs.
        source = source[:source.index("    RegistryEvent.Register<V> getRegisterEvent(")] + \
            member(source, "private static class OverrideOwner") + "\n}\n"
    if name == "ForgeRegistryManager":
        source = source.replace(member(source, "public Map<ResourceLocation, Snapshot> takeSnapshot("), "")
    if name == "ForgeRegistryLog":
        source = header + PACKAGE + """
import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.Logger;
final class ForgeRegistryLog {
    public static final Logger log = LogManager.getLogger("FML");
""" + member(source, "public static void bigWarning(") + "\n}\n"
    if name == "ForgeEntryNames":
        methods = [member(source, marker) for marker in (
            "public static ResourceLocation checkPrefix(String name, boolean warnOverrides)",
            "private static <T extends IForgeRegistryEntry<T>> RegistryBuilder<T> makeRegistry(ResourceLocation name, Class<T> type, int min, int max)",
            "private static <T extends IForgeRegistryEntry<T>> RegistryBuilder<T> makeRegistry(ResourceLocation name, Class<T> type, int max)",
            "private static <T extends IForgeRegistryEntry<T>> RegistryBuilder<T> makeRegistry(ResourceLocation name, Class<T> type, int max, ResourceLocation _default)",
            "public static <V extends IForgeRegistryEntry<V>> RegistryNamespacedDefaultedByKey<ResourceLocation, V> getWrapperDefaulted(",
            "public static <V extends IForgeRegistryEntry<V>> RegistryNamespaced<ResourceLocation, V> getWrapper(")]
        source = header + PACKAGE + "\nimport java.util.Locale;\nimport org.apache.commons.lang3.Validate;\nfinal class ForgeEntryNames {\n" + \
            "\n".join(methods).replace("private static <T", "static <T") + "\n}\n"
        source = source.replace("GameRegistry.findRegistry", "ForgeRegistryLookup.findRegistry")
        source = source.replace("RegistryNamespaced<ResourceLocation, V>", "NativeNamedRegistry<ResourceLocation, V>")
        source = source.replace("RegistryNamespacedDefaultedByKey<ResourceLocation, V>", "NativeDefaultedRegistry<ResourceLocation, V>")
    if name == "ForgeRegistryLookup":
        source = header + PACKAGE + "\nfinal class ForgeRegistryLookup {\n" + \
            member(source, "public static <K extends IForgeRegistryEntry<K>> IForgeRegistry<K> findRegistry(") + "\n}\n"
    source = re.sub(r"^package [^;]+;", PACKAGE, source, count=1, flags=re.MULTILINE)
    source = source.replace("import net.minecraftforge.registries.IForgeRegistry.*;", "import research.orthrus.axiom.IForgeRegistry.*;")
    source = re.sub(r"^import (?:net\.minecraft|net\.minecraftforge|javax\.annotation)[^;]+;\n", "", source, flags=re.MULTILINE)
    source = re.sub(r"@(?:Nonnull|Nullable)\b", "", source)
    # Rename code tokens without changing diagnostic strings or string operations.
    literals = []
    def hide(match):
        literals.append(match[0]); return "AXIOM_STRING_" + str(len(literals) - 1) + "_END"
    source = re.sub(r'"(?:\\.|[^"\\])*"', hide, source)
    for before, after in RENAMES.items():
        source = re.sub(r"\b" + before + r"\b", after, source)
    source = source.replace("GameData.checkPrefix", "ForgeEntryNames.checkPrefix")
    source = source.replace("Loader.instance().activeModContainer()", "FluidEnvironment.current().runtime().activeModContainer()")
    source = re.sub(r"\bModContainer\b", "RegistryRuntime.ActiveMod", source)
    source = source.replace("(mc instanceof InjectedModContainer && ((InjectedModContainer)mc).wrappedContainer instanceof FMLContainer)",
                            "mc.injectedFmlContainer()")
    if name == "ForgeNamespacedRegistry":
        source = source.replace("extends RegistryNamespaced<", "extends NativeNamedRegistry<")
        source = source.replace("public ForgeNamespacedRegistry(ForgeRegistry<V> owner)\n    {",
                                "public ForgeNamespacedRegistry(ForgeRegistry<V> owner)\n    {\n        super(FluidEnvironment.current().runtime());")
        # The native superclass executes through its existing utility binding;
        # the wrapper retains every own method, including those outside that
        # binding's reduced public surface.
        source = source.replace("@Override", "")
    if name == "ForgeDefaultedRegistry":
        source = source.replace("extends RegistryNamespacedDefaultedByKey<", "extends NativeDefaultedRegistry<")
        source = source.replace("super(null);", "super(FluidEnvironment.current().runtime(), null);")
        source = source.replace("@Override", "")
    source = re.sub(r"public (class|interface) " + re.escape(name) + r"\b", r"\1 " + name, source, count=1)
    source = re.sub(r"AXIOM_STRING_(\d+)_END", lambda match: literals[int(match[1])], source)
    return "// Retained selected Cleanroom source; see spec/native-forge-registries.md.\n" + \
        "\n".join(line.rstrip() for line in source.splitlines()) + "\n"
