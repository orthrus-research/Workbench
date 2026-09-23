package research.orthrus.axiom;

import java.io.IOException;
import java.lang.reflect.*;
import java.net.*;
import java.nio.file.*;
import java.util.*;

/**
 * Actual Cleanroom-patched constructor identities, never a name catalog or world.
 * One fresh worker owns this class space. The enclosing pack lifecycle is not run.
 */
final class NativeVanillaIdentities implements AutoCloseable {
    private final NativeMaterialClassLoader loader;
    private final Map<Object, Block> blocks = new IdentityHashMap<>();
    private final Map<Object, Fluid> fluids = new IdentityHashMap<>();
    private final Map<Object, Enchantment> enchantments = new IdentityHashMap<>();
    private Object blockRegistry, enchantmentRegistry;
    private String phase = "uninitialized";
    private boolean closed;
    private boolean materialProgramAttempted;

    private NativeVanillaIdentities(Path images, Path libraries, Map<Path, String> programs, boolean oreAccess) throws IOException {
        NativeRuntime.require();
        var policy = Json.object(Json.parse(Target.resource("/axiom/native-identity-runtime.json")));
        if (!policy.get("schema").equals("axiom.native-identity-runtime.v1")) throw new IllegalArgumentException("Unknown native identity policy");
        var imageRows = Json.array(policy.get("images"));
        if (imageRows.size() != 2) throw new IllegalArgumentException("Unqualified native identity images");
        verifyRoot(images); verifyRoot(libraries);
        NativeRuntime.verifyFiles(images, imageRows);
        var runtimeRows = Json.array(policy.get("libraries")); NativeRuntime.verifyFiles(libraries, runtimeRows);
        var urls = new ArrayList<URL>();
        for (Object row : imageRows) urls.add(images.resolve(Json.string(Json.object(row).get("path"))).toUri().toURL());
        var entries = new HashSet<String>();
        for (var program : programs.entrySet()) {
            urls.add(materialProgram(program.getKey(), program.getValue(), entries));
        }
        for (Object row : runtimeRows) urls.add(libraries.resolve(Json.string(Json.object(row).get("path"))).toUri().toURL());
        // No engine, ambient classpath, vanilla-unpatched JAR or raw Cleanroom JAR parent.
        loader = new NativeMaterialClassLoader(urls.toArray(URL[]::new), entries, oreAccess);
    }

    static NativeVanillaIdentities open(Path images, Path libraries) {
        return openProgram(images, libraries, Map.of());
    }
    static NativeVanillaIdentities openProgram(Path images, Path libraries, Map<Path, String> programs) {
        return openProgram(images, libraries, programs, false);
    }
    static NativeVanillaIdentities openOreProgram(Path images, Path libraries, Map<Path, String> programs) {
        return openProgram(images, libraries, programs, true);
    }
    private static NativeVanillaIdentities openProgram(Path images, Path libraries, Map<Path, String> programs, boolean oreAccess) {
        try { return new NativeVanillaIdentities(images.toAbsolutePath().normalize(), libraries.toAbsolutePath().normalize(), programs, oreAccess); }
        catch (IOException failure) { throw unavailable(failure); }
    }
    Object runMaterialProgram(String entry, String mode) {
        ready();
        if (!entry.matches("[A-Za-z][A-Za-z0-9_]*")) throw new IllegalArgumentException("Invalid material entry point");
        if (materialProgramAttempted) throw new IllegalStateException("Native material program already attempted");
        materialProgramAttempted = true;
        return invoke(type("research.orthrus.axiom.nativeconstruction." + entry), null, "run", new Class<?>[]{String.class}, mode);
    }
    Map<String,Object> materialEventTransformations() { requireOpen(); return loader.transformations(); }
    Map<String,Object> materialAccessTransformations() { requireOpen(); return loader.accessTransformations(); }
    static URL materialProgram(Path input, String digest, Set<String> entries) throws IOException {
        Path path = input.toAbsolutePath(); verifyRoot(path);
        if (!Files.isRegularFile(path) || Files.size(path) > 16 * 1024 * 1024 ||
                !Json.bytesDigest(Files.readAllBytes(path)).equals(digest))
            throw new IllegalArgumentException("Native material program digest differs");
        var names = new HashSet<String>();
        try (var jar = new java.util.zip.ZipFile(path.toFile())) {
            for (var entry : java.util.Collections.list(jar.entries())) {
                String name = entry.getName();
                if (entry.isDirectory() || !name.matches("research/orthrus/axiom/nativeconstruction/[A-Za-z_$][A-Za-z0-9_$]*\\.class") ||
                        entries.contains(name) || !names.add(name))
                    throw new IllegalArgumentException("Native material program class boundary differs");
            }
        }
        if (names.isEmpty()) throw new IllegalArgumentException("Native material program class boundary differs");
        entries.addAll(names);
        return path.toUri().toURL();
    }
    private static void verifyRoot(Path root) {
        for (Path p = root; p != null; p = p.getParent()) if (Files.isSymbolicLink(p))
            throw new IllegalArgumentException("Indirect native identity root");
    }

    void initialize() {
        requireOpen();
        if (!phase.equals("uninitialized")) throw new IllegalStateException("Native identity initialization already attempted: " + phase);
        phase = "constructing";
        Thread thread = Thread.currentThread(); ClassLoader previous = thread.getContextClassLoader();
        thread.setContextClassLoader(loader);
        try {
            invoke(type("net.minecraft.init.Bootstrap"), null, "axiom$materialIdentities", new Class<?>[0]);
            var lookup = type("net.minecraftforge.fml.common.registry.GameRegistry");
            blockRegistry = invoke(lookup, null, "findRegistry", new Class<?>[]{Class.class}, type("net.minecraft.block.Block"));
            enchantmentRegistry = invoke(lookup, null, "findRegistry", new Class<?>[]{Class.class}, type("net.minecraft.enchantment.Enchantment"));
            Class.forName("net.minecraft.init.Enchantments", true, loader);
            // Actual FluidRegistry static initialization constructs block-backed fluids and posts their events.
            Class.forName("net.minecraftforge.fluids.FluidRegistry", true, loader);
            phase = "material-identity-prefix";
        } catch (Throwable failure) { phase = "failed"; throw propagate(failure); }
        finally { thread.setContextClassLoader(previous); }
    }

    String phase() { requireOpen(); return phase; }
    Class<?> type(String name) {
        requireOpen();
        try { return Class.forName(name, false, loader); }
        catch (ClassNotFoundException failure) { throw unavailable(failure); }
    }
    private Object location(String key) {
        return construct(type("net.minecraft.util.ResourceLocation"), new Class<?>[]{String.class}, key);
    }
    private Object lookup(Object registry, String key) {
        ready();
        return invoke(type("net.minecraftforge.registries.IForgeRegistry"), registry, "getValue",
                new Class<?>[]{type("net.minecraft.util.ResourceLocation")}, location(key));
    }
    private int id(Object registry, Object value) {
        return ((Number) invoke(type("net.minecraftforge.registries.ForgeRegistry"), registry, "getID",
                new Class<?>[]{type("net.minecraftforge.registries.IForgeRegistryEntry")}, value)).intValue();
    }
    private String name(Object registry, Object value) {
        return invoke(type("net.minecraftforge.registries.IForgeRegistry"), registry, "getKey",
                new Class<?>[]{type("net.minecraftforge.registries.IForgeRegistryEntry")}, value).toString();
    }
    private Block block(Object raw) { return raw == null ? null : blocks.computeIfAbsent(raw, Block::new); }
    private Fluid fluid(Object raw) { return raw == null ? null : fluids.computeIfAbsent(raw, Fluid::new); }
    Block block(String key) { return block(lookup(blockRegistry, key)); }
    Enchantment enchantment(String key) {
        Object raw = lookup(enchantmentRegistry, key);
        return raw == null ? null : enchantments.computeIfAbsent(raw, Enchantment::new);
    }
    Enchantment enchantmentConstant(String symbol) {
        ready();
        // MCP producer spellings bound to fields in the separately pinned native class image.
        String field = switch (symbol) {
            case "BANE_OF_ARTHROPODS" -> "field_180312_n";
            case "SMITE" -> "field_185303_l";
            case "FIRE_ASPECT" -> "field_77334_n";
            case "LOOTING" -> "field_185304_p";
            case "EFFICIENCY" -> "field_185305_q";
            case "FORTUNE" -> "field_185308_t";
            default -> throw Failure.unsupported("identity.enchantment-constant", "Unbound native constant: " + symbol);
        };
        try {
            Object raw = type("net.minecraft.init.Enchantments").getField(field).get(null);
            return enchantments.computeIfAbsent(raw, Enchantment::new);
        } catch (ReflectiveOperationException failure) { throw propagate(failure); }
    }
    Fluid fluid(String name) {
        ready(); return fluid(invoke(type("net.minecraftforge.fluids.FluidRegistry"), null, "getFluid", new Class<?>[]{String.class}, name));
    }
    Fluid fluidForBlock(Block block) {
        ready();
        return fluid(invoke(type("net.minecraftforge.fluids.FluidRegistry"), null, "lookupFluidForBlock",
                new Class<?>[]{type("net.minecraft.block.Block")}, block.value));
    }
    List<String> enchantmentNames() {
        ready(); var values = (Set<?>) invoke(type("net.minecraftforge.registries.IForgeRegistry"), enchantmentRegistry, "getKeys", new Class<?>[0]);
        return values.stream().map(Object::toString).sorted().toList();
    }
    int blockCount() {
        ready(); return ((Set<?>) invoke(type("net.minecraftforge.registries.IForgeRegistry"), blockRegistry, "getKeys", new Class<?>[0])).size();
    }

    final class Block {
        private final Object value;
        private Block(Object value) { this.value = type("net.minecraft.block.Block").cast(value); }
        String name() { ready(); return NativeVanillaIdentities.this.name(blockRegistry, value); }
        int id() { ready(); return NativeVanillaIdentities.this.id(blockRegistry, value); }
        String translationKey() { ready(); return (String) invoke(type("net.minecraft.block.Block"), value, "func_149739_a", new Class<?>[0]); }
        int stateId(int metadata) {
            ready();
            Object state = invoke(type("net.minecraft.block.Block"), value, "func_176203_a", new Class<?>[]{int.class}, metadata);
            Object map = invoke(type("net.minecraftforge.registries.GameData"), null, "getBlockStateIDMap", new Class<?>[0]);
            return ((Number) invoke(type("net.minecraft.util.ObjectIntIdentityMap"), map, "func_148747_b", new Class<?>[]{Object.class}, state)).intValue();
        }
    }
    final class Enchantment extends EnchantmentIdentity {
        private final Object value;
        private Enchantment(Object value) { this.value = type("net.minecraft.enchantment.Enchantment").cast(value); }
        String name() { ready(); return NativeVanillaIdentities.this.name(enchantmentRegistry, value); }
        int id() { ready(); return NativeVanillaIdentities.this.id(enchantmentRegistry, value); }
        int maxLevel() { ready(); return ((Number) invoke(type("net.minecraft.enchantment.Enchantment"), value, "func_77325_b", new Class<?>[0])).intValue(); }
    }
    final class Fluid {
        private final Object value;
        private Fluid(Object value) { this.value = type("net.minecraftforge.fluids.Fluid").cast(value); }
        String name() { return (String) call("getName"); }
        Block block() { return NativeVanillaIdentities.this.block(call("getBlock")); }
        int temperature() { return ((Number) call("getTemperature")).intValue(); }
        int density() { return ((Number) call("getDensity")).intValue(); }
        int viscosity() { return ((Number) call("getViscosity")).intValue(); }
        int luminosity() { return ((Number) call("getLuminosity")).intValue(); }
        String translationKey() { return (String) call("getUnlocalizedName"); }
        private Object call(String method) { ready(); return invoke(type("net.minecraftforge.fluids.Fluid"), value, method, new Class<?>[0]); }
        Stack stack(int amount) {
            ready();
            return new Stack(construct(type("net.minecraftforge.fluids.FluidStack"), new Class<?>[]{type("net.minecraftforge.fluids.Fluid"), int.class}, value, amount));
        }
    }
    final class Stack {
        private final Object value;
        private Stack(Object value) { this.value = type("net.minecraftforge.fluids.FluidStack").cast(value); }
        Fluid fluid() { ready(); return NativeVanillaIdentities.this.fluid(invoke(type("net.minecraftforge.fluids.FluidStack"), value, "getFluid", new Class<?>[0])); }
        int amount() {
            ready(); try { return type("net.minecraftforge.fluids.FluidStack").getField("amount").getInt(value); }
            catch (ReflectiveOperationException failure) { throw propagate(failure); }
        }
        Stack copy() { ready(); return new Stack(invoke(type("net.minecraftforge.fluids.FluidStack"), value, "copy", new Class<?>[0])); }
    }

    private static Object invoke(Class<?> owner, Object receiver, String method, Class<?>[] parameters, Object... arguments) {
        Thread thread = Thread.currentThread(); ClassLoader previous = thread.getContextClassLoader();
        thread.setContextClassLoader(owner.getClassLoader());
        try { return owner.getMethod(method, parameters).invoke(receiver, arguments); }
        catch (ReflectiveOperationException failure) { throw propagate(failure); }
        finally { thread.setContextClassLoader(previous); }
    }
    private static Object construct(Class<?> owner, Class<?>[] parameters, Object... arguments) {
        Thread thread = Thread.currentThread(); ClassLoader previous = thread.getContextClassLoader();
        thread.setContextClassLoader(owner.getClassLoader());
        try { return owner.getConstructor(parameters).newInstance(arguments); }
        catch (ReflectiveOperationException failure) { throw propagate(failure); }
        finally { thread.setContextClassLoader(previous); }
    }
    private static RuntimeException propagate(Throwable failure) {
        if (failure instanceof InvocationTargetException invocation) return propagate(invocation.getCause());
        if (failure instanceof RuntimeException runtime) return runtime;
        if (failure instanceof Error error) throw error;
        return unavailable(failure);
    }
    private static Failure unavailable(Throwable failure) { return new Failure("incomplete", "identity.native-construction", failure.toString()); }
    private void requireOpen() { if (closed) throw new IllegalStateException("Native identity class space is closed"); }
    private void ready() { requireOpen(); if (!phase.equals("material-identity-prefix")) throw new IllegalStateException("Native identities unavailable: " + phase); }
    @Override public void close() throws IOException { closed = true; blocks.clear(); fluids.clear(); enchantments.clear(); loader.close(); }
}
