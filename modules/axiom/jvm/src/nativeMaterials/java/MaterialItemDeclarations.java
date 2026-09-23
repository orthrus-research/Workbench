// Source-qualified GTCEu members, LGPL-3.0; see sources/material-items.lock.json and spec/material-items.md.
package research.orthrus.axiom.nativeconstruction;
import java.util.*;
import com.google.common.base.CaseFormat;
import net.minecraft.item.*;
import net.minecraftforge.registries.IForgeRegistry;
final class MaterialItemDeclarations {
private static final List<OrePrefix> orePrefixes = new ArrayList<>();
    static {
        orePrefixes.add(OrePrefix.dust);
        orePrefixes.add(OrePrefix.dustSmall);
        orePrefixes.add(OrePrefix.dustTiny);
        orePrefixes.add(OrePrefix.dustImpure);
        orePrefixes.add(OrePrefix.dustPure);
        orePrefixes.add(OrePrefix.crushed);
        orePrefixes.add(OrePrefix.crushedPurified);
        orePrefixes.add(OrePrefix.crushedCentrifuged);
        orePrefixes.add(OrePrefix.gem);
        orePrefixes.add(OrePrefix.gemChipped);
        orePrefixes.add(OrePrefix.gemFlawed);
        orePrefixes.add(OrePrefix.gemFlawless);
        orePrefixes.add(OrePrefix.gemExquisite);
        orePrefixes.add(OrePrefix.ingot);
        orePrefixes.add(OrePrefix.ingotHot);
        orePrefixes.add(OrePrefix.plate);
        orePrefixes.add(OrePrefix.plateDouble);
        orePrefixes.add(OrePrefix.plateDense);
        orePrefixes.add(OrePrefix.foil);
        orePrefixes.add(OrePrefix.stick);
        orePrefixes.add(OrePrefix.stickLong);
        orePrefixes.add(OrePrefix.bolt);
        orePrefixes.add(OrePrefix.screw);
        orePrefixes.add(OrePrefix.ring);
        orePrefixes.add(OrePrefix.nugget);
        orePrefixes.add(OrePrefix.round);
        orePrefixes.add(OrePrefix.spring);
        orePrefixes.add(OrePrefix.springSmall);
        orePrefixes.add(OrePrefix.gear);
        orePrefixes.add(OrePrefix.gearSmall);
        orePrefixes.add(OrePrefix.wireFine);
        orePrefixes.add(OrePrefix.rotor);
        orePrefixes.add(OrePrefix.lens);
        orePrefixes.add(OrePrefix.turbineBlade);
        orePrefixes.add(OrePrefix.toolHeadDrill);
        orePrefixes.add(OrePrefix.toolHeadChainsaw);
        orePrefixes.add(OrePrefix.toolHeadWrench);
        orePrefixes.add(OrePrefix.toolHeadBuzzSaw);
        orePrefixes.add(OrePrefix.toolHeadScrewdriver);
    }
public static final BaseCreativeTab TAB_GREGTECH = new BaseCreativeTab("gregtech" + ".main",
            () -> unqualifiedLogo(), true);
public static final BaseCreativeTab TAB_GREGTECH_MATERIALS = new BaseCreativeTab("gregtech" + ".materials",
            () -> OreDictUnifier.get(OrePrefix.ingot, PrefixDependencies.material("Aluminium")), true);
private static ItemStack unqualifiedLogo() { throw new Failure("incomplete", "item.family", "MetaItem1 LOGO is not qualified"); }

static void construct() {
        for (OrePrefix prefix : orePrefixes) {
            for (MaterialRegistry registry : FluidEnvironment.current().runtime().materials().getRegistries()) {
                String regName = CaseFormat.UPPER_CAMEL.to(CaseFormat.LOWER_UNDERSCORE, prefix.name());
                MetaPrefixItem metaOrePrefix = new MetaPrefixItem(registry, prefix);
                metaOrePrefix.setRegistryName(registry.getModid(), String.format("meta_%s", regName));
            }
        }
}

static void register(IForgeRegistry<Item> registry) {
        for (MetaItem<?> item : MetaItem.getMetaItems()) {
            registry.register(item);
            item.registerSubItems();
        }
}

static void registerOres() {
        for (MetaItem<?> item : MetaItem.getMetaItems()) {
            if (item instanceof MetaPrefixItem) {
                ((MetaPrefixItem) item).registerOreDict();
            }
        }
}
}
