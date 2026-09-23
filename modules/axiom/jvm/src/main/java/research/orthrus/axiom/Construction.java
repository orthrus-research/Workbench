package research.orthrus.axiom;

import java.util.*;
import static research.orthrus.axiom.Domain.*;

/** Admitted RecipeBuilder operations and per-map registration state. */
public final class Construction {
    public final Registry registry;
    public final List<Recipe> definitions = new ArrayList<>();
    public final List<Map<String, Object>> diagnostics = new ArrayList<>();
    public final Map<String, RecipeTree> trees = new LinkedHashMap<>();
    public final Map<String, Boolean> registered = new LinkedHashMap<>();
    private int sequence;

    public Construction(Registry registry) {
        this.registry = registry;
        trees.put("mixer", new RecipeTree(registry)); trees.put("blender", new RecipeTree(registry));
    }

    public final class Builder {
        public final String map;
        public final Location location;
        final List<Ingredient> items = new ArrayList<>(), fluids = new ArrayList<>();
        final List<Item> itemOutputs = new ArrayList<>();
        final List<Fluid> fluidOutputs = new ArrayList<>();
        int duration, eut;
        boolean finished;
        Builder(String map, Location location) { this.map = map; this.location = location; this.eut = map.equals("blender") ? 30 : 0; }
        public Object call(String name, List<Object> args) {
            if (finished) throw Failure.unsupported("source.builder-reuse", "Builder reuse after registration is outside this slice");
            switch (name) {
                case "EUt", "duration", "circuitMeta" -> {
                    arity(args, 1); int value = integer(args.get(0));
                    switch (name) {
                        case "EUt" -> eut = value;
                        case "duration" -> duration = value;
                        case "circuitMeta" -> {
                            if (value < 0 || value > 32) diagnostic("warning", "builder.circuit-range", "Native setter logs INVALID and omits this circuit; Groovy validation does not inspect that status", location);
                            else items.add(new Ingredient("circuit", registry.circuit.id(), 1, true, registry.circuit, null, value));
                        }
                    }
                }
                case "inputs", "notConsumable" -> {
                    if (args.isEmpty()) throw Failure.unsupported("source.overload", "Empty input overload is not admitted");
                    for (Object arg : args) {
                        if (!(arg instanceof Ingredient input) || input.kind().equals("fluid")) throw Failure.unsupported("source.overload", "Expected ore or ordinary item ingredient");
                        if (name.equals("notConsumable")) { arity(args, 1); input = input.nonConsumableInput(); }
                        if (input.kind().equals("item") && input.amount() <= 0)
                            throw Failure.unsupported("item.empty-construction", "Empty concrete ItemStack construction is not admitted");
                        if (input.amount() < 0) diagnostic("warning", "builder.input-amount", "Native builder omits a negative item input", location);
                        else items.add(input);
                    }
                }
                case "fluidInputs", "fluidOutputs" -> {
                    if (args.isEmpty()) throw Failure.unsupported("source.overload", "Empty fluid overload is not admitted");
                    for (Object arg : args) {
                        if (!(arg instanceof Ingredient input) || !input.kind().equals("fluid")) throw Failure.unsupported("source.overload", "Expected fluid stack");
                        if (name.equals("fluidInputs")) {
                            if (input.amount() > 0) fluids.add(input);
                            else diagnostic("warning", "builder.fluid-amount", "Native FluidStack input overload omits a nonpositive amount", location);
                        } else fluidOutputs.add(new Fluid(input.id(), input.amount(), input.tag()));
                    }
                }
                case "outputs" -> {
                    if (args.isEmpty()) throw Failure.unsupported("source.overload", "Empty output overload is not admitted");
                    for (Object arg : args) {
                        if (!(arg instanceof Ingredient input) || !input.kind().equals("item")) throw Failure.unsupported("source.overload", "Ordinary item outputs require concrete item stacks");
                        if (input.amount() > 0) itemOutputs.add(new Item(input.item(), input.amount(), input.tag()));
                    }
                }
                case "buildAndRegister" -> { arity(args, 0); finished = true; finish(this); return null; }
                default -> throw Failure.unsupported("source.builder-method", "Builder operation is not qualified: " + name);
            }
            if (items.size() + fluids.size() + itemOutputs.size() + fluidOutputs.size() > 128)
                throw new Failure("incomplete", "construction.bound", "Builder exceeds 128 fields");
            return this;
        }
    }
    public Builder builder(String map, Location location) { return new Builder(map, location); }
    private void finish(Builder builder) {
        if (definitions.size() >= 512) throw new Failure("incomplete", "construction.bound", "Program exceeds 512 effective definitions");
        String original = "recipe:" + sequence++;
        if (builder.map.equals("mixer")) {
            // Independent representation of Susy-Core's source-observed field
            // projection, not a transplant of its GPL implementation.
            register(snapshot(builder, "blender", "recipe:" + sequence++, original));
        }
        register(snapshot(builder, builder.map, original, null));
    }
    private Recipe snapshot(Builder builder, String map, String id, String parent) {
        return new Recipe(id, map, List.copyOf(builder.items), List.copyOf(builder.fluids),
                builder.itemOutputs.stream().map(Item::copy).toList(), builder.fluidOutputs.stream().map(Fluid::copy).toList(),
                builder.duration, builder.eut, builder.location, parent);
    }
    private void register(Recipe recipe) {
        definitions.add(recipe);
        int maxItems = recipe.map().equals("mixer") ? 6 : 9;
        int maxFluids = recipe.map().equals("mixer") ? 3 : 6;
        // Shapes are still source projections, not an executed pack bootstrap.
        // The field-validation operation itself now runs the extracted upstream code.
        List<String> errors = new BuilderValidation(recipe.eut(), recipe.duration(), recipe.items(), recipe.itemOutputs(),
                recipe.fluids(), recipe.fluidOutputs(), new BuilderValidation.Shape(maxItems, 1, maxFluids, 2)).errors();
        if (!errors.isEmpty()) {
            registered.put(recipe.id(), false);
            diagnostic("rejected", "builder.validate-groovy", "Groovy validation SKIP for " + recipe.id() + ": " + String.join("; ", errors), recipe.location());
            return;
        }
        boolean accepted = trees.get(recipe.map()).register(recipe);
        registered.put(recipe.id(), accepted);
        if (!accepted) diagnostic("rejected", "registration.tree-conflict", "Existing ingredient-tree path prevents registration of " + recipe.id(), recipe.location());
    }
    public void diagnostic(String status, String rule, String message, Location location) {
        diagnostics.add(Map.of("status", status, "rule", rule, "message", message, "source", location.json()));
    }
    static int integer(Object value) {
        if (!(value instanceof Integer result)) throw Failure.unsupported("source.numeric-overload", "This slice admits Java int arguments; other Groovy coercions are unsupported");
        return result;
    }
    static void arity(List<?> args, int expected) {
        if (args.size() != expected) throw Failure.unsupported("source.overload", "Expected " + expected + " arguments");
    }
}
