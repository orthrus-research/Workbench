package research.orthrus.axiom;

import java.nio.file.*;
import java.util.*;

/** Actual native constructor objects, not custom registry or named fluid fixtures. */
public final class NativeIdentityConformance {
    private static void check(boolean condition, String message) { if (!condition) throw new AssertionError(message); }
    public static void main(String[] args) throws Exception {
        NativeRuntime.require(); WorkerIsolation.install();
        if (args.length == 3 && args[2].equals("reject-input")) {
            try (var ignored = NativeVanillaIdentities.open(Path.of(args[0]), Path.of(args[1]))) {
                throw new AssertionError("Changed native input admitted");
            } catch (Failure rejected) {
                check(rejected.rule.equals("runtime.mismatch"), "reject input bytes before class construction");
                System.out.println(Json.write(Map.of("status", "rejected", "rule", rejected.rule))); return;
            } catch (IllegalArgumentException rejected) {
                check(rejected.getMessage().contains("Indirect"), "reject symlink root before class construction");
                System.out.println(Json.write(Map.of("status", "rejected", "rule", "indirect-root"))); return;
            }
        }
        var trace = new ArrayList<Object>();
        NativeVanillaIdentities.Fluid retained;
        try (var identities = NativeVanillaIdentities.open(Path.of(args[0]), Path.of(args[1]))) {
            check(identities.phase().equals("uninitialized"), "explicit construction phase");
            try { identities.fluid("water"); throw new AssertionError("Premature lookup succeeded"); }
            catch (IllegalStateException expected) { check(expected.getMessage().contains("unavailable"), "premature lookup is incomplete"); }
            Thread thread = Thread.currentThread(); ClassLoader previous = thread.getContextClassLoader();
            ClassLoader ambient = new ClassLoader(null) {
                @Override public java.net.URL getResource(String name) { throw new AssertionError("Ambient resource lookup: " + name); }
                @Override public Enumeration<java.net.URL> getResources(String name) { throw new AssertionError("Ambient resources lookup: " + name); }
            };
            try {
                thread.setContextClassLoader(ambient); identities.initialize();
                check(thread.getContextClassLoader() == ambient, "native construction restores caller context loader");
            } finally { thread.setContextClassLoader(previous); }
            try { identities.type("research.orthrus.axiom.Main"); throw new AssertionError("Engine class leaked into native class space"); }
            catch (Failure expected) { check(expected.rule.equals("identity.native-construction"), "native parent excludes ambient engine"); }
            check(identities.phase().equals("material-identity-prefix"), "bounded native phase, not bootstrap complete");
            check(identities.enchantmentNames().size() == 30, "complete native enchantment registration method");
            check(identities.enchantment("fixture:missing") == null, "missing enchantment not fabricated");
            for (String name : identities.enchantmentNames()) {
                var enchantment = identities.enchantment(name);
                check(enchantment == identities.enchantment(name), "native identity interning");
                trace.add(List.of(name, enchantment.id(), enchantment.maxLevel()));
            }
            for (String name : List.of("water", "lava")) {
                var fluid = identities.fluid(name); var block = identities.block("minecraft:" + name);
                check(fluid.block() == block, "native fluid references the actual registered block");
                check(fluid == identities.fluid(name), "native fluid is interned, not re-registered");
                check(block.id() == (name.equals("water") ? 9 : 11), "native stationary block ID");
                check(block.translationKey().equals("tile." + name), "original block translation key");
                check(identities.fluidForBlock(identities.block("minecraft:flowing_" + name)) == fluid,
                        "native flowing-block normalization and fluid block-cache lookup");
                for (int meta = 0; meta < 16; meta++) check(block.stateId(meta) == (block.id() << 4 | meta), "native block-state registration callbacks");
                trace.add(List.of(name, block.name(), block.id(), fluid.translationKey(), fluid.temperature(), fluid.density(), fluid.viscosity(), fluid.luminosity()));
                for (int i = 0; i < 4096; i++) {
                    int amount = i % 7 == 0 ? -i : i;
                    var stack = fluid.stack(amount); var copy = stack.copy();
                    check(stack != copy && stack.fluid() == fluid && copy.fluid() == fluid && copy.amount() == amount, "original stack construction, copy and delegate");
                }
            }
            check(identities.fluid("lava").temperature() == 1300 && identities.fluid("lava").density() == 3000
                    && identities.fluid("lava").viscosity() == 6000 && identities.fluid("lava").luminosity() == 15, "actual lava constructor chain");
            check(identities.fluid("fixture_missing") == null, "missing fluid not invented");
            retained = identities.fluid("water");
            // This existing GT tool-property implementation now consumes real, typed enchantment bindings.
            var properties = ToolProperty.Builder.of(4.0F, 3.0F, 384, 2).enchantability(18)
                    .enchantment(identities.enchantment("minecraft:bane_of_arthropods"), 3)
                    .enchantment(identities.enchantment("minecraft:efficiency"), 1).build();
            check(properties.getEnchantments().containsKey(identities.enchantment("minecraft:efficiency")), "GT tool properties retain the actual bound key");
            var shell = new groovy.lang.GroovyShell(NativeIdentityConformance.class.getClassLoader(), new groovy.lang.Binding(new HashMap<>(Map.of("identities", identities, "properties", properties))));
            var result = shell.evaluate("""
                def water = identities.fluid('water')
                assert water.block().is(identities.block('minecraft:water'))
                assert water.stack(-7).copy().fluid().is(water)
                def efficiency = identities.enchantment('minecraft:efficiency')
                assert properties.enchantments.keySet().any { it.is(efficiency) }
                return [efficiency.name(), water.stack(144).amount()]
                """, "NativeMaterialIdentityConsumer.groovy");
            check(result.equals(List.of("minecraft:efficiency", 144)), "actual Groovy identity consumer");
            var constants = new HashMap<String, Object>();
            for (String name : List.of("BANE_OF_ARTHROPODS", "EFFICIENCY", "SMITE", "FORTUNE", "LOOTING", "FIRE_ASPECT")) {
                var constant = identities.enchantmentConstant(name);
                check(constant == identities.enchantment("minecraft:" + name.toLowerCase(Locale.ROOT)), "actual native Enchantments constant and registry alias");
                constants.put(name, constant);
            }
            shell.setVariable("Enchantments", constants);
            var expressions = Json.array(Json.object(Json.parse(Files.readString(Path.of(args[2])))).get("expressions"));
            check(expressions.size() == 5, "all selected GT enchanted tool-stat expressions");
            for (Object row : expressions) {
                var source = Json.object(row);
                var property = (ToolProperty) shell.evaluate("import research.orthrus.axiom.ToolProperty\n" + Json.string(source.get("source")), Json.string(source.get("path")));
                var keys = new TreeMap<String, Object>();
                for (var key : property.getEnchantments().keySet()) {
                    check(key instanceof NativeVanillaIdentities.Enchantment && constants.containsValue(key), "GT source consumes only actual bound native identities");
                    var level = property.getEnchantments().get(key);
                    keys.put(((NativeVanillaIdentities.Enchantment) key).name(), List.of(level.getLevel(0), level.getLevel(1), level.getLevel(16)));
                }
                trace.add(List.of(keys, property.getToolSpeed(), property.getToolAttackDamage(), property.getToolDurability(), property.getToolEnchantability()));
            }
            trace.add(result); shell.getClassLoader().close();
            try { identities.initialize(); throw new AssertionError("Repeated initialization admitted"); }
            catch (IllegalStateException expected) { check(expected.getMessage().contains("already attempted"), "no artificial reset"); }
            var report = new LinkedHashMap<String, Object>();
            report.putAll(Map.of("schema", "axiom.native-identity-conformance.v1", "status", "passed",
                    "enchantments", identities.enchantmentNames().size(), "blocks", identities.blockCount(), "stackVectors", 8192,
                    "traceDigest", Json.digest(trace), "kernelIsolation", true, "minecraftLaunched", false,
                    "wholePackParity", false, "fullVanillaBootstrap", false));
            report.put("gtToolExpressions", expressions.size()); System.out.println(Json.write(report));
        }
        try { retained.name(); throw new AssertionError("Closed identity handle remained active"); }
        catch (IllegalStateException expected) { check(expected.getMessage().contains("closed"), "closed handle rejects"); }
    }
}
