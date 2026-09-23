package dev.workbench.worldgenobservatory.probe.mixin;

import net.minecraftforge.fml.common.ModContainer;
import net.minecraftforge.fml.common.eventhandler.ASMEventHandler;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.gen.Accessor;

/** Exact accessor for listener attribution on Cleanroom's Forge event bus. */
@Mixin(value = ASMEventHandler.class, remap = false)
public interface ASMEventHandlerAccess {

    @Accessor("owner")
    ModContainer workbench$getOwner();

    @Accessor("readable")
    String workbench$getReadable();
}
