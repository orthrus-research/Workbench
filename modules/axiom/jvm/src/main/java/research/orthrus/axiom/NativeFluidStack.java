// Extracted from pinned GTCEu/Cleanroom source. See spec/native-fluids.md and sources/NOTICE.md.
/*
 * Minecraft Forge
 * Copyright (c) 2016-2020.
 *
 * This library is free software; you can redistribute it and/or
 * modify it under the terms of the GNU Lesser General Public
 * License as published by the Free Software Foundation version 2.1
 * of the License.
 *
 * This library is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
 * Lesser General Public License for more details.
 *
 * You should have received a copy of the GNU Lesser General Public
 * License along with this library; if not, write to the Free Software
 * Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301  USA
 */

package research.orthrus.axiom;




/**
 * ItemStack substitute for Fluids.
 *
 * NOTE: Equality is based on the NativeFluid, not the amount. Use
 * {@link #isFluidStackIdentical(NativeFluidStack)} to determine if FluidID, Amount and NBT Tag are all
 * equal.
 *
 */
class NativeFluidStack
{
    public int amount;
    public NativeNbtCompound tag;
    private IRegistryDelegate<NativeFluid> fluidDelegate;

    public NativeFluidStack(NativeFluid fluid, int amount)
    {
        if (fluid == null)
        {
            ForgeRegistryLog.bigWarning("Null fluid supplied to fluidstack. Did you try and create a stack for an unregistered fluid?");
            throw new IllegalArgumentException("Cannot create a fluidstack from a null fluid");
        }
        else if (!FluidEnvironment.current().registry().isFluidRegistered(fluid))
        {
            ForgeRegistryLog.bigWarning("Failed attempt to create a FluidStack for an unregistered Fluid {} (type {})", fluid.getName(), fluid.getClass().getName());
            throw new IllegalArgumentException("Cannot create a fluidstack from an unregistered fluid");
        }
        this.fluidDelegate = FluidEnvironment.current().registry().makeDelegate(fluid);
        this.amount = amount;
    }

    public NativeFluidStack(NativeFluid fluid, int amount, NativeNbtCompound nbt)
    {
        this(fluid, amount);

        if (nbt != null)
        {
            tag = (NativeNbtCompound) nbt.copy();
        }
    }

    public NativeFluidStack(NativeFluidStack stack, int amount)
    {
        this(stack.getFluid(), amount, stack.tag);
    }

    /**
     * This provides a safe method for retrieving a NativeFluidStack - if the NativeFluid is invalid, the stack
     * will return as null.
     */

    public static NativeFluidStack loadFluidStackFromNBT(NativeNbtCompound nbt)
    {
        if (nbt == null)
        {
            return null;
        }
        if (!nbt.hasKey("FluidName", NativeNbtTypes.TAG_STRING))
        {
            return null;
        }

        String fluidName = nbt.getString("FluidName");
        if (FluidEnvironment.current().registry().getFluid(fluidName) == null)
        {
            return null;
        }
        NativeFluidStack stack = new NativeFluidStack(FluidEnvironment.current().registry().getFluid(fluidName), nbt.getInteger("Amount"));

        if (nbt.hasKey("Tag"))
        {
            stack.tag = nbt.getCompoundTag("Tag");
        }
        return stack;
    }

    public NativeNbtCompound writeToNBT(NativeNbtCompound nbt)
    {
        nbt.setString("FluidName", FluidEnvironment.current().registry().getFluidName(getFluid()));
        nbt.setInteger("Amount", amount);

        if (tag != null)
        {
            nbt.setTag("Tag", tag);
        }
        return nbt;
    }

    public final NativeFluid getFluid()
    {
        return fluidDelegate.get();
    }



    @Deprecated
    public String getUnlocalizedName()
    {
        return this.getFluid().getUnlocalizedName(this);
    }

    public String getTranslationKey()
    {
        return getUnlocalizedName();
    }

    /**
     * @return A copy of this NativeFluidStack
     */
    public NativeFluidStack copy()
    {
        return new NativeFluidStack(getFluid(), amount, tag);
    }

    /**
     * Determines if the FluidIDs and NBT Tags are equal. This does not check amounts.
     *
     * @param other
     *            The NativeFluidStack for comparison
     * @return true if the Fluids (IDs and NBT Tags) are the same
     */
    public boolean isFluidEqual( NativeFluidStack other)
    {
        return other != null && getFluid() == other.getFluid() && isFluidStackTagEqual(other);
    }

    private boolean isFluidStackTagEqual(NativeFluidStack other)
    {
        return tag == null ? other.tag == null : other.tag == null ? false : tag.equals(other.tag);
    }

    /**
     * Determines if the NBT Tags are equal. Useful if the FluidIDs are known to be equal.
     */
    public static boolean areFluidStackTagsEqual( NativeFluidStack stack1,  NativeFluidStack stack2)
    {
        return stack1 == null && stack2 == null ? true : stack1 == null || stack2 == null ? false : stack1.isFluidStackTagEqual(stack2);
    }

    /**
     * Determines if the Fluids are equal and this stack is larger.
     *
     * @param other
     * @return true if this NativeFluidStack contains the other NativeFluidStack (same fluid and >= amount)
     */
    public boolean containsFluid( NativeFluidStack other)
    {
        return isFluidEqual(other) && amount >= other.amount;
    }

    /**
     * Determines if the FluidIDs, Amounts, and NBT Tags are all equal.
     *
     * @param other
     *            - the NativeFluidStack for comparison
     * @return true if the two FluidStacks are exactly the same
     */
    public boolean isFluidStackIdentical(NativeFluidStack other)
    {
        return isFluidEqual(other) && amount == other.amount;
    }

    /**
     * Determines if the FluidIDs and NBT Tags are equal compared to a registered container
     * ItemStack. This does not check amounts.
     *
     * @param other
     *            The ItemStack for comparison
     * @return true if the Fluids (IDs and NBT Tags) are the same
     */


    @Override
    public final int hashCode()
    {
        int code = 1;
        code = 31*code + getFluid().hashCode();
        if (tag != null)
            code = 31*code + tag.hashCode();
        return code;
    }

    /**
     * Default equality comparison for a NativeFluidStack. Same functionality as isFluidEqual().
     *
     * This is included for use in data structures.
     */
    @Override
    public final boolean equals(Object o)
    {
        if (!(o instanceof NativeFluidStack))
        {
            return false;
        }

        return isFluidEqual((NativeFluidStack) o);
    }
}
