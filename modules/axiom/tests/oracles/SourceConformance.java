package research.orthrus.axiom;

import java.util.*;
import java.io.*;
import gregtech.api.recipes.logic.OverclockingLogic;
import static research.orthrus.axiom.Domain.*;

/** Compare Axiom with independently compiled locked source methods; no game classes. */
public final class SourceConformance {
    public static void main(String[] args) throws Throwable {
        NativeRuntime.require();
        Random random = new Random(0x4158494f4dL);
        int overclocks = 0, allocations = 0;
        byte[] compiled;
        try (InputStream stream = OverclockingLogic.class.getResourceAsStream("OverclockingLogic.class")) {
            compiled = Objects.requireNonNull(stream).readAllBytes();
        }
        String compiledSha = Json.bytesDigest(compiled);
        for (int i = 0; i < 20000; i++) {
            int eut = i < 100 ? Integer.MIN_VALUE + i : random.nextInt(1_000_000);
            int duration = random.nextInt(20001) - 1;
            int count = random.nextInt(18) - 1;
            long voltage = 8L << (2 * random.nextInt(13));
            double divisor = random.nextBoolean() ? 2.0 : 4.0;
            int[] expected = OverclockingLogic.standardOverclockingLogic(eut, voltage, duration, count, divisor, 4.0);
            int[] actual = Mixer.standardOverclock(eut, voltage, duration, count, divisor, 4.0);
            if (!Arrays.equals(expected, actual)) throw new AssertionError("Overclock mismatch at vector " + i);
            overclocks++;
        }
        int edgeVectors = 0;
        for (double divisor : new double[]{-4, -0.0, 0, 0.25, 2, 4, Double.NaN, Double.POSITIVE_INFINITY})
            for (double multiplier : new double[]{-4, -0.0, 0, 0.25, 4, Double.NaN, Double.NEGATIVE_INFINITY, Double.POSITIVE_INFINITY})
                for (int eut : new int[]{Integer.MIN_VALUE, -1, 0, 1, Integer.MAX_VALUE}) {
                    int duration = edgeVectors % 3 == 0 ? Integer.MAX_VALUE : edgeVectors % 3 == 1 ? -1 : 1;
                    long voltage = edgeVectors % 2 == 0 ? Long.MAX_VALUE : Long.MIN_VALUE;
                    int[] expected = OverclockingLogic.standardOverclockingLogic(eut, voltage, duration, 17, divisor, multiplier);
                                    int[] actual = Mixer.standardOverclock(eut, voltage, duration, 17, divisor, multiplier);
                    if (!Arrays.equals(expected, actual)) throw new AssertionError("Overclock edge mismatch at " + edgeVectors);
                    edgeVectors++;
                }
        Map<String, Object> registryData = Map.of("scope", "explicit-context", "items", List.of(
                Map.of("id", "fixture:a", "meta", 0, "maxStack", 64, "capabilities", "none"),
                Map.of("id", "fixture:b", "meta", 0, "maxStack", 64, "capabilities", "none"),
                Map.of("id", "fixture:circuit", "meta", 0, "maxStack", 64, "capabilities", "none")),
                "ores", List.of(Map.of("name", "a", "members", List.of(Map.of("id", "fixture:a", "meta", 0))),
                        Map.of("name", "ab", "members", List.of(Map.of("id", "fixture:a", "meta", 0), Map.of("id", "fixture:b", "meta", 0)))),
                "oreLookupOrder", Map.of("fixture:a:0", List.of("a", "ab"), "fixture:b:0", List.of("ab"), "fixture:circuit:0", List.of()),
                "fluids", List.of("feed", "other"), "circuit", Map.of("id", "fixture:circuit", "meta", 0));
        Registry registry = new Registry(registryData);
        int builderVectors = 0;
        for (int i = 0; i < 20000; i++) {
            int eut = i % 7 == 0 ? 0 : random.nextInt();
            int duration = i % 7 == 0 ? 0 : random.nextInt();
            int[] shape = {random.nextInt(12) - 1, random.nextInt(6) - 1, random.nextInt(12) - 1, random.nextInt(6) - 1};
            List<?> items = Collections.nCopies(random.nextInt(16), "item");
            List<?> products = Collections.nCopies(random.nextInt(8), "product");
            List<?> fluids = Collections.nCopies(random.nextInt(16), "fluid");
            List<?> fluidProducts = Collections.nCopies(random.nextInt(8), "fluid product");
            var expected = new UpstreamBuilderValidation(eut, duration, items, products, fluids, fluidProducts, shape).errors();
            var actual = new BuilderValidation(eut, duration, items, products, fluids, fluidProducts,
                    new BuilderValidation.Shape(shape[0], shape[1], shape[2], shape[3])).errors();
            if (!expected.equals(actual)) throw new AssertionError("Native builder diagnostics differ at vector " + i);
            builderVectors++;
        }
        for (int i = 0; i < 20000; i++) {
            List<Ingredient> itemInputs = new ArrayList<>(), fluidInputs = new ArrayList<>();
            List<Item> stacks = new ArrayList<>(); List<Fluid> tanks = new ArrayList<>();
            for (int n = random.nextInt(8); n > 0; n--) {
                itemInputs.add(new Ingredient("ore", random.nextBoolean() ? "a" : "ab", random.nextInt(6), random.nextBoolean(), null, null, 0));
                fluidInputs.add(new Ingredient("fluid", random.nextBoolean() ? "feed" : "other", random.nextInt(12), random.nextBoolean(), null, null, 0));
            }
            for (int n = random.nextInt(8); n > 0; n--) {
                stacks.add(new Item(registry.item(random.nextBoolean() ? "fixture:a" : "fixture:b", 0), random.nextInt(6), null));
                tanks.add(random.nextBoolean() ? null : new Fluid(random.nextBoolean() ? "feed" : "other", random.nextInt(12), null));
            }
            UpstreamMatching oracle = new UpstreamMatching(itemInputs, fluidInputs, registry);
            var expectedItems = oracle.matchesItems(stacks);
            var expectedFluids = oracle.matchesFluid(tanks);
            int[] actualItems = Matching.allocateItems(itemInputs, stacks, registry);
            int[] actualFluids = Matching.allocateFluids(fluidInputs, tanks);
            check(expectedItems, actualItems, "item", i); check(expectedFluids, actualFluids, "fluid", i);
            allocations += 2;
        }
        int materialOperations = MaterialConformance.run();
        System.out.println(Json.write(Map.of("overclockVectors", overclocks + edgeVectors,
                "allocationVectors", allocations, "builderValidationVectors", builderVectors, "sourceMethodsCompared", 5, "wholePackParity", false,
                "materialPropertyOperations", materialOperations, "materialPropertyGraphs", 2000,
                "compiledOverclockClassSha256", compiledSha, "execution", "native-jvm",
                "runtime", NativeRuntime.require())));
    }
    private static void check(UpstreamMatching.Pair<Boolean, int[]> expected, int[] actual, String kind, int vector) {
        if (expected.left != (actual != null) || expected.left && !Arrays.equals(expected.right, actual))
            throw new AssertionError(kind + " allocation mismatch at vector " + vector);
    }
}
