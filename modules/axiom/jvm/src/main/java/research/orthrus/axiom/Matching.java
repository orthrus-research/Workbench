package research.orthrus.axiom;

import java.util.*;
import static research.orthrus.axiom.Domain.*;

/** Extracted from GTCEu Recipe.matches* at the locked revision. See sources/rules.json. */
public final class Matching {
    private Matching() {}
    public record Allocation(boolean matched, int[] items, int[] fluids) {}

    public static Allocation match(Recipe recipe, List<Item> items, List<Fluid> fluids, Registry registry) {
        // Upstream skips a whole domain when the handler has no slots/tanks.
        int[] fluidAmounts = null;
        if (!fluids.isEmpty()) {
            fluidAmounts = allocateFluids(recipe.fluids(), fluids);
            if (fluidAmounts == null) return new Allocation(false, null, null);
        }
        int[] itemAmounts = null;
        if (!items.isEmpty()) {
            itemAmounts = allocateItems(recipe.items(), items, registry);
            if (itemAmounts == null) return new Allocation(false, null, fluidAmounts);
        }
        return new Allocation(true, itemAmounts, fluidAmounts);
    }

    static int[] allocateItems(List<Ingredient> ingredients, List<Item> inputs, Registry registry) {
        int[] amounts = new int[inputs.size()];
        int indexed = 0;
        for (Ingredient ingredient : ingredients) {
            int required = ingredient.amount();
            for (int j = 0; j < inputs.size(); j++) {
                Item stack = inputs.get(j);
                if (j == indexed) { amounts[j] = stack == null || stack.empty() ? 0 : stack.count; indexed++; }
                if (stack == null || stack.empty() || !ingredient.accepts(stack, registry)) continue;
                int taken = Math.min(amounts[j], required);
                required -= taken;
                if (!ingredient.nonConsumable()) amounts[j] -= taken;
                if (required == 0) break;
            }
            if (required > 0) return null;
        }
        return Arrays.copyOf(amounts, indexed);
    }

    static int[] allocateFluids(List<Ingredient> ingredients, List<Fluid> inputs) {
        int[] amounts = new int[inputs.size()];
        int indexed = 0;
        for (Ingredient ingredient : ingredients) {
            int required = ingredient.amount();
            for (int j = 0; j < inputs.size(); j++) {
                Fluid stack = inputs.get(j);
                if (j == indexed) { amounts[j] = stack == null ? 0 : stack.amount; indexed++; }
                if (stack == null || !ingredient.accepts(stack)) continue;
                int taken = Math.min(amounts[j], required);
                required -= taken;
                if (!ingredient.nonConsumable()) amounts[j] -= taken;
                if (required == 0) break;
            }
            if (required > 0) return null;
        }
        return Arrays.copyOf(amounts, indexed);
    }

    public static void consume(Allocation allocation, List<Item> items, List<Fluid> fluids) {
        if (!allocation.matched) throw new IllegalArgumentException("Cannot consume a rejected allocation");
        // Only the indexed prefix is touched; unused trailing slots retain their contents.
        if (allocation.fluids != null) for (int i = 0; i < allocation.fluids.length; i++) {
            if (fluids.get(i) != null) fluids.get(i).amount = allocation.fluids[i];
        }
        if (allocation.items != null) for (int i = 0; i < allocation.items.length; i++) {
            if (items.get(i) != null) items.get(i).count = allocation.items[i];
        }
    }
}
