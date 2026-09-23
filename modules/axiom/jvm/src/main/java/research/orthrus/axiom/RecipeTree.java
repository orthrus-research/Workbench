package research.orthrus.axiom;

import java.util.*;
import java.util.function.Predicate;
import static research.orthrus.axiom.Domain.*;

/** GTCEu's ordered ingredient tree, not a sorted list of matching recipes. */
public final class RecipeTree {
    private final Registry registry;
    private final Branch root = new Branch();
    private boolean hasOres;
    private long visits;
    private static final long MAX_VISITS = 100_000;

    private static final class Branch { final Map<Key, Object> nodes = new HashMap<>(); }
    private final class Key {
        final String kind;
        final String id;
        final Item item;
        final Tag tag;
        final Ingredient ingredient;
        Key(String kind, String id, Item item, Tag tag, Ingredient ingredient) {
            this.kind = kind; this.id = id; this.item = item; this.tag = tag; this.ingredient = ingredient;
        }
        @Override public int hashCode() {
            // Tree operations never iterate buckets. Item identity's hash is replaced
            // consistently on both sides; equality still checks item/metadata first.
            if (kind.equals("item")) return 31 * item.type.id().hashCode() + 31 * item.type.meta() + 31 * Objects.hashCode(tag);
            return Objects.hash(kind, id, tag);
        }
        @Override public boolean equals(Object value) {
            if (this == value) return true;
            if (!(value instanceof RecipeTree.Key other) || !kind.equals(other.kind) || !id.equals(other.id)) return false;
            if (!kind.equals("item")) return Objects.equals(tag, other.tag);
            if (!item.type.equals(other.item.type)) return false;
            // Preserve MapItemStackIngredient's directional lookup equality.
            if (ingredient != null && other.ingredient != null) return ingredient.equalIgnoringAmount(other.ingredient);
            if (ingredient == null && other.ingredient != null) return other.ingredient.accepts(item, registry);
            return false;
        }
    }

    public RecipeTree(Registry registry) { this.registry = registry; }
    public boolean contains(Recipe recipe) { return contains(root, recipe); }
    private boolean contains(Branch branch, Recipe recipe) {
        for (Object value : branch.nodes.values()) {
            if (value == recipe || value instanceof Branch child && contains(child, recipe)) return true;
        }
        return false;
    }
    public boolean register(Recipe recipe) {
        List<List<Key>> keys = new ArrayList<>();
        List<Ingredient> unique = new ArrayList<>();
        for (Ingredient input : recipe.items()) {
            if (unique.stream().anyMatch(input::equalIgnoringAmount)) continue;
            if (input.kind().equals("circuit")) unique.add(0, input); else unique.add(input);
        }
        for (Ingredient input : unique) {
            if (input.kind().equals("ore")) {
                hasOres = true;
                keys.add(List.of(new Key("ore", input.id(), null, null, input)));
            } else {
                Item stack = input.kind().equals("circuit") ? new Item(registry.circuit, 1, Tag.circuit(input.configuration()))
                        : new Item(input.item(), input.amount(), input.tag());
                keys.add(List.of(new Key("item", stack.type.id(), stack, stack.tag, input)));
            }
        }
        for (Ingredient input : recipe.fluids()) keys.add(List.of(new Key("fluid", input.id(), null, input.tag(), input)));
        visits = 0;
        return add(recipe, keys, root, 0, 0);
    }

    private boolean add(Recipe recipe, List<List<Key>> keys, Branch branch, int index, int count) {
        bound();
        if (count >= keys.size()) return true;
        Branch right = new Branch();
        for (Key key : keys.get(index)) {
            Object node = branch.nodes.get(key);
            if (count == keys.size() - 1) {
                if (node == null) { node = recipe; branch.nodes.put(key, recipe); }
            } else if (node == null) { node = right; branch.nodes.put(key, right); }
            if (node instanceof Recipe existing) {
                if (existing == recipe) continue;
                return false;
            }
            boolean added = add(recipe, keys, (Branch)node, (index + 1) % keys.size(), count + 1);
            if (!added) {
                if (count == keys.size() - 1 || ((Branch)node).nodes.isEmpty()) branch.nodes.remove(key);
                return false;
            }
        }
        return true;
    }

    public Recipe find(long voltage, List<Item> items, List<Fluid> fluids) {
        List<List<Key>> keys = new ArrayList<>();
        List<Item> unique = new ArrayList<>();
        for (Item item : items) {
            if (item == null || item.empty() || unique.stream().anyMatch(item::same)) continue;
            unique.add(item);
            List<Key> alternatives = new ArrayList<>();
            alternatives.add(new Key("item", item.type.id(), item, item.tag, null));
            if (hasOres) {
                for (String ore : registry.oreLookupOrder(item.type)) alternatives.add(new Key("ore", ore, null, null, null));
            }
            keys.add(alternatives);
        }
        for (Fluid fluid : fluids) if (fluid != null && fluid.amount != 0) keys.add(List.of(new Key("fluid", fluid.id, null, fluid.tag, null)));
        if (keys.isEmpty()) return null;
        // The concrete MIXER has at most seven item keys and three fluid keys.
        if (keys.size() >= 64) throw Failure.unsupported("lookup.bound", "Packed-long traversal admits fewer than 64 keys");
        Predicate<Recipe> predicate = recipe -> recipe.eut() <= voltage && Matching.match(recipe, items, fluids, registry).matched();
        visits = 0;
        for (int i = 0; i < keys.size(); i++) {
            Recipe result = find(keys, root, predicate, i, 0, 1L << i);
            if (result != null) return result;
        }
        return null;
    }

    private Recipe find(List<List<Key>> keys, Branch branch, Predicate<Recipe> predicate, int index, int count, long skip) {
        bound();
        if (count == keys.size()) return null;
        for (Key key : keys.get(index)) {
            Object node = branch.nodes.get(key);
            if (node instanceof Recipe recipe) {
                if (predicate.test(recipe)) return recipe;
            } else if (node instanceof Branch next) {
                int i = (index + 1) % keys.size();
                while (i != index) {
                    if ((skip & (1L << i)) == 0) {
                        Recipe result = find(keys, next, predicate, i, count + 1, skip | (1L << i));
                        if (result != null) return result;
                    }
                    i = (i + 1) % keys.size();
                }
            }
        }
        return null;
    }
    private void bound() {
        if (++visits > MAX_VISITS) throw new Failure("incomplete", "lookup.bound", "Ingredient traversal exhausted its 100000-visit bound");
    }
}
