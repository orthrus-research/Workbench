// Source-qualified GTCEu members, LGPL-3.0; see sources/material-items.lock.json and spec/material-items.md.
package research.orthrus.axiom.nativeconstruction;

import net.minecraft.creativetab.CreativeTabs;
import net.minecraft.init.Blocks;
import net.minecraft.item.ItemStack;


import java.util.function.Supplier;

public class BaseCreativeTab extends CreativeTabs {

    private final boolean hasSearchBar;
    private final Supplier<ItemStack> iconSupplier;

    public BaseCreativeTab(String TabName, Supplier<ItemStack> iconSupplier, boolean hasSearchBar) {
        super(TabName);
        this.iconSupplier = iconSupplier;
        this.hasSearchBar = hasSearchBar;

        if (hasSearchBar)
            func_78025_a("item_search.png");
    }

    
    @Override
    public ItemStack func_78016_d() {
        if (iconSupplier == null) {
            org.apache.logging.log4j.LogManager.getLogger("axiom.material-items").error("Icon supplier was null for CreativeTab " + func_78013_b());
            return new ItemStack(Blocks.field_150348_b);
        }

        ItemStack stack = iconSupplier.get();
        if (stack == null) {
            org.apache.logging.log4j.LogManager.getLogger("axiom.material-items").error("Icon supplier return null for CreativeTab " + func_78013_b());
            return new ItemStack(Blocks.field_150348_b);
        }

        if (stack == ItemStack.field_190927_a) {
            org.apache.logging.log4j.LogManager.getLogger("axiom.material-items").error("Icon built from iconSupplied is EMPTY for CreativeTab " + func_78013_b());
            return new ItemStack(Blocks.field_150348_b);
        }

        return stack;
    }

    @Override
    public boolean hasSearchBar() {
        return hasSearchBar;
    }
}
