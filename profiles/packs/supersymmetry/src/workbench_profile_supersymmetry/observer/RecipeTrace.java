package dev.workbench.recipe;

import java.lang.reflect.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;

/** Bootstrap-visible bounded collector. No game classes are linked or initialized here. */
public final class RecipeTrace {
    private static final int MAX_EVENTS = 20000, MAX_RECIPES = 4096, MAX_SOURCES = 8192;
    private static final List<Map<String, Object>> events = new ArrayList<>();
    private static final Map<Object, String> recipes = new IdentityHashMap<>();
    private static final Map<Class<?>, Map<String, Object>> sources = new IdentityHashMap<>();
    private static final Map<Object, Map<String, Object>> parserInputs = new IdentityHashMap<>();
    private static final Map<String, Object> hooks = new TreeMap<>();
    private static final Set<String> problems = new TreeSet<>();
    private static final Map<String, String> expectedSources = new HashMap<>();
    private static final ThreadLocal<Deque<Map<String, Object>>> frames = ThreadLocal.withInitial(ArrayDeque::new);
    private static final StackWalker walker = StackWalker.getInstance(StackWalker.Option.RETAIN_CLASS_REFERENCE);
    private static String nonce, candidate;
    private static Set<String> required;
    private static boolean frozen;
    private static long finished;
    private static long propertyBytes;

    public static synchronized void configure(Properties configuration) {
        nonce = configuration.getProperty("nonce"); candidate = configuration.getProperty("candidate");
        required = new TreeSet<>();
        for (String key : configuration.stringPropertyNames()) {
            if (key.startsWith("source.")) expectedSources.put(new String(Base64.getUrlDecoder().decode(key.substring(7)), StandardCharsets.UTF_8), configuration.getProperty(key));
            if (key.startsWith("target.")) required.add(key.substring(7));
        }
    }

    public static String digest(byte[] bytes) throws Exception {
        return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
    }

    public static synchronized void problem(String reason) {
        if (problems.size() < 32 && problems.add(reason.substring(0, Math.min(reason.length(), 512))))
            System.out.println("[WORKBENCH-RECIPE-TRACE-INCOMPLETE] " + reason);
    }

    public static synchronized void installed(String name, String input, String definition, String output, Set<String> methods) {
        if (hooks.containsKey(name)) problem("duplicate-target-definition:" + name);
        hooks.put(name, Map.of("input_sha256", input, "definition_sha256", definition, "output_sha256", output, "methods", List.copyOf(methods)));
    }

    static Object call(Object object, String name) throws Exception {
        Method method = object.getClass().getMethod(name);
        if (!method.canAccess(object)) method.setAccessible(true);
        return method.invoke(object);
    }

    static Object field(Object object, String name) throws Exception {
        for (Class<?> type = object.getClass(); type != null; type = type.getSuperclass()) {
            try { Field f = type.getDeclaredField(name); f.setAccessible(true); return f.get(object); }
            catch (NoSuchFieldException missing) { /* Continue through the known inheritance chain. */ }
        }
        throw new NoSuchFieldException(name);
    }

    public static synchronized void parsed(Object characters, Object unit) {
        if (frozen || parserInputs.size() >= MAX_SOURCES) { if (!frozen) problem("source-bound"); return; }
        try {
            Object input = call(unit, "getSource");
            var uri = (java.net.URI) call(input, "getURI");
            // GroovyScript supplies ordinary scripts as named reader inputs;
            // dependency classes can instead have a FileReaderSource URI.
            // Both require the full compiler path AND exact parser text hash.
            String name = (String) call(unit, "getName");
            Path path = uri != null && "file".equals(uri.getScheme()) ? Path.of(uri)
                : name.startsWith("file:") ? Path.of(java.net.URI.create(name)) : Path.of(name);
            path = path.toAbsolutePath().normalize();
            Path root = Path.of("").toAbsolutePath().normalize();
            if (!path.startsWith(root)) return;
            String relative = root.relativize(path).toString().replace('\\', '/');
            String expected = expectedSources.get(relative);
            if (expected == null) return;
            if (!characters.getClass().getName().startsWith("groovyjarjarantlr4.v4.runtime.CodePointCharStream")
                    || ((Number) call(characters, "size")).intValue() > 1024 * 1024) { problem("parser-input-bound-or-type"); return; }
            // Observe the complete immutable stream passed to the lexer. No reread,
            // wrapper, file mutation, input replacement or parser re-execution.
            if (!digest(characters.toString().getBytes(StandardCharsets.UTF_8)).equals(expected)) {
                problem("compiler-source-mismatch:" + relative); return;
            }
            parserInputs.put(unit, Map.of("path", relative, "sha256", expected));
        } catch (Throwable failure) { problem("source-binding-failed:" + failure.getClass().getName()); }
    }

    public static synchronized void compiled(Object unit, Class<?> type, byte[] bytes) {
        if (frozen || sources.size() >= MAX_SOURCES) { if (!frozen) problem("source-bound"); return; }
        try {
            Object module = call(unit, "getAST"), compilation = call(module, "getUnit");
            String main = type.getName().split("\\$", 2)[0];
            Object actual = compilation.getClass().getMethod("getScriptSourceLocation", String.class).invoke(compilation, main);
            if (actual != null) unit = actual;
            else {
                boolean declared = false;
                for (Object node : (List<?>) call(module, "getClasses")) if (type.getName().equals(call(node, "getName"))) declared = true;
                if (!declared) return;
            }
            Map<String, Object> parsed = parserInputs.get(unit);
            if (parsed == null) return;
            Map<String, Object> source = new LinkedHashMap<>(parsed);
            source.put("class_name", type.getName()); source.put("compiled_sha256", digest(bytes));
            sources.put(type, source);
        } catch (Throwable failure) { problem("compiled-binding-failed:" + failure.getClass().getName()); }
    }

    private static Map<String, Object> origin() {
        return walker.walk(stream -> stream.filter(frame -> sources.containsKey(frame.getDeclaringClass()) && frame.getLineNumber() > 0)
            .findFirst().map(frame -> {
                Map<String, Object> value = new LinkedHashMap<>(sources.get(frame.getDeclaringClass()));
                value.put("line", frame.getLineNumber()); return value;
            }).orElse(null));
    }

    static String recipeId(Object recipe) {
        if (recipe == null) return null;
        if (!recipes.containsKey(recipe)) {
            if (recipes.size() >= MAX_RECIPES) { problem("recipe-bound"); return null; }
            recipes.put(recipe, "o" + recipes.size());
        }
        return recipes.get(recipe);
    }

    private static void bindRecipe(Map<String, Object> row, Object recipe) throws Exception {
        row.put("recipe_id", recipeId(recipe));
        if (recipe != null) {
            Map<String, Object> properties = new LinkedHashMap<>();
            properties.put("duration", call(recipe, "getDuration")); properties.put("eut", call(recipe, "getEUt"));
            properties.put("item_input_amounts", amounts(call(recipe, "getInputs")));
            properties.put("fluid_input_amounts", amounts(call(recipe, "getFluidInputs")));
            try {
                List<Object> items = new ArrayList<>(), fluids = new ArrayList<>(), outputs = new ArrayList<>(), fluidOutputs = new ArrayList<>();
                for (Object input : (List<?>) call(recipe, "getInputs")) {
                    if (Boolean.TRUE.equals(call(input, "isNonConsumable")) || Boolean.TRUE.equals(call(input, "hasNBTMatchingCondition"))) throw new IllegalArgumentException("special-input");
                    Map<String, Object> item = new LinkedHashMap<>(); List<Object> stacks = new ArrayList<>();
                    item.put("ore", null); item.put("amount", call(input, "getAmount")); item.put("stacks", stacks);
                    if (Boolean.TRUE.equals(call(input, "isOreDict"))) {
                        Class<?> dictionary = input.getClass().getClassLoader().loadClass("net.minecraftforge.oredict.OreDictionary");
                        item.put("ore", boundedName(dictionary.getMethod("getOreName", int.class).invoke(null, call(input, "getOreDict"))));
                    } else for (Object stack : (Object[]) call(input, "getInputStacks")) {
                        if (stacks.size() >= 256) throw new IllegalArgumentException("stack-bound");
                        stacks.add(stack(stack, false));
                    }
                    items.add(item);
                }
                for (Object input : (List<?>) call(recipe, "getFluidInputs")) {
                    if (Boolean.TRUE.equals(call(input, "isNonConsumable")) || Boolean.TRUE.equals(call(input, "hasNBTMatchingCondition"))) throw new IllegalArgumentException("special-fluid-input");
                    Object fluid = call(input, "getInputFluidStack");
                    if (field(fluid, "tag") != null) throw new IllegalArgumentException("fluid-nbt");
                    fluids.add(Map.of("name", boundedName(call(call(fluid, "getFluid"), "getName")), "amount", call(input, "getAmount")));
                }
                for (Object output : (List<?>) call(recipe, "getOutputs")) {
                    if (outputs.size() >= 64) throw new IllegalArgumentException("output-bound");
                    outputs.add(stack(output, true));
                }
                for (Object output : (List<?>) call(recipe, "getFluidOutputs")) {
                    if (fluidOutputs.size() >= 64 || field(output, "tag") != null) throw new IllegalArgumentException("fluid-output-bound-or-nbt");
                    fluidOutputs.add(Map.of("name", boundedName(call(call(output, "getFluid"), "getName")), "amount", field(output, "amount")));
                }
                properties.put("resolved_items", items); properties.put("resolved_fluids", fluids);
                properties.put("item_outputs", outputs); properties.put("fluid_outputs", fluidOutputs);
                properties.put("resolution", "captured-consumable-inputs-and-fixed-outputs");
            } catch (Exception unsupported) { properties.put("resolution", "unavailable-or-specialized; quantities-only"); }
            propertyBytes += properties.toString().getBytes(StandardCharsets.UTF_8).length;
            if (propertyBytes > 2 * 1024 * 1024) { problem("property-byte-bound"); return; }
            row.put("properties", properties);
        }
    }

    static Map<String, Object> stack(Object stack, boolean amount) throws Exception {
        if (Boolean.TRUE.equals(call(stack, "func_190926_b")) || call(stack, "func_77978_p") != null) throw new IllegalArgumentException("empty-or-nbt-stack");
        Object item = call(stack, "func_77973_b");
        Map<String, Object> value = new LinkedHashMap<>();
        value.put("item", boundedName(call(item, "getRegistryName"))); value.put("metadata", call(stack, "func_77960_j"));
        if (amount) value.put("amount", call(stack, "func_190916_E"));
        return value;
    }

    private static String boundedName(Object value) {
        String name = value.toString();
        if (name.length() > 512) throw new IllegalArgumentException("name-bound");
        return name;
    }

    private static List<Object> amounts(Object inputs) throws Exception {
        List<Object> values = new ArrayList<>();
        for (Object input : (List<?>) inputs) {
            if (values.size() >= 64) throw new IllegalStateException("input-bound");
            values.add(call(input, "getAmount"));
        }
        return values;
    }

    public static synchronized Object enter(String operation, Object receiver, Object argument) {
        if (frozen) return null;
        try {
            Object map = Set.of("registration", "build", "validation").contains(operation) ? field(receiver, "recipeMap") : receiver;
            if (map == null || !"mixer".equals(field(map, "unlocalizedName"))) return null;
            if (events.size() >= MAX_EVENTS) { problem("event-bound"); return null; }
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("id", "e" + events.size()); row.put("operation", operation);
            row.put("parent", frames.get().isEmpty() ? null : frames.get().peek().get("id"));
            row.put("thread", Thread.currentThread().getId()); row.put("source", origin());
            row.put("outcome", "open"); row.put("recipe_id", null); row.put("properties", null);
            row.put("validation", null); row.put("returned", null); row.put("finished", null);
            if (Set.of("add", "post-validation").contains(operation)) {
                bindRecipe(row, call(argument, "getResult"));
                row.put("validation", ((Enum<?>) call(argument, "getType")).name());
            } else if (Set.of("insertion", "removal").contains(operation)) bindRecipe(row, argument);
            events.add(row); frames.get().push(row); return row;
        } catch (Throwable failure) { problem("event-entry-failed:" + failure.getClass().getName()); return null; }
    }

    @SuppressWarnings("unchecked")
    public static synchronized void exit(Object token, Object returned, boolean threw) {
        if (token == null || frozen) return;
        try {
            Map<String, Object> row = (Map<String, Object>) token;
            if (frames.get().peek() != row) { problem("event-nesting-mismatch"); return; }
            frames.get().pop();
            row.put("outcome", threw ? "threw" : "returned"); row.put("finished", ++finished);
            if (threw) row.put("returned", returned.getClass().getName());
            else if (returned instanceof Boolean || returned instanceof Enum<?>) row.put("returned", returned instanceof Enum<?> e ? e.name() : returned);
            if (!threw && Set.of("build", "post-validation").contains(row.get("operation"))) {
                bindRecipe(row, call(returned, "getResult"));
                row.put("validation", ((Enum<?>) call(returned, "getType")).name());
            }
        } catch (Throwable failure) { problem("event-exit-failed:" + failure.getClass().getName()); }
    }

    public static synchronized List<Object> recipeObjects() { return new ArrayList<>(recipes.keySet()); }

    static String insertion() {
        for (var frame : frames.get()) if (frame.get("operation").equals("insertion")) return (String) frame.get("id");
        return null;
    }

    static String nonce() { return nonce; }
    static String candidate() { return candidate; }

    public static synchronized Map<String, Object> snapshot(Map<Object, String> references, Set<Object> selected, String selectedPath) {
        frozen = true;
        if (required == null || !hooks.keySet().equals(required)) problem("missing-required-hooks");
        if (events.stream().anyMatch(row -> row.get("outcome").equals("open"))) problem("unfinished-operations");
        Set<String> selectedRecipes = new HashSet<>();
        for (Object recipe : selected) if (recipes.containsKey(recipe)) selectedRecipes.add(recipes.get(recipe));
        for (var row : events) if (row.get("source") instanceof Map<?, ?> source && selectedPath.equals(source.get("path"))
                && row.get("recipe_id") instanceof String id) selectedRecipes.add(id);
        RecipeDecisions.relatedIds(selectedRecipes);
        Set<Object> selectedEvents = new HashSet<>();
        for (Map<String, Object> row : events) {
            Object source = row.get("source");
            if (selectedRecipes.contains(row.get("recipe_id")) || source instanceof Map<?, ?> s && selectedPath.equals(s.get("path"))
                    || row.get("operation").equals("clear")) selectedEvents.add(row.get("id"));
        }
        for (int i = events.size() - 1; i >= 0; i--) {
            var row = events.get(i);
            if (row.get("parent") != null && selectedEvents.contains(row.get("id"))) selectedEvents.add(row.get("parent"));
        }
        // Include all children of a selected registration, preserving validation failures.
        for (var row : events) if (selectedEvents.contains(row.get("parent"))) selectedEvents.add(row.get("id"));
        List<Map<String, Object>> retained = events.stream().filter(row -> selectedEvents.contains(row.get("id"))).toList();
        Map<String, Object> links = new TreeMap<>();
        references.forEach((object, reference) -> { if (recipes.containsKey(object)) links.put(reference, recipes.get(object)); });
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("format", "workbench-recipe-lifecycle-v1"); result.put("nonce", nonce); result.put("candidate_id", candidate);
        result.put("state", problems.isEmpty() ? "complete" : "incomplete"); result.put("problems", List.copyOf(problems));
        result.put("hooks", new TreeMap<>(hooks)); result.put("events", retained); result.put("links", links);
        result.put("total_events", events.size()); result.put("source_bindings", sources.size());
        result.put("scope", "Observed MIXER operations; selected recipe identities and selected source file. Not branch coverage or proof of non-execution.");
        return result;
    }
}
