package research.orthrus.axiom;

import java.lang.reflect.*;
import java.nio.file.Path;
import java.util.*;

/** Locked source is compiled independently into Original* classes by the comparison tool. */
public final class RegistryConformance {
    interface Action { Object run() throws Exception; }
    static Object call(Object receiver, String name, Class<?>[] types, Object... args) throws Exception {
        try { return receiver.getClass().getMethod(name, types).invoke(receiver, args); }
        catch (InvocationTargetException failure) {
            if (failure.getCause() instanceof Exception error) throw error;
            throw (Error) failure.getCause();
        }
    }
    static String outcome(Action action) {
        try { return "return:" + String.valueOf(action.run()); }
        catch (Exception error) { return error.getClass().getName() + ":" + error.getMessage(); }
    }
    static Object call(Object receiver, String name) throws Exception { return call(receiver, name, new Class<?>[0]); }
    static void equal(Object actual, Object original, String label) {
        if (!Objects.equals(actual, original)) throw new AssertionError(label + "\n" + actual + "\n" + original);
    }
    static Object registry(Object manager, String name) throws Exception {
        return call(manager, "getRegistry", new Class<?>[]{String.class}, name);
    }
    static List<String> snapshot(Object manager, RegistryRuntime runtime, MaterialState[] materials) throws Exception {
        List<String> state = new ArrayList<>();
        state.add(outcome(() -> call(manager, "getPhase")));
        state.add(outcome(() -> call(manager, "getRegisteredMaterials")));
        var diagnostics = runtime.diagnostics();
        state.add(diagnostics.size() + ":" + (diagnostics.isEmpty() ? "" : diagnostics.getLast()));
        for (String namespace : List.of("gregtech", "susy", "pack", "unknown")) {
            Object registry = registry(manager, namespace);
            state.add(namespace + ":" + call(registry, "getNetworkId") + ":" + call(registry, "isFrozen"));
            state.add(call(registry, "getAllMaterials").toString());
            List<Object> iterated = new ArrayList<>();
            ((Iterable<?>) registry).forEach(iterated::add);
            state.add(iterated.toString());
            for (int id = 0; id < 6; id++) state.add(String.valueOf(call(registry, "getObjectById", new Class<?>[]{int.class}, id)));
            for (MaterialState material : materials) {
                state.add(String.valueOf(call(registry, "getNameForObject", new Class<?>[]{Object.class}, material)));
                state.add(String.valueOf(call(registry, "getIDForObject", new Class<?>[]{Object.class}, material)));
                state.add(String.valueOf(call(registry, "getObject", new Class<?>[]{Object.class}, material.toString())));
            }
        }
        return state;
    }
    static Object operation(Object manager, RegistryRuntime runtime, MaterialState[] materials, int operation, int a, int b) throws Exception {
        Object registry = registry(manager, (a & 1) == 0 ? "susy" : "gregtech");
        return switch (operation) {
            case 0 -> call(registry, "register", new Class<?>[]{int.class, Object.class, Object.class}, b - 1, "m" + a, materials[b]);
            case 1 -> call(manager, "unfreezeRegistries");
            case 2 -> call(manager, "freezeRegistries");
            case 3 -> call(manager, "closeRegistries");
            case 4 -> { runtime.activeMod((a & 1) == 0 ? "gregtech" : null); yield null; }
            case 5 -> call(manager, "createRegistry", new Class<?>[]{String.class}, "pack");
            case 6 -> call(registry, "setFallbackMaterial", new Class<?>[]{MaterialState.class}, materials[b]);
            case 7 -> call(registry, "getFallbackMaterial");
            case 8 -> call(manager, "getMaterial", new Class<?>[]{String.class}, ((a & 1) == 0 ? "susy:" : "unknown:") + "m" + b);
            case 9 -> call(registry, "register", new Class<?>[]{MaterialState.class}, materials[b]);
            default -> throw new AssertionError(operation);
        };
    }
    static int registries(Path root) throws Exception {
        int operations = 0;
        // One pair of verified utility loaders; each graph has a fresh manager in each scope.
        try (var actualRuntime = RegistryRuntime.open(root); var originalRuntime = RegistryRuntime.open(root)) {
            for (int seed = 0; seed < 1000; seed++) {
                Random random = new Random(seed);
                Object actual = new MaterialRegistryManager(actualRuntime);
                Object original = new OriginalMaterialRegistryManager(originalRuntime);
                actualRuntime.activeMod("gregtech"); originalRuntime.activeMod("gregtech");
                MaterialState[] materials = new MaterialState[6];
                for (int i = 0; i < materials.length; i++) materials[i] = new MaterialState(i, "susy", "m" + i, () -> true);
                for (Object manager : List.of(actual, original)) {
                    call(manager, "createRegistry", new Class<?>[]{String.class}, "susy");
                    call(registry(manager, "gregtech"), "setFallbackMaterial", new Class<?>[]{MaterialState.class}, materials[0]);
                }
                for (int step = 0; step < 24; step++) {
                    int op = step < 4 ? 0 : random.nextInt(10), a = random.nextInt(6), b = random.nextInt(6);
                    // createRegistry returns a distinct object; compare its public identity, not Object.toString.
                    String left = outcome(() -> normalize(operation(actual, actualRuntime, materials, op, a, b)));
                    String right = outcome(() -> normalize(operation(original, originalRuntime, materials, op, a, b)));
                    equal(left, right, "registry outcome " + seed + "/" + step);
                    equal(snapshot(actual, actualRuntime, materials), snapshot(original, originalRuntime, materials), "registry state " + seed + "/" + step);
                    operations++;
                }
            }
        }
        return operations;
    }
    static Object normalize(Object value) throws Exception {
        return value instanceof NativeNamedRegistry<?, ?> ? call(value, "getModid") + ":" + call(value, "getNetworkId") : value;
    }
    record Key(String name, int priority) implements FluidRegistration.Key {
        public int getRegistrationPriority() { return priority; }
        public int hashCode() { return name.hashCode(); }
        public String toString() { return name; }
    }
    static final class FluidRun {
        final List<String> effects = new ArrayList<>();
        final Object storage;
        FluidRun(boolean original, Key liquid) {
            java.util.function.Supplier<FluidRegistration.Builder<Key, Integer>> defaults = () -> builder(7);
            java.util.function.Consumer<MaterialState> collision = m -> effects.add("collision:" + m);
            storage = original ? new OriginalFluidRegistration<>(liquid, defaults, collision) : new FluidRegistration<>(liquid, defaults, collision);
        }
        FluidRegistration.Builder<Key, Integer> builder(int id) {
            return (mod, material, key) -> {
                effects.add("build:" + id + ":" + mod + ":" + material + ":" + key);
                if (id == 3) throw new IllegalArgumentException("producer unavailable");
                return id == 4 ? null : id;
            };
        }
        Object operation(int op, Key key, int value, MaterialState material) throws Exception {
            return switch (op) {
                case 0 -> call(storage, "enqueueRegistration", new Class<?>[]{FluidRegistration.Key.class, FluidRegistration.Builder.class}, key, builder(value));
                case 1 -> call(storage, "store", new Class<?>[]{FluidRegistration.Key.class, Object.class}, key, value == 4 ? null : value);
                case 2 -> call(storage, "registerFluids", new Class<?>[]{MaterialState.class}, material);
                case 3 -> call(storage, "get", new Class<?>[]{FluidRegistration.Key.class}, key);
                case 4 -> call(storage, "getQueuedBuilder", new Class<?>[]{FluidRegistration.Key.class}, key) != null;
                default -> throw new AssertionError(op);
            };
        }
        List<String> snapshot(Key[] keys) {
            var state = new ArrayList<>(effects);
            for (Key key : keys) {
                state.add(outcome(() -> operation(3, key, 0, null)));
                state.add(outcome(() -> operation(4, key, 0, null)));
            }
            return state;
        }
    }
    static int fluids() {
        int operations = 0;
        Key[] keys = {new Key("liquid", 0), new Key("Aa", 0), new Key("BB", 0), new Key("plasma", -1),
                new Key("maximum", Integer.MAX_VALUE), new Key("minimum", Integer.MIN_VALUE)};
        MaterialState material = new MaterialState(1, "susy", "coolant", () -> true);
        for (int seed = 0; seed < 2000; seed++) {
            Random random = new Random(seed);
            FluidRun actual = new FluidRun(false, keys[0]), original = new FluidRun(true, keys[0]);
            for (int step = 0; step < 24; step++) {
                int op = step < 4 ? random.nextInt(2) : random.nextInt(5), value = random.nextInt(6);
                Key key = keys[random.nextInt(keys.length)];
                equal(outcome(() -> actual.operation(op, key, value, material)), outcome(() -> original.operation(op, key, value, material)), "fluid outcome " + seed + "/" + step);
                equal(actual.snapshot(keys), original.snapshot(keys), "fluid state " + seed + "/" + step);
                operations++;
            }
        }
        return operations;
    }
    public static void main(String[] args) throws Exception {
        int registries = registries(Path.of(args[0])), fluids = fluids();
        System.out.println(Json.write(Map.of("execution", "native-jvm", "registryOperations", registries,
                "fluidQueueOperations", fluids, "wholePackParity", false, "fluidBuildersExecuted", false)));
    }
}
