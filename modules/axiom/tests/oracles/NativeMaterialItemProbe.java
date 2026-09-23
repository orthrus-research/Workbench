package research.orthrus.axiom.nativeconstruction;

import java.nio.file.*;
import java.util.*;
import net.minecraft.block.Block;
import net.minecraft.item.*;
import net.minecraft.nbt.NBTTagCompound;
import net.minecraft.util.ResourceLocation;
import net.minecraftforge.common.MinecraftForge;
import net.minecraftforge.common.capabilities.ICapabilityProvider;
import net.minecraftforge.event.RegistryEvent;
import net.minecraftforge.fml.common.*;
import net.minecraftforge.fml.common.eventhandler.*;
import net.minecraftforge.fml.common.registry.ForgeRegistries;
import net.minecraftforge.oredict.OreDictionary;

/** Assertions use native registered objects, not a second item model. */
public final class NativeMaterialItemProbe {
    static void check(boolean value, String message) { if (!value) throw new AssertionError(message); }
    static void reject(Runnable operation, String message) {
        try { operation.run(); throw new AssertionError(message); }
        catch (IllegalStateException | IllegalArgumentException | Failure expected) { }
    }
    static List<Object> stack(ItemStack stack) {
        return List.of(stack.func_77973_b().getRegistryName().toString(), stack.func_77952_i(), stack.func_190916_E());
    }
    public static final class Observer {
        int itemEvents, blockEvents;
        final List<Object> ores = new ArrayList<>();
        @SubscribeEvent public void item(RegistryEvent.Register<Item> event) { itemEvents++; }
        @SubscribeEvent public void block(RegistryEvent.Register<Block> event) { blockEvents++; }
        @SubscribeEvent(priority = EventPriority.LOWEST) public void ore(OreDictionary.OreRegisterEvent event) {
            ores.add(List.of(event.getName(), stack(event.getOre())));
        }
    }
    public static final class FailedOre {
        final String name;
        FailedOre(String name) { this.name = name; }
        @SubscribeEvent(priority = EventPriority.HIGHEST) public void ore(OreDictionary.OreRegisterEvent event) {
            if (event.getName().equals(name)) throw new IllegalStateException("material-item-ore-failure");
        }
    }
    public static final class FailedRegistry {
        @SubscribeEvent(priority = EventPriority.LOWEST) public void item(RegistryEvent.Register<Item> event) {
            throw new IllegalStateException("material-item-registry-failure");
        }
    }
    static Map<String,Object> vectors(MaterialContentFamily family) throws Exception {
        var aluminium = SourceMaterialCatalog.Aluminium;
        int count = 0;
        for (var item : family.items()) for (var variant : item.getAllItems()) {
            var stack = variant.getStackForm(3);
            check(stack.func_77973_b() == item && stack.func_77952_i() == variant.metaValue, "native item/material-ID metadata");
            check(item.getItem(stack) == variant && item.getItem(variant.unlocalizedName) == variant, "native subtype indexes");
            check(item.getMaterial(stack).getId() == variant.metaValue, "material ID never compacted");
            check(MetaPrefixItem.tryGetMaterial(stack) == item.getMaterial(stack), "actual native subclass query");
            check(stack.func_77976_d() == item.getOrePrefix().maxStackSize, "native stack-limit virtual dispatch");
            var copy = stack.func_77946_l(); var decoded = new ItemStack(stack.serializeNBT());
            check(stack(copy).equals(stack(stack)) && stack(decoded).equals(stack(stack)), "native copy/NBT identity");
            check(item.initCapabilities(stack, null) instanceof CombinedCapabilityProvider, "original non-null empty combined provider");
            check(!item.initCapabilities(stack, null).hasCapability(null, null), "empty provider original behavior");
            var entry = OreDictUnifier.getUnificationEntry(stack);
            check(entry != null && entry.orePrefix == item.getOrePrefix() && entry.material == item.getMaterial(stack), "every generated form delivered to correct unifier identity");
            check(OreDictionary.getOreIDs(stack).length > 0, "every generated form native membership");
            String alternative = item.getOrePrefix().getAlternativeOreName();
            if (alternative != null)
                check(OreDictUnifier.getOreDictionaryNames(stack).contains(alternative + item.getMaterial(stack).toCamelCaseString()), "original alternative prefix membership");
            check(variant.isItemEqual(copy) && !variant.isItemEqual(ItemStack.field_190927_a), "GT identity comparison");
            count++;
        }
        var dust = family.items().stream().filter(i -> i.getOrePrefix() == OrePrefix.dust).findFirst().orElseThrow();
        ItemStack a = family.resolve(OrePrefix.dust, aluminium, 7);
        check(a.func_77973_b() == dust && a.func_190916_E() == 7, "actual generated dust resolution");
        var tag = new NBTTagCompound(); tag.func_74778_a("fixture", "original"); a.func_77982_d(tag);
        ItemStack b = a.func_77946_l(); b.func_77978_p().func_74778_a("fixture", "changed");
        check(a.func_77978_p().func_74779_i("fixture").equals("original"), "generated native deep NBT copy");
        check(new ItemStack(a.serializeNBT()).func_77973_b() == dust, "generated native NBT registry resolution");
        check(OreDictUnifier.getUnificated(a).func_77978_p() == null, "original unification drops arbitrary NBT");
        check(family.resolve(OrePrefix.ingot, SourceMaterialCatalog.Iron, 1).func_77973_b().getRegistryName().toString().equals("minecraft:iron_ingot"), "ignored generated iron resolves existing vanilla form");
        check(family.resolve(OrePrefix.ingot, SourceMaterialCatalog.Water, 1).func_190926_b(), "ineligible form stays empty");
        for (var material : List.of(SourceMaterialCatalog.Plutonium239, SourceMaterialCatalog.Uranium238)) {
            String suffix = material == SourceMaterialCatalog.Plutonium239 ? "239" : "238";
            var isotope = family.resolve(OrePrefix.dust, material, 1);
            check(OreDictUnifier.getOreDictionaryNames(isotope).contains("dust" + material.toCamelCaseString() + suffix), "original isotope alias membership");
        }
        var saltpeter = family.resolve(OrePrefix.dust, SourceMaterialCatalog.Saltpeter, 1);
        check(OreDictionary.getOres("dust" + SourceMaterialCatalog.Saltpeter.toCamelCaseString()).stream()
                .filter(s -> s.func_77973_b() == saltpeter.func_77973_b() && s.func_77952_i() == saltpeter.func_77952_i()).count() == 1,
                "Saltpeter duplicate alias deduplicates natively");
        reject(() -> family.resolve(OrePrefix.block, aluminium, 1), "unqualified family must not report an absent block");
        reject(() -> dust.func_77663_a(a, null, null, 0, false), "world behavior must not fall through to vanilla");
        reject(() -> dust.onEntityItemUpdate(null), "purification must not return fake success");
        reject(() -> dust.getContainerItem(a), "container behavior must not silently fall through");
        reject(() -> dust.hasContainerItem(a), "container predicate must not silently fall through");
        reject(() -> dust.getItemEnchantability(a), "Forge stack overload must not fall through");
        reject(() -> MaterialItemDeclarations.TAB_GREGTECH.func_78016_d(), "unqualified logo family");
        check(MaterialItemDeclarations.TAB_GREGTECH_MATERIALS.func_78016_d().func_77973_b() != net.minecraft.init.Items.field_190931_a, "source material tab icon resolves registered ingot");
        reject(() -> dust.addItem(Short.MAX_VALUE, "out_of_range"), "exclusive metadata upper bound");
        reject(() -> dust.addItem(-1, "negative"), "negative metadata");
        int existing = dust.getAllItems().iterator().next().metaValue;
        int before = dust.getAllItems().size();
        reject(() -> dust.addItem(existing, "duplicate"), "duplicate ID must reject");
        check(dust.getAllItems().size() == before && dust.getItem("duplicate") == null, "duplicate does not overwrite indexes");
        var invalid = new ItemStack(dust, 1, 32767);
        check(dust.getItem(invalid) == null && dust.getMaterial(invalid) == null && dust.initCapabilities(invalid, null) == null, "unknown material metadata remains null");
        // Separate fixture item: source metadata limits and name-index asymmetry.
        var fixture = new StandardMetaItem((short)1); fixture.setRegistryName("fixture", "metadata_boundaries"); ForgeRegistries.ITEMS.register(fixture);
        var first = fixture.addItem(0, "same"); var last = fixture.addItem(32765, "same");
        check(fixture.getItem("same") == last && fixture.getItem((short)0) == first, "duplicate name replaces name lookup only");
        check(last.getStackForm().func_77952_i() == 32766, "offset-inclusive maximum");
        reject(() -> fixture.addItem(32766, "overflow"), "offset affects metadata bound");
        reject(() -> family.resolve(OrePrefix.dust, aluminium, 1), "adding an unqualified family invalidates qualified queries");
        check(family.phase() == MaterialContentFamily.Phase.FAILED, "changed item universe is terminal");
        return Map.of("generatedStackVectors", count, "metadataBoundaries", true, "failClosedBehavior", true,
                "unqualifiedFamiliesReject", true, "nativeCapabilities", true, "duplicateNameAsymmetry", true,
                "postInventoryFixtureInvalidatesUniverse", true);
    }
    public static Object run(String request) throws Exception {
        var properties = new Properties(); try (var input = Files.newInputStream(Path.of(request))) { properties.load(input); }
        String scenario = properties.getProperty("scenario", "baseline");
        var gameDirectory = net.minecraftforge.fml.relauncher.FMLInjectionData.class.getDeclaredField("minecraftHome");
        gameDirectory.setAccessible(true); gameDirectory.set(null, Path.of(request).getParent().toFile());
        var cfg = CatalogConfiguration.load(Path.of(properties.getProperty("config")), SourceCatalogConfiguration.class);
        var loader = Loader.instance(); var controller = Loader.class.getDeclaredField("modController"); controller.setAccessible(true);
        Object previous = controller.get(loader); check(previous == null, "no discovery or installed loader composition");
        controller.set(loader, new LoadController(loader));
        var metadata = new ModMetadata(); metadata.modId = "gregtech"; metadata.name = "gregtech"; loader.setActiveModContainer(new DummyModContainer(metadata));
        var observer = new Observer(); MinecraftForge.EVENT_BUS.register(observer);
        try (var env = new FluidEnvironment()) {
            check(ForgeRegistries.RECIPES.getKeys().isEmpty(), "natural item checkpoint recipe registry is empty; never clear it");
            OreDictionary.getOreNames(); observer.ores.clear();
            env.bindCatalog(new CatalogInputs(SourceMaterialCatalog.class, cfg));
            new MaterialLifecycle(env.runtime(), new MaterialEvents(), new MaterialLifecycle.Dependencies() {
                public void initializeMarkers() { MarkerMaterials.register(); }
                public void registerMaterials() { SourceMaterialCatalog.register(); }
                public FluidMaterial aluminium() { return SourceMaterialCatalog.Aluminium; }
            }).execute();
            var family = new MaterialContentFamily(MaterialContentFamily.Universe.PREFIX_ITEMS);
            reject(family::registerItems, "phase ordering");
            reject(() -> family.resolve(OrePrefix.dust, SourceMaterialCatalog.Aluminium, 1), "unconstructed query");
            family.construct();
            check(family.items().size() == 39, "original prefix list, not every OrePrefix");
            check(family.items().stream().allMatch(i -> i.getAllItems().isEmpty()), "construction is not subtype registration");
            check(OreDictUnifier.get(OrePrefix.dust, SourceMaterialCatalog.Aluminium).func_190926_b(), "no generated availability at construction");
            reject(family::registerOres, "ore registration must not precede native items");
            reject(family::construct, "family checkpoint cannot be replayed");
            if (scenario.equals("registration-failure")) {
                var failed = new FailedRegistry(); MinecraftForge.EVENT_BUS.register(failed);
                try { reject(family::registerItems, "registry failure must surface"); }
                finally { MinecraftForge.EVENT_BUS.unregister(failed); }
                check(family.phase() == MaterialContentFamily.Phase.FAILED, "registry event failure is terminal");
                check(family.items().stream().allMatch(i -> ForgeRegistries.ITEMS.getValue(i.getRegistryName()) == i), "registered objects survive later event failure");
                check(family.items().stream().anyMatch(i -> !i.getAllItems().isEmpty()), "registered variants survive event failure");
                reject(family::registerItems, "failed family must not retry registry event");
                reject(family::registerOres, "failed family must not advance to ores");
                return Map.of("scenario", scenario, "phase", family.phase().name(), "partialStateRetained", true,
                        "retryRejected", true, "inventory", family.inventory(), "events", observer.ores);
            }
            family.registerItems();
            check(observer.itemEvents == 1 && observer.blockEvents == 0, "original native generic registry event dispatch");
            check(observer.ores.isEmpty(), "native item registry event does not imply ore registration");
            reject(() -> family.resolve(OrePrefix.dust, SourceMaterialCatalog.Aluminium, 1), "registered but not ore-qualified query");
            if (scenario.equals("failure")) {
                var failed = new FailedOre("dustAluminium"); MinecraftForge.EVENT_BUS.register(failed);
                try { reject(family::registerOres, "ore failure must surface"); }
                finally { MinecraftForge.EVENT_BUS.unregister(failed); }
                check(family.phase() == MaterialContentFamily.Phase.FAILED, "terminal failed checkpoint");
                check(!OreDictionary.getOres("dustAluminium", false).isEmpty(), "native ore partial mutation retained");
                check(OreDictUnifier.get(OrePrefix.dust, SourceMaterialCatalog.Aluminium).func_190926_b(), "failed event does not fabricate unifier cache");
                reject(family::registerOres, "failed family must not retry");
                reject(() -> family.resolve(OrePrefix.dust, SourceMaterialCatalog.Aluminium, 1), "failed family must not answer validity");
                return Map.of("scenario", scenario, "phase", family.phase().name(), "partialStateRetained", true,
                        "retryRejected", true, "inventory", family.inventory(), "events", observer.ores);
            }
            family.registerOres();
            var inventory = family.inventory(); var checkpoints = family.checkpoints(); var events = List.copyOf(observer.ores);
            int oreEvents = observer.ores.size(); MaterialItemDeclarations.registerOres();
            check(observer.ores.size() == oreEvents, "original duplicate ore registration deduplicates before event");
            var checks = vectors(family);
            check(ForgeRegistries.RECIPES.getKeys().isEmpty(), "no preload, recipe events or material handlers executed");
            var pending = OrePrefix.class.getDeclaredField("generatedMaterials"); pending.setAccessible(true);
            check(((Set<?>)pending.get(OrePrefix.dust)).contains(SourceMaterialCatalog.Aluminium), "pending material processing preserved");
            return Map.of("scenario", scenario, "inventory", inventory, "checkpoints", checkpoints, "events", events,
                    "checks", checks, "configuration", cfg.evidence(), "materialCount", env.runtime().materials().getRegisteredMaterials().size(),
                    "recipeCount", 0, "pendingMaterialHandlersPreserved", true);
        } finally {
            MinecraftForge.EVENT_BUS.unregister(OreDictUnifier.class); MinecraftForge.EVENT_BUS.unregister(observer);
            loader.setActiveModContainer(null); controller.set(loader, previous);
        }
    }
}
