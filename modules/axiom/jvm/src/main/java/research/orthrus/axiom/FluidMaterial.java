// Extracted from pinned GTCEu/Cleanroom source. See spec/native-fluids.md and sources/NOTICE.md.
package research.orthrus.axiom;
import com.google.common.collect.ImmutableList;
import java.util.*;
import java.util.function.Consumer;
import java.util.function.UnaryOperator;
class FluidMaterial extends MaterialState implements Comparable<FluidMaterial> {
    private final MaterialInfo materialInfo;
    private final MaterialFlags flags;
    private String chemicalFormula;
    private FluidMaterial(MaterialInfo info, MaterialProperties properties, MaterialFlags flags) {
        super(info.metaItemSubId, info.resourceLocation.getNamespace(), info.resourceLocation.getPath(),
              FluidEnvironment.current().runtime().materials()::canModifyMaterials, properties);
        this.materialInfo = info; this.flags = flags;
        registerMaterial();
    }
    // Original marker constructor deliberately leaves the property owner and
    // component list unset and performs no material registration/verification.
    protected FluidMaterial(NativeLocation location) {
        super(0, location.getNamespace(), location.getPath(),
              FluidEnvironment.current().runtime().materials()::canModifyMaterials,
              new MaterialProperties(), false);
        materialInfo = new MaterialInfo(0, location);
        materialInfo.iconSet = MaterialIconSet.DULL;
        flags = new MaterialFlags();
    }
    static FluidMaterial require(MaterialState value) {
        if (!(value instanceof FluidMaterial material)) throw Failure.unsupported("fluid.material", "Native material metadata is required");
        return material;
    }
private String calculateChemicalFormula() {
        if (chemicalFormula != null) return this.chemicalFormula;
        if (materialInfo.element != null) {
            return materialInfo.element.getSymbol();
        }
        if (!materialInfo.componentList.isEmpty()) {
            // prevent parenthesis around single component materials
            if (materialInfo.componentList.size() == 1) {
                MaterialStack stack = materialInfo.componentList.get(0);
                if (stack.amount == 1) {
                    return stack.material.getChemicalFormula();
                }
            }
            StringBuilder components = new StringBuilder();
            for (MaterialStack component : materialInfo.componentList)
                components.append(component.toFormatted());
            return components.toString();
        }
        return "";
    }
public String getChemicalFormula() {
        return chemicalFormula;
    }
public FluidMaterial setFormula(String formula) {
        return setFormula(formula, false);
    }
public FluidMaterial setFormula(String formula, boolean withFormatting) {
        this.chemicalFormula = withFormatting ? SmallDigits.toSmallDownNumbers(formula) : formula;
        return this;
    }
public ImmutableList<MaterialStack> getMaterialComponents() {
        return materialInfo.componentList;
    }
protected void registerMaterial() {
        verifyMaterial();
        FluidEnvironment.current().runtime().materials().getRegistry(getModid()).register(this);
    }
public void addFlags(MaterialFlag... flags) {
        if (FluidEnvironment.current().runtime().materials().canModifyMaterials()) {
            this.flags.addFlags(flags).verify(this);
        } else throw new IllegalStateException("Cannot add flag to material when registry is frozen!");
    }
public void addFlags(String... names) {
        addFlags(Arrays.stream(names)
                .map(MaterialFlag::getByName)
                .filter(Objects::nonNull)
                .toArray(MaterialFlag[]::new));
    }
public boolean hasFlag(MaterialFlag flag) {
        return flags.hasFlag(flag);
    }
public boolean isElement() {
        return materialInfo.element != null;
    }
public Element getElement() {
        return materialInfo.element;
    }
public boolean hasFlags(MaterialFlag... flags) {
        return Arrays.stream(flags).allMatch(this::hasFlag);
    }
public boolean hasAnyOfFlags(MaterialFlag... flags) {
        return Arrays.stream(flags).anyMatch(this::hasFlag);
    }
protected void calculateDecompositionType() {
        if (!materialInfo.componentList.isEmpty() &&
                !hasFlag(MaterialFlags.DECOMPOSITION_BY_CENTRIFUGING) &&
                !hasFlag(MaterialFlags.DECOMPOSITION_BY_ELECTROLYZING) &&
                !hasFlag(MaterialFlags.DISABLE_DECOMPOSITION)) {
            boolean onlyMetalMaterials = true;
            for (MaterialStack materialStack : materialInfo.componentList) {
                FluidMaterial material = materialStack.material;
                onlyMetalMaterials &= material.hasProperty(PropertyKey.INGOT);
            }
            // allow centrifuging of alloy materials only
            if (onlyMetalMaterials) {
                flags.addFlags(MaterialFlags.DECOMPOSITION_BY_CENTRIFUGING);
            } else {
                flags.addFlags(MaterialFlags.DECOMPOSITION_BY_ELECTROLYZING);
            }
        }
    }
public NativeFluid getFluid() {
        FluidProperty prop = getProperty(FluidDomain.FLUID);
        if (prop == null) {
            throw new IllegalArgumentException("Material " + getResourceLocation() + " does not have a Fluid!");
        }

        NativeFluid fluid = prop.get(prop.getPrimaryKey());
        if (fluid != null) return fluid;

        fluid = getFluid(FluidEnvironment.current().storageKeys().LIQUID);
        if (fluid != null) return fluid;

        return getFluid(FluidEnvironment.current().storageKeys().GAS);
    }
public NativeFluid getFluid( FluidStorageKey key) {
        FluidProperty prop = getProperty(FluidDomain.FLUID);
        if (prop == null) {
            throw new IllegalArgumentException("Material " + getResourceLocation() + " does not have a Fluid!");
        }

        return prop.get(key);
    }
public NativeFluidStack getFluid(int amount) {
        return new NativeFluidStack(getFluid(), amount);
    }
public NativeFluidStack getFluid( FluidStorageKey key, int amount) {
        return new NativeFluidStack(getFluid(key), amount);
    }
public NativeFluidStack getPlasma(int amount) {
        return getFluid(FluidEnvironment.current().storageKeys().PLASMA, amount);
    }
public int getBlockHarvestLevel() {
        if (!hasProperty(PropertyKey.DUST)) {
            throw new IllegalArgumentException(
                    "Material " + getResourceLocation() + " does not have a harvest level! Is probably a Fluid");
        }
        int harvestLevel = getProperty(PropertyKey.DUST).getHarvestLevel();
        return harvestLevel > 0 ? harvestLevel - 1 : harvestLevel;
    }
public int getToolHarvestLevel() {
        if (!hasProperty(PropertyKey.TOOL)) {
            throw new IllegalArgumentException("Material " + getResourceLocation() +
                    " does not have a tool harvest level! Is probably not a Tool Material");
        }
        return getProperty(PropertyKey.TOOL).getToolHarvestLevel();
    }
public void setMaterialRGB(int materialRGB) {
        materialInfo.color = materialRGB;
    }
public int getMaterialRGB() {
        return materialInfo.color;
    }
public void setMaterialIconSet(MaterialIconSet materialIconSet) {
        materialInfo.iconSet = materialIconSet;
    }
public MaterialIconSet getMaterialIconSet() {
        return materialInfo.iconSet;
    }
public boolean isRadioactive() {
        if (materialInfo.element != null)
            return materialInfo.element.halfLifeSeconds >= 0;
        for (MaterialStack material : materialInfo.componentList)
            if (material.material.isRadioactive()) return true;
        return false;
    }
public long getProtons() {
        if (materialInfo.element != null)
            return materialInfo.element.getProtons();
        if (materialInfo.componentList.isEmpty())
            return Math.max(1, Elements.Tc.getProtons());
        long totalProtons = 0, totalAmount = 0;
        for (MaterialStack material : materialInfo.componentList) {
            totalAmount += material.amount;
            totalProtons += material.amount * material.material.getProtons();
        }
        return totalProtons / totalAmount;
    }
public long getNeutrons() {
        if (materialInfo.element != null)
            return materialInfo.element.getNeutrons();
        if (materialInfo.componentList.isEmpty())
            return Elements.Tc.getNeutrons();
        long totalNeutrons = 0, totalAmount = 0;
        for (MaterialStack material : materialInfo.componentList) {
            totalAmount += material.amount;
            totalNeutrons += material.amount * material.material.getNeutrons();
        }
        return totalNeutrons / totalAmount;
    }
public long getMass() {
        if (materialInfo.element != null)
            return materialInfo.element.getMass();
        if (materialInfo.componentList.size() == 0)
            return Elements.Tc.getMass();
        long totalMass = 0, totalAmount = 0;
        for (MaterialStack material : materialInfo.componentList) {
            totalAmount += material.amount;
            totalMass += material.amount * material.material.getMass();
        }
        return totalMass / totalAmount;
    }
public int getBlastTemperature() {
        BlastProperty prop = properties.getProperty(FluidDomain.BLAST);
        return prop == null ? 0 : prop.getBlastTemperature();
    }
public String getName() {
        return getResourceLocation().getPath();
    }
public String getModid() {
        return getResourceLocation().getNamespace();
    }
public NativeLocation getResourceLocation() {
        return materialInfo.resourceLocation;
    }
public String toCamelCaseString() {
        return FluidSupport.lowerUnderscoreToUpperCamel(toString());
    }
public String getUnlocalizedName() {
        NativeLocation location = getResourceLocation();
        return location.getNamespace() + ".material." + location.getPath();
    }
public String getRegistryName() {
        NativeLocation location = getResourceLocation();
        return location.getNamespace() + ':' + location.getPath();
    }
public int compareTo(FluidMaterial material) {
        return getName().compareTo(material.getName());
    }
public String toString() {
        return getName();
    }
public int getId() {
        return materialInfo.metaItemSubId;
    }
public MaterialStack multiply(long amount) {
        return new MaterialStack(this, amount);
    }
public MaterialProperties getProperties() {
        return properties;
    }
public <T extends IMaterialProperty> boolean hasProperty(PropertyKey<T> key) {
        return getProperty(key) != null;
    }
public <T extends IMaterialProperty> T getProperty(PropertyKey<T> key) {
        return properties.getProperty(key);
    }
public <T extends IMaterialProperty> void setProperty(PropertyKey<T> key, IMaterialProperty property) {
        if (!FluidEnvironment.current().runtime().materials().canModifyMaterials()) {
            throw new IllegalStateException("Cannot add properties to a Material when registry is frozen!");
        }
        properties.setProperty(key, property);
        properties.verify();
    }
public boolean isSolid() {
        return hasProperty(PropertyKey.INGOT) || hasProperty(PropertyKey.GEM);
    }
public boolean hasFluid() {
        return hasProperty(FluidDomain.FLUID);
    }
public void verifyMaterial() {
        properties.verify();
        flags.verify(this);
        this.chemicalFormula = calculateChemicalFormula();
        calculateDecompositionType();
    }
public static class Builder {

        private final MaterialInfo materialInfo;
        private final MaterialProperties properties;
        private final MaterialFlags flags;

        private final List<Consumer<FluidMaterial>> postProcessors = new ArrayList<>(1);

        /*
         * The temporary list of components for this FluidMaterial.
         */
        private List<MaterialStack> composition = new ArrayList<>();

        /*
         * Temporary value to use to determine how to calculate default RGB
         */
        private boolean averageRGB = false;

        /**
         * Constructs a {@link FluidMaterial}. This Builder replaces the old constructors, and
         * no longer uses a class hierarchy, instead using a {@link MaterialProperties} system.
         *
         * @param id               The MetaItemSubID for this FluidMaterial. Must be unique.
         * @param resourceLocation The ModId and Name of this FluidMaterial. Will be formatted as "<modid>.material.<name>"
         *                         for the Translation Key.
         * @since GTCEu 2.0.0
         */
        public Builder(int id,  NativeLocation resourceLocation) {
            String name = resourceLocation.getPath();
            if (name.charAt(name.length() - 1) == '_') {
                throw new IllegalArgumentException("Material name cannot end with a '_'!");
            }
            materialInfo = new MaterialInfo(id, resourceLocation);
            properties = new MaterialProperties();
            flags = new MaterialFlags();
        }

        /*
         * FluidMaterial Types
         */

        /**
         * Add a {@link FluidProperty} to this FluidMaterial.<br>
         * Will be created as a {@link FluidStorageKeys#LIQUID}, with standard {@link FluidBuilder} defaults.
         * <p>
         * Can be called multiple times to add multiple fluids.
         */
        public Builder fluid() {
            return fluid(FluidEnvironment.current().storageKeys().LIQUID, new FluidBuilder());
        }

        /**
         * Add a {@link FluidProperty} to this FluidMaterial.<br>
         * Will be created with the specified state a with standard {@link FluidBuilder} defaults.
         * <p>
         * Can be called multiple times to add multiple fluids.
         */
        public Builder fluid( FluidStorageKey key,  FluidState state) {
            return fluid(key, new FluidBuilder().state(state));
        }

        /**
         * Add a {@link FluidProperty} to this FluidMaterial.<br>
         * <p>
         * Can be called multiple times to add multiple fluids.
         */
        public Builder fluid( FluidStorageKey key,  FluidBuilder builder) {
            properties.ensureSet(FluidDomain.FLUID);
            FluidProperty property = properties.getProperty(FluidDomain.FLUID);
            property.enqueueRegistration(key, builder);

            return this;
        }

        /**
         * Assign an existing fluid to this material. Useful for things like Lava and Water where MC
         * already has a fluid to use, or for cross-mod compatibility.
         *
         * @param fluid The existing liquid
         */
        public Builder fluid( NativeFluid fluid,  FluidStorageKey key,  FluidState state) {
            properties.ensureSet(FluidDomain.FLUID);
            FluidProperty property = properties.getProperty(FluidDomain.FLUID);
            property.store(key, fluid);

            postProcessors.add(
                    m -> FluidEnvironment.current().registerTooltip(fluid, m, state));
            return this;
        }

        /**
         * Add a liquid for this material.
         *
         * @see #fluid(FluidStorageKey, FluidState)
         */
        public Builder liquid() {
            return fluid(FluidEnvironment.current().storageKeys().LIQUID, FluidState.LIQUID);
        }

        /**
         * Add a liquid for this material.
         *
         * @see #fluid(FluidStorageKey, FluidState)
         */
        public Builder liquid( FluidBuilder builder) {
            return fluid(FluidEnvironment.current().storageKeys().LIQUID, builder.state(FluidState.LIQUID));
        }

        /**
         * Add a plasma for this material.
         *
         * @see #fluid(FluidStorageKey, FluidState)
         */
        public Builder plasma() {
            return fluid(FluidEnvironment.current().storageKeys().PLASMA, FluidState.PLASMA);
        }

        /**
         * Add a plasma for this material.
         *
         * @see #fluid(FluidStorageKey, FluidState)
         */
        public Builder plasma( FluidBuilder builder) {
            return fluid(FluidEnvironment.current().storageKeys().PLASMA, builder.state(FluidState.PLASMA));
        }

        /**
         * Add a gas for this material.
         *
         * @see #fluid(FluidStorageKey, FluidState)
         */
        public Builder gas() {
            return fluid(FluidEnvironment.current().storageKeys().GAS, FluidState.GAS);
        }

        /**
         * Add a gas for this material.
         *
         * @see #fluid(FluidStorageKey, FluidState)
         */
        public Builder gas( FluidBuilder builder) {
            return fluid(FluidEnvironment.current().storageKeys().GAS, builder.state(FluidState.GAS));
        }

        /**
         * Add a {@link DustProperty} to this FluidMaterial.<br>
         * Will be created with a Harvest Level of 2 and no Burn Time (Furnace Fuel).
         *
         * @throws IllegalArgumentException If a {@link DustProperty} has already been added to this FluidMaterial.
         */
        public Builder dust() {
            properties.ensureSet(PropertyKey.DUST);
            return this;
        }

        /**
         * Add a {@link DustProperty} to this FluidMaterial.<br>
         * Will be created with no Burn Time (Furnace Fuel).
         *
         * @param harvestLevel The Harvest Level of this block for Mining.<br>
         *                     If this FluidMaterial also has a {@link ToolProperty}, this value will
         *                     also be used to determine the tool's Mining Level.
         * @throws IllegalArgumentException If a {@link DustProperty} has already been added to this FluidMaterial.
         */
        public Builder dust(int harvestLevel) {
            return dust(harvestLevel, 0);
        }

        /**
         * Add a {@link DustProperty} to this FluidMaterial.
         *
         * @param harvestLevel The Harvest Level of this block for Mining.<br>
         *                     If this FluidMaterial also has a {@link ToolProperty}, this value will
         *                     also be used to determine the tool's Mining Level.
         * @param burnTime     The Burn Time (in ticks) of this FluidMaterial as a Furnace Fuel.
         * @throws IllegalArgumentException If a {@link DustProperty} has already been added to this FluidMaterial.
         */
        public Builder dust(int harvestLevel, int burnTime) {
            properties.setProperty(PropertyKey.DUST, new DustProperty(harvestLevel, burnTime));
            return this;
        }

        /**
         * Add a {@link WoodProperty} to this FluidMaterial.<br>
         * Will be created with a Harvest Level of 0 and a Burn Time of 300 (Furnace Fuel).
         */
        public Builder wood() {
            return wood(0, 300);
        }

        /**
         * Add a {@link WoodProperty} to this FluidMaterial.<br>
         * Will be created with a Burn Time of 300 (Furnace Fuel).
         *
         * @param harvestLevel The Harvest Level of this block for Mining.<br>
         *                     If this FluidMaterial also has a {@link ToolProperty}, this value will
         *                     also be used to determine the tool's Mining Level.
         */
        public Builder wood(int harvestLevel) {
            return wood(harvestLevel, 300);
        }

        /**
         * Add a {@link WoodProperty} to this FluidMaterial.
         *
         * @param harvestLevel The Harvest Level of this block for Mining.<br>
         *                     If this FluidMaterial also has a {@link ToolProperty}, this value will
         *                     also be used to determine the tool's Mining Level.
         * @param burnTime     The Burn Time (in ticks) of this FluidMaterial as a Furnace Fuel.
         */
        public Builder wood(int harvestLevel, int burnTime) {
            properties.setProperty(PropertyKey.DUST, new DustProperty(harvestLevel, burnTime));
            properties.setProperty(PropertyKey.WOOD, new WoodProperty());
            return this;
        }

        /**
         * Add an {@link IngotProperty} to this FluidMaterial.<br>
         * Will be created with a Harvest Level of 2 and no Burn Time (Furnace Fuel).<br>
         * Will automatically add a {@link DustProperty} to this FluidMaterial if it does not already have one.
         *
         * @throws IllegalArgumentException If an {@link IngotProperty} has already been added to this FluidMaterial.
         */
        public Builder ingot() {
            properties.ensureSet(PropertyKey.INGOT);
            return this;
        }

        /**
         * Add an {@link IngotProperty} to this FluidMaterial.<br>
         * Will be created with no Burn Time (Furnace Fuel).<br>
         * Will automatically add a {@link DustProperty} to this FluidMaterial if it does not already have one.
         *
         * @param harvestLevel The Harvest Level of this block for Mining. 2 will make it require a iron tool.<br>
         *                     If this FluidMaterial also has a {@link ToolProperty}, this value will
         *                     also be used to determine the tool's Mining level (-1). So 2 will make the tool harvest
         *                     diamonds.<br>
         *                     If this FluidMaterial already had a Harvest Level defined, it will be overridden.
         * @throws IllegalArgumentException If an {@link IngotProperty} has already been added to this FluidMaterial.
         */
        public Builder ingot(int harvestLevel) {
            return ingot(harvestLevel, 0);
        }

        /**
         * Add an {@link IngotProperty} to this FluidMaterial.<br>
         * Will automatically add a {@link DustProperty} to this FluidMaterial if it does not already have one.
         *
         * @param harvestLevel The Harvest Level of this block for Mining. 2 will make it require a iron tool.<br>
         *                     If this FluidMaterial also has a {@link ToolProperty}, this value will
         *                     also be used to determine the tool's Mining level (-1). So 2 will make the tool harvest
         *                     diamonds.<br>
         *                     If this FluidMaterial already had a Harvest Level defined, it will be overridden.
         * @param burnTime     The Burn Time (in ticks) of this FluidMaterial as a Furnace Fuel.<br>
         *                     If this FluidMaterial already had a Burn Time defined, it will be overridden.
         * @throws IllegalArgumentException If an {@link IngotProperty} has already been added to this FluidMaterial.
         */
        public Builder ingot(int harvestLevel, int burnTime) {
            DustProperty prop = properties.getProperty(PropertyKey.DUST);
            if (prop == null) dust(harvestLevel, burnTime);
            else {
                if (prop.getHarvestLevel() == 2) prop.setHarvestLevel(harvestLevel);
                if (prop.getBurnTime() == 0) prop.setBurnTime(burnTime);
            }
            properties.ensureSet(PropertyKey.INGOT);
            return this;
        }

        /**
         * Add a {@link GemProperty} to this FluidMaterial.<br>
         * Will be created with a Harvest Level of 2 and no Burn Time (Furnace Fuel).<br>
         * Will automatically add a {@link DustProperty} to this FluidMaterial if it does not already have one.
         *
         * @throws IllegalArgumentException If a {@link GemProperty} has already been added to this FluidMaterial.
         */
        public Builder gem() {
            properties.ensureSet(PropertyKey.GEM);
            return this;
        }

        /**
         * Add a {@link GemProperty} to this FluidMaterial.<br>
         * Will be created with no Burn Time (Furnace Fuel).<br>
         * Will automatically add a {@link DustProperty} to this FluidMaterial if it does not already have one.
         *
         * @param harvestLevel The Harvest Level of this block for Mining.<br>
         *                     If this FluidMaterial also has a {@link ToolProperty}, this value will
         *                     also be used to determine the tool's Mining level.<br>
         *                     If this FluidMaterial already had a Harvest Level defined, it will be overridden.
         * @throws IllegalArgumentException If a {@link GemProperty} has already been added to this FluidMaterial.
         */
        public Builder gem(int harvestLevel) {
            return gem(harvestLevel, 0);
        }

        /**
         * Add a {@link GemProperty} to this FluidMaterial.<br>
         * Will automatically add a {@link DustProperty} to this FluidMaterial if it does not already have one.
         *
         * @param harvestLevel The Harvest Level of this block for Mining.<br>
         *                     If this FluidMaterial also has a {@link ToolProperty}, this value will
         *                     also be used to determine the tool's Mining level.<br>
         *                     If this FluidMaterial already had a Harvest Level defined, it will be overridden.
         * @param burnTime     The Burn Time (in ticks) of this FluidMaterial as a Furnace Fuel.<br>
         *                     If this FluidMaterial already had a Burn Time defined, it will be overridden.
         */
        public Builder gem(int harvestLevel, int burnTime) {
            DustProperty prop = properties.getProperty(PropertyKey.DUST);
            if (prop == null) dust(harvestLevel, burnTime);
            else {
                if (prop.getHarvestLevel() == 2) prop.setHarvestLevel(harvestLevel);
                if (prop.getBurnTime() == 0) prop.setBurnTime(burnTime);
            }
            properties.ensureSet(PropertyKey.GEM);
            return this;
        }

        /**
         * Add a {@link PolymerProperty} to this FluidMaterial.<br>
         * Will be created with a Harvest Level of 2 and no Burn Time (Furnace Fuel).<br>
         * Will automatically add a {@link DustProperty} to this FluidMaterial if it does not already have one.
         *
         * @throws IllegalArgumentException If an {@link PolymerProperty} has already been added to this FluidMaterial.
         */
        public Builder polymer() {
            properties.ensureSet(PropertyKey.POLYMER);
            return this;
        }

        /**
         * Add a {@link PolymerProperty} to this FluidMaterial.<br>
         * Will automatically add a {@link DustProperty} to this FluidMaterial if it does not already have one.
         * Will have a burn time of 0
         *
         * @param harvestLevel The Harvest Level of this block for Mining.<br>
         *                     If this FluidMaterial also has a {@link ToolProperty}, this value will
         *                     also be used to determine the tool's Mining level.<br>
         *                     If this FluidMaterial already had a Harvest Level defined, it will be overridden.
         * @throws IllegalArgumentException If an {@link PolymerProperty} has already been added to this FluidMaterial.
         */
        public Builder polymer(int harvestLevel) {
            DustProperty prop = properties.getProperty(PropertyKey.DUST);
            if (prop == null) dust(harvestLevel, 0);
            else if (prop.getHarvestLevel() == 2) prop.setHarvestLevel(harvestLevel);
            properties.ensureSet(PropertyKey.POLYMER);
            return this;
        }

        public Builder burnTime(int burnTime) {
            DustProperty prop = properties.getProperty(PropertyKey.DUST);
            if (prop == null) {
                dust();
                prop = properties.getProperty(PropertyKey.DUST);
            }
            prop.setBurnTime(burnTime);
            return this;
        }

        /**
         * Set the Color of this FluidMaterial.<br>
         * Defaults to 0xFFFFFF unless {@link Builder#colorAverage()} was called, where
         * it will be a weighted average of the components of the FluidMaterial.
         *
         * @param color The RGB-formatted Color.
         */
        public Builder color(int color) {
            this.materialInfo.color = color;
            return this;
        }

        public Builder colorAverage() {
            this.averageRGB = true;
            return this;
        }

        /**
         * Set the {@link MaterialIconSet} of this FluidMaterial.<br>
         * Defaults vary depending on if the FluidMaterial has a:<br>
         * <ul>
         * <li>{@link GemProperty}, it will default to {@link MaterialIconSet#GEM_VERTICAL}
         * <li>{@link IngotProperty} or {@link DustProperty}, it will default to {@link MaterialIconSet#DULL}
         * <li>{@link FluidProperty}, it will default to {@link MaterialIconSet#FLUID}
         * </ul>
         * Default will be determined by first-found Property in this order, unless specified.
         *
         * @param iconSet The {@link MaterialIconSet} of this FluidMaterial.
         */
        public Builder iconSet(MaterialIconSet iconSet) {
            materialInfo.iconSet = iconSet;
            return this;
        }

        public Builder components(Object... components) {
            FluidSupport.checkArgument(
                    components.length % 2 == 0,
                    "Material Components list malformed!");

            for (int i = 0; i < components.length; i += 2) {
                if (components[i] == null) {
                    throw new IllegalArgumentException(
                            "Material in Components List is null for Material " + this.materialInfo.resourceLocation);
                }
                composition.add(new MaterialStack(
                        (FluidMaterial) components[i],
                        (Integer) components[i + 1]));
            }
            return this;
        }

        public Builder components(MaterialStack... components) {
            composition = Arrays.asList(components);
            return this;
        }

        public Builder components(ImmutableList<MaterialStack> components) {
            composition = components;
            return this;
        }

        /**
         * Add {@link MaterialFlags} to this FluidMaterial.<br>
         * Dependent Flags (for example, {@link MaterialFlags#GENERATE_LONG_ROD} requiring
         * {@link MaterialFlags#GENERATE_ROD}) will be automatically applied.
         */
        public Builder flags(MaterialFlag... flags) {
            this.flags.addFlags(flags);
            return this;
        }

        /**
         * Add {@link MaterialFlags} to this FluidMaterial.<br>
         * Dependent Flags (for example, {@link MaterialFlags#GENERATE_LONG_ROD} requiring
         * {@link MaterialFlags#GENERATE_ROD}) will be automatically applied.
         *
         * @param f1 A {@link Collection} of {@link MaterialFlag}. Provided this way for easy Flag presets to be
         *           applied.
         * @param f2 An Array of {@link MaterialFlag}. If no {@link Collection} is required, use
         *           {@link Builder#flags(MaterialFlag...)}.
         */
        public Builder flags(Collection<MaterialFlag> f1, MaterialFlag... f2) {
            this.flags.addFlags(f1.toArray(new MaterialFlag[0]));
            this.flags.addFlags(f2);
            return this;
        }

        public Builder element(Element element) {
            this.materialInfo.element = element;
            return this;
        }

        /**
         * Replaced the old toolStats methods which took many parameters.
         * Use {@link ToolProperty.Builder} instead to create a Tool Property.
         */
        public Builder toolStats(ToolProperty toolProperty) {
            properties.setProperty(PropertyKey.TOOL, toolProperty);
            return this;
        }

        public Builder rotorStats(float speed, float damage, int durability) {
            properties.setProperty(PropertyKey.ROTOR, new RotorProperty(speed, damage, durability));
            return this;
        }

        /** @deprecated use {@link FluidMaterial.Builder#blast(int)}. */

        @Deprecated
        public Builder blastTemp(int temp) {
            return blast(temp);
        }

        /** @deprecated use {@link FluidMaterial.Builder#blast(int, BlastProperty.GasTier)}. */

        @Deprecated
        public Builder blastTemp(int temp, BlastProperty.GasTier gasTier) {
            return blast(temp, gasTier);
        }

        /** @deprecated use {@link FluidMaterial.Builder#blast(UnaryOperator)} for more detailed stats. */

        @Deprecated
        public Builder blastTemp(int temp, BlastProperty.GasTier gasTier, int eutOverride) {
            return blast(b -> b.temp(temp, gasTier).blastStats(eutOverride));
        }

        /** @deprecated use {@link FluidMaterial.Builder#blast(UnaryOperator)} for more detailed stats. */

        @Deprecated
        public Builder blastTemp(int temp, BlastProperty.GasTier gasTier, int eutOverride, int durationOverride) {
            return blast(b -> b.temp(temp, gasTier).blastStats(eutOverride, durationOverride));
        }

        public Builder blast(int temp) {
            properties.setProperty(FluidDomain.BLAST, new BlastProperty(temp));
            return this;
        }

        public Builder blast(int temp, BlastProperty.GasTier gasTier) {
            properties.setProperty(FluidDomain.BLAST, new BlastProperty(temp, gasTier));
            return this;
        }

        public Builder blast(UnaryOperator<BlastProperty.Builder> b) {
            properties.setProperty(FluidDomain.BLAST, b.apply(new BlastProperty.Builder()).build());
            return this;
        }

        public Builder ore() {
            properties.ensureSet(PropertyKey.ORE);
            return this;
        }

        public Builder ore(boolean emissive) {
            properties.setProperty(PropertyKey.ORE, new OreProperty(1, 1, emissive));
            return this;
        }

        public Builder ore(int oreMultiplier, int byproductMultiplier) {
            properties.setProperty(PropertyKey.ORE, new OreProperty(oreMultiplier, byproductMultiplier));
            return this;
        }

        public Builder ore(int oreMultiplier, int byproductMultiplier, boolean emissive) {
            properties.setProperty(PropertyKey.ORE, new OreProperty(oreMultiplier, byproductMultiplier, emissive));
            return this;
        }

        public Builder washedIn(FluidMaterial m) {
            properties.ensureSet(PropertyKey.ORE);
            properties.getProperty(PropertyKey.ORE).setWashedIn(m);
            return this;
        }

        public Builder washedIn(FluidMaterial m, int washedAmount) {
            properties.ensureSet(PropertyKey.ORE);
            properties.getProperty(PropertyKey.ORE).setWashedIn(m, washedAmount);
            return this;
        }

        public Builder separatedInto(FluidMaterial... m) {
            properties.ensureSet(PropertyKey.ORE);
            properties.getProperty(PropertyKey.ORE).setSeparatedInto(m);
            return this;
        }

        public Builder oreSmeltInto(FluidMaterial m) {
            properties.ensureSet(PropertyKey.ORE);
            properties.getProperty(PropertyKey.ORE).setDirectSmeltResult(m);
            return this;
        }

        public Builder polarizesInto(FluidMaterial m) {
            properties.ensureSet(PropertyKey.INGOT);
            properties.getProperty(PropertyKey.INGOT).setMagneticMaterial(m);
            return this;
        }

        public Builder arcSmeltInto(FluidMaterial m) {
            properties.ensureSet(PropertyKey.INGOT);
            properties.getProperty(PropertyKey.INGOT).setArcSmeltingInto(m);
            return this;
        }

        public Builder macerateInto(FluidMaterial m) {
            properties.ensureSet(PropertyKey.INGOT);
            properties.getProperty(PropertyKey.INGOT).setMacerateInto(m);
            return this;
        }

        public Builder ingotSmeltInto(FluidMaterial m) {
            properties.ensureSet(PropertyKey.INGOT);
            properties.getProperty(PropertyKey.INGOT).setSmeltingInto(m);
            return this;
        }

        public Builder addOreByproducts(FluidMaterial... byproducts) {
            properties.ensureSet(PropertyKey.ORE);
            properties.getProperty(PropertyKey.ORE).addOreByProducts(byproducts);
            return this;
        }

        public Builder cableProperties(long voltage, int amperage, int loss) {
            cableProperties((int) voltage, amperage, loss, false);
            return this;
        }

        public Builder cableProperties(long voltage, int amperage, int loss, boolean isSuperCon) {
            properties.setProperty(PropertyKey.WIRE, new WireProperties((int) voltage, amperage, loss, isSuperCon));
            return this;
        }

        public Builder cableProperties(long voltage, int amperage, int loss, boolean isSuperCon,
                                       int criticalTemperature) {
            properties.setProperty(PropertyKey.WIRE,
                    new WireProperties((int) voltage, amperage, loss, isSuperCon, criticalTemperature));
            return this;
        }

        public Builder fluidPipeProperties(int maxTemp, int throughput, boolean gasProof) {
            return fluidPipeProperties(maxTemp, throughput, gasProof, false, false, false);
        }

        public Builder fluidPipeProperties(int maxTemp, int throughput, boolean gasProof, boolean acidProof,
                                           boolean cryoProof, boolean plasmaProof) {
            properties.setProperty(PropertyKey.FLUID_PIPE,
                    new FluidPipeProperties(maxTemp, throughput, gasProof, acidProof, cryoProof, plasmaProof));
            return this;
        }

        public Builder itemPipeProperties(int priority, float stacksPerSec) {
            properties.setProperty(PropertyKey.ITEM_PIPE, new ItemPipeProperties(priority, stacksPerSec));
            return this;
        }

        // TODO Clean this up post 2.5 release
        @Deprecated
        public Builder addDefaultEnchant(EnchantmentIdentity enchant, int level) {
            if (!properties.hasProperty(PropertyKey.TOOL)) // cannot assign default here
                throw new IllegalArgumentException("Material cannot have an Enchant without Tools!");
            properties.getProperty(PropertyKey.TOOL).addEnchantmentForTools(enchant, level);
            return this;
        }

        public FluidMaterial build() {
            materialInfo.componentList = ImmutableList.copyOf(composition);
            materialInfo.verifyInfo(properties, averageRGB);
            FluidMaterial m = new FluidMaterial(materialInfo, properties, flags);
            if (!postProcessors.isEmpty()) postProcessors.forEach(p -> p.accept(m));
            return m;
        }
    }
private static class MaterialInfo {

        /**
         * The modid and unlocalized name of this FluidMaterial.
         * <p>
         * Required.
         */
        private final NativeLocation resourceLocation;

        /**
         * The MetaItem ID of this FluidMaterial.
         * <p>
         * Required.
         */
        private final int metaItemSubId;

        /**
         * The color of this FluidMaterial.
         * <p>
         * Default: 0xFFFFFF if no Components, otherwise it will be the average of Components.
         */
        private int color = -1;

        /**
         * The IconSet of this FluidMaterial.
         * <p>
         * Default: - GEM_VERTICAL if it has GemProperty.
         * - DULL if has DustProperty or IngotProperty.
         * - FLUID if only has FluidProperty.
         */
        private MaterialIconSet iconSet;

        /**
         * The components of this FluidMaterial.
         * <p>
         * Default: none.
         */
        private ImmutableList<MaterialStack> componentList;

        /**
         * The Element of this FluidMaterial, if it is a direct Element.
         * <p>
         * Default: none.
         */
        private Element element;

        private MaterialInfo(int metaItemSubId,  NativeLocation resourceLocation) {
            this.metaItemSubId = metaItemSubId;
            String name = resourceLocation.getPath();
            if (!FluidSupport.toLowerCaseUnderscore(FluidSupport.lowerUnderscoreToUpperCamel(name)).equals(name)) {
                throw new IllegalArgumentException(
                        "Cannot add materials with names like 'materialnumber'! Use 'material_number' instead.");
            }
            this.resourceLocation = resourceLocation;
        }

        private void verifyInfo(MaterialProperties p, boolean averageRGB) {
            // Verify IconSet
            if (iconSet == null) {
                if (p.hasProperty(PropertyKey.GEM)) {
                    iconSet = MaterialIconSet.GEM_VERTICAL;
                } else if (p.hasProperty(PropertyKey.DUST) || p.hasProperty(PropertyKey.INGOT) ||
                        p.hasProperty(PropertyKey.POLYMER)) {
                            iconSet = MaterialIconSet.DULL;
                        } else
                    if (p.hasProperty(FluidDomain.FLUID)) {
                        iconSet = MaterialIconSet.FLUID;
                    } else {
                        iconSet = MaterialIconSet.DULL;
                    }
            }

            // Verify MaterialRGB
            if (color == -1) {
                if (!averageRGB || componentList.isEmpty())
                    color = 0xFFFFFF;
                else {
                    long colorTemp = 0;
                    int divisor = 0;
                    for (MaterialStack stack : componentList) {
                        colorTemp += stack.material.getMaterialRGB() * stack.amount;
                        divisor += stack.amount;
                    }
                    color = (int) (colorTemp / divisor);
                }
            }
        }
    }
}
