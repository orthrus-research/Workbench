// Extracted from pinned GTCEu/Cleanroom source. See spec/native-fluids.md and sources/NOTICE.md.
package research.orthrus.axiom;




class FluidProperty implements IMaterialProperty {

    private final FluidRegistration<FluidStorageKey, NativeFluid> storage = FluidEnvironment.current().newStorage();
    private FluidStorageKey primaryKey = null;
    private  NativeFluid solidifyingFluid = null;

    public FluidProperty() {}

    /**
     * Helper constructor which automatically calls {@link #enqueueRegistration(FluidStorageKey, FluidBuilder)} for a
     * builder.
     * <p>
     * This is primarily useful for adding FluidProperties to materials after they are registered with a single fluid
     * stored.
     *
     * @param key     the fluid storage key to store the builder with
     * @param builder the builder to enqueue
     */
    public FluidProperty( FluidStorageKey key,  FluidBuilder builder) {
        enqueueRegistration(key, builder);
    }






    /**
     * @see FluidStorageImpl#registerFluids(FluidMaterial)
     */

    public void registerFluids( FluidMaterial material) {
        this.storage.registerFluids(material);
    }


    public void enqueueRegistration( FluidStorageKey key,  FluidBuilder builder) {
        storage.enqueueRegistration(key, builder);
        if (primaryKey == null) {
            primaryKey = key;
        }
    }


    public void store( FluidStorageKey key,  NativeFluid fluid) {
        storage.store(key, fluid);
        if (primaryKey == null) {
            primaryKey = key;
        }
    }


    public  NativeFluid get( FluidStorageKey key) {
        return storage.get(key);
    }


    public  FluidBuilder getQueuedBuilder( FluidStorageKey key) {
        return (FluidBuilder) storage.getQueuedBuilder(key);
    }

    /**
     *
     * @return the key the fluid is stored with primarily
     */
    public  FluidStorageKey getPrimaryKey() {
        return primaryKey;
    }

    /**
     * @param primaryKey the key to use primarily
     */
    public void setPrimaryKey( FluidStorageKey primaryKey) {
        this.primaryKey = primaryKey;
    }


    public void verifyProperty(MaterialProperties properties) {
        if (this.primaryKey == null) {
            throw new IllegalStateException("FluidProperty cannot be empty");
        }
    }

    /**
     * @return the NativeFluid which solidifies into the material.
     */
    public  NativeFluid solidifiesFrom() {
        if (this.solidifyingFluid == null) {
            return storage.get(FluidEnvironment.current().storageKeys().LIQUID);
        }
        return solidifyingFluid;
    }

    /**
     * @param amount the size of the returned NativeFluidStack.
     * @return a NativeFluidStack of the NativeFluid which solidifies into the material.
     */
    public NativeFluidStack solidifiesFrom(int amount) {
        return new NativeFluidStack(solidifiesFrom(), amount);
    }

    /**
     * Sets the fluid that solidifies into the material.
     *
     * @param solidifyingFluid The NativeFluid which solidifies into the material. If left null, it will be left as the
     *                         default value: the material's liquid.
     */
    public void setSolidifyingFluid( NativeFluid solidifyingFluid) {
        this.solidifyingFluid = solidifyingFluid;
    }
}
