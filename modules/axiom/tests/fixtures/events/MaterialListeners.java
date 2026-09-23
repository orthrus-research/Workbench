package fixtureevents;

import research.orthrus.axiom.nativeevents.*;
import research.orthrus.axiom.materialevents.*;
import java.util.function.Consumer;

/** Controlled listeners only, not replacement GT/Susy/pack producers. */
public class MaterialListeners {
    final Consumer<String> effect;
    public MaterialListeners(Consumer<String> effect) {this.effect=effect;}
    @SubscribeEvent public void registry(MaterialRegistryEvent event) {effect.accept("registry");}
    @SubscribeEvent(priority=EventPriority.HIGH) public void materialHigh(MaterialEvent event) {effect.accept("material-high");}
    @SubscribeEvent(priority=EventPriority.LOWEST) public void materialLow(MaterialEvent event) {effect.accept("material-low");}
    @SubscribeEvent(priority=EventPriority.HIGH) public void postHigh(PostMaterialEvent event) {effect.accept("post-high");}
    @SubscribeEvent public void postNormal(PostMaterialEvent event) {effect.accept("post-normal");}
}
