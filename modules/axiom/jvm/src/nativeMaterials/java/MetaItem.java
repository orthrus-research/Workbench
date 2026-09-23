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
public abstract class MetaItem<T extends MetaItem<?>.MetaValueItem> extends Item {
private static final List<MetaItem<?>> META_ITEMS = new ArrayList<>();
private final Map<String, T> names = new Object2ObjectOpenHashMap<>();
protected final Short2ObjectMap<T> metaItems = new Short2ObjectLinkedOpenHashMap<>();
protected final Short2ObjectMap<ModelResourceLocation> metaItemsModels = new Short2ObjectOpenHashMap<>();
protected final Short2ObjectMap<ModelResourceLocation[]> specialItemsModels = new Short2ObjectOpenHashMap<>();
protected static final ModelResourceLocation MISSING_LOCATION = new ModelResourceLocation("builtin/missing",
            "inventory");
protected final short metaItemOffset;
private CreativeTabs[] defaultCreativeTabs = new CreativeTabs[] { MaterialItemDeclarations.TAB_GREGTECH };
private final Set<CreativeTabs> additionalCreativeTabs = new ObjectArraySet<>();
private String translationKey = "metaitem";
public static List<MetaItem<?>> getMetaItems() {
        return Collections.unmodifiableList(META_ITEMS);
    }
public MetaItem(short metaItemOffset) {
        func_77627_a(true);
        this.metaItemOffset = metaItemOffset;
        META_ITEMS.add(this);
    }
@Override
public boolean showDurabilityBar( ItemStack stack) { throw new Failure("incomplete", "item.behavior", "Unqualified material item method: showDurabilityBar"); }
@Override
public double getDurabilityForDisplay( ItemStack stack) { throw new Failure("incomplete", "item.behavior", "Unqualified material item method: getDurabilityForDisplay"); }
@Override
public EnumRarity func_77613_e( ItemStack stack) { throw new Failure("incomplete", "item.behavior", "Unqualified material item method: getRarity"); }
public final T addItem(int metaValue, String unlocalizedName) {
        Validate.inclusiveBetween(0, Short.MAX_VALUE - 1, metaValue + metaItemOffset,
                "MetaItem ID should be in range from 0 to Short.MAX_VALUE-1");
        T metaValueItem = constructMetaValueItem((short) metaValue, unlocalizedName);
        if (metaItems.containsKey((short) metaValue)) {
            T registeredItem = metaItems.get((short) metaValue);
            throw new IllegalArgumentException(
                    String.format("MetaId %d is already occupied by item %s (requested by item %s)", metaValue,
                            registeredItem.unlocalizedName, unlocalizedName));
        }
        metaItems.put((short) metaValue, metaValueItem);
        names.put(unlocalizedName, metaValueItem);
        return metaValueItem;
    }
public final Collection<T> getAllItems() {
        return Collections.unmodifiableCollection(metaItems.values());
    }
public final T getItem(short metaValue) {
        return metaItems.get(formatRawItemDamage(metaValue));
    }
public final T getItem(String valueName) {
        return names.get(valueName);
    }
public final T getItem(ItemStack itemStack) {
        return getItem((short) (itemStack.func_77952_i() - metaItemOffset));
    }
protected short formatRawItemDamage(short metaValue) {
        return metaValue;
    }
public void registerSubItems() {}
public ICapabilityProvider initCapabilities( ItemStack stack,  NBTTagCompound nbt) {
        T metaValueItem = getItem(stack);
        if (metaValueItem == null) {
            return null;
        }
        ArrayList<ICapabilityProvider> providers = new ArrayList<>();
        for (IItemComponent itemComponent : metaValueItem.getAllStats()) {
            if (itemComponent instanceof IItemCapabilityProvider provider) {
                providers.add(provider.createProvider(stack));
            }
        }
        return new CombinedCapabilityProvider(providers);
    }
public int getItemBurnTime( ItemStack itemStack) {
        T metaValueItem = getItem(itemStack);
        if (metaValueItem == null) {
            return super.getItemBurnTime(itemStack);
        }
        return metaValueItem.getBurnValue();
    }
@Override
public int getItemStackLimit( ItemStack stack) { throw new Failure("incomplete", "item.behavior", "Unqualified material item method: getItemStackLimit"); }
@Override
public EnumAction func_77661_b( ItemStack stack) { throw new Failure("incomplete", "item.behavior", "Unqualified material item method: getItemUseAction"); }
@Override
public int func_77626_a( ItemStack stack) { throw new Failure("incomplete", "item.behavior", "Unqualified material item method: getMaxItemUseDuration"); }
@Override
public void onUsingTick( ItemStack stack,  EntityLivingBase player, int count) { throw new Failure("incomplete", "item.behavior", "Unqualified material item method: onUsingTick"); }
@Override
public void func_77615_a( ItemStack stack,  World world,  EntityLivingBase player,
                                     int timeLeft) { throw new Failure("incomplete", "item.behavior", "Unqualified material item method: onPlayerStoppedUsing"); }
@Override
public ItemStack func_77654_b( ItemStack stack,  World world,  EntityLivingBase player) { throw new Failure("incomplete", "item.behavior", "Unqualified material item method: onItemUseFinish"); }
@Override
public boolean onLeftClickEntity( ItemStack stack,  EntityPlayer player,  Entity entity) { throw new Failure("incomplete", "item.behavior", "Unqualified material item method: onLeftClickEntity"); }
@Override
public boolean func_111207_a( ItemStack stack,  EntityPlayer playerIn,
                                             EntityLivingBase target,  EnumHand hand) { throw new Failure("incomplete", "item.behavior", "Unqualified material item method: itemInteractionForEntity"); }
@Override
public ActionResult<ItemStack> func_77659_a( World world, EntityPlayer player,  EnumHand hand) { throw new Failure("incomplete", "item.behavior", "Unqualified material item method: onItemRightClick"); }
@Override
public EnumActionResult onItemUseFirst(EntityPlayer player,  World world,  BlockPos pos,
                                            EnumFacing side, float hitX, float hitY, float hitZ,
                                            EnumHand hand) { throw new Failure("incomplete", "item.behavior", "Unqualified material item method: onItemUseFirst"); }
@Override
public EnumActionResult func_180614_a(EntityPlayer player,  World world,  BlockPos pos,
                                       EnumHand hand,  EnumFacing facing, float hitX, float hitY,
                                      float hitZ) { throw new Failure("incomplete", "item.behavior", "Unqualified material item method: onItemUse"); }
@Override
public Multimap<String, AttributeModifier> getAttributeModifiers( EntityEquipmentSlot slot,
                                                                      ItemStack stack) { throw new Failure("incomplete", "item.behavior", "Unqualified material item method: getAttributeModifiers"); }
@Override
public boolean func_77616_k( ItemStack stack) { throw new Failure("incomplete", "item.behavior", "Unqualified material item method: isEnchantable"); }
@Override
public int getItemEnchantability( ItemStack stack) { throw new Failure("incomplete", "item.behavior", "Unqualified material item method: getItemEnchantability"); }
@Override
public boolean canApplyAtEnchantingTable( ItemStack stack,  Enchantment enchantment) { throw new Failure("incomplete", "item.behavior", "Unqualified material item method: canApplyAtEnchantingTable"); }
@Override
public void func_77663_a( ItemStack stack,  World worldIn,  Entity entityIn, int itemSlot,
                         boolean isSelected) { throw new Failure("incomplete", "item.behavior", "Unqualified material item method: onUpdate"); }
@Override
public boolean shouldCauseReequipAnimation( ItemStack oldStack,  ItemStack newStack,
                                               boolean slotChanged) { throw new Failure("incomplete", "item.behavior", "Unqualified material item method: shouldCauseReequipAnimation"); }
public MetaItem<T> func_77655_b( String key) {
        this.translationKey = Objects.requireNonNull(key, "key == null");
        return this;
    }
public String func_77658_a() {
        return getTranslationKey((T) null);
    }
public String func_77667_c( ItemStack stack) {
        return getTranslationKey(getItem(stack));
    }
protected String getTranslationKey( T metaValueItem) {
        return metaValueItem == null ? this.translationKey : this.translationKey + "." + metaValueItem.unlocalizedName;
    }
@Override
public String func_77653_i(ItemStack stack) { throw new Failure("incomplete", "item.behavior", "Unqualified material item method: getItemStackDisplayName"); }
@Override
public void func_77624_a( ItemStack itemStack,  World worldIn,  List<String> lines,
                                ITooltipFlag tooltipFlag) { throw new Failure("incomplete", "item.behavior", "Unqualified material item method: addInformation"); }
@Override
public boolean hasContainerItem( ItemStack itemStack) { throw new Failure("incomplete", "item.behavior", "Unqualified material item method: hasContainerItem"); }
@Override
public ItemStack getContainerItem( ItemStack itemStack) { throw new Failure("incomplete", "item.behavior", "Unqualified material item method: getContainerItem"); }
public CreativeTabs  [] getCreativeTabs() {
        if (additionalCreativeTabs.isEmpty()) return defaultCreativeTabs; // short circuit
        Set<CreativeTabs> tabs = new ObjectArraySet<>(additionalCreativeTabs);
        tabs.addAll(Arrays.asList(defaultCreativeTabs));
        return tabs.toArray(new CreativeTabs[0]);
    }
public MetaItem<T> func_77637_a( CreativeTabs tab) {
        this.defaultCreativeTabs = new CreativeTabs[] { tab };
        return this;
    }
public MetaItem<T> setCreativeTabs( CreativeTabs ... tabs) {
        this.defaultCreativeTabs = tabs;
        return this;
    }
public void addAdditionalCreativeTabs( CreativeTabs ... tabs) {
        for (CreativeTabs tab : tabs) {
            if (!ArrayUtils.contains(defaultCreativeTabs, tab) && tab != CreativeTabs.field_78027_g) {
                additionalCreativeTabs.add(tab);
            }
        }
    }
protected boolean func_194125_a( CreativeTabs tab) {
        return tab == CreativeTabs.field_78027_g ||
                ArrayUtils.contains(defaultCreativeTabs, tab) ||
                additionalCreativeTabs.contains(tab);
    }
@Override
public void func_150895_a( CreativeTabs tab,  NonNullList<ItemStack> subItems) { throw new Failure("incomplete", "item.behavior", "Unqualified material item method: getSubItems"); }
protected abstract T constructMetaValueItem(short metaValue, String unlocalizedName);
public class MetaValueItem {
public final int metaValue;
public final String unlocalizedName;
private final List<IItemComponent> allStats = new ArrayList<>();
private int burnValue = 0;
public MetaItem<T> getMetaItem() {
            return MetaItem.this;
        }
protected MetaValueItem(int metaValue, String unlocalizedName) {
            this.metaValue = metaValue;
            this.unlocalizedName = unlocalizedName;
        }
public int getMetaValue() {
            return metaValue;
        }
public List<IItemComponent> getAllStats() {
            return Collections.unmodifiableList(allStats);
        }
public int getBurnValue() {
            return burnValue;
        }
public ItemStack getStackForm(int amount) {
            return new ItemStack(MetaItem.this, amount, metaItemOffset + metaValue);
        }
public ItemStack getStackForm() {
            return getStackForm(1);
        }
public boolean isItemEqual(ItemStack itemStack) {
            return itemStack.func_77973_b() == MetaItem.this && itemStack.func_77952_i() == (metaItemOffset + metaValue);
        }
public MetaValueItem setBurnValue(int burnValue) {
            if (burnValue <= 0) {
                throw new IllegalArgumentException("Cannot set Burn Value to negative or zero number.");
            }
            this.burnValue = burnValue;
            return this;
        }
public MetaValueItem setMaterialInfo(ItemMaterialInfo materialInfo) {
            if (materialInfo == null) {
                throw new IllegalArgumentException("Cannot add null ItemMaterialInfo.");
            }
            OreDictUnifier.registerOre(getStackForm(), materialInfo);
            return this;
        }
public MetaValueItem setUnificationData(OrePrefix prefix,  FluidMaterial material) {
            if (prefix == null) {
                throw new IllegalArgumentException("Cannot add null OrePrefix.");
            }
            OreDictUnifier.registerOre(getStackForm(), prefix, material);
            return this;
        }
public MetaValueItem addOreDict(String oreDictName) {
            if (oreDictName == null) {
                throw new IllegalArgumentException("Cannot add null OreDictName.");
            }
            OreDictionary.registerOre(oreDictName, getStackForm());
            return this;
        }
}
}
