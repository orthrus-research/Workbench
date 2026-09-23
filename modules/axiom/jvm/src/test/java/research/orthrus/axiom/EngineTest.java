package research.orthrus.axiom;

import static org.junit.jupiter.api.Assertions.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.stream.Stream;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import static research.orthrus.axiom.Domain.*;

class EngineTest {
    static final String IMPORTS = "import static prePostInit.Recipemaps.*\nimport static gregtech.api.GTValues.*\n";
    static final String BASIC = "MIXER.recipeBuilder().inputs(ore('a')).fluidInputs(fluid('feed') * 1000).fluidOutputs(fluid('product') * 1000).duration(200).EUt(VA[LV]).buildAndRegister()";
    static Map<String, Object> obj(Object value) { return Json.object(value); }
    static List<Object> list(Object value) { return Json.array(value); }
    static Map<String, Object> registry() {
        List<Object> types = new ArrayList<>();
        for (String name : List.of("a", "b", "c", "circuit", "dust1", "dust2", "dust3", "dust4"))
            types.add(Map.of("id", "fixture:" + name, "meta", 0, "maxStack", 64, "capabilities", "none"));
        List<Object> ores = new ArrayList<>();
        Map<String, List<String>> bindings = Map.of("a", List.of("a"), "b", List.of("b"), "ab", List.of("a", "b"),
                "dyeCyan", List.of("dust1"), "dustTinyBorax", List.of("dust2"), "dustSodiumHydroxide", List.of("dust3"), "dustTinyMercaptobenzothiazole", List.of("dust4"));
        bindings.entrySet().stream().sorted(Map.Entry.comparingByKey()).forEach(entry -> ores.add(Map.of("name", entry.getKey(),
                "members", entry.getValue().stream().map(item -> Map.of("id", "fixture:" + item, "meta", 0)).toList())));
        Map<String, Object> order = new LinkedHashMap<>();
        for (Object item : types) {
            String id = (String)obj(item).get("id");
            order.put(id + ":0", bindings.entrySet().stream().filter(entry -> entry.getValue().contains(id.substring(8))).map(Map.Entry::getKey).sorted().toList());
        }
        return new LinkedHashMap<>(Map.of("scope", "explicit-context", "items", types, "ores", ores, "oreLookupOrder", order,
                "fluids", List.of("feed", "product", "other", "ethylene_glycol", "coolant", "advanced_coolant", "polydimethylsiloxane"),
                "circuit", Map.of("id", "fixture:circuit", "meta", 0)));
    }
    static Map<String, Object> request(String source) {
        return new LinkedHashMap<>(Map.of("schema", "axiom.request.v1", "targetId", Target.ID, "registry", registry(),
                "files", List.of(Map.of("path", "postInit/test.groovy", "text", source))));
    }
    static Map<String, Object> item(String name, int count) { return new LinkedHashMap<>(Map.of("id", "fixture:" + name, "meta", 0, "count", count)); }
    static Map<String, Object> fluid(String name, int amount) { return Map.of("id", name, "amount", amount); }
    static List<Object> slots(Object... entries) { return new ArrayList<>(Arrays.asList(entries)); }
    static Map<String, Object> machine(int tier) {
        Map<String, Object> value = new LinkedHashMap<>();
        value.put("machine", "gregtech:mixer." + List.of("", "lv", "mv", "hv", "ev", "iv").get(tier));
        value.put("entry", "idle-search"); value.put("inputItems", slots(item("a", 1), null, null, null, null, null));
        value.put("inputFluids", slots(fluid("feed", 1000), null, null));
        value.put("outputItems", slots((Object)null)); value.put("outputFluids", slots(null, null));
        value.put("ghostCircuit", -1); value.put("energy", List.of(0, 2000, 8000, 30000, 100000, 500000).get(tier));
        value.put("overclockTier", tier); value.put("voidItems", false); value.put("voidFluids", false);
        value.put("allowInputFromOutputSideItems", false); value.put("allowInputFromOutputSideFluids", false);
        value.put("cache", null); value.put("outputsFull", false); return value;
    }
    static Map<String, Object> queryRequest(String source, Map<String, Object> machine) {
        Map<String, Object> value = request(source); value.put("machine", machine); value.put("loads", List.of()); value.put("queryKind", "select-and-start"); return value;
    }
    static Map<String, Object> run(String operation, Map<String, Object> request) { return new Engine().run(operation, request); }
    static Map<String, Object> body(Map<String, Object> result) { return obj(result.get("result")); }
    static Map<String, Object> state(Map<String, Object> result) { return obj(obj(body(result).get("machine")).get("after")); }
    static void status(String expected, Map<String, Object> result) { assertEquals(expected, result.get("status"), Json.write(result)); }
    static String coolant() throws Exception {
        try (var stream = EngineTest.class.getResourceAsStream("/Coolants.groovy")) { return new String(Objects.requireNonNull(stream).readAllBytes(), StandardCharsets.UTF_8); }
    }

    @Test void actualCoolantsConstructsOriginalDerivedAndExplicitBlender() throws Exception {
        var result = run("check", request(coolant())); status("accepted", result);
        var definitions = list(body(result).get("definitions")); assertEquals(3, definitions.size());
        assertEquals("blender", obj(definitions.get(0)).get("map")); assertEquals("recipe:0", obj(definitions.get(0)).get("derivedFrom"));
        assertEquals("mixer", obj(definitions.get(1)).get("map")); assertNull(obj(definitions.get(2)).get("derivedFrom"));
        assertEquals(30, obj(definitions.get(1)).get("eut"));
        assertEquals(10000, obj(list(obj(definitions.get(1)).get("fluidOutputs")).get(0)).get("amount"));
        assertEquals(false, body(result).get("wholePackParity"));
    }
    @ParameterizedTest @ValueSource(ints={1,2,3,4,5}) void ordinaryStartConsumesButDoesNotDeliverOrDrawEnergy(int tier) {
        var machine = machine(tier); var result = run("query", queryRequest(IMPORTS + BASIC, machine)); status("accepted", result);
        var after = state(result); assertEquals(Json.integer(machine.get("energy")), after.get("energy")); assertEquals(1, after.get("progress"));
        assertNull(list(after.get("inputItems")).get(0)); assertNull(list(after.get("inputFluids")).get(0));
        assertNull(list(after.get("outputFluids")).get(0)); assertNotNull(after.get("reservedOutputs"));
        assertEquals(1, obj(list(machine.get("inputItems")).get(0)).get("count"), "Query must not mutate caller data");
    }
    @Test void lvEmptyOutputRejectsTenBucketsAndRetainsCacheAndInputs() throws Exception {
        var machine = coolantMachine(1); var result = run("query", queryRequest(coolant(), machine)); status("rejected", result);
        assertEquals(true, state(result).get("outputsFull")); assertNotNull(state(result).get("cache"));
        assertEquals(1, obj(list(state(result).get("inputItems")).get(0)).get("count"));
    }
    static Map<String, Object> coolantMachine(int tier) {
        var machine = machine(tier);
        machine.put("inputItems", slots(item("dust1", 1), item("dust2", 1), item("dust3", 1), null, null, null));
        machine.put("inputFluids", tier == 1 ? slots(fluid("ethylene_glycol", 8000), fluid("ethylene_glycol", 2000), null) : slots(fluid("ethylene_glycol", 10000), null, null));
        return machine;
    }
    @ParameterizedTest @ValueSource(ints={2,3,4,5}) void higherTiersStartActualCoolant(int tier) throws Exception {
        var result = run("query", queryRequest(coolant(), coolantMachine(tier))); status("accepted", result);
    }
    @Test void lvExistingSplitOutputIsConcreteWitnessNotUniversalImpossibility() throws Exception {
        var machine = coolantMachine(1); machine.put("outputFluids", slots(fluid("coolant", 1000), fluid("coolant", 1000)));
        status("accepted", run("query", queryRequest(coolant(), machine)));
    }
    @Test void lvVoidBypassesFitWithoutClaimingRecovery() throws Exception {
        var machine = coolantMachine(1); machine.put("voidFluids", true);
        var result = run("query", queryRequest(coolant(), machine)); status("accepted", result);
        assertNull(list(state(result).get("outputFluids")).get(0));
    }
    @Test void underpoweredRecipeIsStillCachedAndLeavesOutputsFullUntouched() {
        var machine = machine(1); machine.put("energy", 239); machine.put("outputsFull", true);
        var result = run("query", queryRequest(IMPORTS + BASIC, machine)); status("rejected", result);
        assertNotNull(state(result).get("cache")); assertEquals(true, state(result).get("outputsFull"));
        machine.put("energy", 240); status("accepted", run("query", queryRequest(IMPORTS + BASIC, machine)));
    }
    @Test void guiTanksCanSplitButCapabilityCannotSpillIntoSecondEmptyTank() throws Exception {
        var machine = coolantMachine(1); machine.put("voidFluids", true); machine.put("inputFluids", slots(null, null, null));
        var request = queryRequest(coolant(), machine);
        request.put("loads", List.of(Map.of("route", "capability-fluid", "side", "ordinary", "stack", fluid("ethylene_glycol", 10000))));
        var capped = run("query", request); status("rejected", capped); assertEquals(8000, obj(list(state(capped).get("inputFluids")).get(0)).get("amount"));
        request.put("loads", List.of(Map.of("route", "gui-fluid-tank", "slot", 0, "stack", fluid("ethylene_glycol", 8000)),
                Map.of("route", "gui-fluid-tank", "slot", 1, "stack", fluid("ethylene_glycol", 2000))));
        status("accepted", run("query", request));
    }
    @Test void fullMatchingOutputTankPreventsSpillingToEmptyTank() {
        var machine = machine(1); machine.put("outputFluids", slots(fluid("product", 8000), null));
        status("rejected", run("query", queryRequest(IMPORTS + BASIC, machine)));
    }
    @Test void wrongFluidAndInsufficientItemAreNotSelected() {
        var machine = machine(1); machine.put("inputFluids", slots(fluid("other", 1000), null, null));
        status("rejected", run("query", queryRequest(IMPORTS + BASIC, machine)));
        machine = machine(1); machine.put("inputItems", slots(null, null, null, null, null, null));
        status("rejected", run("query", queryRequest(IMPORTS + BASIC, machine)));
    }
    @ParameterizedTest @ValueSource(ints={0,1,32}) void ghostCircuitIsRequiredAndNonconsumable(int circuit) {
        String source = IMPORTS + BASIC.replace(".duration", ".circuitMeta(" + circuit + ").duration");
        status("rejected", run("query", queryRequest(source, machine(1))));
        var machine = machine(1); machine.put("ghostCircuit", circuit);
        var result = run("query", queryRequest(source, machine)); status("accepted", result); assertEquals(circuit, state(result).get("ghostCircuit"));
    }
    @Test void duplicateCircuitsCountTowardLimitsButOneGhostCanSatisfyBoth() {
        var machine = machine(1); machine.put("ghostCircuit", 0);
        String source = IMPORTS + BASIC.replace(".duration", ".circuitMeta(0).circuitMeta(0).duration");
        status("accepted", run("query", queryRequest(source, machine)));
        source = IMPORTS + BASIC.replace(".duration", ".circuitMeta(0)".repeat(6) + ".duration");
        var result = run("check", request(source)); status("rejected", result);
        var definitions = list(body(result).get("definitions"));
        assertEquals(true, obj(definitions.get(0)).get("registered"), "Callback precedes invalid MIXER validation");
        assertEquals(false, obj(definitions.get(1)).get("registered"));
    }
    @ParameterizedTest @ValueSource(ints={-1,33}) void invalidCircuitSetterIsWarningNotGroovyValidationRejection(int circuit) {
        var result = run("check", request(IMPORTS + BASIC.replace(".duration", ".circuitMeta(" + circuit + ").duration")));
        status("accepted", result); assertFalse(list(body(result).get("diagnostics")).isEmpty());
        assertEquals(1, list(obj(list(body(result).get("definitions")).get(1)).get("items")).size());
    }
    @Test void multiplicationSetsAmountRatherThanMultiplyingExistingCount() {
        var result = run("check", request(IMPORTS + BASIC.replace("ore('a')", "ore('a') * 2 * 3")));
        status("accepted", result); assertEquals(3, obj(list(obj(list(body(result).get("definitions")).get(1)).get("items")).get(0)).get("amount"));
    }
    @Test void greedyOrderIsNotBacktracking() {
        Registry registry = new Registry(registry());
        Ingredient broad = new Ingredient("ore", "ab", 1, false, null, null, 0);
        Ingredient narrow = new Ingredient("ore", "a", 1, false, null, null, 0);
        var stacks = new ArrayList<>(List.of(registry.readItem(item("a", 1)), registry.readItem(item("b", 1))));
        assertNull(Matching.allocateItems(List.of(broad, narrow), stacks, registry));
        assertNotNull(Matching.allocateItems(List.of(narrow, broad), stacks, registry));
    }
    @Test void nonconsumableMatchesAfterConsumableOnlyWhenStockRemains() {
        Registry registry = new Registry(registry());
        Ingredient input = new Ingredient("ore", "a", 1, false, null, null, 0);
        var stacks = List.of(registry.readItem(item("a", 1)));
        assertNull(Matching.allocateItems(List.of(input, input.nonConsumableInput()), stacks, registry));
        assertNotNull(Matching.allocateItems(List.of(input.nonConsumableInput(), input), stacks, registry));
    }
    @Test void allocatorLeavesTrailingSlotsAndSplitsQuantities() {
        Registry registry = new Registry(registry());
        Ingredient input = new Ingredient("ore", "a", 3, false, null, null, 0);
        var stacks = List.of(registry.readItem(item("a", 2)), registry.readItem(item("a", 2)), registry.readItem(item("b", 10)));
        assertArrayEquals(new int[]{0,1}, Matching.allocateItems(List.of(input), stacks, registry));
    }
    @Test void zeroSizedDomainsPreserveUpstreamSkipButMachineForbidsImpossibleShape() {
        Registry registry = new Registry(registry());
        var recipe = new Recipe("r", "mixer", List.of(new Ingredient("ore", "a", 1, false, null, null, 0)), List.of(), List.of(), List.of(), 1, 1, new Location("a.groovy", "x", 1, 1), null);
        assertTrue(Matching.match(recipe, List.of(), List.of(), registry).matched());
        var machine = machine(1); machine.put("inputItems", List.of());
        status("request-error", run("query", queryRequest(IMPORTS + BASIC, machine)));
    }
    @Test void emptyRecipeCanRegisterButNeverHasReachableTreeLeaf() {
        var result = run("check", request(IMPORTS + "MIXER.recipeBuilder().duration(1).EUt(1).buildAndRegister()"));
        status("accepted", result);
        for (Object row : list(body(result).get("definitions"))) { assertEquals(true, obj(row).get("registered")); assertEquals(false, obj(row).get("treeReachable")); }
    }
    @Test void sameTreeKeyConflictDoesNotBecomePriorityList() {
        var result = run("check", request(IMPORTS + BASIC + "\n" + BASIC.replace("200", "100")));
        status("rejected", result); assertEquals(false, obj(list(body(result).get("definitions")).get(3)).get("registered"));
    }
    @Test void emptyOreIsDifferentFromMissingRegistryFact() {
        var request = request(IMPORTS + BASIC.replace("ore('a')", "ore('missing')"));
        status("requires-context", run("check", request));
        list(obj(request.get("registry")).get("ores")).add(Map.of("name", "missing", "members", List.of()));
        var result = run("check", request); status("accepted", result);
        assertEquals(0, obj(list(obj(list(body(result).get("definitions")).get(1)).get("items")).get(0)).get("amount"));
    }
    @Test void cachedSelectionSurvivesFailureButCannotCrossSourceIdentity() {
        var request = queryRequest(IMPORTS + BASIC, machine(1)); var result = run("query", request); status("accepted", result);
        obj(request.get("machine")).put("cache", state(result).get("cache"));
        var cached = run("query", request); status("accepted", cached); assertEquals("previous-recipe", obj(body(cached).get("machine")).get("selection"));
        request.put("files", List.of(Map.of("path", "postInit/test.groovy", "text", IMPORTS + BASIC + "\n// candidate edit")));
        status("request-error", run("query", request));
    }
    @Test void physicalCircuitMissingTagIsDifferentInFreshLookupAndCachePredicate() {
        String source = IMPORTS + BASIC.replace(".duration", ".circuitMeta(0).duration");
        var machine = machine(1); list(machine.get("inputItems")).set(1, item("circuit", 1));
        var request = queryRequest(source, machine); status("rejected", run("query", request));
        var checked = run("check", request(source));
        machine.put("cache", Map.of("programId", body(checked).get("programId"), "recipeId", "recipe:0"));
        var result = run("query", request); status("accepted", result);
        assertEquals(Tag.circuit(0).json(), obj(list(state(result).get("inputItems")).get(1)).get("nbt"));
    }
    @Test void sourceAndRegistryChangeInvalidateProgramIdentity() {
        var request = request(IMPORTS + BASIC); String first = (String)body(run("check", request)).get("programId");
        obj(request.get("registry")).put("fluids", List.of("feed", "product", "extra"));
        assertNotEquals(first, body(run("check", request)).get("programId"));
    }
    @ParameterizedTest @ValueSource(strings={"new File('/tmp/axiom-escape').text = 'bad'", "System.getenv()", "'echo bad'.execute()", "while(true) {}", "def helper() { return 1 }; helper()", "@Grab('example:bad:1') class Payload {}", "class Payload { static { System.exit(7) } }", "import java.nio.file.Files\nFiles.readString(null)", "def x = 1", "MIXER.recipeBuilder().cleanroom('cleanroom')", "MIXER.recipeBuilder().property('temperature', 1)"})
    void unsupportedCodeNeverBecomesAccepted(String source) { status("unsupported", run("check", request(IMPORTS + source))); }
    @Test void syntaxErrorIsNotRecipeRejection() { status("source-error", run("check", request(IMPORTS + "MIXER.recipeBuilder("))); }
    @Test void duplicateJsonUnknownFieldsAndWrongTypesAreErrors() {
        assertThrows(Failure.class, () -> Json.parse("{\"x\":1,\"x\":2}"));
        assertThrows(Failure.class, () -> Json.parse("{\"x\":01}"));
        assertThrows(Failure.class, () -> Json.number("1"));
        var request = request(IMPORTS + BASIC); request.put("mystery", true); status("request-error", run("check", request));
    }
    @Test void unsupportedMachineAndNbtDoNotDefaultToGenericSuccess() {
        var machine = machine(1); machine.put("machine", "supersymmetry:blender");
        status("unsupported", run("query", queryRequest(IMPORTS + BASIC, machine)));
        machine = machine(1); obj(list(machine.get("inputItems")).get(0)).put("nbt", Map.of("type", "float", "value", 1));
        status("unsupported", run("query", queryRequest(IMPORTS + BASIC, machine)));
    }
    @Test void intNbtDoesNotEqualByteNbt() { assertNotEquals(Tag.read(Map.of("type", "int", "value", 1)), Tag.read(Map.of("type", "byte", "value", 1))); }
    @Test void startDoesNotClaimCompletion() { assertTrue(Json.write(Target.coverage()).contains("processing ticks")); }
    @Test void nativeJvmDoesNotClaimUpstreamRecipeExecution() {
        Map<?, ?> execution = (Map<?, ?>) Target.coverage().get("execution");
        assertEquals("profile-pinned-native-jvm", execution.get("javaSemantics"));
        assertEquals(false, execution.get("customJvmImplemented"));
        assertEquals(false, execution.get("upstreamRecipeEnvironmentExecuted"));
        assertEquals(false, Target.coverage().get("wholePackParity"));
        assertEquals("native-jvm", ((Map<?, ?>)Target.coverage().get("runtime")).get("execution"));
    }
    @Test void standardOverclockBoundaries() {
        assertArrayEquals(new int[]{30,200}, Mixer.overclock(30,200,1)); assertArrayEquals(new int[]{120,100}, Mixer.overclock(30,200,2));
        assertArrayEquals(new int[]{480,50}, Mixer.overclock(30,200,3)); assertArrayEquals(new int[]{28,100}, Mixer.overclock(7,200,2));
        assertArrayEquals(new int[]{30,1}, Mixer.overclock(30,1,5)); assertArrayEquals(new int[]{120,1}, Mixer.overclock(30,3,2));
    }
    @Test void oreLookupOrderChoosesTreeLeafButCacheCanChooseAnotherRegisteredRecipe() {
        String source = IMPORTS + BASIC + "\n" + BASIC.replace("ore('a')", "ore('ab')").replace("200", "100");
        var request = queryRequest(source, machine(1));
        obj(obj(request.get("registry")).get("oreLookupOrder")).put("fixture:a:0", List.of("ab", "a"));
        var fresh = run("query", request); status("accepted", fresh);
        assertEquals("recipe:2", obj(body(fresh).get("machine")).get("selectedRecipe"));
        obj(request.get("machine")).put("cache", Map.of("programId", body(fresh).get("programId"), "recipeId", "recipe:0"));
        var cached = run("query", request); status("accepted", cached);
        assertEquals("recipe:0", obj(body(cached).get("machine")).get("selectedRecipe"));
    }
    @Test void consumableOreThatMatchesGhostClearsTheControl() {
        var request = queryRequest(IMPORTS + BASIC.replace("ore('a')", "ore('selector')"), machine(1));
        obj(request.get("machine")).put("ghostCircuit", 0);
        list(obj(request.get("registry")).get("ores")).add(Map.of("name", "selector", "members", List.of(Map.of("id", "fixture:circuit", "meta", 0))));
        obj(obj(request.get("registry")).get("oreLookupOrder")).put("fixture:circuit:0", List.of("selector"));
        var result = run("query", request); status("accepted", result);
        assertEquals(-1, state(result).get("ghostCircuit"));
    }
    @Test void outputSideItemAdmissionReadsTheFluidControl() {
        var machine = machine(1); machine.put("inputItems", slots(null, null, null, null, null, null));
        machine.put("allowInputFromOutputSideItems", true);
        var request = queryRequest(IMPORTS + BASIC, machine);
        request.put("loads", List.of(Map.of("route", "capability-item-slot", "side", "output", "slot", 0, "stack", item("a", 1))));
        status("rejected", run("query", request));
        machine.put("allowInputFromOutputSideItems", false); machine.put("allowInputFromOutputSideFluids", true);
        status("accepted", run("query", request));
    }
    @Test void itemOutputCapacityAndRecipeVoltageAreIndependentGates() {
        String source = IMPORTS + BASIC.replace(".duration", ".outputs(item('fixture:b') * 2).duration");
        var machine = machine(1); machine.put("outputItems", slots(item("b", 63)));
        status("rejected", run("query", queryRequest(source, machine)));
        machine.put("voidItems", true); status("accepted", run("query", queryRequest(source, machine)));
        status("rejected", run("query", queryRequest(IMPORTS + BASIC.replace("VA[LV]", "VA[MV]"), machine(1))));
    }
    @Test void sourceDependenciesAreExplicitAndCannotLeakAcrossCalls() {
        status("unsupported", run("check", request(BASIC)));
        status("accepted", run("check", request(IMPORTS + BASIC)));
        status("unsupported", run("check", request(BASIC)));
    }
    @Test void invalidGhostAndMalformedUnicodeAreRequestErrors() {
        var machine = machine(1); machine.put("ghostCircuit", 33);
        status("request-error", run("query", queryRequest(IMPORTS + BASIC, machine)));
        assertThrows(Failure.class, () -> Json.parse("\"\\ud800\""));
        assertEquals("😀", Json.parse("\"\\ud83d\\ude00\""));
    }
    @Test void jsonNumbersAndEscapesRequireAsciiGrammar() {
        for (String text : List.of("1١", "1.١", "1e١", "\"\\u+061\"", "\"\\u-001\"", "\"\\u٠٠٦١\""))
            assertThrows(Failure.class, () -> Json.parse(text), text);
        assertEquals("a", Json.parse("\"\\u0061\""));
    }
}
