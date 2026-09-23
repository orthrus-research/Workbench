"""Explicit extraction of GT material registry lifecycle around native utility classes."""

import re
from axiom_material_sources import declaration, replace_once

PATHS = {
    "GTControlledRegistry": "src/main/java/gregtech/api/util/GTControlledRegistry.java",
    "IMaterialRegistryManager": "src/main/java/gregtech/api/unification/material/registry/IMaterialRegistryManager.java",
    "MaterialRegistry": "src/main/java/gregtech/api/unification/material/registry/MaterialRegistry.java",
    "MaterialRegistryImpl": "src/main/java/gregtech/core/unification/material/internal/MaterialRegistryImpl.java",
    "MaterialRegistryManager": "src/main/java/gregtech/core/unification/material/internal/MaterialRegistryManager.java",
}
NOTICE = "// Extracted from pinned GTCEu; LGPL-3.0. See spec/native-registries.md for dependency substitutions.\n"
FLUID_PATH = "src/main/java/gregtech/api/fluids/store/FluidStorageImpl.java"


def fluid_storage(source, prefix=""):
    text = replace_once(source, "package gregtech.api.fluids.store;", "package research.orthrus.axiom;")
    text = re.sub(r"^import (?:gregtech\.|net\.minecraft|org\.jetbrains)[^;]+;\n", "", text, flags=re.MULTILINE)
    text = replace_once(text, "public final class FluidStorageImpl implements FluidStorage {", "final class FluidRegistration<K extends FluidRegistration.Key, F> {")
    text = replace_once(text, "    public FluidStorageImpl() {}", '''    interface Key { int getRegistrationPriority(); }
    @FunctionalInterface interface Builder<K, F> {
        F build(String modid, MaterialState material, K key);
    }

    private final K liquidKey;
    private final java.util.function.Supplier<Builder<K, F>> defaultBuilder;
    private final java.util.function.Consumer<MaterialState> collision;

    FluidRegistration(K liquidKey, java.util.function.Supplier<Builder<K, F>> defaultBuilder,
                      java.util.function.Consumer<MaterialState> collision) {
        this.liquidKey = java.util.Objects.requireNonNull(liquidKey);
        this.defaultBuilder = java.util.Objects.requireNonNull(defaultBuilder);
        this.collision = java.util.Objects.requireNonNull(collision);
    }''')
    text = text.replace("new FluidBuilder()", "defaultBuilder.get()")
    text = text.replace("FluidStorageKeys.LIQUID", "liquidKey")
    text = re.sub(r"\bFluidStorageKey\b", "K", text)
    text = text.replace('"K "', '"FluidStorageKey "')
    text = re.sub(r"\bFluidBuilder\b", "Builder<K, F>", text)
    text = re.sub(r"\bFluid\b", "F", text)
    text = re.sub(r"\bMaterial\b", "MaterialState", text)
    text = replace_once(text, 'GTLog.logger.error("{} already has an associated fluid for material {}", material);', "collision.accept(material);")
    for annotation in ("@NotNull", "@Nullable", "@Override", "@ApiStatus.Internal"):
        text = text.replace(annotation, "")
    if prefix:
        text = re.sub(r"\bFluidRegistration\b", prefix + "FluidRegistration", text)
        # Oracle receives the exact same caller key/builder contracts, not a second predicate model.
        text = text.replace(prefix + "FluidRegistration.Key", "FluidRegistration.Key")
        start = text.index("    interface Key {")
        end = text.index("    private final K liquidKey;", start)
        text = text[:start] + text[end:]
        text = text.replace("Builder<K, F>", "FluidRegistration.Builder<K, F>")
    return NOTICE + "\n".join(line.rstrip() for line in text.splitlines()) + "\n"


def extract(name, source, prefix=""):
    if name not in PATHS:
        raise ValueError("unadmitted registry source: " + name)
    text = re.sub(r"^package [^;]+;", "package research.orthrus.axiom;", source, count=1)
    text = re.sub(r"^import (?:gregtech\.|net\.minecraft|org\.jetbrains|com\.google\.common\.base\.)[^;]+;\n", "", text, flags=re.MULTILINE)
    text = text.replace("@NotNull", "").replace("@Nullable", "")
    text = re.sub(r"\bMaterial\b", "MaterialState", text)
    text = text.replace("GTValues.MODID", '"gregtech"')
    text = re.sub(r"\bPhase\b", "MaterialPhase", text)
    if name == "IMaterialRegistryManager":
        start = text.index("    enum MaterialPhase {")
        text = text[:start] + "}\n"
    if name == "GTControlledRegistry":
        text = text.replace("extends RegistryNamespaced<K, V>", "extends NativeNamedRegistry<K, V>")
        text = replace_once(text, "public GTControlledRegistry(int maxId) {", "public GTControlledRegistry(RegistryRuntime runtime, int maxId) {\n        super(runtime);")
        text = text.replace("private static boolean checkActiveModContainerIsGregtech()", "private boolean checkActiveModContainerIsGregtech()")
        text = text.replace("ModContainer container = Loader.instance().activeModContainer();", "RegistryRuntime.ActiveMod container = runtime.activeModContainer();")
    if name == "MaterialRegistry":
        text = replace_once(text, "public MaterialRegistry() {\n        super(Short.MAX_VALUE);", "public MaterialRegistry(RegistryRuntime runtime) {\n        super(runtime, Short.MAX_VALUE);")
    if name == "MaterialRegistryImpl":
        text = replace_once(text, "    private static int networkIdCounter;", "    private final MaterialRegistryManager owner;")
        text = replace_once(text, "private final int networkId = networkIdCounter++;", "private final int networkId;")
        text = replace_once(text, "protected MaterialRegistryImpl( String modid) {\n        super();",
                            "protected MaterialRegistryImpl(RegistryRuntime runtime, MaterialRegistryManager owner, String modid) {\n        super(runtime);\n        this.owner = owner;\n        this.networkId = runtime.nextNetworkId();")
        text = text.replace("CoreModule.logger.error(", "runtime.error(")
        text = text.replace("MaterialRegistryManager.getInstance().getDefaultFallback()", "owner.getDefaultFallback()")
    if name == "MaterialRegistryManager":
        text = replace_once(text, "    private static MaterialRegistryManager INSTANCE;", "    private final RegistryRuntime runtime;")
        text = replace_once(text, "private final MaterialRegistryImpl gregtechRegistry = createInternalRegistry();", "private final MaterialRegistryImpl gregtechRegistry;")
        text = replace_once(text, "private MaterialRegistryManager() {}", "MaterialRegistryManager(RegistryRuntime runtime) {\n        this.runtime = runtime;\n        this.gregtechRegistry = createInternalRegistry();\n    }")
        singleton = declaration(text, "public static MaterialRegistryManager getInstance()")
        text = replace_once(text, singleton, "")
        text = replace_once(text, '''        Preconditions.checkArgument(!registries.containsKey(modid),
                "MaterialState registry already exists for modid %s", modid);''', '''        if (registries.containsKey(modid))
            throw new IllegalArgumentException(String.format("Material registry already exists for modid %s", modid));''')
        text = text.replace("new MaterialRegistryImpl(", "new MaterialRegistryImpl(runtime, this, ")
    # Material in prose/messages is not the substituted Java type.
    text = text.replace('"MaterialState ', '"Material ')
    text = re.sub(r"public (final |abstract )?(class|interface) " + name + r"\b", lambda m: (m[1] or "") + m[2] + " " + name, text, count=1)
    if name == "MaterialRegistry":
        text = text.replace("abstract class MaterialRegistry", "public abstract class MaterialRegistry", 1)
    if prefix:
        for original in sorted(PATHS, key=len, reverse=True):
            text = re.sub(r"\b" + original + r"\b", prefix + original, text)
    return NOTICE + "\n".join(line.rstrip() for line in text.splitlines()) + "\n"
