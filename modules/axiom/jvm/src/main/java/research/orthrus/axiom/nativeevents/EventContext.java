package research.orthrus.axiom.nativeevents;

import java.util.Objects;
import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.Logger;

/** Explicit loader-owner port. One instance per isolated event class space.
 * Like the selected Loader field, ownership is not thread-local. */
public final class EventContext {
    public record Owner(String modId, String name) {
        public Owner { Objects.requireNonNull(modId); Objects.requireNonNull(name); }
        public String getModId() { return modId; }
        public String getName() { return name; }
    }
    public static final Logger log = LogManager.getLogger("axiom.nativeevents");
    private static final EventContext INSTANCE = new EventContext();
    private Owner active, minecraft;
    private EventContext() {}
    public static EventContext instance() { return INSTANCE; }
    public Owner activeModContainer() { return active; }
    public void setActiveModContainer(Owner owner) { active = owner; }
    public void setMinecraftModContainer(Owner owner) { minecraft = Objects.requireNonNull(owner); }
    public Owner getMinecraftModContainer() {
        if (minecraft == null) throw new IllegalStateException("Minecraft owner has not been supplied by the selected composition");
        return minecraft;
    }
}
