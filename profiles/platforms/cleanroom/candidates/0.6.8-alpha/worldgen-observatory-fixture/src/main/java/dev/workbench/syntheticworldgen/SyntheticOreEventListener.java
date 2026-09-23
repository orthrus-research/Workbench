package dev.workbench.syntheticworldgen;

import net.minecraftforge.event.terraingen.OreGenEvent;
import net.minecraftforge.fml.common.eventhandler.Event;
import net.minecraftforge.fml.common.eventhandler.EventPriority;
import net.minecraftforge.fml.common.eventhandler.SubscribeEvent;

/**
 * Exercises ordinary Forge per-listener dispatch without knowing that an
 * observer exists.  Forge's ore gate rejects only {@code DENY}, so changing
 * {@code DEFAULT} to {@code ALLOW} exposes a real before/after event mutation
 * while preserving the generated result in this otherwise listener-free
 * fixture.
 */
public final class SyntheticOreEventListener {

    @SubscribeEvent(priority = EventPriority.LOWEST)
    public void allowStandardGeneration(OreGenEvent.GenerateMinable event) {
        if (event.getResult() == Event.Result.DEFAULT) {
            event.setResult(Event.Result.ALLOW);
        }
    }
}
