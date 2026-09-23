package research.orthrus.axiom.materialtest;

import gregtech.api.GregTechAPI;
import gregtech.api.fluids.store.FluidStorage;
import gregtech.api.unification.material.*;
import gregtech.api.unification.material.info.MaterialFlags;
import gregtech.api.unification.material.properties.*;
import gregtech.api.unification.material.registry.*;
import gregtech.api.unification.stack.MaterialStack;
import gregtech.api.util.GTControlledRegistry;
import gregtech.core.unification.material.internal.MaterialRegistryManager;
import research.orthrus.axiom.materialhost.NativeBoundary;
import research.orthrus.axiom.materialhost.NativeMaterialContent;
import gregtech.api.items.metaitem.MetaItem;
import gregtech.api.items.metaitem.StandardMetaItem;
import net.minecraft.util.ResourceLocation;
import net.minecraftforge.fml.common.*;
import java.lang.reflect.*;
import java.util.*;

/** Original Java API witnesses; does not stand in for GroovyScript acceptance. */
public final class MaterialApiProbe {
    private static void check(boolean value, String message) {
        if (!value) throw new AssertionError(message);
    }
    private static DummyModContainer owner(String name) {
        var metadata = new ModMetadata(); metadata.modId = name; metadata.name = name;
        return new DummyModContainer(metadata);
    }
    private static Material.Builder named(int id, String name) {
        return new Material.Builder(id, new ResourceLocation("supersymmetry", name));
    }
    public static Object run(String mode) throws Exception {
        check(Material.class.getName().equals("gregtech.api.unification.material.Material"), "original binary name");
        check(Material.class.getSuperclass() == Object.class, "no relocated carrier superclass");
        check(FluidStorage.class.isAssignableFrom(FluidProperty.class), "native fluid storage interface");
        check(PropertyKey.class.getField("DUST").get(null) == PropertyKey.DUST, "one original static key identity");
        check(GregTechAPI.materialManager == null && GregTechAPI.markerMaterialRegistry == null,
                "native manager fields are not eagerly initialized");
        var loader = Loader.instance();
        var controller = Loader.class.getDeclaredField("modController"); controller.setAccessible(true);
        check(controller.get(loader) == null, "no game loader startup");
        controller.set(loader, new LoadController(loader));
        loader.setActiveModContainer(owner("gregtech"));
        GregTechAPI.materialManager = MaterialRegistryManager.getInstance();
        GregTechAPI.markerMaterialRegistry = MarkerMaterialRegistry.getInstance();
        var manager = MaterialRegistryManager.getInstance();
        check(manager.getPhase() == IMaterialRegistryManager.Phase.PRE, "original PRE phase");
        MarkerMaterials.register();
        manager.unfreezeRegistries();
        if (mode.startsWith("content-")) return contentContext(mode, manager, loader);
        if (mode.equals("catalog")) {
            Materials.register();
            check(manager.getDefaultRegistry().getAllMaterials().size() == 602, "complete seven-group GT catalog");
            manager.getDefaultRegistry().setFallbackMaterial(Materials.Aluminium);
            var tooltips = gregtech.api.util.FluidTooltipUtil.class.getDeclaredField("tooltips");
            tooltips.setAccessible(true);
            check(!((Map<?,?>)tooltips.get(null)).isEmpty(), "native lazy tooltip suppliers remain registered");
            check(NativeBoundary.gaps().isEmpty(), "registration did not evaluate client tooltip suppliers");
        } else if (!mode.equals("api")) throw new IllegalArgumentException("unknown API witness");
        loader.setActiveModContainer(owner("supersymmetry"));
        int baselineCount = manager.getDefaultRegistry().getAllMaterials().size();
        var a = named(31000, "axiom_identity").dust().color(0x123456).build();
        check(manager.getMaterial("supersymmetry:axiom_identity") == a, "resource namespace resolves original registry identity");
        check(manager.getMaterial("unregistered_owner:axiom_identity") == a, "unknown namespace uses native fallback");
        check(manager.getRegistries().size() == 1 && manager.getDefaultRegistry().getModid().equals("gregtech"),
                "no invented namespace registry");
        check(a.getRegistryName().equals("supersymmetry:axiom_identity"), "resource name differs from storage owner");
        MaterialStack multiplied = a.multiply(4L);
        check(multiplied.material == a && multiplied.amount == 4L, "original multiplication retains material identity");
        var b = named(31001, "axiom_components").dust().components(multiplied).build();
        check(b.getMaterialComponents().getFirst().material == a, "native component identity");
        var dust = a.getProperty(PropertyKey.DUST);
        try { dust.setHarvestLevel(0); throw new AssertionError("invalid property setter returned"); }
        catch (IllegalArgumentException expected) { /* original native validation */ }
        check(a.getProperty(PropertyKey.DUST) == dust, "property instance not copied by API");
        // Java's native Object... overload is NOT GroovyScript's mixed-component expansion.
        try { named(31002, "axiom_wrong_java_components").components(new Object[]{a, multiplied});
              throw new AssertionError("Java Object... accepted a stack as numeric amount"); }
        catch (IllegalArgumentException | ClassCastException expected) { }
        var registry = new GTControlledRegistry<String, Object>(32767);
        Object first = new Object(), second = new Object();
        registry.register(123, "first", first);
        try { registry.register(123, "second", second); throw new AssertionError("duplicate ID admitted"); }
        catch (IllegalArgumentException expected) { }
        check(registry.func_82594_a("second") == second && registry.func_148754_a(123) == first,
                "native key insertion precedes ID failure; no rollback");
        loader.setActiveModContainer(owner("gregtech")); manager.closeRegistries();
        check(manager.getRegisteredMaterials().size() == baselineCount + 2, "two native registered objects");
        var late = named(31003, "axiom_late").dust().build();
        check(late != null && manager.getMaterial("axiom_late") == null, "closed registration logs/skips but object exists");
        a.setFormula("Aa4").addFlags(MaterialFlags.NO_SMELTING);
        check(a.getChemicalFormula().equals("Aa4") && a.hasFlag(MaterialFlags.NO_SMELTING), "post-material changes on same object");
        manager.freezeRegistries();
        check(manager.getPhase() == IMaterialRegistryManager.Phase.FROZEN, "native frozen checkpoint");
        try { a.setProperty(PropertyKey.GEM, new GemProperty()); throw new AssertionError("frozen mutation admitted"); }
        catch (IllegalStateException expected) { }
        try { a.getLocalizedName(); throw new AssertionError("unqualified localization returned"); }
        catch (UnsupportedOperationException expected) { }
        check(NativeBoundary.gaps().equals(List.of("material.localization")), "caught unsupported port remains sticky");
        Class<?> raw = Class.forName("net.minecraft.util.IObjectIntIterable");
        check(raw.getTypeParameters().length == 0, "compiler-only view did not enter native runtime");
        String generics;
        try { Class.forName("net.minecraft.util.registry.RegistryNamespaced").getGenericInterfaces(); generics = "resolved"; }
        catch (MalformedParameterizedTypeException nativeMetadata) { generics = "native-malformed-generic-arity"; }
        return Map.of("mode", mode, "baselineMaterials", baselineCount, "registeredMaterials", manager.getRegisteredMaterials().size(),
                "originalApiIdentity", true, "partialMutationPreserved", true, "closedRegistrationSkipped", true,
                "caughtGap", NativeBoundary.gaps(), "nativeGenericReflection", generics,
                "compilerViewInRuntime", false, "groovyExecutionQualified", false);
    }
    /** Trusted composition fixture, not admission of custom-item Groovy constructors. */
    private static Object contentContext(String mode, MaterialRegistryManager manager, Loader loader) {
        String expected = switch (mode) {
            case "content-open" -> "material-content.requires-frozen-catalog";
            case "content-custom-item" -> "material-content.custom-item-composition";
            case "content-owner" -> "material-content.requires-gt-owner";
            default -> throw new IllegalArgumentException("unknown content context witness");
        };
        if (!mode.equals("content-open")) { manager.closeRegistries(); manager.freezeRegistries(); }
        if (mode.equals("content-custom-item")) new StandardMetaItem();
        if (mode.equals("content-owner")) loader.setActiveModContainer(owner("supersymmetry"));
        var existing = List.copyOf(MetaItem.getMetaItems());
        var content = new NativeMaterialContent();
        try { content.execute(); throw new AssertionError("unqualified content composition executed"); }
        catch (UnsupportedOperationException refused) { check(refused.getMessage().contains(expected), "exact context refusal"); }
        check(NativeBoundary.gaps().equals(List.of(expected)), "context gap is sticky rather than a native source error");
        var progress = content.progress();
        check(progress.get("phase").equals("FAILED") && progress.get("failedPhase").equals("NEW"), "failure before native construction");
        check(progress.get("completedCheckpoints").equals(List.of()) && !content.constructedCheckpointReached(), "no invented checkpoint");
        check(!progress.containsKey("interruptedState"), "unvisited content not inspected");
        check(MetaItem.getMetaItems().equals(existing), "original meta-item objects retained without registry clearing");
        try { content.execute(); throw new AssertionError("failed lifecycle retried"); }
        catch (UnsupportedOperationException refused) { check(refused.getMessage().contains("repeated-execution:FAILED"), "terminal retry refusal"); }
        check(content.progress().equals(progress), "retry did not replace original stopped evidence");
        check(MetaItem.getMetaItems().equals(existing), "retry did not remove existing objects");
        return Map.of("mode", mode, "coverageGaps", NativeBoundary.gaps(), "contentProgress", progress,
                "existingMetaItems", existing.size(), "sameNativeObjectsRetained", true,
                "retryPreservedProgress", true, "fixtureScope", "trusted-Java-context-not-custom-item-authoring");
    }
}
