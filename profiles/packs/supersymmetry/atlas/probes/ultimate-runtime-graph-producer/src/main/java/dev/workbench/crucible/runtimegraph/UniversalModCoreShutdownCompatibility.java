package dev.workbench.crucible.runtimegraph;

import com.google.common.collect.ArrayListMultimap;
import com.google.common.collect.Multimap;

import java.lang.reflect.Field;
import java.util.IdentityHashMap;
import java.util.Map;

import net.minecraft.world.World;
import net.minecraftforge.common.ForgeChunkManager;
import net.minecraftforge.event.world.WorldEvent;
import net.minecraftforge.fml.common.eventhandler.EventPriority;
import net.minecraftforge.fml.common.eventhandler.SubscribeEvent;
import net.minecraftforge.fml.relauncher.ReflectionHelper;

import org.apache.logging.log4j.Logger;

/**
 * Capture-only bridge for UniversalModCore 1.2.2's shutdown ticket request.
 *
 * <p>Forge clears its complete ticket map at HIGHEST priority once the server
 * has entered shutdown. UniversalModCore's NORMAL-priority unload callback may
 * then request a ticket for a dimension that did not tick. This HIGH-priority
 * handler supplies only the missing map value and the LOWEST-priority handler
 * removes it after third-party callbacks have completed. It is armed by the
 * server-stopping lifecycle and therefore cannot affect ordinary dimension
 * unloads or the preceding runtime capture.</p>
 */
final class UniversalModCoreShutdownCompatibility {
    private static final Field FORGE_TICKETS = ReflectionHelper.findField(
        ForgeChunkManager.class,
        "tickets"
    );

    private final Logger logger;
    private final Map<World, Multimap<String, ForgeChunkManager.Ticket>> inserted =
        new IdentityHashMap<World, Multimap<String, ForgeChunkManager.Ticket>>();
    private boolean armed;

    UniversalModCoreShutdownCompatibility(Logger logger) {
        this.logger = logger;
    }

    void arm() {
        armed = true;
        logger.info("Workbench shutdown compatibility entered the stopping lifecycle");
    }

    @SubscribeEvent(priority = EventPriority.HIGH)
    public void provideShutdownTicketCollection(WorldEvent.Unload event) {
        if (!armed) return;
        Map<World, Multimap<String, ForgeChunkManager.Ticket>> tickets = tickets();
        World world = event.getWorld();
        if (!tickets.containsKey(world)) {
            Multimap<String, ForgeChunkManager.Ticket> supplied =
                ArrayListMultimap.<String, ForgeChunkManager.Ticket>create();
            tickets.put(world, supplied);
            inserted.put(world, supplied);
            logger.info(
                "Workbench supplied the bounded shutdown ticket collection for dimension {}",
                world.provider.getDimension()
            );
        }
    }

    @SubscribeEvent(priority = EventPriority.LOWEST)
    public void removeShutdownTicketCollection(WorldEvent.Unload event) {
        if (!armed) return;
        World world = event.getWorld();
        Multimap<String, ForgeChunkManager.Ticket> supplied = inserted.remove(world);
        Map<World, Multimap<String, ForgeChunkManager.Ticket>> tickets = tickets();
        if (supplied != null && tickets.get(world) == supplied) {
            tickets.remove(world);
            logger.info(
                "Workbench removed the bounded shutdown ticket collection for dimension {}",
                world.provider.getDimension()
            );
        }
    }

    @SuppressWarnings("unchecked")
    private static Map<World, Multimap<String, ForgeChunkManager.Ticket>> tickets() {
        try {
            return (Map<World, Multimap<String, ForgeChunkManager.Ticket>>) FORGE_TICKETS.get(null);
        } catch (IllegalAccessException exception) {
            throw new IllegalStateException("cannot access Forge shutdown ticket state", exception);
        }
    }
}
