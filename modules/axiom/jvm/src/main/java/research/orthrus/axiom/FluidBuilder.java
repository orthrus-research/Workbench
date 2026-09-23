// Extracted from pinned GTCEu/Cleanroom source. See spec/native-fluids.md and sources/NOTICE.md.
package research.orthrus.axiom;




import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;

import static research.orthrus.axiom.FluidConstants.*;

class FluidBuilder implements FluidRegistration.Builder<FluidStorageKey, NativeFluid> {

    private static final int INFER_TEMPERATURE = -1;
    private static final int INFER_COLOR = 0xFFFFFFFF;
    private static final int INFER_DENSITY = -1;
    private static final int INFER_LUMINOSITY = -1;
    private static final int INFER_VISCOSITY = -1;

    private String name = null;
    private String translationKey = null;

    private final Collection<FluidAttribute> attributes = new ArrayList<>();

    private FluidState state = null;
    private int temperature = INFER_TEMPERATURE;
    private int color = INFER_COLOR;
    private boolean isColorEnabled = true;
    private int density = INFER_DENSITY;
    private int luminosity = INFER_LUMINOSITY;
    private int viscosity = INFER_VISCOSITY;

    private NativeLocation still = null;
    private NativeLocation flowing = null;
    private boolean hasCustomStill = false;
    private boolean hasCustomFlowing = false;

    private boolean hasFluidBlock = false;
    private boolean hasBucket = true;
    private String alternativeName = null;

    public FluidBuilder() {}

    /**
     * @param name the name of the fluid
     * @return this
     */
    public  FluidBuilder name( String name) {
        this.name = name;
        return this;
    }

    /**
     * @param translationKey the translation key of the fluid
     * @return this
     */
    public  FluidBuilder translation( String translationKey) {
        this.translationKey = translationKey;
        return this;
    }

    /**
     * @param state the fluid's state of matter
     * @return this
     */
    public  FluidBuilder state( FluidState state) {
        this.state = state;
        return this;
    }

    /**
     * @param temperature the temperature of the fluid in Kelvin
     * @return this;
     */
    public  FluidBuilder temperature(int temperature) {
        FluidSupport.checkArgument(temperature > 0, "temperature must be > 0");
        this.temperature = temperature;
        return this;
    }

    /**
     * The color may be in either {@code RGB} or {@code ARGB} format.
     * RGB format will assume an alpha of {@code 0xFF}.
     *
     * @param color the color
     * @return this
     */
    public  FluidBuilder color(int color) {
        this.color = FluidSupport.convertRGBtoARGB(color);
        if (this.color == INFER_COLOR) {
            return disableColor();
        }
        return this;
    }

    /**
     * Disables coloring the fluid. A color should still be specified.
     *
     * @return this
     */
    public  FluidBuilder disableColor() {
        this.isColorEnabled = false;
        return this;
    }

    /**
     * @param mcDensity the density in MC's units
     * @return this
     */
    public  FluidBuilder density(int mcDensity) {
        this.density = mcDensity;
        return this;
    }

    /**
     * @param density the density in g/cm^3
     * @return this
     */
    public  FluidBuilder density(double density) {
        return density(convertToMCDensity(density));
    }

    /**
     * Converts a density value in g/cm^3 to an MC fluid density by comparison to air's density.
     *
     * @param density the density to convert
     * @return the MC integer density
     */
    private static int convertToMCDensity(double density) {
        // conversion formula from GT6
        if (density > 0.001225) {
            return (int) (1000 * density);
        } else if (density < 0.001225) {
            return (int) (-0.1 / density);
        }
        return 0;
    }

    /**
     * @param luminosity of the fluid from [0, 16)
     * @return this
     */
    public  FluidBuilder luminosity(int luminosity) {
        FluidSupport.checkArgument(luminosity >= 0 && luminosity < 16, "luminosity must be >= 0 and < 16");
        this.luminosity = luminosity;
        return this;
    }

    /**
     * @param mcViscosity the MC viscosity of the fluid
     * @return this
     */
    public  FluidBuilder viscosity(int mcViscosity) {
        FluidSupport.checkArgument(mcViscosity >= 0, "viscosity must be >= 0");
        this.viscosity = mcViscosity;
        return this;
    }

    /**
     * @param viscosity the viscosity of the fluid in Poise
     * @return this
     */
    public  FluidBuilder viscosity(double viscosity) {
        return viscosity(convertViscosity(viscosity));
    }

    /**
     * Converts viscosity in Poise to MC viscosity
     *
     * @param viscosity the viscosity to convert
     * @return the converted value
     */
    private static int convertViscosity(double viscosity) {
        return (int) (viscosity * 10000);
    }

    /**
     * @param attribute the attribute to add
     * @return this
     */
    public  FluidBuilder attribute( FluidAttribute attribute) {
        this.attributes.add(attribute);
        return this;
    }

    /**
     * @param attributes the attributes to add
     * @return this
     */
    public  FluidBuilder attributes( FluidAttribute ... attributes) {
        Collections.addAll(this.attributes, attributes);
        return this;
    }

    /**
     * @param name Alternative registry name for this fluid to look for
     * @return this
     */
    public  FluidBuilder alternativeName( String name) {
        this.alternativeName = name;
        return this;
    }

    /**
     * Mark this fluid as having a custom still texture
     *
     * @return this
     */
    public  FluidBuilder customStill() {
        this.hasCustomStill = true;
        this.isColorEnabled = false;
        return this;
    }

    /**
     * Mark this fluid as having a custom flowing texture
     *
     * @return this
     */
    public  FluidBuilder customFlow() {
        this.hasCustomFlowing = true;
        this.isColorEnabled = false;
        return this;
    }

    /**
     * @param hasCustomStill   if the fluid has a custom still texture
     * @param hasCustomFlowing if the fluid has a custom flowing texture
     * @return this
     */
    public  FluidBuilder textures(boolean hasCustomStill, boolean hasCustomFlowing) {
        this.hasCustomStill = hasCustomStill;
        this.hasCustomFlowing = hasCustomFlowing;
        this.isColorEnabled = false;
        return this;
    }

    /**
     * Generate a fluid block for the fluid
     *
     * @return this
     */
    public  FluidBuilder block() {
        this.hasFluidBlock = true;
        return this;
    }

    /**
     * Disables the auto-generated fluid bucket for the fluid
     *
     * @return this
     */
    public  FluidBuilder disableBucket() {
        this.hasBucket = false;
        return this;
    }

    public NativeFluid build(String modid, MaterialState carrier, FluidStorageKey key) {
        FluidMaterial material = carrier == null ? null : FluidMaterial.require(carrier);
        determineName(material, key);
        determineTextures(material, key, modid);

        if (name == null) {
            throw new IllegalStateException("Could not determine fluid name");
        }

        if (state == null) {
            if (key != null && key.getDefaultFluidState() != null) {
                state = key.getDefaultFluidState();
            } else {
                state = FluidState.LIQUID; // default fallback
            }
        }

        // try to find an already registered fluid that we can use instead of a new one
        NativeFluid fluid = FluidEnvironment.current().registry().getFluid(name);
        if (fluid == null && alternativeName != null) {
            // try to use alternative fluid name if needed
            fluid = FluidEnvironment.current().registry().getFluid(alternativeName);
        }

        boolean needsRegistration = false;
        if (fluid == null) {
            needsRegistration = true;
            if (material == null) {
                fluid = new GTFluid(name, still, flowing, state);
            } else if (key != null) {
                if (translationKey == null) {
                    translationKey = key.getTranslationKeyFor(material);
                }
                fluid = new GTFluid.GTMaterialFluid(name, still, flowing, state, translationKey, material);
            } else {
                throw new IllegalArgumentException("Fluids with materials must have a FluidStorageKey");
            }
        }

        if (fluid instanceof AttributedFluid attrFluid) {
            attributes.forEach(attrFluid::addAttribute);
        } else if (!attributes.isEmpty()) {
            FluidEnvironment.current().logger()
                    .warn("Unable to set Fluid Attributes for Fluid {}, as it is owned by another mod! Skipping...");
        }

        determineTemperature(material);
        fluid.setTemperature(temperature);

        determineColor(material);
        if (isColorEnabled) {
            fluid.setColor(color);
        }

        determineDensity();
        fluid.setDensity(density);

        determineLuminosity(material);
        fluid.setLuminosity(luminosity);

        determineViscosity(material);
        fluid.setViscosity(viscosity);

        if (needsRegistration) {
            FluidEnvironment.current().registration().registerFluid(fluid, modid, hasBucket);
        } else if (hasBucket) {
            // In case it didn't have it before, but now it does
            FluidEnvironment.current().registry().addBucketForFluid(fluid);
        }

        if (material != null) {
            FluidEnvironment.current().unifier().registerFluid(fluid, material);
        }

        FluidEnvironment.current().registerTooltip(fluid, material, state);

        if (hasFluidBlock) {
            throw Failure.unsupported("fluid.world-block", "Fluid block construction/update is not implemented; earlier effects are retained");
        }

        // register cross mod compat for colors
        if (FluidEnvironment.current().topAddonsLoaded()) {
            int displayColor = isColorEnabled || material == null ? color : material.getMaterialRGB();
            FluidEnvironment.current().addonColors().put(name, displayColor);
        }

        return fluid;
    }

    private void determineName( FluidMaterial material,  FluidStorageKey key) {
        if (name != null) return;
        if (material == null || key == null) throw new IllegalArgumentException("Fluid must have a name");
        name = key.getRegistryNameFor(material);
    }

    private void determineTextures( FluidMaterial material,  FluidStorageKey key,  String modid) {
        if (material != null && key != null) {
            if (hasCustomStill) {
                still = new NativeLocation(modid, "blocks/fluids/fluid." + name);
            } else {
                still = key.getIconType().getBlockTexturePath(material.getMaterialIconSet());
            }
        } else {
            still = new NativeLocation(modid, "blocks/fluids/fluid." + name);
        }

        if (hasCustomFlowing) {
            flowing = new NativeLocation(modid, "blocks/fluids/fluid." + name + "_flow");
        } else {
            flowing = still;
        }
    }

    private void determineTemperature( FluidMaterial material) {
        if (temperature != INFER_TEMPERATURE) return;
        if (material == null) {
            temperature = ROOM_TEMPERATURE;
        } else {
            BlastProperty property = material.getProperty(FluidDomain.BLAST);
            if (property == null) {
                temperature = switch (state) {
                    case LIQUID -> {
                        if (material.hasProperty(PropertyKey.DUST)) {
                            yield SOLID_LIQUID_TEMPERATURE;
                        }
                        yield ROOM_TEMPERATURE;
                    }
                    case GAS -> ROOM_TEMPERATURE;
                    case PLASMA -> {
                        if (material.hasFluid() && material.getFluid() != null) {
                            yield BASE_PLASMA_TEMPERATURE + material.getFluid().getTemperature();
                        }
                        yield BASE_PLASMA_TEMPERATURE;
                    }
                };
            } else {
                temperature = property.getBlastTemperature() + switch (state) {
                    case LIQUID -> LIQUID_TEMPERATURE_OFFSET;
                    case GAS -> GAS_TEMPERATURE_OFFSET;
                    case PLASMA -> BASE_PLASMA_TEMPERATURE;
                };
            }
        }
    }

    private void determineColor( FluidMaterial material) {
        if (color != INFER_COLOR) return;
        if (isColorEnabled && material != null) {
            color = FluidSupport.convertRGBtoARGB(material.getMaterialRGB());
        }
    }

    private void determineDensity() {
        if (density != INFER_DENSITY) return;
        density = switch (state) {
            case LIQUID -> DEFAULT_LIQUID_DENSITY;
            case GAS -> DEFAULT_GAS_DENSITY;
            case PLASMA -> DEFAULT_PLASMA_DENSITY;
        };
    }

    private void determineLuminosity( FluidMaterial material) {
        if (luminosity != INFER_LUMINOSITY) return;
        if (state == FluidState.PLASMA) {
            luminosity = 15;
        } else if (material != null) {
            if (material.hasFlag(MaterialFlags.GLOWING)) {
                luminosity = 15;
            } else if (state == FluidState.LIQUID && material.hasProperty(PropertyKey.DUST)) {
                // liquids only glow if not phosphorescent
                luminosity = 10;
            }
        }
    }

    private void determineViscosity( FluidMaterial material) {
        if (viscosity != INFER_VISCOSITY) return;
        viscosity = switch (state) {
            case LIQUID -> {
                if (material != null && material.hasFlag(MaterialFlags.STICKY)) {
                    yield STICKY_LIQUID_VISCOSITY;
                }
                yield DEFAULT_LIQUID_VISCOSITY;
            }
            case GAS -> DEFAULT_GAS_VISCOSITY;
            case PLASMA -> DEFAULT_PLASMA_VISCOSITY;
        };
    }
}
