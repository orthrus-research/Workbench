package dev.workbench.worldgenprototype.compat.groovy;

import com.cleanroommc.groovyscript.api.GroovyPlugin;
import com.cleanroommc.groovyscript.compat.mods.GroovyContainer;
import com.cleanroommc.groovyscript.compat.mods.GroovyPropertyContainer;
import dev.workbench.worldgenprototype.WorldgenPrototypeMod;
import java.util.Arrays;
import java.util.Collection;
import net.minecraftforge.fml.common.Optional;

/** Optional GroovyScript property-container integration discovered through ASM. */
@Optional.Interface(
        modid = "groovyscript",
        iface = "com.cleanroommc.groovyscript.api.GroovyPlugin",
        striprefs = true
)
public final class WorldStudioGroovyPlugin implements GroovyPlugin {

    @Override
    public String getModId() {
        return WorldgenPrototypeMod.MOD_ID;
    }

    @Override
    public String getContainerName() {
        return "World Studio";
    }

    @Override
    public Collection<String> getAliases() {
        return Arrays.asList(
                WorldgenPrototypeMod.MOD_ID,
                "worldStudio",
                "worldstudio"
        );
    }

    @Override
    public GroovyPropertyContainer createGroovyPropertyContainer() {
        return new WorldStudioPropertyContainer();
    }

    @Override
    public void onCompatLoaded(GroovyContainer<?> container) {
        // The property container is the complete first-slice integration surface.
    }
}
