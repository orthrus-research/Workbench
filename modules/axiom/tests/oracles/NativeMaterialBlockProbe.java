package research.orthrus.axiom.nativeconstruction;

import java.nio.file.*;
import java.util.*;
import net.minecraft.block.Block;
import net.minecraft.item.*;
import net.minecraft.util.*;
import net.minecraftforge.common.MinecraftForge;
import net.minecraftforge.event.RegistryEvent;
import net.minecraftforge.fml.common.*;
import net.minecraftforge.fml.common.eventhandler.*;
import net.minecraftforge.fml.common.registry.ForgeRegistries;
import net.minecraftforge.oredict.OreDictionary;

/** Native witnesses, not a parallel state/property/item implementation. */
public final class NativeMaterialBlockProbe {
    static void check(boolean value, String message) { if (!value) throw new AssertionError(message); }
    static void reject(Runnable operation, String message) {
        try { operation.run(); throw new AssertionError(message); }
        catch (IllegalStateException | IllegalArgumentException | IndexOutOfBoundsException | Failure expected) { }
    }
    static List<Object> stack(ItemStack s) {
        return List.of(s.func_77973_b().getRegistryName().toString(), s.func_77952_i(), s.func_190916_E());
    }
    public static final class Observer {
        final List<String> registries = new ArrayList<>();
        final List<Object> ores = new ArrayList<>();
        @SubscribeEvent public void item(RegistryEvent.Register<Item> e) { registries.add("item"); }
        @SubscribeEvent public void block(RegistryEvent.Register<Block> e) { registries.add("block"); }
        @SubscribeEvent(priority = EventPriority.LOWEST) public void ore(OreDictionary.OreRegisterEvent e) {
            ores.add(List.of(e.getName(), stack(e.getOre())));
        }
    }
    public static final class Failures {
        final String scenario;
        Failures(String scenario) { this.scenario = scenario; }
        @SubscribeEvent(priority = EventPriority.LOWEST) public void block(RegistryEvent.Register<Block> e) {
            if (scenario.equals("block-failure")) throw new IllegalStateException("block registry witness");
        }
        @SubscribeEvent(priority = EventPriority.LOWEST) public void item(RegistryEvent.Register<Item> e) {
            if (scenario.equals("registration-failure")) throw new IllegalStateException("item registry witness");
        }
        @SubscribeEvent(priority = EventPriority.HIGHEST) public void ore(OreDictionary.OreRegisterEvent e) {
            if (scenario.equals("failure") && e.getName().equals("blockAluminium")) throw new IllegalStateException("block ore witness");
        }
    }
    /** Deliberate fixture registries, not an assertion that addon composition ran. */
    public static final class SparseMaterials {
        @SubscribeEvent public void registries(MaterialRegistryEvent event) {
            FluidEnvironment.current().runtime().materials().createRegistry("fixture_a");
            FluidEnvironment.current().runtime().materials().createRegistry("fixture_b");
        }
        @SubscribeEvent public void materials(MaterialEvent event) {
            for (String mod : List.of("fixture_a", "fixture_b")) for (int id : new int[]{15, 16, 31, 32})
                new FluidMaterial.Builder(id, new ResourceLocation(mod, "boundary_" + id)).ingot().flags(MaterialFlags.GENERATE_FRAME).build();
        }
    }
    static FluidMaterial expectedOreMaterial(FluidMaterial material) {
        if (!material.getModid().startsWith("fixture_")) return material;
        // These fixture materials intentionally share ore names across namespaces.
        // The retained unifier resolves the first registry with that bare name.
        // Fastutil's values iterator and spliterator need not have the same order.
        // Use the original enhanced-for traversal, not stream().findFirst().
        for (var registry : FluidEnvironment.current().runtime().materials().getRegistries()) {
            var candidate = registry.getObject(material.getName());
            if (candidate != null) return (FluidMaterial)candidate;
        }
        throw new AssertionError("fixture material is missing from registry traversal");
    }
    static Map<String,FluidBuilder> deferredBlocks(FluidEnvironment env) throws Exception {
        var result = new TreeMap<String,FluidBuilder>();
        var flag = FluidBuilder.class.getDeclaredField("hasFluidBlock"); flag.setAccessible(true);
        for (var state : env.runtime().materials().getRegisteredMaterials()) {
            var material = FluidMaterial.require(state);
            if (material.hasProperty(FluidDomain.FLUID)) for (var key : List.of(env.storageKeys().LIQUID, env.storageKeys().GAS, env.storageKeys().PLASMA)) {
                var builder = material.getProperty(FluidDomain.FLUID).getQueuedBuilder(key);
                if (builder != null && (boolean)flag.get(builder)) result.put(material.getRegistryName(), builder);
            }
        }
        return result;
    }
    static Map<String,Object> vectors(MaterialContentFamily family) {
        int count = 0, itemCount = 0, holes = 0;
        var absent = SourceMaterialCatalog.NULL;
        for (var item : family.items()) for (var value : item.getAllItems()) {
            var s = value.getStackForm(3); var entry = OreDictUnifier.getUnificationEntry(s);
            check(s.func_77973_b() == item && s.func_77952_i() == item.getMaterial(s).getId(), "prefix-item identity remains intact");
            check(entry != null && entry.orePrefix == item.getOrePrefix() && entry.material == expectedOreMaterial(item.getMaterial(s)),
                    "prefix-item reverse membership: " + item.getOrePrefix() + ":" + item.getMaterial(s).getRegistryName() + " -> " +
                    (entry == null ? "null" : entry.orePrefix + ":" + entry.material.getRegistryName()) +
                    " expected " + expectedOreMaterial(item.getMaterial(s)).getRegistryName() + " ores " + OreDictUnifier.getOreDictionaryNames(s));
            check(s.func_77976_d() == item.getOrePrefix().maxStackSize, "native prefix-item stack limit");
            check(stack(s.func_77946_l()).equals(stack(s)) && stack(new ItemStack(s.serializeNBT())).equals(stack(s)), "prefix-item NBT identity");
            itemCount++;
        }
        for (var block : family.blocks()) {
            var property = block.getVariantProperty(); var allowed = property.func_177700_c();
            check(allowed.size() == 16, "original sixteen-slot property is not compacted");
            check(block.func_176223_P().func_177230_c() == block, "native default state owner");
            var nativeItem = Item.func_150898_a(block);
            check(nativeItem instanceof MaterialItemBlock && ((ItemBlock)nativeItem).func_179223_d() == block, "native registry callback block-item mapping");
            check(nativeItem.getRegistryName().equals(block.getRegistryName()) && nativeItem.func_77614_k(), "ItemBlock namespace and subtype identity");
            check(block.getGtMaterial(16) == allowed.get(0) && block.getGtMaterial(Integer.MAX_VALUE) == allowed.get(0), "source high metadata falls back to first slot");
            reject(() -> block.getGtMaterial(-1), "negative metadata is not silently clamped");
            check(property.func_185929_b("unknown__material").get() == absent, "source property unknown-name sentinel");
            if (!allowed.contains(absent)) reject(() -> block.func_176223_P().func_177226_a(property, property.func_185929_b("unknown__material").get()), "sentinel parse is not proof it is an allowed state");
            reject(() -> block.getItem(SourceMaterialCatalog.Water), "material outside this property cannot become a fabricated block item");
            check(nativeItem.func_77647_b(-1) == -1 && nativeItem.func_77647_b(16) == 16, "ItemBlock metadata is passed through, not normalized");
            reject(() -> nativeItem.func_77653_i(new ItemStack(nativeItem)), "unqualified localization does not fall through");
            reject(() -> block.getFlammability(null, null, null), "unqualified world lookup does not fall through");
            if (block instanceof BlockFrame frame) {
                reject(() -> frame.func_180639_a(null, null, null, null, null, null, 0, 0, 0), "unqualified pipe/world interaction");
                check(BlockFrame.getFrameBlockFromItem(new ItemStack(nativeItem)) == frame, "native frame ItemBlock type query");
            }
            var creative = NonNullList.<ItemStack>func_191196_a();
            block.func_149666_a(MaterialItemDeclarations.TAB_GREGTECH_MATERIALS, creative);
            check(creative.stream().noneMatch(s -> block.getGtMaterial(s) == absent), "creative enumeration omits NULL without changing slots");
            for (int metadata = 0; metadata < 16; metadata++) {
                var material = allowed.get(metadata); var state = block.func_176203_a(metadata);
                check(state.func_177229_b(property) == material, "native state property resolves original slot");
                int stateMetadata = allowed.indexOf(material);
                check(block.func_176201_c(state) == stateMetadata && block.func_180651_a(state) == stateMetadata, "source duplicate-slot canonicalization");
                check(block.func_176203_a(stateMetadata) == state, "native cached state round trip");
                var s = block.getItem(material);
                check(s.func_77973_b() == nativeItem && s.func_77952_i() == stateMetadata, "source state-to-ItemStack conversion");
                check(nativeItem.func_77647_b(metadata) == metadata, "ItemBlock passes raw metadata unchanged");
                check(stack(new ItemStack(s.serializeNBT())).equals(stack(s)) && stack(s.func_77946_l()).equals(stack(s)), "block-item native copy and NBT");
                check(property.func_185929_b(property.func_177702_a(material)).get() == material, "namespaced property material parse");
                if (material == absent) { holes++; continue; }
                check(material.getId() % 16 == metadata, "source material metadata remainder");
                check(block.getRegistryName().func_110623_a().endsWith("_" + material.getId() / 16), "source material group quotient");
                check(block.getRegistryName().func_110624_b().equals(material.getModid()), "material registry namespace retained");
                OrePrefix prefix = block instanceof BlockCompressed ? OrePrefix.block : OrePrefix.frameGt;
                var entry = OreDictUnifier.getUnificationEntry(s);
                var selected = expectedOreMaterial(material);
                check(entry != null && entry.orePrefix == prefix && entry.material == selected, "generated block reverse ore identity and namespace precedence");
                check(!family.resolve(prefix, selected, 2).func_190926_b(), "generated block qualified lookup");
                if (selected != material) check(family.resolve(prefix, material, 2).func_190926_b(), "same-name later registry does not acquire fabricated unifier identity");
                count++;
            }
        }
        check(family.resolve(OrePrefix.block, SourceMaterialCatalog.Iron, 1).func_77973_b().getRegistryName().toString().equals("minecraft:iron_block"), "ignored generated form keeps existing vanilla selection");
        check(family.resolve(OrePrefix.block, SourceMaterialCatalog.Water, 1).func_190926_b(), "qualified ineligible block is empty");
        reject(() -> family.resolve(OrePrefix.ore, SourceMaterialCatalog.Aluminium, 1), "unqualified ore family is incomplete not absent");
        return Map.of("generatedStackVectors", itemCount, "generatedBlockVariants", count, "nullSlots", holes,
                "nativeStateMetadataAndNbt", true, "unqualifiedBehaviorRejected", true);
    }
    public static Object run(String request) throws Exception {
        var properties = new Properties(); try (var input = Files.newInputStream(Path.of(request))) { properties.load(input); }
        String scenario = properties.getProperty("scenario", "baseline");
        var gameDirectory = net.minecraftforge.fml.relauncher.FMLInjectionData.class.getDeclaredField("minecraftHome");
        gameDirectory.setAccessible(true); gameDirectory.set(null, Path.of(request).getParent().toFile());
        var cfg = CatalogConfiguration.load(Path.of(properties.getProperty("config")), SourceCatalogConfiguration.class);
        var loader = Loader.instance(); var controller = Loader.class.getDeclaredField("modController"); controller.setAccessible(true);
        Object previous = controller.get(loader); check(previous == null, "no installed loader composition"); controller.set(loader, new LoadController(loader));
        var metadata = new ModMetadata(); metadata.modId = "gregtech"; metadata.name = "gregtech"; loader.setActiveModContainer(new DummyModContainer(metadata));
        var observer = new Observer(); var failures = new Failures(scenario); var sparse = new SparseMaterials();
        MinecraftForge.EVENT_BUS.register(observer); MinecraftForge.EVENT_BUS.register(failures);
        if (scenario.equals("sparse")) MinecraftForge.EVENT_BUS.register(sparse);
        try (var env = new FluidEnvironment()) {
            check(ForgeRegistries.RECIPES.getKeys().isEmpty(), "no recipe clearing or launch");
            OreDictionary.getOreNames(); observer.ores.clear();
            env.bindCatalog(new CatalogInputs(SourceMaterialCatalog.class, cfg));
            new MaterialLifecycle(env.runtime(), new MaterialEvents(), new MaterialLifecycle.Dependencies() {
                public void initializeMarkers() { MarkerMaterials.register(); }
                public void registerMaterials() { SourceMaterialCatalog.register(); }
                public FluidMaterial aluminium() { return SourceMaterialCatalog.Aluminium; }
            }).execute();
            var deferred = deferredBlocks(env);
            check(deferred.keySet().equals(Set.of("gregtech:natural_gas", "gregtech:oil", "gregtech:oil_heavy", "gregtech:oil_light", "gregtech:oil_medium")), "five source-declared fluid block requests remain pending");
            var family = new MaterialContentFamily(MaterialContentFamily.Universe.PREFIX_ITEMS_AND_MATERIAL_BLOCKS);
            reject(family::registerBlocks, "phase ordering"); family.construct();
            reject(family::registerItems, "items cannot precede block registration");
            reject(family::registerOres, "ores cannot precede item registration");
            reject(family::construct, "construction is not replayable");
            check(family.blocks().stream().allMatch(b -> Item.func_150898_a(b) == net.minecraft.init.Items.field_190931_a), "construction is not an ItemBlock binding");
            try {
                family.registerBlocks();
                check(family.blocks().stream().allMatch(b -> Item.func_150898_a(b) == net.minecraft.init.Items.field_190931_a), "block registration is not item registration");
                family.registerItems();
                check(observer.registries.equals(List.of("block", "item")), "native typed registry events have source order");
                check(observer.ores.isEmpty(), "registry events do not imply ore membership");
                reject(() -> family.resolve(OrePrefix.block, SourceMaterialCatalog.Aluminium, 1), "registered but unqualified query");
                family.registerOres();
            } catch (RuntimeException failure) {
                if (!Set.of("block-failure", "registration-failure", "failure", "collision").contains(scenario)) throw failure;
                check(family.phase() == MaterialContentFamily.Phase.FAILED, "native failure makes composition terminal");
                check(family.blocks().stream().anyMatch(b -> ForgeRegistries.BLOCKS.getValue(b.getRegistryName()) == b), "native block partial state is retained");
                if (!scenario.equals("collision")) check(family.blocks().stream().allMatch(b -> ForgeRegistries.BLOCKS.getValue(b.getRegistryName()) == b), "later event failure keeps completed block registrations");
                reject(family::registerBlocks, "failed block retry"); reject(family::registerItems, "failed item retry"); reject(family::registerOres, "failed ore retry");
                reject(() -> family.resolve(OrePrefix.block, SourceMaterialCatalog.Aluminium, 1), "failed query");
                check(deferred.equals(deferredBlocks(env)), "failed block registration does not consume pending fluid builders");
                return Map.of("scenario", scenario, "phase", family.phase().name(), "partialStateRetained", true,
                        "retryRejected", true, "inventory", family.inventory(), "events", observer.ores,
                        "failureType", failure.getClass().getSimpleName(), "failureMessage", Objects.toString(failure.getMessage(), ""));
            }
            check(!Set.of("block-failure", "registration-failure", "failure", "collision").contains(scenario), "failure witness actually executed");
            var checks = vectors(family); var inventory = family.inventory(); var checkpoints = family.checkpoints(); var events = List.copyOf(observer.ores);
            int eventCount = events.size(); MaterialItemDeclarations.registerOres(); MaterialBlockDeclarations.registerOres();
            check(observer.ores.size() == eventCount, "native duplicate ore deduplication");
            check(ForgeRegistries.RECIPES.getKeys().isEmpty(), "recipe handlers stay pending");
            check(deferred.equals(deferredBlocks(env)), "successful composition preserves original pending fluid builder objects");
            var pending = OrePrefix.class.getDeclaredField("generatedMaterials"); pending.setAccessible(true);
            check(((Set<?>)pending.get(OrePrefix.block)).contains(SourceMaterialCatalog.Aluminium), "pending block recipe handlers preserved");
            MaterialBlockDeclarations.COMPRESSED.clear();
            reject(() -> family.resolve(OrePrefix.block, SourceMaterialCatalog.Aluminium, 1), "post-completion source index mutation invalidates queries");
            check(family.phase() == MaterialContentFamily.Phase.FAILED, "universe drift is terminal");
            return Map.of("scenario", scenario, "inventory", inventory, "checkpoints", checkpoints, "events", events,
                    "checks", checks, "configuration", cfg.evidence(), "materialCount", env.runtime().materials().getRegisteredMaterials().size(),
                    "recipeCount", 0, "pendingMaterialHandlersPreserved", true, "deferredFluidBlocks", new ArrayList<>(deferred.keySet()));
        } finally {
            MinecraftForge.EVENT_BUS.unregister(OreDictUnifier.class); MinecraftForge.EVENT_BUS.unregister(observer);
            MinecraftForge.EVENT_BUS.unregister(failures); MinecraftForge.EVENT_BUS.unregister(sparse);
            loader.setActiveModContainer(null); controller.set(loader, previous);
        }
    }
}
