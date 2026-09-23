// Source-qualified GTCEu members, LGPL-3.0; see sources/material-items.lock.json and spec/material-items.md.
package research.orthrus.axiom.nativeconstruction;

import java.util.*;
import net.minecraft.item.*;
import net.minecraft.block.Block;
import net.minecraft.creativetab.CreativeTabs;
import net.minecraft.client.renderer.block.model.ModelResourceLocation;
import net.minecraft.client.util.ITooltipFlag;
import net.minecraft.entity.*;
import net.minecraft.entity.item.EntityItem;
import net.minecraft.entity.player.EntityPlayer;
import net.minecraft.entity.ai.attributes.AttributeModifier;
import net.minecraft.inventory.EntityEquipmentSlot;
import net.minecraft.enchantment.Enchantment;
import net.minecraft.nbt.NBTTagCompound;
import net.minecraft.util.*;
import net.minecraft.util.math.BlockPos;
import net.minecraft.world.World;
import net.minecraftforge.common.capabilities.*;
import net.minecraftforge.oredict.OreDictionary;
import com.google.common.collect.Multimap;
import org.apache.commons.lang3.ArrayUtils;
import org.apache.commons.lang3.Validate;
import it.unimi.dsi.fastutil.objects.*;
import it.unimi.dsi.fastutil.shorts.*;
public class MetaPrefixItem extends StandardMetaItem {

    private final MaterialRegistry registry;
    private final OrePrefix prefix;

    public static final Map<OrePrefix, OrePrefix> purifyMap = new HashMap<>();

    static {
        purifyMap.put(OrePrefix.crushed, OrePrefix.crushedPurified);
        purifyMap.put(OrePrefix.dustImpure, OrePrefix.dust);
        purifyMap.put(OrePrefix.dustPure, OrePrefix.dust);
    }

public MetaPrefixItem( MaterialRegistry registry,  OrePrefix orePrefix) {
        super();
        this.registry = registry;
        this.prefix = orePrefix;
        this.func_77637_a(MaterialItemDeclarations.TAB_GREGTECH_MATERIALS);
    }
public void registerSubItems() {
        for (MaterialState state : registry) {
            FluidMaterial material = (FluidMaterial) state;
            short i = (short) registry.getIDForObject(material);
            if (prefix != null && canGenerate(prefix, material)) {
                addItem(i, new UnificationEntry(prefix, material).toString());
            }
        }
    }
public void registerOreDict() {
        for (short metaItem : metaItems.keySet()) {
            FluidMaterial material = getMaterial(metaItem);
            ItemStack item = new ItemStack(this, 1, metaItem);
            OreDictUnifier.registerOre(item, prefix, material);
            registerSpecialOreDict(item, material, prefix);
        }
    }
private static void registerSpecialOreDict(ItemStack item, FluidMaterial material, OrePrefix prefix) {
        if (prefix.getAlternativeOreName() != null) {
            OreDictUnifier.registerOre(item, prefix.getAlternativeOreName(), material);
        }

        if (material == PrefixDependencies.material("Plutonium239")) {
            OreDictUnifier.registerOre(item, prefix.name() + material.toCamelCaseString() + "239");
        } else if (material == PrefixDependencies.material("Uranium238")) {
            OreDictUnifier.registerOre(item, prefix.name() + material.toCamelCaseString() + "238");
        } else if (material == PrefixDependencies.material("Saltpeter")) {
            OreDictUnifier.registerOre(item, prefix.name() + material.toCamelCaseString());
        }
    }
protected static boolean canGenerate(OrePrefix orePrefix, FluidMaterial material) {
        return orePrefix.doGenerateItem(material);
    }
@Override
public String func_77653_i( ItemStack itemStack) { throw new Failure("incomplete", "item.behavior", "Unqualified material item method: getItemStackDisplayName"); }
public int getItemStackLimit( ItemStack stack) {
        if (prefix == null) return 64;
        return prefix.maxStackSize;
    }
@Override
public void func_77663_a( ItemStack itemStack,  World worldIn,  Entity entityIn, int itemSlot,
                         boolean isSelected) { throw new Failure("incomplete", "item.behavior", "Unqualified material item method: onUpdate"); }
@Override
public void func_77624_a( ItemStack itemStack,  World worldIn,  List<String> lines,
                                ITooltipFlag tooltipFlag) { throw new Failure("incomplete", "item.behavior", "Unqualified material item method: addInformation"); }
public FluidMaterial getMaterial( ItemStack stack) {
        return (FluidMaterial) registry.getObjectById(stack.func_77960_j());
    }
protected FluidMaterial getMaterial(int metadata) {
        return (FluidMaterial) Objects.requireNonNull(registry.getObjectById(metadata));
    }
public static FluidMaterial tryGetMaterial( ItemStack itemStack) {
        if (itemStack.func_77973_b() instanceof MetaPrefixItem metaPrefixItem) {
            return metaPrefixItem.getMaterial(itemStack);
        }
        return null;
    }
public OrePrefix getOrePrefix() {
        return this.prefix;
    }
public int getItemBurnTime( ItemStack itemStack) {
        FluidMaterial material = getMaterial(itemStack);
        DustProperty property = material == null ? null : material.getProperty(PropertyKey.DUST);
        if (property != null) return (int) (property.getBurnTime() * prefix.getMaterialAmount(material) / MaterialVoltages.M);
        return super.getItemBurnTime(itemStack);
    }
public boolean isBeaconPayment( ItemStack stack) {
        FluidMaterial material = getMaterial(stack);
        if (material != null && this.prefix != OrePrefix.ingot && this.prefix != OrePrefix.gem) {
            ToolProperty property = material.getProperty(PropertyKey.TOOL);
            return property != null && property.getToolHarvestLevel() >= 2;
        }
        return false;
    }
@Override
public boolean onEntityItemUpdate(EntityItem itemEntity) { throw new Failure("incomplete", "item.behavior", "Unqualified material item method: onEntityItemUpdate"); }
}
