package research.orthrus.axiom.nativeconstruction;

import com.google.common.collect.HashBiMap;
import java.util.*;
import net.minecraft.util.ResourceLocation;
import net.minecraftforge.common.MinecraftForge;
import net.minecraftforge.fluids.*;
import net.minecraftforge.fml.common.*;
import net.minecraftforge.fml.common.eventhandler.SubscribeEvent;

/** Explicit owner/listener fixtures, NOT recovered mod discovery or installed pack membership. */
public final class NativeProducerConformance {
    private static void check(boolean value, String message) { if (!value) throw new AssertionError(message); }
    public static final class Observer {
        final List<Object> events = new ArrayList<>();
        String fail;
        @SubscribeEvent public void registered(FluidRegistry.FluidRegisterEvent event) {
            Fluid fluid = FluidRegistry.getFluid(event.getFluidName());
            events.add(List.of(event.getFluidName(), event.getFluidID(), Loader.instance().activeModContainer().getModId(),
                    FluidEnvironment.current().registry().masterFluidReference.inverse().get(fluid)));
            check(new FluidStack(fluid, 1).getFluid() == fluid, "delegate exists during original registration dispatch");
            if (event.getFluidName().equals(fail)) throw new IllegalStateException("native-listener-sentinel");
        }
    }
    private static ModContainer owner(String name) {
        var metadata = new ModMetadata(); metadata.modId = name; metadata.name = name;
        return new DummyModContainer(metadata);
    }
    private static FluidMaterial.Builder builder(String name, int id) {
        return new FluidMaterial.Builder(id, new ResourceLocation("gregtech", name));
    }
    private static Object capture(Fluid fluid) {
        var result = new ArrayList<Object>(List.of(fluid.getName(), FluidRegistry.getRegisteredFluidIDs().get(fluid),
                fluid.getTemperature(), fluid.getDensity(), fluid.getViscosity(), fluid.getLuminosity(), fluid.getColor(),
                fluid.isGaseous(), FluidRegistry.getDefaultFluidName(fluid), fluid.getStill().toString(), fluid.getFlowing().toString()));
        if (fluid instanceof GTFluid gt) result.add(List.of(gt.getState().name(), gt.getAttributes().stream().map(a -> a.toString()).toList()));
        if (fluid instanceof GTFluid.GTMaterialFluid gt) result.add(gt.getMaterial().getRegistryName());
        return result;
    }
    public static Object run(String mode) throws Exception {
        var loader = Loader.instance();
        var controller = Loader.class.getDeclaredField("modController"); controller.setAccessible(true);
        Object previous = controller.get(loader);
        check(previous == null, "only the bounded identity prefix is admitted, not an initialized mod loader");
        controller.set(loader, new LoadController(loader));
        var observer = new Observer();
        try (var env = new FluidEnvironment()) {
            loader.setActiveModContainer(owner("gregtech"));
            MinecraftForge.EVENT_BUS.register(observer);
            var manager = env.runtime().materials();
            manager.createRegistry("susy"); manager.unfreezeRegistries();
            if (mode.equals("producers")) {
                SourceElementProducer.register();
                check(manager.getDefaultRegistry().getAllMaterials().size() == 130, "complete GT element producer");
                loader.setActiveModContainer(owner("susy")); LockedSusyProducer.init();
                check(manager.getRegistry("susy").getAllMaterials().size() == 10, "complete Susy unknown-composition producer");
                manager.closeRegistries();
                loader.setActiveModContainer(owner("gregtech")); env.registerMaterialFluids();
                manager.freezeRegistries();
                var materials = new TreeMap<String,Object>();
                int fluidBindings = 0;
                for (var state : manager.getRegisteredMaterials()) {
                    var material = FluidMaterial.require(state);
                    var bindings = new ArrayList<Object>();
                    for (var key : List.of(env.storageKeys().LIQUID, env.storageKeys().GAS, env.storageKeys().PLASMA)) {
                        if (!material.hasProperty(FluidDomain.FLUID)) continue;
                        Fluid fluid = material.getFluid(key);
                        if (fluid == null) continue;
                        fluidBindings++;
                        check(fluid instanceof GTFluid.GTMaterialFluid, "producer fluid is an actual native Fluid subclass");
                        check(((GTFluid.GTMaterialFluid) fluid).getMaterial() == material, "native subclass retains material identity");
                        check(material.getFluid(key, 144).getFluid() == fluid, "material stack uses actual native delegate");
                        bindings.add(capture(fluid));
                    }
                    materials.put(material.getRegistryName(), List.of(material.getId(), material.getMaterialRGB(), material.getChemicalFormula(), bindings));
                }
                check(observer.events.size() == FluidRegistry.getRegisteredFluids().size() - 2, "one original event per new fluid, excluding preexisting vanilla");
                for (var event : observer.events) {
                    var row = (List<?>) event;
                    check(row.get(2).equals("gregtech") && row.get(3).equals("gregtech:" + row.get(0)), "event observes native owner before retained GT owner fix");
                }
                for (var material : LockedSusyProducer.values()) {
                    if (!material.hasProperty(FluidDomain.FLUID)) continue;
                    var fluid = material.getFluid();
                    if (fluid != null) check(FluidRegistry.getDefaultFluidName(fluid).equals("susy:" + fluid.getName()), "Susy owner fix updates actual native maps after dispatch");
                }
                FluidRegistry.validateFluidRegistry();
                return Map.of("gtDeclarations", 130, "susyDeclarations", 10, "fluidBindings", fluidBindings,
                        "materials", materials, "events", observer.events, "phase", manager.getPhase().name(),
                        "ownerAuthority", "explicit-native-DummyModContainer-fixtures");
            }
            if (mode.equals("reuse")) {
                var water = FluidRegistry.WATER; var lava = FluidRegistry.LAVA;
                Object waterBlock = water.getBlock(), lavaBlock = lava.getBlock();
                var w = builder("water_binding", 4000).fluid(water, env.storageKeys().LIQUID, FluidState.LIQUID).build();
                var l = builder("lava_binding", 4001).fluid(lava, env.storageKeys().LIQUID, FluidState.LIQUID).build();
                var mutated = new FluidBuilder().name("water").temperature(317).color(0xff123456).build("gregtech", null, env.storageKeys().LIQUID);
                check(mutated == water && w.getFluid() == water && l.getFluid() == lava, "no vanilla fluid conversion or re-registration");
                check(water.getBlock() == waterBlock && lava.getBlock() == lavaBlock && waterBlock != null, "actual vanilla block identity retained");
                check(water.getTemperature() == 317 && water.getColor() == 0xff123456, "retained builder mutates original vanilla object");
                check(observer.events.isEmpty() && FluidRegistry.getRegisteredFluids().size() == 2, "reuse does not post a registration event");
                var stack = w.getFluid(144); check(stack.getFluid() == water && stack.copy().getFluid() == water, "actual native stack/copy path");
                loader.setActiveModContainer(owner("fixture"));
                var alternative = new Fluid("water", new ResourceLocation("fixture", "still"), new ResourceLocation("fixture", "flow"));
                check(!FluidRegistry.registerFluid(alternative), "native duplicate registration records alternative without replacing default");
                check(observer.events.isEmpty(), "alternative registration does not dispatch");
                FluidRegistry.initFluidIDs(HashBiMap.create(FluidRegistry.getRegisteredFluidIDs()), new HashSet<>(List.of("fixture:water")));
                check(FluidRegistry.getFluid("water") == alternative && stack.getFluid() == alternative && w.getFluid(1).getFluid() == alternative,
                        "native default selection rebinds existing and newly constructed material stacks");
                check(w.getFluid() == water && water.getBlock() == waterBlock, "material holds original fluid object while delegate follows new default");
                FluidRegistry.validateFluidRegistry();
                return Map.of("reuse", true, "nativeBlockIdentity", true, "nativeMutation", true, "nativeDelegateRebinding", true, "events", observer.events.size());
            }
            if (mode.equals("listener-failure")) {
                observer.fail = "listener_failure";
                var build = new FluidBuilder().name(observer.fail).attributes(env.attributes().ACID);
                try { build.build("susy", null, env.storageKeys().LIQUID); throw new AssertionError("listener failure swallowed"); }
                catch (IllegalStateException expected) { check(expected.getMessage().equals("native-listener-sentinel"), "original listener exception propagates"); }
                var fluid = FluidRegistry.getFluid(observer.fail);
                check(fluid instanceof GTFluid && ((GTFluid)fluid).getAttributes().contains(env.attributes().ACID), "pre-event construction and attributes survive failure");
                check(FluidRegistry.getDefaultFluidName(fluid).equals("gregtech:" + observer.fail), "owner fix has not run after failed dispatch");
                check(new FluidStack(fluid, 9).getFluid() == fluid, "native map and delegate survive failed dispatch");
                check(env.sprites().isEmpty() && env.tooltips().isEmpty() && !FluidRegistry.getBucketFluids().contains(fluid), "post-dispatch effects have not run");
                observer.fail = null;
                check(build.build("susy", null, env.storageKeys().LIQUID) == fluid, "retry reuses partially registered native object");
                check(observer.events.size() == 1 && FluidRegistry.getDefaultFluidName(fluid).startsWith("gregtech:"), "retry does not invent dispatch or owner correction");
                return Map.of("partialStateRetained", true, "nativeAttributeIdentity", true, "retryReuses", true, "events", observer.events.size());
            }
            throw new IllegalArgumentException("Unknown native producer fixture");
        } finally {
            MinecraftForge.EVENT_BUS.unregister(observer); controller.set(loader, previous);
        }
    }
}
