package research.orthrus.axiom;

import java.util.*;
import static research.orthrus.axiom.Domain.*;

/** Ordinary SimpleMachine MIXER: input loading and one idle recipe-search/start transition. */
public final class Mixer {
    private static final long[] VOLTAGE = {8,32,128,512,2048,8192,32768,131072,524288,2097152,8388608,33554432,134217728,536870912,Integer.MAX_VALUE};
    private static final Map<String, Integer> TIERS = Map.of("gregtech:mixer.lv", 1, "gregtech:mixer.mv", 2,
            "gregtech:mixer.hv", 3, "gregtech:mixer.ev", 4, "gregtech:mixer.iv", 5);
    private final Registry registry;
    private final String machine;
    private final int tier, tankCapacity, overclockTier;
    private final long energy;
    private final boolean voidItems, voidFluids, allowOutputSideFluids;
    private final List<Item> items, outputItems;
    private final List<Fluid> fluids, outputFluids;
    private final List<Map<String, Object>> effects = new ArrayList<>();
    private final List<Map<String, Object>> stages = new ArrayList<>();
    private int circuit;
    private String previous;
    private boolean outputsFull;

    public Mixer(Object raw, Registry registry, String programId) {
        this.registry = registry;
        Map<String, Object> state = Json.object(raw);
        Json.keys(state, "machine", "entry", "inputItems", "inputFluids", "outputItems", "outputFluids", "ghostCircuit", "energy",
                "overclockTier", "voidItems", "voidFluids", "allowInputFromOutputSideItems", "allowInputFromOutputSideFluids", "cache", "outputsFull");
        machine = Json.string(state.get("machine"));
        if (!TIERS.containsKey(machine)) throw Failure.unsupported("machine.catalog", "Only ordinary LV..IV MIXER factories are admitted; BLENDER construction is not machine coverage");
        tier = TIERS.get(machine); tankCapacity = tier == 1 ? 8000 : tier == 2 ? 12000 : 16000;
        if (!"idle-search".equals(state.get("entry"))) throw Failure.unsupported("machine.entry", "Only idle trySearchNewRecipe entry is admitted, not a ticking machine");
        items = itemList(state.get("inputItems"), 6); fluids = fluidList(state.get("inputFluids"), 3);
        outputItems = itemList(state.get("outputItems"), 1); outputFluids = fluidList(state.get("outputFluids"), 2);
        circuit = Json.number(state.get("ghostCircuit")); circuitRange(circuit);
        energy = Json.integer(state.get("energy"));
        if (energy < 0 || energy > VOLTAGE[tier] * 64L) throw Failure.request("Energy exceeds ordinary machine buffer");
        overclockTier = Json.number(state.get("overclockTier"));
        if (overclockTier < 0 || overclockTier > tier) throw Failure.request("Overclock tier must be 0..machine tier");
        voidItems = Json.bool(state.get("voidItems")); voidFluids = Json.bool(state.get("voidFluids"));
        // Retain validation of both controls but preserve the upstream capability bug:
        // the FLUID control is read by both item and fluid output-side admission.
        Json.bool(state.get("allowInputFromOutputSideItems"));
        allowOutputSideFluids = Json.bool(state.get("allowInputFromOutputSideFluids"));
        outputsFull = Json.bool(state.get("outputsFull"));
        if (state.get("cache") != null) {
            Map<String, Object> cache = Json.object(state.get("cache")); Json.keys(cache, "programId", "recipeId");
            if (!programId.equals(cache.get("programId"))) throw Failure.request("Cache belongs to a different target/source/registry identity");
            previous = Json.string(cache.get("recipeId"));
        }
    }
    private List<Item> itemList(Object raw, int size) {
        List<Object> values = Json.array(raw);
        if (values.size() != size) throw Failure.request("MIXER requires " + size + " physical item slots");
        return new ArrayList<>(values.stream().map(registry::readItem).toList());
    }
    private List<Fluid> fluidList(Object raw, int size) {
        List<Object> values = Json.array(raw);
        if (values.size() != size) throw Failure.request("MIXER requires " + size + " physical fluid tanks");
        List<Fluid> result = new ArrayList<>(values.stream().map(registry::readFluid).toList());
        for (Fluid fluid : result) if (fluid != null && fluid.amount > tankCapacity) throw Failure.request("Fluid exceeds per-tank capacity");
        return result;
    }
    private static void circuitRange(int value) { if (value < -1 || value > 32) throw Failure.request("Ghost circuit must be -1 (absent) or 0..32"); }

    public void load(Object raw) {
        List<Object> actions = Json.array(raw);
        if (actions.size() > 128) throw Failure.request("At most 128 loading operations");
        for (Object action : actions) {
            Map<String, Object> input = Json.object(action);
            Json.keys(input, "route", "slot", "stack", "side", "configuration");
            String route = Json.string(input.get("route"));
            int inserted = 0, requested = 0;
            switch (route) {
                case "ghost-control" -> { circuit = Json.number(input.get("configuration")); circuitRange(circuit); }
                case "gui-fluid-tank", "capability-fluid" -> {
                    Fluid fluid = registry.readFluid(input.get("stack"));
                    if (fluid == null) throw Failure.request("Loading requires a fluid");
                    requested = fluid.amount;
                    if (route.equals("gui-fluid-tank")) inserted = fillOne(fluids, slot(input, fluids.size()), fluid, requested, tankCapacity);
                    else if (admitSide(input)) inserted = fillDistinct(fluids, fluid, requested, tankCapacity);
                }
                case "gui-item-slot", "capability-item-slot" -> {
                    Item item = registry.readItem(input.get("stack"));
                    if (item == null) throw Failure.request("Loading requires an item");
                    requested = item.count;
                    int slot = slot(input, items.size());
                    if (route.equals("gui-item-slot") || admitSide(input)) inserted = fillItem(items, slot, item);
                }
                default -> throw Failure.unsupported("handler.route", "Unmodeled loading route: " + route);
            }
            effects.add(Map.of("rule", "handler.load", "route", route, "requested", requested, "inserted", inserted, "uninserted", requested - inserted));
        }
    }
    private boolean admitSide(Map<String, Object> input) {
        String side = Json.string(input.get("side"));
        if (!side.equals("ordinary") && !side.equals("output")) throw Failure.request("Capability side must be ordinary or this channel's output side");
        return side.equals("ordinary") || allowOutputSideFluids;
    }
    private int slot(Map<String, Object> input, int limit) {
        int slot = Json.number(input.get("slot"));
        if (slot < 0 || slot >= limit) throw Failure.request("Physical slot out of range; ghost circuits are controls, not insertable slots");
        return slot;
    }

    public Map<String, Object> start(Construction program, String programId) {
        Map<String, Object> original = state(programId, null, null);
        List<Item> visibleItems = new ArrayList<>(items);
        visibleItems.add(circuit < 0 ? null : new Item(registry.circuit, 1, Tag.circuit(circuit)));
        Recipe selected = null;
        String selection = "fresh-lookup";
        if (previous != null) {
            Recipe cached = program.definitions.stream().filter(recipe -> recipe.id().equals(previous)).findFirst()
                    .orElseThrow(() -> Failure.request("Cache recipe does not exist in this program"));
            if (!cached.map().equals("mixer") || !Boolean.TRUE.equals(program.registered.get(previous))) throw Failure.request("Cache must name a registered MIXER recipe");
            if (cached.eut() <= VOLTAGE[tier] && Matching.match(cached, visibleItems, fluids, registry).matched()) {
                selected = cached; selection = "previous-recipe";
            }
        }
        if (selected == null) selected = program.trees.get("mixer").find(VOLTAGE[tier], visibleItems, fluids);
        if (selected == null) {
            stage("lookup.selection", "rejected", "No tree-selected recipe matches this state");
            return finish("rejected", null, selection, original, programId, null, null);
        }
        previous = selected.id(); // upstream caches before eligibility/preparation
        effects.add(Map.of("rule", "lookup.cache", "recipeId", previous));
        stage("lookup.selection", "accepted", "Selected " + previous + " by " + selection);
        stage("machine.cleanroom", "accepted", "Admitted builder subset has no cleanroom property");
        // Output limits equal final map shape, all admitted outputs deterministic,
        // ordinary machine parallel limit is one, discounts/hooks are absent.
        stage("machine.prepare", "accepted", "No output trimming or parallel expansion for this admitted ordinary machine");
        int[] clock = overclock(selected.eut(), selected.duration(), overclockTier);
        effects.add(Map.of("rule", "machine.overclock", "eut", clock[0], "duration", clock[1]));
        boolean power = clock[0] >= 0 ? energy >= ((long)clock[0] << 3) : energy - (long)clock[0] <= VOLTAGE[tier] * 64L;
        if (!power) {
            stage("machine.start-power", "rejected", "Insufficient eight-tick buffer, or insufficient room for one generated packet");
            return finish("rejected", selected, selection, original, programId, clock, null);
        }
        stage("machine.start-power", "accepted", "Start energy gate satisfied; no energy is drawn at start");
        List<Item> itemOverlay = new ArrayList<>(outputItems.stream().map(item -> item == null ? null : item.copy()).toList());
        if (!voidItems) for (Item item : selected.itemOutputs()) {
            int inserted = fillItem(itemOverlay, 0, item);
            if (inserted != item.count) {
                outputsFull = true; stage("machine.output-items", "rejected", "Deterministic item outputs do not fit");
                return finish("rejected", selected, selection, original, programId, clock, null);
            }
        }
        stage("machine.output-items", "accepted", voidItems ? "Item voiding bypasses fit gate" : "Item outputs fit an overlay");
        List<Fluid> fluidOverlay = new ArrayList<>(outputFluids.stream().map(fluid -> fluid == null ? null : fluid.copy()).toList());
        if (!voidFluids) for (Fluid fluid : selected.fluidOutputs()) {
            if (fillDistinct(fluidOverlay, fluid, fluid.amount, tankCapacity) != fluid.amount) {
                outputsFull = true; stage("machine.output-fluids", "rejected", "Fluid outputs do not fit distinct-fill output tanks");
                return finish("rejected", selected, selection, original, programId, clock, null);
            }
        }
        stage("machine.output-fluids", "accepted", voidFluids ? "Fluid voiding bypasses fit gate; recovery is not certified" : "Fluid outputs fit an overlay");
        outputsFull = false;
        Matching.Allocation allocation = Matching.match(selected, visibleItems, fluids, registry);
        if (!allocation.matched()) {
            stage("machine.consume", "rejected", "Input recheck failed");
            return finish("rejected", selected, selection, original, programId, clock, null);
        }
        List<Map<String, Object>> draws = new ArrayList<>();
        if (allocation.fluids() != null) for (int i = 0; i < allocation.fluids().length; i++) {
            int count = fluids.get(i) == null ? 0 : fluids.get(i).amount;
            if (count != allocation.fluids()[i]) draws.add(Map.of("domain", "fluid", "slot", i, "amount", count - allocation.fluids()[i]));
        }
        if (allocation.items() != null) for (int i = 0; i < allocation.items().length; i++) {
            int count = visibleItems.get(i) == null ? 0 : visibleItems.get(i).count;
            if (count != allocation.items()[i]) draws.add(Map.of("domain", "item", "slot", i, "amount", count - allocation.items()[i]));
        }
        Matching.consume(allocation, visibleItems, fluids);
        if (allocation.items() != null && allocation.items().length > 6 && circuit >= 0 && allocation.items()[6] < 1) {
            // GhostCircuitItemStackHandler.extractItem clears its control when
            // some consumable ingredient (not circuitMeta) draws the virtual slot.
            circuit = -1;
        }
        effects.add(Map.of("rule", "machine.consume", "draws", draws));
        stage("machine.consume", "accepted", "Native handler extraction/drain requests applied");
        effects.add(Map.of("rule", "machine.notify-input", "handler", "importItems"));
        stage("machine.start", "accepted", "Inputs consumed; progress=1 and outputs reserved, not delivered");
        return finish("accepted", selected, selection, original, programId, clock, draws);
    }
    private void stage(String rule, String status, String message) { stages.add(Map.of("rule", rule, "status", status, "message", message)); }
    private Map<String, Object> finish(String status, Recipe selected, String selection, Object original, String programId, int[] clock, Object draws) {
        Set<String> evaluated = new HashSet<>(); stages.forEach(stage -> evaluated.add((String)stage.get("rule")));
        for (String rule : List.of("lookup.selection", "machine.cleanroom", "machine.prepare", "machine.start-power", "machine.output-items", "machine.output-fluids", "machine.consume", "machine.start"))
            if (!evaluated.contains(rule) && !(rule.equals("machine.consume") && status.equals("accepted"))) stage(rule, "not-evaluated", "Earlier stage did not reach this operation");
        Map<String, Object> result = new LinkedHashMap<>(Map.of("status", status, "selection", selection, "stages", stages, "effects", effects,
                "before", original, "after", state(programId, status.equals("accepted") ? selected : null, clock)));
        result.put("selectedRecipe", selected == null ? null : selected.id()); result.put("allocations", draws);
        return result;
    }
    private Map<String, Object> state(String programId, Recipe running, int[] clock) {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("machine", machine); out.put("inputItems", items.stream().map(item -> item == null || item.empty() ? null : item.json()).toList());
        out.put("inputFluids", fluids.stream().map(fluid -> fluid == null || fluid.amount == 0 ? null : fluid.json()).toList());
        out.put("outputItems", outputItems.stream().map(item -> item == null ? null : item.json()).toList());
        out.put("outputFluids", outputFluids.stream().map(fluid -> fluid == null ? null : fluid.json()).toList());
        out.put("ghostCircuit", circuit); out.put("energy", energy); out.put("outputsFull", outputsFull);
        out.put("cache", previous == null ? null : Map.of("programId", programId, "recipeId", previous));
        out.put("progress", running == null ? 0 : 1); out.put("active", running != null);
        out.put("overclockResults", clock == null ? null : List.of(clock[0], clock[1]));
        out.put("reservedOutputs", running == null ? null : Map.of("items", running.itemOutputs().stream().map(Item::json).toList(), "fluids", running.fluidOutputs().stream().map(Fluid::json).toList()));
        out.put("tankCapacity", tankCapacity); out.put("energyCapacity", VOLTAGE[tier] * 64L);
        return out;
    }

    static int[] overclock(int eut, int duration, int maximumTier) {
        int recipeTier = 0;
        while (recipeTier < VOLTAGE.length - 1 && VOLTAGE[recipeTier] < eut) recipeTier++;
        int count = maximumTier <= 1 ? 0 : maximumTier - recipeTier - (recipeTier == 0 ? 1 : 0);
        if (count <= 0) return new int[]{eut, duration};
        return standardOverclock(Math.abs(eut), VOLTAGE[maximumTier], duration, count, 2.0, 4.0);
    }
    /** Literal arithmetic/control-flow extraction of OverclockingLogic.standardOverclockingLogic. */
    public static int[] standardOverclock(int recipeEUt, long maxVoltage, int recipeDuration,
                                         int numberOfOCs, double durationDivisor, double voltageMultiplier) {
        double resultDuration = recipeDuration;
        double resultVoltage = recipeEUt;
        for (; numberOfOCs > 0; numberOfOCs--) {
            if (resultDuration == 1) break;
            double potentialVoltage = resultVoltage * voltageMultiplier;
            if (potentialVoltage > maxVoltage) break;
            double potentialDuration = resultDuration / durationDivisor;
            if (potentialDuration < 1) potentialDuration = 1;
            resultDuration = potentialDuration;
            resultVoltage = potentialVoltage;
        }
        return new int[]{(int)resultVoltage, (int)resultDuration};
    }
    static int fillItem(List<Item> slots, int slot, Item offered) {
        Item current = slots.get(slot);
        if (current != null && !current.empty() && !current.same(offered)) return 0;
        int present = current == null || current.empty() ? 0 : current.count;
        int amount = Math.min(offered.count, Math.min(64, offered.type.maxStack()) - present);
        if (amount > 0) slots.set(slot, new Item(offered.type, present + amount, offered.tag));
        return amount;
    }
    static int fillOne(List<Fluid> tanks, int index, Fluid offered, int amount, int capacity) {
        Fluid current = tanks.get(index);
        if (current != null && current.amount > 0 && !current.same(offered)) return 0;
        int present = current == null || current.amount <= 0 ? 0 : current.amount;
        int inserted = Math.min(amount, capacity - present);
        if (inserted > 0) tanks.set(index, new Fluid(offered.id, present + inserted, offered.tag));
        return Math.max(0, inserted);
    }
    /** Plain, unfiltered FluidTankList(false) and OverlayedFluidHandler share these admitted fill rules. */
    static int fillDistinct(List<Fluid> tanks, Fluid offered, int amount, int capacity) {
        if (amount <= 0) return 0;
        int total = 0;
        boolean distinct = false;
        for (int i = 0; i < tanks.size(); i++) {
            if (offered.same(tanks.get(i))) {
                int inserted = fillOne(tanks, i, offered, amount, capacity);
                total += inserted; amount -= inserted;
                if (amount <= 0) return total;
                distinct = true;
            }
        }
        if (!distinct) for (int i = 0; i < tanks.size(); i++) {
            if (tanks.get(i) == null || tanks.get(i).amount <= 0) {
                total += fillOne(tanks, i, offered, amount, capacity);
                break;
            }
        }
        return total;
    }
}
