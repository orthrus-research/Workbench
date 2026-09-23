package research.orthrus.axiom;

import java.util.*;

/** Game-independent values. Registries are explicit inputs, never fabricated world state. */
public final class Domain {
    private Domain() {}

    /** Typed NBT subset admitted by this slice. Other tag kinds fail closed. */
    public record Tag(String type, Object value) {
        public static Tag read(Object raw) {
            if (raw == null) return null;
            Map<String, Object> obj = Json.object(raw);
            Json.keys(obj, "type", "value");
            String type = Json.string(obj.get("type"));
            Object value = obj.get("value");
            switch (type) {
                case "compound" -> {
                    Map<String, Tag> entries = new LinkedHashMap<>();
                    Json.object(value).forEach((key, entry) -> {
                        Tag tag = read(entry);
                        if (tag == null) throw Failure.request("Null compound member");
                        entries.put(key, tag);
                    });
                    return new Tag(type, Collections.unmodifiableMap(entries));
                }
                case "string" -> { return new Tag(type, Json.string(value)); }
                case "byte", "short", "int", "long" -> {
                    long number = Json.integer(value);
                    long min = switch (type) { case "byte" -> -128; case "short" -> -32768; case "int" -> Integer.MIN_VALUE; default -> Long.MIN_VALUE; };
                    long max = switch (type) { case "byte" -> 127; case "short" -> 32767; case "int" -> Integer.MAX_VALUE; default -> Long.MAX_VALUE; };
                    if (number < min || number > max) throw Failure.request("NBT value exceeds " + type);
                    return new Tag(type, number);
                }
                default -> throw Failure.unsupported("nbt.kind", "NBT kind not admitted: " + type);
            }
        }
        public static Tag circuit(int configuration) {
            return new Tag("compound", Map.of("Configuration", new Tag("int", (long) configuration)));
        }
        @SuppressWarnings("unchecked")
        public int configuration() {
            if (!type.equals("compound")) throw Failure.request("Stack NBT must be compound");
            Tag entry = ((Map<String, Tag>)value).get("Configuration");
            return entry != null && entry.value instanceof Long number ? number.intValue() : 0;
        }
        public Map<String, Object> json() {
            if (type.equals("compound")) {
                Map<String, Object> entries = new LinkedHashMap<>();
                ((Map<?, ?>)value).forEach((key, tag) -> entries.put(key.toString(), ((Tag)tag).json()));
                return Map.of("type", type, "value", entries);
            }
            return Map.of("type", type, "value", value);
        }
        /** Minecraft 1.12 primitive/compound NBT hashes; used by full-NBT lookup keys. */
        @Override public int hashCode() {
            int id = switch(type) { case "byte" -> 1; case "short" -> 2; case "int" -> 3; case "long" -> 4; case "string" -> 8; case "compound" -> 10; default -> throw new AssertionError(type); };
            int hash = value instanceof Long n ? (type.equals("long") ? Long.hashCode(n) : n.intValue()) : value.hashCode();
            return id ^ hash;
        }
    }

    public record ItemType(String id, int meta, int maxStack) {
        public String key() { return id + ":" + meta; }
    }
    public static final class Item {
        public final ItemType type;
        public int count;
        public Tag tag;
        public Item(ItemType type, int count, Tag tag) { this.type = type; this.count = count; this.tag = tag; }
        public boolean empty() { return count <= 0; }
        public Item copy() { return new Item(type, count, tag); }
        public boolean same(Item other) { return other != null && type.equals(other.type) && Objects.equals(tag, other.tag); }
        public Map<String, Object> json() {
            Map<String, Object> out = new LinkedHashMap<>(Map.of("id", type.id, "meta", type.meta, "count", count));
            out.put("nbt", tag == null ? null : tag.json()); return out;
        }
    }
    public static final class Fluid {
        public final String id;
        public int amount;
        public final Tag tag;
        public Fluid(String id, int amount, Tag tag) { this.id = id; this.amount = amount; this.tag = tag; }
        public Fluid copy() { return new Fluid(id, amount, tag); }
        public boolean same(Fluid other) { return other != null && id.equals(other.id) && Objects.equals(tag, other.tag); }
        public Map<String, Object> json() {
            Map<String, Object> out = new LinkedHashMap<>(Map.of("id", id, "amount", amount));
            out.put("nbt", tag == null ? null : tag.json()); return out;
        }
    }
    public record Ore(String name, List<ItemType> members) {}

    public static final class Registry {
        public final Map<String, ItemType> items = new LinkedHashMap<>();
        public final Map<String, Ore> ores = new LinkedHashMap<>();
        public final Set<String> fluids = new LinkedHashSet<>();
        private final Map<String, List<String>> lookupOrder = new HashMap<>();
        public final ItemType circuit;
        public final Object declaration;

        public Registry(Object raw) {
            Map<String, Object> input = Json.object(raw);
            Json.keys(input, "scope", "items", "ores", "fluids", "circuit", "oreLookupOrder");
            if (!"explicit-context".equals(input.get("scope"))) throw Failure.request("Registry scope must be explicit-context; no whole-pack registry is qualified");
            for (Object row : Json.array(input.get("items"))) {
                Map<String, Object> item = Json.object(row);
                Json.keys(item, "id", "meta", "maxStack", "capabilities");
                if (!"none".equals(item.get("capabilities"))) throw Failure.unsupported("item.capabilities", "Capability-bearing items require a qualified predicate");
                ItemType type = new ItemType(identifier(item.get("id")), Json.number(item.get("meta")), Json.number(item.get("maxStack")));
                if (type.meta < 0 || type.meta >= 32767 || type.maxStack < 1 || type.maxStack > 64)
                    throw Failure.unsupported("item.shape", "Admitted items require concrete metadata and ordinary 1..64 stack limits");
                if (items.putIfAbsent(type.key(), type) != null) throw Failure.request("Duplicate item declaration: " + type.key());
            }
            for (Object row : Json.array(input.get("ores"))) {
                Map<String, Object> ore = Json.object(row);
                Json.keys(ore, "name", "members");
                String name = Json.string(ore.get("name"));
                List<ItemType> members = new ArrayList<>();
                for (Object member : Json.array(ore.get("members"))) {
                    Map<String, Object> value = Json.object(member); Json.keys(value, "id", "meta");
                    members.add(item(Json.string(value.get("id")), Json.number(value.get("meta"))));
                }
                if (ores.putIfAbsent(name, new Ore(name, List.copyOf(members))) != null) throw Failure.request("Duplicate ore declaration: " + name);
            }
            for (Object name : Json.array(input.get("fluids")))
                if (!fluids.add(Json.string(name))) throw Failure.request("Duplicate fluid declaration");
            Json.object(input.get("oreLookupOrder")).forEach((key, value) -> {
                ItemType type = items.get(key);
                if (type == null) throw Failure.request("Ore lookup order names an undeclared item");
                List<String> order = Json.array(value).stream().map(Json::string).toList();
                Set<String> expected = new HashSet<>();
                ores.forEach((name, ore) -> { if (ore.members.contains(type)) expected.add(name); });
                if (new HashSet<>(order).size() != order.size() || !expected.equals(new HashSet<>(order)))
                    throw Failure.request("Ore lookup order must name every membership exactly once");
                lookupOrder.put(key, order);
            });
            Map<String, Object> circuitData = Json.object(input.get("circuit"));
            Json.keys(circuitData, "id", "meta");
            circuit = item(Json.string(circuitData.get("id")), Json.number(circuitData.get("meta")));
            declaration = raw;
        }
        public ItemType item(String id, int meta) {
            ItemType result = items.get(id + ":" + meta);
            if (result == null) throw Failure.context("Missing item/metadata declaration: " + id + ":" + meta);
            return result;
        }
        public Ore ore(String name) {
            Ore result = ores.get(name);
            if (result == null) throw Failure.context("Missing ore expansion: " + name + "; absence is not an empty ore");
            return result;
        }
        public Item readItem(Object raw) {
            if (raw == null) return null;
            Map<String, Object> item = Json.object(raw); Json.keys(item, "id", "meta", "count", "nbt");
            ItemType type = item(Json.string(item.get("id")), Json.number(item.get("meta")));
            int count = Json.number(item.get("count"));
            if (count < 1 || count > type.maxStack) throw Failure.request("Item count outside physical stack limit");
            return new Item(type, count, compound(item.get("nbt")));
        }
        public Fluid readFluid(Object raw) {
            if (raw == null) return null;
            Map<String, Object> fluid = Json.object(raw); Json.keys(fluid, "id", "amount", "nbt");
            String id = Json.string(fluid.get("id")); requireFluid(id);
            int amount = Json.number(fluid.get("amount"));
            if (amount <= 0) throw Failure.request("Physical fluid amount must be positive");
            return new Fluid(id, amount, compound(fluid.get("nbt")));
        }
        public void requireFluid(String name) { if (!fluids.contains(name)) throw Failure.context("Missing fluid declaration: " + name); }
        public List<String> oreLookupOrder(ItemType item) {
            List<String> order = lookupOrder.get(item.key());
            if (order == null) throw Failure.context("Missing ordered OreDictionary.getOreIDs result for " + item.key());
            return order;
        }
    }
    static Tag compound(Object raw) {
        Tag tag = Tag.read(raw);
        if (tag != null && !tag.type.equals("compound")) throw Failure.request("Stack NBT must be a compound");
        return tag;
    }
    private static String identifier(Object raw) {
        String id = Json.string(raw);
        if (!id.matches("[a-z0-9_.-]+:[a-z0-9_./-]+")) throw Failure.request("Invalid registry identifier");
        return id;
    }

    public record Ingredient(String kind, String id, int amount, boolean nonConsumable, ItemType item, Tag tag, int configuration) {
        public Ingredient withAmount(int count) { return new Ingredient(kind, id, count, nonConsumable, item, tag, configuration); }
        public Ingredient nonConsumableInput() { return new Ingredient(kind, id, amount, true, item, tag, configuration); }
        public boolean accepts(Item stack, Registry registry) {
            if (stack == null || stack.empty()) return false;
            return switch (kind) {
                case "ore" -> registry.ore(id).members.contains(stack.type);
                case "item" -> item.equals(stack.type) && Objects.equals(tag, stack.tag);
                case "circuit" -> {
                    if (!registry.circuit.equals(stack.type)) yield false;
                    // getCircuitConfiguration calls isIntegratedCircuit, initializing missing NBT.
                    if (stack.tag == null) stack.tag = Tag.circuit(0);
                    yield stack.tag.configuration() == configuration;
                }
                default -> throw new AssertionError(kind);
            };
        }
        public boolean accepts(Fluid stack) { return stack != null && stack.amount != 0 && id.equals(stack.id) && Objects.equals(tag, stack.tag); }
        public boolean equalIgnoringAmount(Ingredient other) {
            return kind.equals(other.kind) && id.equals(other.id) && Objects.equals(item, other.item) && Objects.equals(tag, other.tag) && configuration == other.configuration;
        }
        public Map<String, Object> json() {
            Map<String, Object> value = new LinkedHashMap<>(Map.of("kind", kind, "id", id, "amount", amount, "nonConsumable", nonConsumable));
            if (item != null) value.put("meta", item.meta);
            if (tag != null) value.put("nbt", tag.json());
            if (kind.equals("circuit")) value.put("configuration", configuration);
            return value;
        }
    }

    public record Location(String path, String sha256, int line, int column) {
        public Map<String, Object> json() { return Map.of("path", path, "sha256", sha256, "line", line, "column", column); }
    }
    public record Recipe(String id, String map, List<Ingredient> items, List<Ingredient> fluids,
                         List<Item> itemOutputs, List<Fluid> fluidOutputs, int duration, int eut,
                         Location location, String derivedFrom) {
        public Map<String, Object> json() {
            Map<String, Object> value = new LinkedHashMap<>(Map.of("id", id, "map", map, "items", items.stream().map(Ingredient::json).toList(),
                    "fluids", fluids.stream().map(Ingredient::json).toList(), "duration", duration, "eut", eut,
                    "itemOutputs", itemOutputs.stream().map(Item::json).toList(), "fluidOutputs", fluidOutputs.stream().map(Fluid::json).toList(),
                    "source", location.json()));
            value.put("derivedFrom", derivedFrom); return value;
        }
    }
}
