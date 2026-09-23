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

// Extracted from pinned GTCEu/Cleanroom source. See spec/native-fluids.md and sources/NOTICE.md.
package research.orthrus.axiom;
import java.util.Locale;
class NativeFluid {
    public static final int BUCKET_VOLUME = 1000;

    /** The unique identification name for this fluid. */
    protected final String fluidName;

    /** The unlocalized name of this fluid. */
    protected String unlocalizedName;

    protected final NativeLocation still;
    protected final NativeLocation flowing;


    protected final NativeLocation overlay;




    /**
     * The light level emitted by this fluid.
     *
     * Default value is 0, as most fluids do not actively emit light.
     */
    protected int luminosity = 0;

    /**
     * Density of the fluid - completely arbitrary; negative density indicates that the fluid is
     * lighter than air.
     *
     * Default value is approximately the real-life density of water in kg/m^3.
     */
    protected int density = 1000;

    /**
     * Temperature of the fluid - completely arbitrary; higher temperature indicates that the fluid is
     * hotter than air.
     *
     * Default value is approximately the real-life room temperature of water in degrees Kelvin.
     */
    protected int temperature = 300;

    /**
     * Viscosity ("thickness") of the fluid - completely arbitrary; negative values are not
     * permissible.
     *
     * Default value is approximately the real-life density of water in m/s^2 (x10^-3).
     *
     * Higher viscosity means that a fluid flows more slowly, like molasses.
     * Lower viscosity means that a fluid flows more quickly, like helium.
     *
     */
    protected int viscosity = 1000;

    /**
     * This indicates if the fluid is gaseous.
     *
     * Generally this is associated with negative density fluids.
     */
    protected boolean isGaseous;

    /**
     * The rarity of the fluid.
     *
     * Used primarily in tool tips.
     */


    /**
     * If there is a Block implementation of the NativeFluid, the Block is linked here.
     *
     * The default value of null should remain for any NativeFluid without a Block implementation.
     */


    /**
     * Color used by universal bucket and the ModelFluid baked model.
     * Note that this int includes the alpha so converting this to RGB with alpha would be
     *   float r = ((color >> 16) & 0xFF) / 255f; // red
     *   float g = ((color >> 8) & 0xFF) / 255f; // green
     *   float b = ((color >> 0) & 0xFF) / 255f; // blue
     *   float a = ((color >> 24) & 0xFF) / 255f; // alpha
     */
    protected int color = 0xFFFFFFFF;

public NativeFluid(String fluidName, NativeLocation still, NativeLocation flowing)
    {
        this(fluidName, still, flowing, (NativeLocation) null);
    }
public NativeFluid(String fluidName, NativeLocation still, NativeLocation flowing,  NativeLocation overlay)
    {
        this.fluidName = fluidName.toLowerCase(Locale.ENGLISH);
        this.unlocalizedName = fluidName;
        this.still = still;
        this.flowing = flowing;
        this.overlay = overlay;
    }
public NativeFluid setTranslationKey(String translationKey)
    {
        this.unlocalizedName = translationKey;
        return this;
    }
public NativeFluid setLuminosity(int luminosity)
    {
        this.luminosity = luminosity;
        return this;
    }
public NativeFluid setDensity(int density)
    {
        this.density = density;
        return this;
    }
public NativeFluid setTemperature(int temperature)
    {
        this.temperature = temperature;
        return this;
    }
public NativeFluid setViscosity(int viscosity)
    {
        this.viscosity = viscosity;
        return this;
    }
public NativeFluid setGaseous(boolean isGaseous)
    {
        this.isGaseous = isGaseous;
        return this;
    }
public NativeFluid setColor(int color)
    {
        this.color = color;
        return this;
    }
public final String getName()
    {
        return this.fluidName;
    }
public String getTranslationKey()
    {
        return getUnlocalizedName();
    }
public String getUnlocalizedName()
    {
        return "fluid." + this.unlocalizedName;
    }
public final int getLuminosity()
    {
        return this.luminosity;
    }
public final int getDensity()
    {
        return this.density;
    }
public final int getTemperature()
    {
        return this.temperature;
    }
public final int getViscosity()
    {
        return this.viscosity;
    }
public final boolean isGaseous()
    {
        return this.isGaseous;
    }
public int getColor()
    {
        return color;
    }
public NativeLocation getStill()
    {
        return still;
    }
public NativeLocation getFlowing()
    {
        return flowing;
    }
public NativeLocation getOverlay()
    {
        return overlay;
    }
public String getUnlocalizedName(NativeFluidStack stack)
    {
        return this.getUnlocalizedName();
    }
public String getTranslationKey(NativeFluidStack stack)
    {
        return getUnlocalizedName(stack);
    }
}
