// Observation-only, installed profile resource. Never applied to the source checkout.
import com.google.gson.GsonBuilder
import gregtech.api.recipes.RecipeMap
import gregtech.integration.groovy.GroovyScriptModule
import net.minecraft.item.ItemStack
import net.minecraft.util.ResourceLocation
import net.minecraftforge.fluids.FluidRegistry
import net.minecraftforge.fluids.FluidStack
import net.minecraftforge.fml.common.Loader
import net.minecraftforge.fml.common.LoaderState
import net.minecraftforge.fml.common.gameevent.TickEvent
import net.minecraftforge.oredict.OreDictionary
import net.minecraftforge.fml.common.registry.ForgeRegistries
import prePostInit.Recipemaps

def gson = new GsonBuilder().serializeNulls().create()
def spec = gson.fromJson(__SPEC_JSON__, Map)
def identity = { row -> [kind: row.kind, name: row.name, metadata: row.metadata as int] }
// Gson map key order is explicitly canonical, matching the host's identity keys.
def identityKey = { row -> gson.toJson(new TreeMap(identity(row))) }
def stackValue = { stack ->
    if (stack == null || stack.isEmpty() || stack.tagCompound != null || stack.metadata == OreDictionary.WILDCARD_VALUE)
        throw new IllegalStateException('unsupported item representative or NBT')
    [item: stack.item.registryName.toString(), metadata: stack.metadata]
}
def sortRows = { values -> values.sort { a, b -> gson.toJson(new TreeMap(a)) <=> gson.toJson(new TreeMap(b)) } }
def fluidValue = { stack ->
    if (stack == null || stack.tag != null) throw new IllegalStateException('unsupported fluid NBT')
    [name: stack.fluid.name, amount: stack.amount]
}
def capture = {
    def result = [format: 'workbench-recipe-capture-v4', nonce: spec.nonce, expectation_id: spec.expectation_id, state: 'incomplete',
                  phase: 'render-tick-after-load-complete', resolution: [:], records: [], queries: [], map: null, error: null, lifecycle: null, decisions: null]
    try {
        def map = RecipeMap.getByName('mixer')
        if (map == null || !Recipemaps.MIXER.is(map)) throw new IllegalStateException('MIXER registry/alias mismatch')
        result.map = map.getUnlocalizedName()
        def itemStacks = [:]
        spec.identities.each { row ->
            def key = identityKey(row)
            if (result.resolution.containsKey(key)) return
            if (row.kind == 'fluid') {
                if (FluidRegistry.getFluid(row.name) == null) throw new IllegalStateException('unresolved fluid ' + row.name)
                result.resolution[key] = row.name
            } else {
                def values
                if (row.kind == 'ore') values = OreDictionary.getOres(row.name, false)
                else if (row.kind == 'metaitem') values = [GroovyScriptModule.getMetaItem(row.name)]
                else {
                    def item = ForgeRegistries.ITEMS.getValue(new ResourceLocation(row.name))
                    values = item == null ? [] : [new ItemStack(item, 1, row.metadata as int)]
                }
                def unique = new TreeMap()
                values.each { stack -> unique[gson.toJson(new TreeMap(stackValue(stack)))] = stack.copy() }
                if (unique.isEmpty() || unique.size() > (spec.max_queries as int)) throw new IllegalStateException('unresolved or over-bound item ' + row.name)
                itemStacks[key] = new ArrayList(unique.values())
                result.resolution[key] = sortRows(unique.values().collect { stackValue(it) })
            }
        }
        def combinations = [[]]
        spec.selector.item_inputs.each { row ->
            def next = []
            combinations.each { prefix -> itemStacks[identityKey(row)].each { stack ->
                if (next.size() >= (spec.max_queries as int)) throw new IllegalStateException('input expansion bound exceeded')
                def copy = stack.copy(); copy.setCount(row.amount as int)
                next.add(prefix + [copy])
            } }
            combinations = next
        }
        def fluids = spec.selector.fluid_inputs.collect { new FluidStack(FluidRegistry.getFluid(it.name), it.amount as int) }
        if (map.getMaxInputs() < spec.selector.item_inputs.size() || map.getMaxFluidInputs() < fluids.size()
            || map.getMaxInputs() > 64 || map.getMaxFluidInputs() > 64) throw new IllegalStateException('recipe-map input slots outside bound')
        def active = Collections.newSetFromMap(new IdentityHashMap())
        map.getRecipeList().each { active.add(it) }
        def category = Collections.newSetFromMap(new IdentityHashMap())
        map.getRecipesByCategory().values().each { entries -> entries.each { category.add(it) } }
        def union = Collections.newSetFromMap(new IdentityHashMap())
        union.addAll(active); union.addAll(category)
        if (union.size() > (spec.max_recipes as int)) throw new IllegalStateException('recipe-map inventory bound exceeded')
        def encodeRecipe = { recipe ->
            if (recipe.isHidden() || recipe.getIsCTRecipe() || !recipe.getPropertyValues().isEmpty()
                || !recipe.getChancedOutputs().getChancedEntries().isEmpty()
                || !recipe.getChancedFluidOutputs().getChancedEntries().isEmpty()) return null
            try {
                def items = recipe.getInputs().collect { input ->
                    if (input.isNonConsumable() || input.hasNBTMatchingCondition()) throw new IllegalStateException('special input')
                    [ore: input.isOreDict() ? OreDictionary.getOreName(input.getOreDict()) : null,
                     stacks: input.isOreDict() ? [] : sortRows(input.getInputStacks().collect { stackValue(it) }), amount: input.getAmount()]
                }
                def fluidInputs = recipe.getFluidInputs().collect { input ->
                    if (input.isNonConsumable() || input.hasNBTMatchingCondition()) throw new IllegalStateException('special fluid input')
                    [name: input.getInputFluidStack().fluid.name, amount: input.getAmount()]
                }
                [map: map.getUnlocalizedName(), duration: recipe.getDuration(), eut: recipe.getEUt(),
                 item_inputs: sortRows(items), fluid_inputs: sortRows(fluidInputs),
                 item_outputs: sortRows(recipe.getOutputs().collect { stackValue(it) + [amount: it.count] }),
                 fluid_outputs: sortRows(recipe.getFluidOutputs().collect { fluidValue(it) })]
            } catch (IllegalStateException unsupported) { null }
        }
        // These references identify objects in this capture only, never across runs.
        def references = new IdentityHashMap()
        def reference = { recipe ->
            if (!references.containsKey(recipe)) {
                def id = 'r' + references.size()
                references.put(recipe, id)
                def encoded = encodeRecipe(recipe)
                result.records.add([id: id, recipe: encoded, lookup_active: active.contains(recipe), category_present: category.contains(recipe),
                                    unsupported_reason: encoded == null ? 'Outside ordinary fixed-output, consumable, non-NBT recipe semantics' : null])
            }
            references.get(recipe)
        }
        def tracedRecipes = []
        try { tracedRecipes = __TRACE_RECIPES__ } catch (Throwable unavailable) { /* Snapshot-only evidence remains usable. */ }
        def selectedTraceRecipes = Collections.newSetFromMap(new IdentityHashMap())
        def traceSelectionIncomplete = false
        def acceptanceCount = 0
        combinations.each { combination ->
            def queryItems = combination.collect { it.copy() }
            def queryFluids = fluids.collect { it.copy() }
            while (queryItems.size() < map.getMaxInputs()) queryItems.add(ItemStack.EMPTY)
            while (queryFluids.size() < map.getMaxFluidInputs()) queryFluids.add(null)
            def accepting = []
            tracedRecipes.each { recipe ->
                try { if (recipe.matches(false, queryItems, queryFluids)) selectedTraceRecipes.add(recipe) }
                catch (Throwable unsupportedTraceRecipe) { traceSelectionIncomplete = true }
            }
            union.each { recipe -> if (recipe.matches(false, queryItems, queryFluids)) accepting.add(reference(recipe)) }
            acceptanceCount += accepting.size()
            if (acceptanceCount > (spec.max_acceptances as int)) throw new IllegalStateException('acceptance link bound exceeded')
            // Explicit unlimited-voltage lookup, not exact-EU/t matching or a machine simulation.
            def queryId = 'q' + result.queries.size()
            try { __TRACE_QUERY_BEGIN__ } catch (Throwable unavailable) { /* No retry or substitute lookup. */ }
            def winner
            try { winner = map.findRecipe(Integer.MAX_VALUE as long, queryItems, queryFluids, false) }
            finally { try { __TRACE_QUERY_END__ } catch (Throwable unavailable) { /* Snapshot evidence remains independent. */ } }
            if (winner != null && !union.contains(winner)) throw new IllegalStateException('lookup winner outside captured inventory')
            def encoded = winner == null ? null : encodeRecipe(winner)
            def winnerId = winner == null ? null : reference(winner)
            if (winnerId != null && !accepting.contains(winnerId)) throw new IllegalStateException('lookup winner does not accept captured inputs')
            result.queries.add([id: queryId, accepting: accepting, winner_id: winnerId,
                                items: combination.collect { stackValue(it) + [amount: it.count] },
                                fluids: fluids.collect { fluidValue(it) }, voltage_limit: Integer.MAX_VALUE,
                                winner: encoded, unsupported: winner != null && encoded == null])
        }
        try {
                result.lifecycle = __TRACE_SNAPSHOT__
                if (result.lifecycle != null) {
                if (traceSelectionIncomplete) {
                    result.lifecycle.state = 'incomplete'; result.lifecycle.problems = result.lifecycle.problems + ['historical-recipe-selection-incomplete']
                }
                if (gson.toJson(result.lifecycle).length() > 512 * 1024) {
                    result.lifecycle.state = 'incomplete'; result.lifecycle.problems = ['trace-byte-bound']
                    result.lifecycle.events = []; result.lifecycle.links = [:]
                }
                }
        } catch (Throwable unavailable) {
                result.lifecycle = [state: 'incomplete', problems: ['trace-snapshot-failed']]
        }
        try {
            result.decisions = __TRACE_DECISIONS__
            if (result.decisions != null && gson.toJson(result.decisions).getBytes('UTF-8').length > 512 * 1024) {
                result.decisions = [state: 'incomplete', problems: ['decision-byte-bound']]
            }
        } catch (Throwable unavailable) {
            result.decisions = [state: 'incomplete', problems: ['decision-snapshot-failed']]
        }
        result.state = 'complete'
    } catch (Throwable failure) {
        result.state = 'incomplete'
        result.error = (failure.class.name + ': ' + String.valueOf(failure.message)).take(2000)
    }
    def encoded = gson.toJson(result)
    if (encoded.length() > 8 * 1024 * 1024 && (result.lifecycle != null || result.decisions != null)) {
        result.lifecycle = [state: 'incomplete', problems: ['combined-capture-byte-bound']]
        result.decisions = [state: 'incomplete', problems: ['combined-capture-byte-bound']]
        encoded = gson.toJson(result)
    }
    if (encoded.length() > 8 * 1024 * 1024) {
        result.state = 'incomplete'; result.error = 'capture byte bound exceeded'
        result.records = []; result.queries = []; result.resolution = [:]
        encoded = gson.toJson(result)
    }
    // The Object overload supplies a dummy 0 formatting argument. Passing JSON
    // as the format string would replace its first empty object with that 0.
    log.infoMC('{}', ['[WORKBENCH-RECIPE-CAPTURE]' + encoded] as Object[])
}
// GroovyScript's packaged event wrapper owns class-loader-safe registration.
def captured = false
eventManager.listen { TickEvent.RenderTickEvent event ->
    // Groovy's event.phase selects Event.getPhase() (priority), not TickEvent.phase.
    if (!captured && event.@phase == TickEvent.Phase.END && Loader.instance().hasReachedState(LoaderState.AVAILABLE)) {
        captured = true
        capture.call()
    }
}
