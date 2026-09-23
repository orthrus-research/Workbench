package dev.workbench.worldgenobservatory.world;

import java.lang.reflect.Constructor;
import net.minecraft.world.World;
import net.minecraft.world.WorldType;
import net.minecraft.world.gen.ChunkGeneratorOverworld;
import net.minecraft.world.gen.IChunkGenerator;
import net.minecraftforge.fml.common.Loader;

/**
 * An explicit opt-in world type. The candidate wraps Forge's patched vanilla
 * generator so lifecycle ownership remains with the implementation that
 * already owns it.
 */
public final class ObservatoryWorldType extends WorldType {

    public static final String WORLD_TYPE_NAME = "wb_observe";
    public static final String DELEGATE_OPTION = "workbench-generator-class=";

    public ObservatoryWorldType() {
        super(WORLD_TYPE_NAME);
    }

    @Override
    public IChunkGenerator getChunkGenerator(World world, String generatorOptions) {
        IChunkGenerator delegate = createDelegate(world, generatorOptions);
        return new ObservingChunkGenerator(world, delegate);
    }

    private static IChunkGenerator createDelegate(World world, String generatorOptions) {
        if (generatorOptions == null || generatorOptions.isEmpty()) {
            return new ChunkGeneratorOverworld(
                    world,
                    world.getSeed(),
                    world.getWorldInfo().isMapFeaturesEnabled(),
                    generatorOptions
            );
        }

        if (!generatorOptions.startsWith(DELEGATE_OPTION)) {
            throw new IllegalArgumentException(
                    "wb_observe accepts only an empty generator setting or "
                            + DELEGATE_OPTION + "<binary-class-name>"
            );
        }

        String className = generatorOptions.substring(DELEGATE_OPTION.length());
        if (!className.matches(
                "(?:[A-Za-z_$][A-Za-z0-9_$]*\\.)*[A-Za-z_$][A-Za-z0-9_$]*"
        )) {
            throw new IllegalArgumentException(
                    "Invalid workbench generator binary class name: " + className
            );
        }

        try {
            Class<?> rawClass = Class.forName(
                    className,
                    true,
                    Loader.instance().getModClassLoader()
            );
            Class<? extends IChunkGenerator> generatorClass = rawClass.asSubclass(
                    IChunkGenerator.class
            );
            Constructor<? extends IChunkGenerator> constructor = generatorClass.getConstructor(
                    World.class
            );
            return constructor.newInstance(world);
        } catch (ReflectiveOperationException | ClassCastException failure) {
            throw new IllegalArgumentException(
                    "Could not construct workbench generator delegate " + className
                            + " with public constructor (World)",
                    failure
            );
        }
    }
}
