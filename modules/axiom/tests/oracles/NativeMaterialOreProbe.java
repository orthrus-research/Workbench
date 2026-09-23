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
import static research.orthrus.axiom.nativeconstruction.NativeMaterialBlockProbe.*;

/** Native source/installed witnesses, not a substitute stone/metadata algorithm. */
public final class NativeMaterialOreProbe {
    public static final class AddonMaterials {
        final ModContainer owner;
        final String scenario;
        AddonMaterials(ModContainer owner, String scenario) { this.owner = owner; this.scenario = scenario; }
        @SubscribeEvent public void registry(MaterialRegistryEvent e) { FluidEnvironment.current().runtime().materials().createRegistry("susy"); }
        @SubscribeEvent public void material(MaterialEvent e) {
            var loader = Loader.instance(); var previous = loader.activeModContainer();
            try { loader.setActiveModContainer(owner); SusyStoneMaterials.registerHostMaterials(); }
            finally { loader.setActiveModContainer(previous); }
        }
        @SubscribeEvent public void post(PostMaterialEvent e) {
            if (scenario.equals("disabled")) SusyStoneMaterials.Fluorite.addFlags(MaterialFlags.DISABLE_ORE_BLOCK);
        }
    }
    public static final class OreFailure {
        final String scenario;
        OreFailure(String scenario) { this.scenario = scenario; }
        @SubscribeEvent(priority = EventPriority.HIGHEST) public void ore(OreDictionary.OreRegisterEvent e) {
            if (scenario.equals("failure") && e.getName().equals("oreAluminium")) throw new IllegalStateException("ore membership witness");
        }
        @SubscribeEvent(priority = EventPriority.LOWEST) public void block(RegistryEvent.Register<Block> e) {
            if (scenario.equals("block-failure")) throw new IllegalStateException("ore block registry witness");
        }
        @SubscribeEvent(priority = EventPriority.LOWEST) public void item(RegistryEvent.Register<Item> e) {
            if (scenario.equals("registration-failure")) throw new IllegalStateException("ore item registry witness");
        }
    }
    static ModContainer owner(String id) { var m = new ModMetadata(); m.modId = id; m.name = id; return new DummyModContainer(m); }
    static Map<String,Object> vectors(MaterialContentFamily family, String scenario) {
        int itemCount = 0, variants = 0, ordinaryRedirects = 0, silkDifferences = 0, unparsed = 0;
        for (var item : family.items()) for (var value : item.getAllItems()) {
            var stack = value.getStackForm(2);
            check(stack.func_77973_b() == item && item.getMaterial(stack).getId() == stack.func_77952_i(), "prefix identities still native");
            itemCount++;
        }
        var content = family.oreContent();
        for (var stone : content.stones()) {
            var host = stone.stone.get();
            check(host != null && host.func_177230_c() != null, "source stone supplier resolves real native host");
            if (stone.name.equals("gabbro")) check(host.func_177230_c() instanceof SusyStoneVariantBlock && host.func_177230_c().getRegistryName().toString().equals("susy:susy_stone_smooth"), "Susy native host ownership");
            if (stone.name.equals("black_granite")) check(host.func_177230_c() instanceof StoneVariantBlock, "GT native host identity");
            if (stone.name.equals("quartzite")) check(stone.stoneMaterial == SourceMaterialCatalog.Quartzite, "GT material reused by native Susy host declaration");
        }
        for (var block : content.ores()) {
            var property = block.STONE_TYPE; var allowed = property.func_177700_c();
            check(!allowed.isEmpty() && allowed.size() <= 16, "bounded original stone property");
            check(!property.func_185929_b("missing_stone").isPresent(), "stone parse absence is not a sentinel");
            check(block.func_176203_a(allowed.size()) == block.func_176203_a(0) && block.func_176203_a(Integer.MAX_VALUE) == block.func_176203_a(0), "native high metadata fallback");
            reject(() -> block.func_176203_a(-1), "negative ore metadata is not normalized");
            var item = Item.func_150898_a(block);
            check(item instanceof OreItemBlock && ((ItemBlock)item).func_179223_d() == block && item.func_77614_k(), "native ore ItemBlock callback/subtypes");
            check(item.func_77647_b(-1) == -1 && item.func_77647_b(100) == 100, "raw item metadata passes through");
            reject(() -> item.func_77653_i(new ItemStack(item)), "unqualified localization is rejected");
            reject(() -> block.isFireSource(null, null, null), "unqualified world fire query rejects");
            reject(() -> block.canRenderInLayer(block.func_176223_P(), null), "unqualified renderer rejects");
            var creative = NonNullList.<ItemStack>func_191196_a(); block.func_149666_a(OreHostBlocks.TAB_GREGTECH_ORES, creative);
            check(creative.size() == allowed.stream().filter(s -> s.shouldBeDroppedAsItem).count(), "creative visibility differs from generated state existence");
            for (int meta = 0; meta < allowed.size(); meta++) {
                var stone = allowed.get(meta); var state = block.func_176203_a(meta);
                check(state.func_177229_b(property) == stone && block.func_176201_c(state) == meta && block.getOreBlock(stone) == state, "native ore state/metadata/cache identity");
                check(property.func_185929_b(property.func_177702_a(stone)).get() == stone, "native stone property names");
                var form = family.generatedOre(block.material, stone);
                check(form.func_77973_b() == item && form.func_77952_i() == meta, "generated form is the exact ore variant");
                check(stack(new ItemStack(form.serializeNBT())).equals(stack(form)) && stack(form.func_77946_l()).equals(stack(form)), "native ore copy/NBT");
                var entry = OreDictUnifier.getUnificationEntry(form);
                var names = OreDictUnifier.getOreDictionaryNames(form);
                check(names.size() == 1, "single source ore membership");
                OrePrefix exactPrefix = OrePrefix.getPrefix(names.iterator().next());
                if (exactPrefix != null && !exactPrefix.isSelfReferencing) {
                    check(entry == null && family.resolve(stone.processingPrefix, block.material, 1).func_190926_b(), "whole-name prefix shadowing retains membership but does not create unification identity");
                    unparsed++;
                } else {
                    check(entry != null && entry.orePrefix == stone.processingPrefix && entry.material == block.material,
                        "reverse ore membership: " + block.material.getRegistryName() + "/" + stone.name + " expected " + stone.processingPrefix +
                        " actual " + (entry == null ? "null" : entry.orePrefix + "/" + entry.material.getRegistryName()) + " names " + OreDictUnifier.getOreDictionaryNames(form));
                    check(!family.resolve(stone.processingPrefix, block.material, 1).func_190926_b(), "unambiguous registered ore resolves through original unifier");
                }
                check(Objects.equals(block.getHarvestTool(state), stone.stone.get().func_177230_c().getHarvestTool(stone.stone.get())), "harvest tool reaches actual host state, including native null");
                check(block.getHarvestLevel(state) == Math.max(stone.stoneMaterial.getBlockHarvestLevel(), block.material.getBlockHarvestLevel()), "source ore and host harvest levels");
                check(block.getSoundType(state, null, null, null) == stone.soundType, "world-independent sound selection");
                var drop = block.func_180660_a(state, new Random(0), 0); int damage = block.func_180651_a(state);
                int id = StoneType.STONE_TYPE_REGISTRY.getIDForObject(stone);
                var stoneBlock = OreDeclarations.getOreForMaterial(block.material).get(StoneTypes.STONE).func_177230_c();
                check(drop == (stone.shouldBeDroppedAsItem || id < 16 ? item : Item.func_150898_a(stoneBlock)), "original ordinary-drop block selection");
                check(damage == (stone.shouldBeDroppedAsItem ? meta : 0), "original ordinary-drop metadata");
                if (drop != item) ordinaryRedirects++;
                var silk = block.func_180643_i(state);
                check(silk.func_77973_b() == item && silk.func_77952_i() == (stone.shouldBeDroppedAsItem ? meta : block.func_176201_c(block.func_176223_P())), "silk uses current block default, not ordinary-drop redirection");
                if (silk.func_77973_b() != drop || silk.func_77952_i() != damage) silkDifferences++;
                check(stack(block.getPickBlock(state, null, null, null, null)).equals(stack(form)), "pick block retains exact variant independently of drop policy");
                variants++;
            }
            for (var stone : content.stones()) if (!allowed.contains(stone)) {
                check(!property.func_185929_b(stone.name).isPresent(), "registered but disallowed stone property rejects");
                reject(() -> block.getOreBlock(stone), "cannot fabricate another stone state");
            }
        }
        check(family.generatedOre(SourceMaterialCatalog.Water, StoneTypes.STONE).func_190926_b(), "known ineligible ore has no generated form");
        check(family.resolve(OrePrefix.ore, SourceMaterialCatalog.Water, 1).func_190926_b(), "known ineligible ore has empty unification result");
        reject(() -> family.resolve(OrePrefix.pipeNormalFluid, SourceMaterialCatalog.Aluminium, 1), "unadmitted family remains incomplete");
        reject(() -> StoneType.computeStoneType(null, null, null), "worldgen identification is not simulated");
        if (scenario.equals("disabled")) check(family.generatedOre(SusyStoneMaterials.Fluorite, StoneTypes.STONE).func_190926_b(), "source generation-disable flag prevents ore blocks");
        if (scenario.equals("sparse")) {
            for (var stone : List.of(SusyStoneTypes.GNEISS, SusyStoneTypes.LIMESTONE, SusyStoneTypes.PHYLLITE))
                check(family.generatedOre(SourceMaterialCatalog.Aluminium, stone).func_190926_b(), "first null truncates later occupied slots without compaction");
        }
        var secondary = new TreeMap<String,Object>();
        for (var stone : content.stones()) if (StoneType.STONE_TYPE_REGISTRY.getIDForObject(stone) >= 12)
            secondary.put(stone.name, stone.processingPrefix.secondaryMaterials.stream().map(s -> List.of(s.material.getRegistryName(), s.amount)).toList());
        return Map.of("generatedStackVectors", itemCount, "generatedOreVariants", variants, "ordinaryDropRedirects", ordinaryRedirects, "unparsedOreMemberships", unparsed,
                "silkOrdinaryDifferences", silkDifferences, "secondaryMaterials", secondary, "nativeStateMetadataAndNbt", true);
    }
    public static Object run(String request) throws Exception {
        var properties = new Properties(); try (var in = Files.newInputStream(Path.of(request))) { properties.load(in); }
        String scenario = properties.getProperty("scenario", "baseline"); boolean susy = !scenario.equals("gt-only");
        var home = net.minecraftforge.fml.relauncher.FMLInjectionData.class.getDeclaredField("minecraftHome"); home.setAccessible(true); home.set(null, Path.of(request).getParent().toFile());
        var cfg = CatalogConfiguration.load(Path.of(properties.getProperty("config")), SourceCatalogConfiguration.class);
        var loader = Loader.instance(); var controller = Loader.class.getDeclaredField("modController"); controller.setAccessible(true);
        Object previous = controller.get(loader); check(previous == null, "explicit owner fixture, no installed loader"); controller.set(loader, new LoadController(loader));
        var gtOwner = owner("gregtech"); var susyOwner = owner("susy"); loader.setActiveModContainer(gtOwner);
        var namedMods = Loader.class.getDeclaredField("namedMods"); namedMods.setAccessible(true);
        Object previousMods = namedMods.get(loader); check(previousMods == null, "no discovered mod map");
        namedMods.set(loader, susy ? Map.of("gregtech", gtOwner, "susy", susyOwner) : Map.of("gregtech", gtOwner));
        var addon = new AddonMaterials(susyOwner, scenario); var observer = new NativeMaterialBlockProbe.Observer(); var failure = new OreFailure(scenario);
        if (susy) MinecraftForge.EVENT_BUS.register(addon);
        MinecraftForge.EVENT_BUS.register(observer); MinecraftForge.EVENT_BUS.register(failure);
        try (var env = new FluidEnvironment()) {
            OreDictionary.getOreNames(); observer.ores.clear();
            env.bindCatalog(new CatalogInputs(SourceMaterialCatalog.class, cfg));
            new MaterialLifecycle(env.runtime(), new MaterialEvents(), new MaterialLifecycle.Dependencies() {
                public void initializeMarkers() { MarkerMaterials.register(); }
                public void registerMaterials() { SourceMaterialCatalog.register(); }
                public FluidMaterial aluminium() { return SourceMaterialCatalog.Aluminium; }
            }).execute();
            var deferred = deferredBlocks(env);
            var family = new MaterialContentFamily(susy ? MaterialContentFamily.Universe.ADDON_ORE_CONTENT : MaterialContentFamily.Universe.GT_ORE_CONTENT);
            var sourceAddon = susy ? new NativeOreAddonProbe() : null;
            family.construct(); reject(family::registerBlocks, "host preparation precedes generation");
            var failing = Set.of("failure", "block-failure", "registration-failure", "collision", "gap-start", "duplicate-stone");
            try {
                family.prepareOreHosts(susy ? susyOwner : null, sourceAddon);
                check(loader.activeModContainer() == gtOwner, "native owner is restored after addon declarations");
                reject(() -> family.prepareOreHosts(susy ? susyOwner : null, sourceAddon), "no host replay");
                check(OreDeclarations.ORES.isEmpty(), "preload preparation does not generate ores");
                family.registerBlocks();
                check(family.oreContent().ores().stream().allMatch(b -> Item.func_150898_a(b) == net.minecraft.init.Items.field_190931_a), "block registration does not register ItemBlocks");
                family.registerItems(); check(observer.ores.isEmpty(), "ItemBlocks alone do not create ore membership");
                reject(() -> family.generatedOre(SourceMaterialCatalog.Aluminium, StoneTypes.STONE), "partial query rejects");
                family.registerOres();
            } catch (RuntimeException problem) {
                if (!failing.contains(scenario)) throw problem;
                check(family.phase() == MaterialContentFamily.Phase.FAILED, "native failure is terminal");
                check(loader.activeModContainer() == gtOwner, "failure restores native owner");
                reject(family::construct, "no failure reset"); reject(family::registerBlocks, "no failed block retry"); reject(family::registerItems, "no failed item retry"); reject(family::registerOres, "no failed ore retry");
                check(deferred.equals(deferredBlocks(env)), "failure preserves pending fluid builders");
                return Map.of("scenario", scenario, "phase", family.phase().name(), "partialOres", OreDeclarations.ORES.size(),
                        "registeredBlocks", OreDeclarations.ORES.stream().filter(b -> ForgeRegistries.BLOCKS.getValue(b.getRegistryName()) == b).count(),
                        "registeredItems", OreDeclarations.ORES.stream().filter(b -> Item.func_150898_a(b) instanceof OreItemBlock).count(),
                        "events", observer.ores, "failureType", problem.getClass().getSimpleName(), "failureMessage", Objects.toString(problem.getMessage(), ""), "retryRejected", true);
            }
            check(!failing.contains(scenario), "directed failure must execute");
            var checks = vectors(family, scenario); var inventory = family.inventory(); var checkpoints = family.checkpoints(); var events = List.copyOf(observer.ores);
            OreDeclarations.registerOres(); check(events.size() == observer.ores.size(), "native repeated membership deduplicates");
            check(deferred.equals(deferredBlocks(env)), "original pending fluid builders remain intact");
            check(ForgeRegistries.RECIPES.getKeys().isEmpty(), "no recipe handlers or Minecraft launch");
            if (scenario.equals("host-drift") || OreDeclarations.oreBlockTable.isEmpty()) OreHostBlocks.STONE_BLOCKS.clear();
            else if (scenario.equals("addon-host-drift")) SusyOreHostBlocks.SUSY_STONE_BLOCKS.clear();
            else if (scenario.equals("stone-drift")) new StoneType(90, "unadmitted", net.minecraft.block.SoundType.field_185851_d, OrePrefix.ore, SourceMaterialCatalog.Stone, StoneTypes.STONE.stone, s -> false, false);
            else if (scenario.equals("stone-orphan")) reject(() -> new StoneType(0, "orphan", net.minecraft.block.SoundType.field_185851_d, OrePrefix.ore, SourceMaterialCatalog.Stone, StoneTypes.STONE.stone, s -> false, false), "native duplicate ID rejects after name insertion");
            else if (scenario.equals("ore-state-drift")) {
                var b = family.oreContent().ores().getFirst();
                var setter = Block.class.getDeclaredMethod("func_180632_j", net.minecraft.block.state.IBlockState.class);
                setter.setAccessible(true); setter.invoke(b, b.func_176203_a(1));
            }
            else OreDeclarations.oreBlockTable.clear();
            reject(() -> family.generatedOre(SourceMaterialCatalog.Aluminium, StoneTypes.STONE), "post-completion drift invalidates identity query");
            check(family.phase() == MaterialContentFamily.Phase.FAILED, "drift is terminal");
            return Map.of("scenario", scenario, "inventory", inventory, "checkpoints", checkpoints, "events", events, "checks", checks,
                    "configuration", cfg.evidence(), "materialCount", env.runtime().materials().getRegisteredMaterials().size(),
                    "deferredFluidBlocks", new ArrayList<>(deferred.keySet()), "recipeCount", 0, "pendingMaterialHandlersPreserved", true);
        } finally {
            MinecraftForge.EVENT_BUS.unregister(addon); MinecraftForge.EVENT_BUS.unregister(observer); MinecraftForge.EVENT_BUS.unregister(failure);
            MinecraftForge.EVENT_BUS.unregister(OreDictUnifier.class); loader.setActiveModContainer(null); controller.set(loader, previous); namedMods.set(loader, previousMods);
        }
    }
}
