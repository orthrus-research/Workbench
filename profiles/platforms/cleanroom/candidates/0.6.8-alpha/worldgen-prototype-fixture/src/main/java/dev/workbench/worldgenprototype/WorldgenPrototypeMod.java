package dev.workbench.worldgenprototype;

import dev.workbench.worldgenprototype.world.PrototypeWorldType;
import net.minecraft.world.WorldType;
import net.minecraftforge.fml.common.Mod;

@Mod(
        modid = WorldgenPrototypeMod.MOD_ID,
        name = WorldgenPrototypeMod.NAME,
        version = WorldgenPrototypeMod.VERSION,
        acceptedMinecraftVersions = "[1.12.2]",
        dependencies = "required-after:cleanroom@[0.6.8-alpha];after:groovyscript;after:biomesoplenty"
)
public final class WorldgenPrototypeMod {

    public static final String MOD_ID = "workbench_worldgen_prototype";
    public static final String NAME = "Workbench Worldgen Prototype";
    public static final String VERSION = "0.4.0";

    public static final WorldType WORLD_TYPE = new PrototypeWorldType();
}
