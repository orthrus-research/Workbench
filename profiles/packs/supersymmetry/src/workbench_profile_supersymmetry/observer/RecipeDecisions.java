package dev.workbench.recipe;

import java.util.*;

/** Bounded decision evidence. Shared monitor prevents collector lock-order inversions. */
public final class RecipeDecisions {
    private static final int MAX_STEPS = 40000, MAX_QUERY_STEPS = 4096;
    private static final List<Map<String, Object>> steps = new ArrayList<>(), queries = new ArrayList<>();
    private static final Map<Object, String> branches = new IdentityHashMap<>();
    private static final Set<String> problems = new TreeSet<>();
    private static final ThreadLocal<Map<String, Object>> pending = new ThreadLocal<>(), active = new ThreadLocal<>();
    private static Set<String> methods = Set.of();
    private static long textBytes;
    private static boolean frozen;

    public static void problem(String value) {
        synchronized (RecipeTrace.class) { if (problems.size() < 32) problems.add(value.substring(0, Math.min(512, value.length()))); }
    }
    public static void installed(Set<String> names) {
        synchronized (RecipeTrace.class) { methods = Set.copyOf(names); }
    }

    /** Mark only the single probe invocation; acceptance scans and other game lookups are excluded. */
    public static void query(String id) {
        synchronized (RecipeTrace.class) {
            try {
                if (id == null) {
                    var row = pending.get();
                    if (row == null || !Integer.valueOf(1).equals(row.get("calls")) || row.get("outcome").equals("open")) problem("query-not-once-and-closed");
                    pending.remove(); active.remove(); return;
                }
                if (frozen || pending.get() != null || !id.matches("q[0-9]{1,3}") || queries.size() >= 64
                        || queries.stream().anyMatch(row -> row.get("id").equals(id))) { problem("query-scope-or-bound"); return; }
                Map<String, Object> row = new LinkedHashMap<>();
                row.put("id", id); row.put("calls", 0); row.put("thread", Thread.currentThread().getId());
                row.put("arguments", null); row.put("outcome", "open"); row.put("selected", null); row.put("exception", null);
                row.put("step_start", steps.size()); row.put("step_end", null);
                queries.add(row); pending.set(row);
            } catch (Throwable failure) { problem("query-scope-failed:" + failure.getClass().getName()); }
        }
    }

    public static Object enterLookup(Object map, long voltage, Object items, Object fluids, boolean exact) {
        synchronized (RecipeTrace.class) {
            var row = pending.get();
            if (row == null || frozen) return null;
            try {
                row.put("calls", ((Integer) row.get("calls")) + 1);
                if (active.get() != null || !"mixer".equals(RecipeTrace.field(map, "unlocalizedName"))) {
                    problem("nested-or-wrong-map-lookup"); return null;
                }
                List<Object> itemRows = new ArrayList<>(), fluidRows = new ArrayList<>();
                if (((List<?>) items).size() > 64 || ((List<?>) fluids).size() > 64) throw new IllegalArgumentException("argument-bound");
                for (Object item : (List<?>) items) if (!Boolean.TRUE.equals(RecipeTrace.call(item, "func_190926_b"))) itemRows.add(RecipeTrace.stack(item, true));
                for (Object fluid : (List<?>) fluids) if (fluid != null) {
                    if (RecipeTrace.field(fluid, "tag") != null) throw new IllegalArgumentException("fluid-nbt");
                    fluidRows.add(Map.of("name", RecipeTrace.call(RecipeTrace.call(fluid, "getFluid"), "getName"), "amount", RecipeTrace.field(fluid, "amount")));
                }
                row.put("arguments", Map.of("map", "mixer", "items", itemRows, "fluids", fluidRows, "voltage_limit", voltage,
                    "exact_voltage", exact, "item_slots", ((List<?>) items).size(), "fluid_slots", ((List<?>) fluids).size()));
                active.set(row); return row;
            } catch (Throwable failure) { problem("lookup-entry-failed:" + failure.getClass().getName()); return null; }
        }
    }

    @SuppressWarnings("unchecked")
    public static void exitLookup(Object token, Object result, boolean threw) {
        synchronized (RecipeTrace.class) {
            if (token == null || frozen) return;
            try {
                var row = (Map<String, Object>) token;
                if (active.get() != row) { problem("lookup-nesting-mismatch"); return; }
                row.put("outcome", threw ? "threw" : "returned");
                row.put("selected", threw ? null : RecipeTrace.recipeId(result));
                row.put("exception", threw ? result.getClass().getName() : null);
                row.put("step_end", steps.size()); active.remove();
            } catch (Throwable failure) { problem("lookup-exit-failed:" + failure.getClass().getName()); }
        }
    }

    private static Object key(Object key) throws Exception {
        if (key == null) return null;
        String type = key.getClass().getName();
        Map<String, Object> value = new LinkedHashMap<>(); value.put("type", type);
        // Observe already constructed keys. Never invoke equals, hashCode, matches or tree traversal.
        switch (type) {
            case "gregtech.api.recipes.map.MapOreDictIngredient" -> value.put("ore_id", RecipeTrace.field(key, "ore"));
            case "gregtech.api.recipes.map.MapItemStackIngredient" -> {
                if (RecipeTrace.field(key, "tag") != null) throw new IllegalArgumentException("key-nbt");
                value.put("stack", RecipeTrace.stack(RecipeTrace.field(key, "stack"), false));
                value.put("metadata", RecipeTrace.field(key, "meta"));
            }
            case "gregtech.api.recipes.map.MapFluidIngredient" -> {
                if (RecipeTrace.field(key, "tag") != null) throw new IllegalArgumentException("key-nbt");
                value.put("fluid", RecipeTrace.call(RecipeTrace.field(key, "fluid"), "getName"));
            }
            default -> throw new IllegalArgumentException("unsupported-key:" + type);
        }
        return value;
    }

    public static void step(String site, String outcome, Object recipe, Object competing, Object key, int depth, Object branch) {
        synchronized (RecipeTrace.class) {
            if (frozen || !problems.isEmpty()) return;
            String operation = RecipeTrace.insertion(); var query = active.get();
            if (operation == null && query == null) return;
            try {
                if (steps.size() >= MAX_STEPS || query != null && steps.size() - (Integer) query.get("step_start") >= MAX_QUERY_STEPS) {
                    problem("decision-step-bound"); return;
                }
                if (branch != null && !branches.containsKey(branch)) {
                    if (branches.size() >= 8192) throw new IllegalArgumentException("branch-bound");
                    branches.put(branch, "b" + branches.size());
                }
                Map<String, Object> row = new LinkedHashMap<>();
                row.put("id", "d" + steps.size()); row.put("site", site); row.put("outcome", outcome);
                row.put("operation", operation); row.put("query", query == null ? null : query.get("id"));
                row.put("thread", Thread.currentThread().getId()); row.put("recipe", RecipeTrace.recipeId(recipe));
                row.put("competing", RecipeTrace.recipeId(competing));
                try { row.put("key", key(key)); }
                catch (Exception unsupported) { row.put("key", Map.of("type", key.getClass().getName(), "unavailable", true)); }
                row.put("depth", depth); row.put("branch", branch == null ? null : branches.get(branch));
                textBytes += row.toString().getBytes(java.nio.charset.StandardCharsets.UTF_8).length;
                if (textBytes > 8 * 1024 * 1024) { problem("decision-text-bound"); return; }
                steps.add(row);
            } catch (Throwable failure) { problem("decision-step-failed:" + failure.getClass().getName()); }
        }
    }

    static void relatedIds(Set<String> ids) {
        synchronized (RecipeTrace.class) {
            for (var query : queries) if (query.get("selected") instanceof String id) ids.add(id);
            for (var step : steps) if (step.get("query") != null && step.get("recipe") instanceof String id) ids.add(id);
            Map<String, Set<String>> adjacency = new HashMap<>();
            for (var step : steps) if (step.get("recipe") instanceof String recipe && step.get("competing") instanceof String other) {
                adjacency.computeIfAbsent(recipe, key -> new HashSet<>()).add(other);
                adjacency.computeIfAbsent(other, key -> new HashSet<>()).add(recipe);
            }
            Deque<String> pendingIds = new ArrayDeque<>(ids);
            while (!pendingIds.isEmpty()) for (String related : adjacency.getOrDefault(pendingIds.removeFirst(), Set.of()))
                if (ids.add(related)) pendingIds.addLast(related);
        }
    }

    public static Map<String, Object> snapshot(Map<Object, String> references, Map<?, ?> lifecycle) {
        synchronized (RecipeTrace.class) {
            frozen = true;
            if (methods.isEmpty()) problem("decision-hooks-unavailable");
            for (var query : queries) if (!Integer.valueOf(1).equals(query.get("calls")) || query.get("outcome").equals("open")) problem("query-not-once-and-closed");
            Set<Object> operations = new HashSet<>();
            for (Object object : (List<?>) lifecycle.get("events")) operations.add(((Map<?, ?>) object).get("id"));
            var retained = steps.stream().filter(row -> row.get("query") != null || operations.contains(row.get("operation"))).toList();
            Map<String, String> links = new TreeMap<>();
            references.forEach((recipe, reference) -> links.put(reference, RecipeTrace.recipeId(recipe)));
            Map<String, Object> result = new LinkedHashMap<>();
            result.put("format", "workbench-recipe-decisions-v1"); result.put("nonce", RecipeTrace.nonce()); result.put("candidate_id", RecipeTrace.candidate());
            result.put("state", problems.isEmpty() ? "complete" : "incomplete"); result.put("problems", List.copyOf(problems));
            result.put("methods", new TreeSet<>(methods)); result.put("steps", retained); result.put("queries", new ArrayList<>(queries));
            result.put("links", links); result.put("total_steps", steps.size());
            return result;
        }
    }
}
