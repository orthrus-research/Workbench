package research.orthrus.axiom;

import java.nio.file.Path;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;

/** Native source comparison against explicitly synthetic catalog/config inputs. */
public final class PrefixConformance {
    private static int nextId;
    private static final Map<String, FluidMaterial> materials = new TreeMap<>();
    private static boolean lowQuality, uniqueStones;
    private static final List<Object> trace = new ArrayList<>();
    private static FluidMaterial make(String name) {
        return new FluidMaterial.Builder(++nextId, new NativeLocation("gregtech", name)).dust().build();
    }
    private static void check(boolean value, String message) { if (!value) throw new AssertionError(message); }
    @FunctionalInterface private interface Action { Object run(); }
    private static Object capture(Action action) {
        try { return action.run(); }
        catch (RuntimeException failure) { return Arrays.asList(failure.getClass().getName(), failure.getMessage()); }
    }
    private static String digest(Object object) throws Exception {
        return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(Json.write(object).getBytes(StandardCharsets.UTF_8)));
    }
    private static Object markers() {
        MarkerMaterials.register();
        var colors = new ArrayList<Object>();
        NativeDyeColor[] original = NativeDyeColor.values();
        for (var color : original) {
            var marker = MarkerMaterials.Color.COLORS.get(color);
            colors.add(Arrays.asList(color.enumName(), color.getName(), color.getMetadata(), color.getDyeDamage(),
                    marker == null ? null : marker.toString()));
        }
        check(original.length == 16, "native dye catalog size");
        check(MarkerMaterials.Color.COLORS.containsKey(original[8]), "silver key retained");
        check(MarkerMaterials.Color.COLORS.get(original[8]) == null, "silver must not be normalized to light_gray");
        check(MarkerMaterials.Color.COLORS.inverse().get(null) == original[8], "native BiMap null-value identity");
        check(MarkerMaterials.Color.valueOf("light_gray") == MarkerMaterials.Color.LightGray, "light gray marker still exists");
        original[0] = null;
        check(NativeDyeColor.values()[0] != null, "values array must be cloned");
        check(NativeDyeColor.values()[9] == MarkerMaterials.Color.COLORS.inverse().get(MarkerMaterials.Color.Cyan), "cyan identity");
        check(FluidEnvironment.current().markers().getAll().stream().noneMatch(m -> m.toString().equals("resistor")), "component markers are lazy");
        check(MarkerMaterials.Component.Resistor.toString().equals("resistor"), "component initialization");
        check(FluidEnvironment.current().markers().getAll().stream().filter(m -> Set.of("resistor", "transistor", "capacitor", "diode", "inductor").contains(m.toString())).count() == 5, "all component markers retained");
        return Arrays.asList(colors, Arrays.stream(MarkerMaterials.Color.VALUES).map(Object::toString).toList(),
                FluidEnvironment.current().markers().getAll().stream().map(Object::toString).sorted().toList());
    }
    private static Object snapshot() {
        var result = new ArrayList<Object>();
        for (var p : OrePrefix.values().stream().sorted(Comparator.comparingInt(p -> p.id)).toList()) {
            var ignored = new ArrayList<String>();
            var amounts = new TreeMap<String, Long>();
            for (var entry : materials.entrySet()) {
                if (p.isIgnored(entry.getValue())) ignored.add(entry.getKey());
                if (p.isAmountModified(entry.getValue())) amounts.put(entry.getKey(), p.getMaterialAmount(entry.getValue()));
            }
            result.add(Arrays.asList(p.name, p.id, p.isSelfReferencing, p.isUnificationEnabled, p.getMaterialAmount(null),
                    String.valueOf(p.materialType), String.valueOf(p.materialIconType), p.maxStackSize, p.isMarkerPrefix(),
                    ignored, amounts, p.secondaryMaterials.stream().map(s -> List.of(s.material.toString(), s.amount)).toList()));
        }
        return result;
    }
    private static void contracts() {
        var dust = make("fixture_dust");
        var gem = new FluidMaterial.Builder(++nextId, new NativeLocation("gregtech", "fixture_gem")).gem().build();
        check(OrePrefix.dust.doGenerateItem(dust), "dust property predicate");
        check(!OrePrefix.gem.doGenerateItem(dust), "gem property predicate");
        lowQuality = false; check(!OrePrefix.gemChipped.doGenerateItem(gem), "config false");
        lowQuality = true; check(OrePrefix.gemChipped.doGenerateItem(gem), "live config true");
        check(!OrePrefix.paneGlass.doGenerateItem(gem), "self referencing never generates");
        check(OrePrefix.block.doGenerateItem(dust), "null condition means true, not false");
        var wood = new FluidMaterial.Builder(++nextId, new NativeLocation("gregtech", "fixture_wood")).wood()
                .fluidPipeProperties(300, 1, false).build();
        for (var p : List.of(OrePrefix.pipeTinyFluid, OrePrefix.pipeHugeFluid, OrePrefix.pipeQuadrupleFluid, OrePrefix.pipeNonupleFluid))
            check(p.isIgnored(wood), "wood ignored by " + p.name);
        check(!OrePrefix.pipeSmallFluid.isIgnored(wood), "wood small pipe not excluded");
        var originalTiny = OrePrefix.pipeTinyFluid;
        OrePrefix.values().remove(originalTiny);
        var replacementTiny = new OrePrefix("pipeTinyFluid", 1, null, null, 0, null);
        var secondWood = new FluidMaterial.Builder(++nextId, new NativeLocation("gregtech", "fixture_second_wood")).wood()
                .fluidPipeProperties(300, 1, false).build();
        check(originalTiny.isIgnored(secondWood) && !replacementTiny.isIgnored(secondWood), "wood uses source static identity, not name lookup");

        int id = new OrePrefix("fixture_before_failure", 1, null, null, 0, null).id;
        var duplicate = capture(() -> new OrePrefix("fixture_before_failure", 1, null, null, 0, null));
        check(duplicate instanceof List<?>, "duplicate rejection");
        var failed = capture(() -> new OrePrefix("fixture_failed_self", 1, null, null, OrePrefix.Flags.SELF_REFERENCING, null));
        check(failed instanceof List<?>, "null self reference rejection");
        check(OrePrefix.getPrefix("fixture_failed_self") == null, "failed constructor must not register");
        var after = new OrePrefix("fixture_after_failure", 1, null, null, 0, null);
        check(after.id == id + 2, "null self-reference consumes ID, duplicate does not");
        trace.add(Arrays.asList(duplicate, failed, after.id - id));
        var live = OrePrefix.values(); live.remove(after);
        check(OrePrefix.getPrefix(after.name) == null, "values is live mutable view");
        check(new OrePrefix(after.name, 1, null, null, 0, null).id == after.id + 1, "name can be reused after removal");

        var p = new OrePrefix("fixture_handlers", 0, null, null, 0, null);
        var calls = new ArrayList<String>(); var fail = new boolean[]{true};
        p.addProcessingHandler((prefix, material) -> {
            check(OrePrefix.getCurrentProcessingPrefix() == prefix && OrePrefix.getCurrentMaterial() == material, "native handler context");
            calls.add("first"); if (fail[0]) throw new IllegalArgumentException("fixture handler failure");
        }, (prefix, material) -> calls.add("second"));
        p.processOreRegistration(dust); p.processOreRegistration(dust);
        capture(() -> { OrePrefix.runMaterialHandlers(); return null; });
        check(calls.equals(List.of("first")), "failure stops subsequent handler");
        check(OrePrefix.getCurrentMaterial() == dust && OrePrefix.getCurrentProcessingPrefix() == p, "no invented finally cleanup");
        fail[0] = false; OrePrefix.runMaterialHandlers();
        check(calls.equals(List.of("first", "first", "second")), "failed work is retained; registration deduplicated");
        check(OrePrefix.getCurrentMaterial() == null && OrePrefix.getCurrentProcessingPrefix() == null, "normal completion clears context");
        OrePrefix.runMaterialHandlers(); check(calls.size() == 3, "normal completion clears generated set");
        trace.add(calls);

        var guarded = new OrePrefix("fixture_guarded", 0, null, null, 0, null);
        var guardedCalls = new ArrayList<String>();
        guarded.addProcessingHandler(PropertyKey.GEM, (prefix, material, property) -> guardedCalls.add(material.getName()));
        guarded.processOreRegistration(dust); guarded.processOreRegistration(gem);
        OrePrefix.runMaterialHandlers(); check(guardedCalls.size() == 1, "property-specific handler guard");
        gem.addFlags(MaterialFlags.NO_UNIFICATION); guarded.processOreRegistration(gem);
        OrePrefix.runMaterialHandlers(); check(guardedCalls.size() == 1, "NO_UNIFICATION handler guard");
        check(!guarded.addProcessingHandler(new IOreRegistrationHandler[0]), "empty handler add");
        check(capture(() -> guarded.addProcessingHandler((IOreRegistrationHandler) null)) instanceof List<?>, "null handler rejected before add");
        var defaultPrefix = new OrePrefix("fixture_default", 1, dust, null, OrePrefix.Flags.SELF_REFERENCING, null);
        defaultPrefix.addProcessingHandler((prefix, material) -> check(material == dust, "default material identity"));
        defaultPrefix.processOreRegistration(null); OrePrefix.runMaterialHandlers();

        var icon = new MaterialIconType("fixtureCamelCase");
        check(icon.name.equals("fixture_camel_case"), "native CaseFormat icon name");
        check(capture(() -> new MaterialIconType("fixtureCamelCase")) instanceof List<?>, "normalized icon duplicate");
        check(new MaterialIconType("fixtureAfterDuplicate").id == icon.id + 1, "icon duplicate does not consume ID");
    }
    private static void vectors() {
        var material = make("fixture_vectors"); var random = new Random(0x505245464958L);
        var p = new OrePrefix("fixture_vectors", 1234567, null, null, OrePrefix.Flags.ENABLE_UNIFICATION, null);
        for (int i = 0; i < 4096; i++) {
            int operation = i % 8;
            float amount = Float.intBitsToFloat(random.nextInt());
            Object result = capture(() -> {
                switch (operation) {
                    case 0 -> p.modifyMaterialAmount(material, amount);
                    case 1 -> p.setIgnored(material);
                    case 2 -> p.removeIgnored(material);
                    case 3 -> p.setGenerationCondition(m -> m.hasProperty(PropertyKey.GEM));
                    case 4 -> p.setGenerationCondition(null);
                    case 5 -> p.setMarkerPrefix(!p.isMarkerPrefix());
                    case 6 -> p.modifyMaterialAmount(null, amount);
                    case 7 -> p.setAlternativeOreName("alternative_" + Float.floatToRawIntBits(amount));
                    default -> throw new AssertionError();
                }
                return null;
            });
            trace.add(Arrays.asList(operation, Float.floatToRawIntBits(amount), result, p.getMaterialAmount(material),
                    p.getMaterialAmount(null), p.isAmountModified(null), p.isIgnored(material), p.doGenerateItem(material),
                    p.isMarkerPrefix(), p.getAlternativeOreName()));
        }
    }
    private static void generationMatrix() throws Exception {
        var flags = new ArrayList<MaterialFlag>();
        for (var field : MaterialFlags.class.getDeclaredFields())
            if (java.lang.reflect.Modifier.isStatic(field.getModifiers()) && field.getType() == MaterialFlag.class)
                flags.add((MaterialFlag) field.get(null));
        var random = new Random(0x47454e4552415445L);
        var prefixes = OrePrefix.values().stream().sorted(Comparator.comparingInt(p -> p.id)).toList();
        for (int i = 0; i < 64; i++) {
            var b = new FluidMaterial.Builder(++nextId, new NativeLocation("gregtech", "matrix_" + (char)('a' + i / 26) + (char)('a' + i % 26))).dust();
            if ((i & 1) != 0) b.ingot();
            if ((i & 2) != 0) b.gem();
            if ((i & 4) != 0) b.ore();
            if ((i & 8) != 0) b.rotorStats(1, 1, 100);
            if ((i & 16) != 0) b.toolStats(ToolProperty.Builder.of(1, 1, 100, 1).build());
            if ((i & 32) != 0) b.blast(i == 32 ? 1750 : 1751);
            for (var flag : flags) if (random.nextBoolean()) b.flags(flag);
            // Ingot and gem deliberately conflict; preserve the source failure,
            // do not collapse invalid graphs into a successful generation result.
            Object built = capture(b::build);
            if (!(built instanceof FluidMaterial material)) { trace.add(built); continue; }
            for (boolean low : new boolean[]{false, true}) {
                lowQuality = low;
                trace.add(Arrays.asList(i, low, prefixes.stream().map(p -> capture(() -> p.doGenerateItem(material))).toList()));
            }
        }
    }
    private static void groovy() throws Exception {
        var material = make("fixture_groovy");
        var prefix = new OrePrefix("fixture_groovy", 42, null, null, 0, null);
        var binding = new groovy.lang.Binding(Map.of("prefix", prefix, "material", material, "dustKey", PropertyKey.DUST));
        var shell = new groovy.lang.GroovyShell(PrefixConformance.class.getClassLoader(), binding);
        // Controlled source fixture, not a rewritten pack producer or a game harness.
        Object result = shell.evaluate("""
            assert prefix.doGenerateItem(material)
            prefix.setIgnored(material)
            assert !prefix.doGenerateItem(material)
            prefix.removeIgnored(material)
            prefix.setGenerationCondition { candidate -> candidate.hasProperty(dustKey) }
            assert prefix.doGenerateItem(material)
            prefix.modifyMaterialAmount(material, 0.5f)
            try {
                prefix.setIgnored('not a material')
                assert false : 'must preserve the typed material argument'
            } catch (groovy.lang.MissingMethodException expected) {
                assert expected.method == 'setIgnored'
            }
            return [prefix.getMaterialAmount(material), prefix.isIgnored(material)]
            """, "PrefixDeveloperFixture.groovy");
        check(result.equals(List.of(MaterialVoltages.M / 2, false)), "native Groovy dispatch and typed argument rejection");
        trace.add(result);
        shell.getClassLoader().close();
    }
    public static void main(String[] args) throws Exception {
        NativeRuntime.require(); WorkerIsolation.install();
        try (var runtime = RegistryRuntime.open(Path.of(args[0])); var env = FluidEnvironment.isolatedProducer(runtime)) {
            runtime.activeMod("gregtech"); runtime.materials().unfreezeRegistries();
            // All required catalog fields are explicit fixtures, not recovered GT material producers.
            for (String field : args[1].split(",")) materials.put(field, make("fixture_" + field.toLowerCase(Locale.ROOT)));
            uniqueStones = Boolean.parseBoolean(args[2]);
            env.bindPrefixes(new PrefixDependencies.Inputs() {
                public FluidMaterial material(String field) {
                    if (!materials.containsKey(field)) throw new AssertionError("Undeclared fixture field: " + field);
                    return materials.get(field);
                }
                public boolean generateLowQualityGems() { return lowQuality; }
                public boolean allUniqueStoneTypes() { return uniqueStones; }
            });
            if (args.length > 5 && args[5].equals("null-stone")) {
                materials.put("Stone", null);
                try { OrePrefix.values(); throw new AssertionError("null Stone must fail self-reference construction"); }
                catch (ExceptionInInitializerError failure) {
                    check(failure.getCause() instanceof NullPointerException, "source constructor failure must survive");
                    check(failure.getCause().getMessage().equals("Material is null for self-referencing OrePrefix"), "source constructor diagnostic");
                    // Native class initialization is erroneous afterward, not automatically retried.
                    try { OrePrefix.values(); throw new AssertionError("failed class must remain erroneous"); }
                    catch (NoClassDefFoundError expected) { /* native JVM state */ }
                    System.out.println(Json.write(Map.of("status", "rejected-null-stone", "wholePackParity", false, "kernelIsolation", true)));
                    return;
                }
            }
            Object markers = markers();
            int catalogCount = OrePrefix.values().size();
            check(catalogCount == Integer.parseInt(args[3]), "every source prefix declaration must register");
            int iconCount = MaterialIconType.ICON_TYPES.size();
            check(iconCount == Integer.parseInt(args[4]), "every source icon declaration must register");
            OrePrefix.init();
            Object catalog = snapshot();
            check(OrePrefix.oreGranite.secondaryMaterials.size() == (uniqueStones ? 1 : 0), "unique stone config");
            check(OrePrefix.block.getMaterialAmount(materials.get("Glowstone")) == MaterialVoltages.M * 4, "native init amounts");
            check(OrePrefix.dustTiny.isIgnored(materials.get("Lapotron")), "native init exclusions");
            generationMatrix(); contracts(); vectors(); groovy();
            // A second invocation appends secondary materials again; init is not idempotent.
            int previous = OrePrefix.ore.secondaryMaterials.size(); OrePrefix.init();
            check(OrePrefix.ore.secondaryMaterials.size() == previous + 1, "init append semantics");
            System.out.println(Json.write(Map.of("prefixes", catalogCount, "icons", iconCount, "vectors", 4096,
                    "catalogDigest", digest(catalog), "markerDigest", digest(markers), "traceDigest", digest(trace),
                    "uniqueStones", uniqueStones, "wholePackParity", false, "minecraftLaunched", false, "kernelIsolation", true)));
        }
    }
}
