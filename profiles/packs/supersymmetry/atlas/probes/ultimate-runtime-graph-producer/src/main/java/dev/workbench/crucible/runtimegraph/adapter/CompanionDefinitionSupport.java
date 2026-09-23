package dev.workbench.crucible.runtimegraph.adapter;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;

import dev.workbench.crucible.runtimegraph.ReflectionAccess;
import dev.workbench.crucible.runtimegraph.StableValueEncoder;

import net.minecraft.block.Block;
import net.minecraft.item.Item;
import net.minecraft.item.ItemStack;
import net.minecraft.init.Items;
import net.minecraft.util.ResourceLocation;

import java.lang.reflect.Field;
import java.lang.reflect.Modifier;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

/** Shared deterministic projection helpers for definition-first companion adapters. */
final class CompanionDefinitionSupport {
    private CompanionDefinitionSupport() {}

    static int captureOwnedBlocks(
        String recordPrefix,
        Set<String> namespaces,
        List<JsonObject> records,
        StableValueEncoder encoder
    ) {
        List<ResourceLocation> names = new ArrayList<ResourceLocation>(Block.REGISTRY.getKeys());
        Collections.sort(names);
        int count = 0;
        for (ResourceLocation name : names) {
            if (!namespaces.contains(name.getNamespace())) continue;
            Block block = Block.REGISTRY.getObject(name);
            if (block == null) throw new IllegalStateException("missing owned block " + name);
            JsonObject row = definition(recordPrefix, "block", name, block.getClass());
            row.add("default_state", encoder.encode(block.getDefaultState()));
            Item item = Item.getItemFromBlock(block);
            ResourceLocation itemName = item == null ? null : Item.REGISTRY.getNameForObject(item);
            if (item == null || item == Items.AIR || itemName == null) {
                row.add("item_form", com.google.gson.JsonNull.INSTANCE);
            }
            else row.addProperty("item_form", itemName.toString());
            records.add(row);
            count++;
        }
        return count;
    }

    static int captureOwnedItems(
        String recordPrefix,
        Set<String> namespaces,
        List<JsonObject> records,
        StableValueEncoder encoder
    ) {
        List<ResourceLocation> names = new ArrayList<ResourceLocation>(Item.REGISTRY.getKeys());
        Collections.sort(names);
        int count = 0;
        for (ResourceLocation name : names) {
            if (!namespaces.contains(name.getNamespace())) continue;
            Item item = Item.REGISTRY.getObject(name);
            if (item == null) throw new IllegalStateException("missing owned item " + name);
            JsonObject row = definition(recordPrefix, "item", name, item.getClass());
            row.add("default_stack", encoder.encode(new ItemStack(item)));
            row.addProperty("has_subtypes", item.getHasSubtypes());
            row.addProperty("max_damage", item.getMaxDamage());
            row.addProperty("max_stack_size", item.getItemStackLimit());
            records.add(row);
            count++;
        }
        return count;
    }

    static int captureConfigurationObject(
        String recordPrefix,
        String group,
        Object owner,
        List<JsonObject> records,
        StableValueEncoder encoder
    ) {
        if (owner == null) throw new IllegalStateException("null configuration group " + group);
        List<Field> fields = new ArrayList<Field>();
        for (Field field : owner.getClass().getDeclaredFields()) {
            if (Modifier.isStatic(field.getModifiers()) || field.isSynthetic()) continue;
            field.setAccessible(true);
            fields.add(field);
        }
        Collections.sort(fields, fieldComparator());
        for (Field field : fields) {
            JsonObject row = new JsonObject();
            row.addProperty("record_type", recordPrefix + "-configuration-value");
            row.addProperty("configuration_id", group + '#' + field.getName());
            row.addProperty("configuration_group", group);
            row.addProperty("field_name", field.getName());
            row.addProperty("declared_type", field.getType().getName());
            row.add("value", encoder.encode(ReflectionAccess.read(field, owner)));
            records.add(row);
        }
        return fields.size();
    }

    static Set<String> namespaces(String... values) {
        Set<String> result = new LinkedHashSet<String>();
        Collections.addAll(result, values);
        return result;
    }

    static JsonArray classHierarchy(Class<?> type) {
        JsonArray result = new JsonArray();
        for (Class<?> current = type; current != null && current != Object.class;
             current = current.getSuperclass()) {
            result.add(current.getName());
        }
        return result;
    }

    static String packageFamily(Class<?> type, String root) {
        String name = type.getName();
        if (!name.startsWith(root)) return "external";
        String remainder = name.substring(root.length());
        int separator = remainder.indexOf('.');
        return separator < 0 ? "root" : remainder.substring(0, separator);
    }

    static Comparator<Class<?>> classComparator() {
        return new Comparator<Class<?>>() {
            @Override
            public int compare(Class<?> left, Class<?> right) {
                return left.getName().compareTo(right.getName());
            }
        };
    }

    private static JsonObject definition(
        String recordPrefix,
        String kind,
        ResourceLocation name,
        Class<?> runtimeClass
    ) {
        JsonObject row = new JsonObject();
        row.addProperty("record_type", recordPrefix + '-' + kind + "-definition");
        row.addProperty("definition_id", name.toString());
        row.addProperty("registry_name", name.toString());
        row.addProperty("namespace", name.getNamespace());
        row.addProperty("runtime_class", runtimeClass.getName());
        Package runtimePackage = runtimeClass.getPackage();
        row.addProperty(
            "runtime_package", runtimePackage == null ? "" : runtimePackage.getName()
        );
        row.add("class_hierarchy", classHierarchy(runtimeClass));
        return row;
    }

    private static Comparator<Field> fieldComparator() {
        return new Comparator<Field>() {
            @Override
            public int compare(Field left, Field right) {
                return left.getName().compareTo(right.getName());
            }
        };
    }
}
