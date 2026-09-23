// Retained pinned GTCEu source; LGPL-3.0. See sources/native-items.lock.json and spec/native-items.md.
package research.orthrus.axiom.nativeconstruction;


import net.minecraft.item.Item;
import net.minecraft.item.ItemStack;


public final class ItemAndMetadata {

    
    public final Item item;
    public final int itemDamage;

    public ItemAndMetadata( Item item, int itemDamage) {
        this.item = item;
        this.itemDamage = itemDamage;
    }

    public ItemAndMetadata( ItemStack itemStack) {
        this.item = itemStack.func_77973_b();
        this.itemDamage = itemStack.func_77952_i();
    }

    
    public ItemStack toItemStack() {
        return new ItemStack(item, 1, itemDamage);
    }

    
    public ItemStack toItemStack(int stackSize) {
        return new ItemStack(item, stackSize, itemDamage);
    }

    public boolean isWildcard() {
        return this.itemDamage == ItemConstants.W;
    }

    
    public ItemAndMetadata toWildcard() {
        return this.isWildcard() ? this : new ItemAndMetadata(item, ItemConstants.W);
    }

    @Override
    public boolean equals(Object o) {
        if (this == o) return true;
        if (!(o instanceof ItemAndMetadata)) return false;

        ItemAndMetadata that = (ItemAndMetadata) o;

        if (itemDamage != that.itemDamage) return false;
        return item.equals(that.item);
    }

    @Override
    public int hashCode() {
        int result = item.hashCode();
        result = 31 * result + itemDamage;
        return result;
    }

    @Override
    public String toString() {
        return this.item.func_77667_c(toItemStack());
    }
}
