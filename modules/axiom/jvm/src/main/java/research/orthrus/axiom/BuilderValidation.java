package research.orthrus.axiom;

import java.util.*;
import java.util.function.Supplier;

/**
 * Native execution of GTCEu RecipeBuilder's Groovy validation operation.
 *
 * Source: GregTechCEu/GregTech 9fe140febe8747bbe2f06dfd570421331ec06f4b,
 * src/main/java/gregtech/api/recipes/RecipeBuilder.java, validateGroovy and
 * getRequiredString. LGPL-3.0; copyright remains with upstream contributors.
 * Only the surrounding field/logging carriers and annotations are substituted.
 * This operation checks builder fields, NOT registry existence or machine validity.
 */
final class BuilderValidation {
    record Shape(int maxInputs, int maxOutputs, int maxFluidInputs, int maxFluidOutputs) {
        int getMaxInputs() { return maxInputs; }
        int getMaxOutputs() { return maxOutputs; }
        int getMaxFluidInputs() { return maxFluidInputs; }
        int getMaxFluidOutputs() { return maxFluidOutputs; }
    }
    static final class Messages {
        final List<String> values = new ArrayList<>();
        void add(boolean condition, Supplier<String> text) { if (condition) values.add(text.get()); }
    }

    private final int EUt, duration;
    private final List<?> inputs, outputs, fluidInputs, fluidOutputs;
    private final Shape recipeMap;

    BuilderValidation(int eut, int duration, List<?> inputs, List<?> outputs,
                      List<?> fluidInputs, List<?> fluidOutputs, Shape shape) {
        this.EUt = eut; this.duration = duration; this.inputs = inputs; this.outputs = outputs;
        this.fluidInputs = fluidInputs; this.fluidOutputs = fluidOutputs; this.recipeMap = shape;
    }

    List<String> errors() {
        Messages messages = new Messages();
        validateGroovy(messages);
        return List.copyOf(messages.values);
    }

    private void validateGroovy(Messages errorMsg) {
        errorMsg.add(EUt == 0, () -> "EU/t must not be to 0");
        errorMsg.add(duration <= 0, () -> "Duration must not be less or equal to 0");
        int maxInput = recipeMap.getMaxInputs();
        int maxOutput = recipeMap.getMaxOutputs();
        int maxFluidInput = recipeMap.getMaxFluidInputs();
        int maxFluidOutput = recipeMap.getMaxFluidOutputs();
        errorMsg.add(inputs.size() > maxInput, () -> getRequiredString(maxInput, inputs.size(), "item input"));
        errorMsg.add(outputs.size() > maxOutput, () -> getRequiredString(maxOutput, outputs.size(), "item output"));
        errorMsg.add(fluidInputs.size() > maxFluidInput,
                () -> getRequiredString(maxFluidInput, fluidInputs.size(), "fluid input"));
        errorMsg.add(fluidOutputs.size() > maxFluidOutput,
                () -> getRequiredString(maxFluidOutput, fluidOutputs.size(), "fluid output"));
    }

    private static String getRequiredString(int max, int actual, String type) {
        if (max <= 0) {
            return "No " + type + "s allowed, but found " + actual;
        }
        String out = "Must have at most " + max + " " + type;
        if (max != 1) {
            out += "s";
        }
        out += ", but found " + actual;
        return out;
    }
}
