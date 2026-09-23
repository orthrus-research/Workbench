package research.orthrus.axiom.nativeconstruction;

import net.minecraftforge.common.MinecraftForge;
import net.minecraftforge.fml.common.eventhandler.Event;

/** The retained lifecycle posts original GT event declarations to the native bus. */
final class MaterialEvents {
    Object construct(String name) {
        return switch (name) {
            case "research.orthrus.axiom.nativeconstruction.MaterialRegistryEvent" -> new MaterialRegistryEvent();
            case "research.orthrus.axiom.nativeconstruction.MaterialEvent" -> new MaterialEvent();
            case "research.orthrus.axiom.nativeconstruction.PostMaterialEvent" -> new PostMaterialEvent();
            default -> throw new IllegalArgumentException("Unbound native material event: " + name);
        };
    }
    boolean post(Object event) { return MinecraftForge.EVENT_BUS.post((Event)event); }
}
