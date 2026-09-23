// Source-qualified GTCEu/Susy-Core members; see sources/material-ores.lock.json and spec/material-ores.md.
package research.orthrus.axiom.nativeconstruction;


import net.minecraft.block.SoundType;
import net.minecraft.block.state.IBlockState;
import net.minecraft.util.math.BlockPos;
import net.minecraft.world.IBlockAccess;

import com.google.common.base.Preconditions;

import java.util.function.Predicate;
import java.util.function.Supplier;

/**
 * For ore generation
 */
public class StoneType implements Comparable<StoneType> {

    public final String name;

    public final OrePrefix processingPrefix;
    public final FluidMaterial stoneMaterial;
    public final Supplier<IBlockState> stone;
    public final SoundType soundType;
    // we are using guava predicate because isReplaceableOreGen uses it
    @SuppressWarnings("Guava")
    private final com.google.common.base.Predicate<IBlockState> predicate;
    public final boolean shouldBeDroppedAsItem;

    public static final GTControlledRegistry<String, StoneType> STONE_TYPE_REGISTRY = new GTControlledRegistry<>(FluidEnvironment.current().runtime(), 128);

    public StoneType(int id, String name, SoundType soundType, OrePrefix processingPrefix, FluidMaterial stoneMaterial,
                     Supplier<IBlockState> stone, Predicate<IBlockState> predicate, boolean shouldBeDroppedAsItem) {
        Preconditions.checkArgument(
                stoneMaterial.hasProperty(PropertyKey.DUST),
                "Stone type must be made with a Material with the Dust Property!");
        this.name = name;
        this.soundType = soundType;
        this.processingPrefix = processingPrefix;
        this.stoneMaterial = stoneMaterial;
        this.stone = stone;
        this.predicate = predicate::test;
        this.shouldBeDroppedAsItem = shouldBeDroppedAsItem || PrefixDependencies.allUniqueStoneTypes();
        STONE_TYPE_REGISTRY.register(id, name, this);
        if (net.minecraftforge.fml.common.Loader.isModLoaded("jei") && this.shouldBeDroppedAsItem) {
            throw new Failure("incomplete", "ore.jei", "JEI integration is outside this native content context");
        }
    }

    @Override
    public int compareTo( StoneType stoneType) {
        return STONE_TYPE_REGISTRY.getIDForObject(this) - STONE_TYPE_REGISTRY.getIDForObject(stoneType);
    }

    private static final ThreadLocal<Boolean> hasDummyPredicateRan = ThreadLocal.withInitial(() -> false);
    private static final com.google.common.base.Predicate<IBlockState> dummyPredicate = state -> {
        hasDummyPredicateRan.set(true);
        return false;
    };

    public static void init() {
        // noinspection ResultOfMethodCallIgnored
        StoneTypes.STONE.name.getBytes();
    }

    public static StoneType computeStoneType(IBlockState state, IBlockAccess world, BlockPos pos) { throw new Failure("incomplete", "ore.behavior", "Unqualified ore/host-stone method: public static StoneType computeStoneType"); }

    @Override
    public boolean equals(Object o) {
        if (this == o) return true;
        if (o == null || getClass() != o.getClass()) return false;

        StoneType stoneType = (StoneType) o;

        if (shouldBeDroppedAsItem != stoneType.shouldBeDroppedAsItem) return false;
        if (!name.equals(stoneType.name)) return false;
        if (!processingPrefix.equals(stoneType.processingPrefix)) return false;
        if (!stoneMaterial.equals(stoneType.stoneMaterial)) return false;
        if (!stone.equals(stoneType.stone)) return false;
        if (!soundType.equals(stoneType.soundType)) return false;
        return predicate.equals(stoneType.predicate);
    }

    @Override
    public int hashCode() {
        int result = name.hashCode();
        result = 31 * result + processingPrefix.hashCode();
        result = 31 * result + stoneMaterial.hashCode();
        result = 31 * result + stone.hashCode();
        result = 31 * result + soundType.hashCode();
        result = 31 * result + predicate.hashCode();
        result = 31 * result + (shouldBeDroppedAsItem ? 1 : 0);
        return result;
    }
}
