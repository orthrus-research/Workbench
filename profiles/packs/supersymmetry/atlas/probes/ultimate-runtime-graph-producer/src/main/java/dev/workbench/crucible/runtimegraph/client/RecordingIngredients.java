package dev.workbench.crucible.runtimegraph.client;

import mezz.jei.api.ingredients.IIngredientHelper;
import mezz.jei.api.ingredients.IIngredientRegistry;
import mezz.jei.api.ingredients.IIngredients;
import mezz.jei.api.recipe.IIngredientType;

import java.util.ArrayList;
import java.util.Collections;
import java.util.IdentityHashMap;
import java.util.List;
import java.util.Map;

/** Public-API wrapper sampler with HEI's declared subtype expansion. */
@SuppressWarnings("deprecation")
final class RecordingIngredients implements IIngredients {
    private final IIngredientRegistry registry;
    private final Map<IIngredientType<?>, List<List<Object>>> inputs =
        new IdentityHashMap<IIngredientType<?>, List<List<Object>>>();
    private final Map<IIngredientType<?>, List<List<Object>>> outputs =
        new IdentityHashMap<IIngredientType<?>, List<List<Object>>>();

    RecordingIngredients(IIngredientRegistry registry) { this.registry = registry; }

    @Override public <T> void setInput(IIngredientType<T> type, T value) {
        setInputs(type, Collections.singletonList(value));
    }
    @Override public <T> void setInputs(IIngredientType<T> type, List<T> values) {
        List<List<T>> slots = new ArrayList<List<T>>();
        for (T value : required(values)) slots.add(Collections.singletonList(value));
        setInputLists(type, slots);
    }
    @Override public <T> void setInputLists(IIngredientType<T> type, List<List<T>> values) {
        inputs.put(type, expand(type, values));
    }
    @Override public <T> void setOutput(IIngredientType<T> type, T value) {
        setOutputs(type, Collections.singletonList(value));
    }
    @Override public <T> void setOutputs(IIngredientType<T> type, List<T> values) {
        List<List<T>> slots = new ArrayList<List<T>>();
        for (T value : required(values)) slots.add(Collections.singletonList(value));
        setOutputLists(type, slots);
    }
    @Override public <T> void setOutputLists(IIngredientType<T> type, List<List<T>> values) {
        outputs.put(type, expand(type, values));
    }
    @Override public <T> void setInput(Class<? extends T> type, T value) {
        setInput(type(type), value);
    }
    @Override public <T> void setInputs(Class<? extends T> type, List<T> values) {
        setInputs(type(type), values);
    }
    @Override public <T> void setInputLists(
        Class<? extends T> type, List<List<T>> values
    ) { setInputLists(type(type), values); }
    @Override public <T> void setOutput(Class<? extends T> type, T value) {
        setOutput(type(type), value);
    }
    @Override public <T> void setOutputs(Class<? extends T> type, List<T> values) {
        setOutputs(type(type), values);
    }
    @Override public <T> void setOutputLists(
        Class<? extends T> type, List<List<T>> values
    ) { setOutputLists(type(type), values); }
    @Override public <T> List<List<T>> getInputs(IIngredientType<T> type) {
        return typed(inputs.get(type));
    }
    @Override public <T> List<List<T>> getOutputs(IIngredientType<T> type) {
        return typed(outputs.get(type));
    }
    @Override public <T> List<List<T>> getInputs(Class<? extends T> type) {
        return getInputs(type(type));
    }
    @Override public <T> List<List<T>> getOutputs(Class<? extends T> type) {
        return getOutputs(type(type));
    }

    Map<IIngredientType<?>, List<List<Object>>> inputSnapshot() { return inputs; }
    Map<IIngredientType<?>, List<List<Object>>> outputSnapshot() { return outputs; }

    private <T> List<List<Object>> expand(IIngredientType<T> type, List<List<T>> slots) {
        if (type == null) throw new IllegalStateException("HEI wrapper used null type");
        IIngredientHelper<T> helper = registry.getIngredientHelper(type);
        if (helper == null) throw new IllegalStateException("HEI type lacks helper");
        List<List<Object>> result = new ArrayList<List<Object>>();
        for (List<T> slot : required(slots)) {
            List<T> expanded = helper.expandSubtypes(new ArrayList<T>(required(slot)));
            if (expanded == null) throw new IllegalStateException("HEI expansion returned null");
            result.add(new ArrayList<Object>(expanded));
        }
        return result;
    }

    private <T> IIngredientType<T> type(Class<? extends T> type) {
        IIngredientType<T> result = registry.getIngredientType(type);
        if (result == null) throw new IllegalStateException("HEI class lacks type " + type);
        return result;
    }
    private static <T> List<T> required(List<T> values) {
        if (values == null) throw new IllegalStateException("HEI wrapper returned null list");
        return values;
    }
    @SuppressWarnings("unchecked")
    private static <T> List<List<T>> typed(List<List<Object>> values) {
        if (values == null) return Collections.emptyList();
        return (List<List<T>>) (List<?>) values;
    }
}
