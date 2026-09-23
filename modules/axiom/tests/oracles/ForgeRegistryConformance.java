package research.orthrus.axiom;

import java.nio.file.Path;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;

/** Controlled native entry fixtures, never manufactured vanilla/mod identities. */
public final class ForgeRegistryConformance {
    public static class Entry extends IForgeRegistryEntry.Impl<Entry> {
        final String label;
        public Entry(String label) { this.label = label; }
        @Override public String toString() { return label; }
    }
    public static class Child extends Entry { public Child(String label) { super(label); } }
    public static class Other extends IForgeRegistryEntry.Impl<Other> {}
    private static final List<Object> trace = new ArrayList<>();
    private static void check(boolean value, String message) { if (!value) throw new AssertionError(message); }
    private static NativeLocation key(String name) { return new NativeLocation("fixture", name); }
    private static Entry entry(String name) { return new Entry(name).setRegistryName(key(name)); }
    @FunctionalInterface private interface Action { Object run(); }
    private static Object capture(Action action) {
        try { return action.run(); }
        catch (RuntimeException failure) { return Arrays.asList(failure.getClass().getName(), failure.getMessage()); }
    }
    private static <T extends Throwable> T fails(Class<T> type, Action action) {
        try { action.run(); } catch (Throwable failure) {
            if (type.isInstance(failure)) return type.cast(failure);
            throw new AssertionError("Expected " + type.getName(), failure);
        }
        throw new AssertionError("Expected " + type.getName());
    }
    private static ForgeRegistry<Entry> registry(String name, int min, int max, boolean modifiable, NativeLocation defaultKey) {
        var builder = new ForgeRegistryBuilder<Entry>().setName(key(name)).setType(Entry.class).setIDRange(min, max).setDefaultKey(defaultKey);
        if (modifiable) builder.allowModification();
        return (ForgeRegistry<Entry>) builder.create();
    }
    private static void reset() { ForgeRegistryManager.ACTIVE.clean(); ForgeRegistryManager.VANILLA.clean(); ForgeRegistryManager.FROZEN.clean(); }
    private static void names(RegistryRuntime runtime) {
        reset(); runtime.activeMod(null);
        check(new Entry("unnamed").getRegistryName() == null, "entry initially unnamed");
        check(new Entry("implicit").setRegistryName("thing").getRegistryName().toString().equals("minecraft:thing"), "no-owner prefix");
        runtime.activeMod("HOST");
        check(ForgeEntryNames.checkPrefix("other:Thing", false).toString().equals("host:thing"), "warn=false discards supplied foreign prefix; native location folds path case");
        check(ForgeEntryNames.checkPrefix("other:Thing", true).toString().equals("other:thing"), "warn=true accepts foreign prefix; native location folds path case");
        check(ForgeEntryNames.checkPrefix("one:two:tail", true).toString().equals("one:two:tail"), "last-colon splitting retained");
        runtime.activeMod("framework", true);
        check(ForgeEntryNames.checkPrefix("thing", true).toString().equals("minecraft:thing"), "injected FML owner is vanilla");
        runtime.activeMod("fixture");
        var child = new Child("child");
        check(child.getRegistryType() == Entry.class && child.delegate.type() == Entry.class, "native TypeToken resolves inherited generic argument");
        var first = new Entry("first"); var second = new Other();
        check(first.delegate.equals(second.delegate), "unnamed delegates compare equal across types");
        check(first.delegate.hashCode() == second.delegate.hashCode(), "native null delegate hash");
        first.setRegistryName("first");
        check(first.delegate.name() == null, "setting entry name does not name its delegate");
        fails(IllegalStateException.class, () -> first.setRegistryName("again"));
        ((RegistryDelegate<Entry>) first.delegate).setName(key("official"));
        check(first.getRegistryName().equals(key("official")), "delegate name takes precedence");
        trace.add(Arrays.asList(child.getRegistryType().getName(), first.getRegistryName().toString(), first.delegate.equals(second.delegate)));
    }
    private static void ranges() {
        reset(); var r = registry("range", 3, 5, true, null);
        var below = entry("below"); check(r.add(0, below) == 0, "explicit positive ID below min is allowed");
        var a = entry("a"); var b = entry("b"); var c = entry("c");
        check(r.add(-1, a) == 3 && r.add(3, b) == 4 && r.add(-1, c) == 5, "native allocation skips occupied IDs");
        fails(RuntimeException.class, () -> r.add(-1, a)); // Exhaustion check precedes duplicate identity check.
        check(r.remove(b.getRegistryName()) == b, "remove returns entry");
        fails(RuntimeException.class, () -> r.add(-1, entry("new"))); // Removal leaves allocation bit set.
        check(r.getRaw(4) == null, "remove clears id->entry even though bit remains occupied");
        r.block(10); check(r.getValue(10) == null, "block has no inserted entry");
        var iterator = r.iterator(); var values = new ArrayList<String>();
        while (iterator.hasNext()) values.add(iterator.next().label);
        check(values.equals(List.of("below", "a", "c")), "iterator skips holes and follows numeric ID order");
        check(iterator.next() == null, "native exhausted iterator returns null");
        trace.add(values);
        reset(); var frozen = registry("freeze", 0, 20, false, null);
        var existing = entry("existing"); frozen.register(existing); frozen.freeze();
        check(frozen.add(-1, existing) == 0, "same-object duplicate precedes freeze guard");
        fails(IllegalStateException.class, () -> frozen.add(-1, entry("late")));
        fails(UnsupportedOperationException.class, () -> { frozen.clear(); return null; });
        reset();
        fails(NegativeArraySizeException.class, () -> registry("overflow", 0, Integer.MAX_VALUE, true, null));
    }
    private static void overrides(RegistryRuntime runtime) throws Exception {
        reset(); var active = registry("override", 0, 20, true, null);
        runtime.activeMod("FIXTURE");
        var old = entry("same"); active.register(old);
        var ownersField = ForgeRegistry.class.getDeclaredField("owners"); ownersField.setAccessible(true);
        var owners = (Map<?, ?>) ownersField.get(active);
        var ownerKey = owners.keySet().iterator().next();
        var ownerField = ownerKey.getClass().getDeclaredField("owner"); ownerField.setAccessible(true);
        String owner = (String) ownerField.get(ownerKey);
        check(owner.equals("FIXTURE".toLowerCase()), "registration owner uses default locale");
        check(ForgeEntryNames.checkPrefix("implicit", true).toString().equals("fixture:implicit"), "prefix uses ROOT locale independently");
        trace.add(owner);
        var current = new Entry("replacement").setRegistryName(key("same"));
        runtime.activeMod(null);
        fails(IllegalStateException.class, () -> active.add(-1, current));
        runtime.activeMod("fixture", true);
        fails(IllegalStateException.class, () -> active.add(-1, current));
        runtime.activeMod("fixture");
        check(active.add(-1, current) == 0, "override reuses original ID");
        check(old.delegate.get() == current && current.delegate.get() == current, "ACTIVE redirects old delegate");
        check(old.delegate.equals(current.delegate), "same-name delegate equality");
        active.resetDelegates(); check(old.delegate.get() == old, "native reset restores old references too");
        var staging = ForgeRegistryManager.FROZEN.<Entry>getRegistry(key("override"), ForgeRegistryManager.ACTIVE);
        check(staging.getValuesCollection().isEmpty(), "registry-manager copy copies definition, not entries");
        var stageOld = new Entry("stage_old").setRegistryName(key("same"));
        var stageNew = new Entry("stage_new").setRegistryName(key("same"));
        staging.register(stageOld); staging.register(stageNew);
        check(stageOld.delegate.get() == stageOld, "non-ACTIVE override does not redirect old delegate");
        trace.add(Arrays.asList(active.getID(current), staging.getID(stageNew), old.delegate.get().label, stageOld.delegate.get().label));
        reset();
        var noOverrides = (ForgeRegistry<Entry>) new ForgeRegistryBuilder<Entry>().setName(key("no_override")).setType(Entry.class).disableOverrides().create();
        noOverrides.register(entry("same"));
        fails(IllegalArgumentException.class, () -> { noOverrides.register(entry("same")); return null; });
    }
    private static void removals() {
        reset(); var r = registry("default", 0, 20, true, key("default"));
        fails(NullPointerException.class, () -> { r.validateKey(); return null; });
        var d = entry("default"); var e = entry("entry"); r.registerAll(d, e); r.validateKey();
        r.addAlias(key("alias"), key("entry"));
        check(r.containsKey(key("alias")) && r.getValue(key("alias")) == e, "alias lookup resolves");
        check(r.getID(key("alias")) == r.getID(d), "getID(name) does not resolve aliases; default fallback applies");
        check(r.getValue(key("missing")) == d && r.getKey(new Entry("unknown")).equals(key("default")), "default fallback");
        r.remove(key("default"));
        check(r.getValue(999) == d && r.getID(new Entry("unknown")) == -1, "removed default object remains fallback but has no ID");
        r.clear();
        check(r.getKeys().isEmpty() && r.getDefault() == d, "clear does not reset defaultValue");
        fails(IllegalStateException.class, () -> { r.register(entry("default")); return null; });
        check(r.getValue(key("missing")) == d, "failed default replacement preserves old fallback");
        trace.add(Arrays.asList(r.getValue(999).label, r.getKeys().size()));
    }
    private static void callbacks() {
        reset(); var calls = new ArrayList<String>();
        var builder = new ForgeRegistryBuilder<Entry>().setName(key("callbacks")).setType(Entry.class).allowModification();
        builder.add((IForgeRegistry.AddCallback<Entry>) (r, s, id, e, old) -> calls.add("one"));
        var first = (ForgeRegistry<Entry>) builder.create();
        builder.add((IForgeRegistry.AddCallback<Entry>) (r, s, id, e, old) -> calls.add("two"));
        first.register(entry("first")); check(calls.equals(List.of("one")), "single callback is captured directly");
        reset(); calls.clear(); var aggregate = (ForgeRegistry<Entry>) builder.create();
        builder.add((IForgeRegistry.AddCallback<Entry>) (r, s, id, e, old) -> calls.add("three"));
        aggregate.register(entry("aggregate")); check(calls.equals(List.of("one", "two", "three")), "aggregate callback reads live builder list");
        trace.add(new ArrayList<>(calls));
        reset(); calls.clear();
        var failing = (ForgeRegistry<Entry>) new ForgeRegistryBuilder<Entry>().setName(key("failing")).setType(Entry.class).allowModification()
                .add((IForgeRegistry.AddCallback<Entry>) (r, s, id, e, old) -> { calls.add("add"); throw new IllegalArgumentException("fixture add failure"); })
                .add((IForgeRegistry.ClearCallback<Entry>) (r, s) -> { calls.add("clear"); throw new IllegalArgumentException("fixture clear failure"); }).create();
        var e = entry("effect"); fails(IllegalArgumentException.class, () -> { failing.register(e); return null; });
        check(failing.getValue(key("effect")) == e && e.delegate.name().equals(key("effect")), "add callback failure keeps map/id/delegate effects");
        fails(IllegalArgumentException.class, () -> { failing.clear(); return null; });
        check(failing.getValue(key("effect")) == e, "clear callback fails before clearing");
        trace.add(Arrays.asList(calls, failing.getID(e)));
        reset();
        var name = key("create_fails");
        fails(IllegalStateException.class, () -> new ForgeRegistryBuilder<Entry>().setName(name).setType(Entry.class)
                .add((IForgeRegistry.CreateCallback<Entry>) (r, s) -> { r.register(entry("during_create")); throw new IllegalStateException("fixture create"); }).create());
        check(ForgeRegistryManager.ACTIVE.getRegistry(name) == null, "creation callback runs before manager inserts registry");
    }
    private static void manager() {
        reset(); var name = key("parent"); registry("parent", 0, 30, true, null);
        check(ForgeRegistryManager.ACTIVE.<Entry>getSuperType(name) == Entry.class, "registry super type");
        check(ForgeRegistryLookup.findRegistry(Entry.class) == ForgeRegistryManager.ACTIVE.getRegistry(name), "native typed registry lookup");
        fails(IllegalArgumentException.class, () -> new ForgeRegistryBuilder<Entry>().setName(key("duplicate_parent")).setType(Entry.class).create());
        // The raw generic call exercises the native runtime parent-type guard.
        @SuppressWarnings({"rawtypes", "unchecked"}) var childBuilder = new ForgeRegistryBuilder().setName(key("child")).setType(Child.class);
        fails(IllegalArgumentException.class, childBuilder::create);
        reset();
        childBuilder.create(); // Registering child first does not prohibit parent afterward.
        registry("parent_after_child", 0, 30, false, null);
        check(ForgeRegistryManager.ACTIVE.registries.size() == 2, "parent overlap check is directional");
        trace.add(ForgeRegistryManager.ACTIVE.registries.keySet().stream().map(Object::toString).sorted().toList());
    }
    private static void wrapper() {
        reset();
        fails(NullPointerException.class, () -> ForgeEntryNames.getWrapper(Entry.class));
        var r = (ForgeRegistry<Entry>) ForgeEntryNames.makeRegistry(key("wrapper"), Entry.class, 20).create();
        var w = (ForgeNamespacedRegistry<Entry>) ForgeEntryNames.getWrapper(Entry.class);
        fails(IllegalArgumentException.class, () -> w.getRandomObject(new Random(1)));
        var named = entry("named"); w.register(2, key("ignored_key"), named);
        check(w.getObject(key("named")) == named && w.getObject(key("ignored_key")) == null, "existing entry name wins over wrapper argument");
        var unnamed = new Entry("unnamed"); w.register(2, key("assigned"), unnamed);
        check(unnamed.getRegistryName().equals(key("assigned")) && w.getIDForObject(unnamed) == 0, "wrapper name assignment and occupied requested ID");
        w.lock();
        fails(IllegalStateException.class, () -> { w.putObject(key("late"), new Entry("late")); return null; });
        r.register(entry("forge_after_wrapper_lock")); // Wrapper locking is separate from Forge registry freeze.
        check(r.getValuesCollection().size() == 3, "Forge registration remains available after wrapper lock");
        trace.add(Arrays.asList(w.getKeys().stream().map(Object::toString).sorted().toList(), w.getIDForObject(named)));
        reset(); registry("no_factory", 0, 20, false, null);
        fails(NullPointerException.class, () -> ForgeEntryNames.getWrapper(Entry.class));
    }
    private static void sync() {
        reset(); var original = registry("sync", 0, 30, true, null);
        original.registerAll(entry("one"), entry("two")); original.addAlias(key("alias"), key("two")); original.addDummy(key("dummy"));
        var target = ForgeRegistryManager.FROZEN.<Entry>getRegistry(key("sync"), ForgeRegistryManager.ACTIVE);
        target.freeze(); target.sync(key("sync"), original);
        check(!target.isLocked() && target.getValue(key("alias")) == original.getValue(key("two")), "sync unlocks and copies aliases/entries");
        check(target.isDummied(key("dummy")), "sync preserves dummy markers after adding values");
        target.validateContent(key("sync"));
        fails(IllegalArgumentException.class, () -> { target.sync(key("sync"), target); return null; });
        trace.add(snapshot(target));
    }
    private static void defaultedWrapper() {
        reset();
        fails(NullPointerException.class, () -> ForgeEntryNames.getWrapperDefaulted(Entry.class));
        var r = (ForgeRegistry<Entry>) ForgeEntryNames.makeRegistry(key("defaulted_wrapper"), Entry.class, 20, key("default")).allowModification().create();
        var w = (ForgeDefaultedRegistry<Entry>) ForgeEntryNames.getWrapperDefaulted(Entry.class);
        check(w instanceof NativeDefaultedRegistry, "defaulted wrapper retains native superclass binding");
        fails(NullPointerException.class, () -> { w.validateKey(); return null; });
        fails(IllegalArgumentException.class, () -> w.getRandomObject(new Random(1)));
        var fallback = entry("default"); w.register(4, key("ignored"), fallback); w.validateKey();
        check(w.getObject(key("missing")) == fallback && w.getObjectById(999) == fallback, "default object fallback");
        check(w.getNameForObject(new Entry("unknown")).equals(key("default")), "default name fallback");
        check(w.getIDForObject(new Entry("unknown")) == 4, "default ID fallback");
        check(!w.containsKey(key("missing")), "default lookup does not manufacture membership");
        var e = new Entry("second"); w.register(4, key("second"), e);
        check(w.getIDForObject(e) == 0 && e.getRegistryName().equals(key("second")), "occupied ID and unnamed registration");
        var keys = w.getKeys(); w.lock();
        fails(IllegalStateException.class, () -> { w.putObject(key("late"), new Entry("late")); return null; });
        r.register(entry("native_after_lock"));
        check(keys.size() == 3, "wrapper lock does not freeze registry or its live keys");
        r.remove(key("default"));
        check(w.getObjectById(4) == fallback && w.getIDForObject(fallback) == -1, "removed default retains fallback, not its ID");
        r.clear();
        check(w.getObject(key("missing")) == fallback && w.getKeys().isEmpty(), "clear preserves native default");
        fails(IllegalArgumentException.class, () -> w.getRandomObject(new Random(1)));
        trace.add(Arrays.asList(w.getObjectById(4).label, w.getIDForObject(fallback), keys.size()));
        reset(); ForgeEntryNames.makeRegistry(key("not_defaulted"), Entry.class, 20).create();
        // Both native factories intentionally use the same slave-map key; a wrong wrapper casts, not misses.
        fails(ClassCastException.class, () -> ForgeEntryNames.getWrapperDefaulted(Entry.class));
    }
    private static void views() {
        reset(); var r = registry("views", 0, 30, true, null); var values = r.getValuesCollection(); var copy = r.getValues();
        r.register(entry("one")); check(values.size() == 1 && copy.isEmpty(), "live values vs immutable snapshot");
        fails(UnsupportedOperationException.class, () -> { values.clear(); return null; });
        r.setSlaveMap(key("slave"), "value");
        Object unchecked = r.getSlaveMap(key("slave"), Integer.class);
        check(unchecked.equals("value"), "slave Class parameter does not impose a runtime type check");
        r.clear(); check(r.getSlaveMap(key("slave"), Object.class).equals("value"), "clear preserves slave maps");
        trace.add(Arrays.asList(values.size(), copy.size(), unchecked));
    }
    private static Object snapshot(ForgeRegistry<Entry> r) {
        var names = new TreeMap<String, Object>();
        for (var key : r.getKeys()) { var value = r.getValue(key); names.put(key.toString(), Arrays.asList(value.label, r.getID(value), value.delegate.get().label)); }
        var order = new ArrayList<String>(); for (var value : r) order.add(value.label);
        return Arrays.asList(names, order, r.isLocked(), r.getDefault() == null ? null : r.getDefault().label);
    }
    private static void vectors(RegistryRuntime runtime) {
        var random = new Random(0x464f524745L); ForgeRegistry<Entry> current = null;
        for (int i = 0; i < 8192; i++) {
            if (i % 128 == 0) { reset(); current = registry("vectors", random.nextInt(3), 12, true, null); }
            final var r = current; final int step = i; final int id = random.nextInt(18) - 2;
            String name = "entry_" + random.nextInt(8); int operation = random.nextInt(10);
            runtime.activeMod(i % 7 == 0 ? null : "fixture");
            Object result = capture(() -> switch (operation) {
                case 0, 1, 2 -> r.add(id, new Entry("value_" + step).setRegistryName(key(name)));
                case 3 -> { var removed = r.remove(key(name)); yield removed == null ? null : removed.label; }
                case 4 -> { r.freeze(); yield null; }
                case 5 -> { r.unfreeze(); yield null; }
                case 6 -> { r.clear(); yield null; }
                case 7 -> { r.block(Math.max(0, id)); yield null; }
                case 8 -> { r.addAlias(key("alias_" + name), key(name)); yield r.getValue(key("alias_" + name)) == null; }
                case 9 -> { var existing = r.getValue(key(name)); yield existing == null ? null : r.add(-1, existing); }
                default -> throw new AssertionError();
            });
            trace.add(Arrays.asList(operation, id, result, snapshot(r)));
        }
    }
    private static void groovy() throws Exception {
        reset(); var r = registry("groovy", 0, 30, true, null); var e = entry("groovy");
        var shell = new groovy.lang.GroovyShell(ForgeRegistryConformance.class.getClassLoader(), new groovy.lang.Binding(Map.of("registry", r, "entry", e)));
        Object value = shell.evaluate("""
            registry.register(entry)
            assert registry.getValue(entry.registryName).is(entry)
            assert entry.delegate.get().is(entry)
            try { registry.register('not an entry'); assert false }
            catch (groovy.lang.MissingMethodException expected) { assert expected.method == 'register' }
            registry.freeze()
            return [registry.getID(entry), registry.locked]
            """, "ForgeRegistryDeveloperFixture.groovy");
        check(value.equals(List.of(0, true)), "native Groovy dispatch and typed rejection"); trace.add(value);
        shell.getClassLoader().close();
    }
    private static String digest(Object object) throws Exception {
        return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(Json.write(object).getBytes(StandardCharsets.UTF_8)));
    }
    public static void main(String[] args) throws Exception {
        NativeRuntime.require(); WorkerIsolation.install();
        // Comparisons cover state and failures, not source-line stack trace text.
        org.apache.logging.log4j.core.config.Configurator.setLevel("FML", org.apache.logging.log4j.Level.OFF);
        try (var runtime = RegistryRuntime.open(Path.of(args[0])); var env = FluidEnvironment.isolatedProducer(runtime)) {
            runtime.activeMod("fixture");
            if (args.length > 1 && args[1].equals("source-edit")) {
                var r = (ForgeRegistry<Entry>) ForgeEntryNames.makeRegistry(key("edit"), Entry.class, 20).create();
                var e = entry("edit"); r.register(e);
                System.out.println(Json.write(Map.of("firstId", r.getID(e), "name", e.getRegistryName().toString(), "wholePackParity", false)));
                return;
            }
            names(runtime); ranges(); overrides(runtime); removals(); callbacks(); manager(); wrapper(); defaultedWrapper(); sync(); views(); groovy(); vectors(runtime);
            System.out.println(Json.write(Map.of("scenarios", 11, "operations", 8192, "traceDigest", digest(trace),
                    "minecraftLaunched", false, "wholePackParity", false, "kernelIsolation", true)));
        }
    }
}
