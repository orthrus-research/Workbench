package research.orthrus.axiom;

import java.lang.reflect.*;
import java.util.*;
import java.util.function.BooleanSupplier;

/** Same operation stream, separately compiled locked property bodies, explicit shared name/phase carriers. */
final class MaterialConformance {
    private static final String[] KEYS = {"DUST", "GEM", "INGOT", "EMPTY"};

    static int run() throws Throwable {
        var actual = new Access("research.orthrus.axiom.");
        var original = new Access("research.orthrus.axiom.oracle.");
        var random = new Random(0x4d4154455249414cL);
        int operations = 0;
        for (int graph = 0; graph < 2000; graph++) {
            var a = new Scenario(actual);
            var b = new Scenario(original);
            for (int i = 0; i < 24; i++) {
                int op = random.nextInt(9), target = random.nextInt(3), key = random.nextInt(3);
                int value = random.nextInt(7) - 2, reference = random.nextInt(3);
                String got = a.apply(op, target, key, value, reference);
                String expected = b.apply(op, target, key, value, reference);
                if (!got.equals(expected) || !a.snapshot().equals(b.snapshot()))
                    throw new AssertionError("Native material mismatch at graph " + graph + " step " + i
                            + ": " + got + " != " + expected + "; " + a.snapshot() + " != " + b.snapshot());
                operations++;
            }
        }
        return operations;
    }

    private static final class Access {
        final Class<?> material, properties, key, property, dust, gem, ingot, phase;
        final Object[] keys = new Object[4], phases;
        final Map<String, Method> methods = new HashMap<>();
        Access(String prefix) throws Exception {
            material = Class.forName(prefix + "MaterialState");
            properties = Class.forName(prefix + "MaterialProperties");
            key = Class.forName(prefix + "PropertyKey");
            property = Class.forName(prefix + "IMaterialProperty");
            dust = Class.forName(prefix + "DustProperty");
            gem = Class.forName(prefix + "GemProperty");
            ingot = Class.forName(prefix + "IngotProperty");
            phase = Class.forName(prefix + "MaterialPhase");
            phases = phase.getEnumConstants();
            for (int i = 0; i < keys.length; i++) {
                Field field = key.getField(KEYS[i]);
                field.setAccessible(true);
                keys[i] = field.get(null);
            }
        }
        Object construct(Class<?> type, Class<?>[] types, Object... args) throws Throwable {
            var constructor = type.getDeclaredConstructor(types);
            constructor.setAccessible(true);
            try { return constructor.newInstance(args); }
            catch (InvocationTargetException failure) { throw failure.getCause(); }
        }
        Object call(Object receiver, String name, Object... args) throws Throwable {
            String id = receiver.getClass().getName() + ":" + name + ":" + args.length;
            Method method = methods.get(id);
            if (method == null) {
                for (Method candidate : receiver.getClass().getDeclaredMethods()) {
                    if (candidate.getName().equals(name) && candidate.getParameterCount() == args.length) {
                        if (method != null) throw new AssertionError("Ambiguous comparison method " + id);
                        method = candidate;
                    }
                }
                if (method == null) throw new AssertionError("Missing comparison method " + id);
                method.setAccessible(true);
                methods.put(id, method);
            }
            try { return method.invoke(receiver, args); }
            catch (InvocationTargetException failure) { throw failure.getCause(); }
        }
        Object value(int which, int number) throws Throwable {
            return which == 0 ? construct(dust, new Class<?>[]{int.class, int.class}, number, number)
                    : construct(which == 1 ? gem : ingot, new Class<?>[]{});
        }
    }

    private static final class Scenario {
        final Access access;
        final Object[] materials = new Object[3], properties = new Object[3];
        int phase = 1;
        Scenario(Access access) throws Throwable {
            this.access = access;
            BooleanSupplier canModify = () -> {
                try { return (boolean) access.call(access.phases[phase], "canModifyMaterials"); }
                catch (Throwable failure) { throw new AssertionError(failure); }
            };
            for (int i = 0; i < 3; i++) {
                materials[i] = access.construct(access.material, new Class<?>[]{String.class, BooleanSupplier.class}, "material_" + i, canModify);
                properties[i] = access.call(materials[i], "getProperties");
            }
        }
        String apply(int op, int target, int key, int value, int reference) throws Throwable {
            try {
                Object properties = this.properties[target];
                switch (op) {
                    case 0 -> access.call(properties, "verify");
                    case 1 -> access.call(properties, "setProperty", access.keys[key], access.value(key, value));
                    case 2 -> access.call(materials[target], "setProperty", access.keys[key], access.value(key, value));
                    case 3 -> access.call(properties, "ensureSet", access.keys[key], value % 2 == 0);
                    case 4 -> {
                        Object ingot = access.value(2, value);
                        String method = new String[]{"setSmeltingInto", "setArcSmeltingInto", "setMacerateInto", "setMagneticMaterial"}[Math.floorMod(value, 4)];
                        access.call(ingot, method, materials[reference]);
                        access.call(materials[target], "setProperty", access.keys[2], ingot);
                    }
                    case 5 -> phase = Math.floorMod(value, 4);
                    case 6, 7 -> {
                        Object dust = access.call(properties, "getProperty", access.keys[0]);
                        if (dust != null) access.call(dust, op == 6 ? "setHarvestLevel" : "setBurnTime", value);
                    }
                    case 8 -> access.call(properties, "setProperty", access.keys[key], null);
                    default -> throw new AssertionError(op);
                }
                return "ok";
            } catch (IllegalArgumentException | IllegalStateException failure) {
                return failure.getClass().getSimpleName() + ":" + failure.getMessage();
            }
        }
        List<Object> snapshot() throws Throwable {
            var out = new ArrayList<Object>();
            out.add(phase);
            for (Object properties : this.properties) {
                for (int key = 0; key < 4; key++) {
                    Object value = access.call(properties, "getProperty", access.keys[key]);
                    out.add(value == null ? "absent" : KEYS[key]);
                    if (value != null && key == 0) {
                        out.add(access.call(value, "getHarvestLevel"));
                        out.add(access.call(value, "getBurnTime"));
                    }
                    if (value != null && key == 2) for (String name : List.of("getSmeltingInto", "getArcSmeltInto", "getMacerateInto", "getMagneticMaterial")) {
                        Object reference = access.call(value, name);
                        out.add(reference == null ? "null" : reference.toString());
                    }
                }
            }
            return out;
        }
    }
}
