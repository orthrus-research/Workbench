package research.orthrus.axiom;

import org.junit.jupiter.api.*;
import java.io.*;
import java.nio.file.*;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;

class NativeFluidTest {
    private RegistryRuntime runtime;
    private FluidEnvironment env;
    @BeforeEach void open() {
        String root = System.getProperty("axiom.test.registryRoot");
        Assumptions.assumeTrue(root != null, "Use tools/build_axiom.py for original runtime inputs");
        runtime = RegistryRuntime.open(Path.of(root));
        env = FluidEnvironment.isolatedProducer(runtime);
        runtime.materials().createRegistry("susy");
        runtime.activeMod("gregtech");
        runtime.materials().unfreezeRegistries();
    }
    @AfterEach void close() throws IOException {
        if (env != null) env.close();
        if (runtime != null) runtime.close();
    }
    private FluidMaterial.Builder material(int id, String name) {
        return new FluidMaterial.Builder(id, new NativeLocation("susy", name));
    }
    private void register() {
        runtime.materials().closeRegistries();
        runtime.materials().freezeRegistries();
        env.registerMaterialFluids();
    }
    private NativeFluid bare(String name) {
        return new NativeFluid(name, new NativeLocation("susy", "still"), new NativeLocation("susy", "flow"));
    }
    @Test void originalResourceLocationExecutesNativeNormalization() {
        var location = new NativeLocation("SUSY:Fluids/Mixed");
        assertEquals("susy", location.getNamespace());
        assertEquals("fluids/mixed", location.getPath());
        assertEquals(new NativeLocation("susy", "fluids/mixed"), location);
        assertEquals("minecraft:water", new NativeLocation(":water").toString());
        assertEquals("minecraft:", new NativeLocation("").toString());
    }
    @Test void scalarForgeDefaultsAndNativeNameNormalizationAreNotBuilderDefaults() {
        var fluid = bare("CoOlAnT");
        assertEquals("coolant", fluid.getName());
        assertEquals(300, fluid.getTemperature());
        assertEquals(0, fluid.getLuminosity());
        assertEquals(0xffffffff, fluid.getColor());
        assertSame(fluid, fluid.setTemperature(-2));
        assertEquals(-2, fluid.getTemperature());
        assertSame(fluid, fluid.setViscosity(-3));
        assertEquals(-3, fluid.getViscosity()); // Forge scalar setter does not enforce the builder guard.
    }
    @Test void nativeMaterialQueuesAreConstructedThenResolvedByIdentity() {
        var coolant = material(27057, "coolant").liquid().color(0x46dde8).build();
        assertNull(coolant.getFluid());
        assertSame(coolant, runtime.materials().getMaterial("susy:coolant"));
        register();
        var fluid = coolant.getFluid();
        assertSame(fluid, env.registry().getFluid("coolant"));
        assertSame(coolant, ((GTFluid.GTMaterialFluid) fluid).getMaterial());
        assertSame(coolant, env.unifier().getMaterialFromFluid(fluid));
        assertEquals(293, fluid.getTemperature());
        assertEquals(0xff46dde8, fluid.getColor());
        assertEquals(-1, fluid.getLuminosity()); // Deliberately not clamped to zero.
        assertEquals(1000, fluid.getDensity());
        assertEquals(1000, fluid.getViscosity());
        assertEquals("susy:coolant", env.registry().getDefaultFluidName(fluid));
        assertEquals("gregtech:blocks/material_sets/fluid/liquid", fluid.getStill().toString());
        assertTrue(env.registry().hasBucket(fluid));
        assertEquals(1, env.tooltipBindings(fluid).size());
        assertEquals(false, env.coverage().get("wholePackParity"));
        assertThrows(IllegalStateException.class, () -> coolant.setProperty(PropertyKey.DUST, new DustProperty()));
    }
    @Test void materialAndFluidViewsShareOnePropertyOwner() throws Exception {
        var material=material(31,"shared_properties").liquid().build();
        MaterialState base=material;
        assertSame(material.getProperties(),base.getProperties());
        var storage=MaterialState.class.getDeclaredField("properties");storage.setAccessible(true);
        assertSame(base.getProperties(),storage.get(material));
        base.setProperty(PropertyKey.DUST,new DustProperty());
        assertSame(material.getProperty(PropertyKey.DUST),base.getProperties().getProperty(PropertyKey.DUST));
        assertThrows(NoSuchFieldException.class,()->FluidMaterial.class.getDeclaredField("nativeProperties"));
    }
    @Test void dustAndFlagsDriveUpstreamDefaults() {
        var molten = material(1, "molten").dust().liquid().build();
        var sticky = material(2, "sticky").liquid().flags(MaterialFlags.STICKY, MaterialFlags.GLOWING).build();
        register();
        assertEquals(1200, molten.getFluid().getTemperature());
        assertEquals(10, molten.getFluid().getLuminosity());
        assertEquals(2000, sticky.getFluid().getViscosity());
        assertEquals(15, sticky.getFluid().getLuminosity());
    }
    @Test void plasmaRunsAfterPrimaryFluidAndUsesItsTemperature() {
        var value = material(1, "phase").liquid(new FluidBuilder().temperature(450)).gas().plasma().build();
        register();
        assertEquals("phase", value.getFluid().getName());
        assertEquals("gas.phase", value.getFluid(env.storageKeys().GAS).getName());
        var plasma = value.getFluid(env.storageKeys().PLASMA);
        assertEquals("plasma.phase", plasma.getName());
        assertEquals(10450, plasma.getTemperature());
        assertEquals(15, plasma.getLuminosity());
        assertTrue(plasma.isGaseous());
    }
    @Test void blastArithmeticUsesJvmOverflow() {
        var value = material(1, "blast").liquid().plasma().build();
        value.setProperty(FluidDomain.BLAST, new BlastProperty(Integer.MAX_VALUE));
        register();
        assertEquals(Integer.MAX_VALUE + FluidConstants.LIQUID_TEMPERATURE_OFFSET, value.getFluid().getTemperature());
        assertEquals(Integer.MAX_VALUE + 10000, value.getFluid(env.storageKeys().PLASMA).getTemperature());
    }
    @Test void existingAlternativeIsMutatedWithoutChangingForeignStateOrOwnership() {
        runtime.activeMod("foreign");
        var foreign = bare("foreign_coolant").setGaseous(false).setColor(0xff112233);
        env.registry().registerFluid(foreign);
        runtime.activeMod("gregtech");
        var result = new FluidBuilder().name("missing").alternativeName("foreign_coolant")
                .state(FluidState.GAS).disableColor().temperature(99).build("susy", null, null);
        assertSame(foreign, result);
        assertFalse(result.isGaseous()); // Existing fluid's state flag is not reset.
        assertEquals(99, result.getTemperature());
        assertEquals(0xff112233, result.getColor());
        assertEquals("foreign:foreign_coolant", env.registry().getDefaultFluidName(result));
        assertNull(env.registry().getFluid("missing"));
    }
    @Test void builderReuseKeepsPreviouslyInferredFieldsAndAppendsLazyBindings() {
        var first = material(1, "first").dust().liquid().color(0x112233).build();
        var second = material(2, "second").liquid().color(0xaabbcc).build();
        var builder = new FluidBuilder();
        var a = builder.build("susy", first, env.storageKeys().LIQUID);
        var b = builder.build("susy", second, env.storageKeys().LIQUID);
        assertSame(a,b);
        assertEquals("first", b.getName());
        assertEquals(1200, b.getTemperature());
        assertEquals(0xff112233, b.getColor());
        assertSame(second, env.unifier().getMaterialFromFluid(b));
        assertSame(first, ((GTFluid.GTMaterialFluid)b).getMaterial());
        assertEquals(2, env.tooltipBindings(b).size());
    }
    @Test void attributeIdentityDeduplicatesInInsertionOrderWithoutEagerTooltipExecution() {
        var first = new FluidAttribute(new NativeLocation("susy", "acid"), lines -> fail("not lazy"), lines -> {});
        var same = new FluidAttribute(new NativeLocation("susy", "acid"), lines -> {}, lines -> {});
        var second = new FluidAttribute(new NativeLocation("susy", "hot"), lines -> {}, lines -> {});
        var result = (GTFluid) new FluidBuilder().name("attributes").attributes(first, same, second).build("susy", null, null);
        assertEquals(List.of(first, second), new ArrayList<>(result.getAttributes()));
        assertEquals(1, env.tooltipBindings(result).size());
    }
    @Test void foreignAttributesWarnButDoNotReject() {
        env.registry().registerFluid(bare("foreign"));
        var attribute = new FluidAttribute(new NativeLocation("susy", "acid"), lines -> {}, lines -> {});
        var result = new FluidBuilder().name("foreign").attribute(attribute).build("susy", null, null);
        assertNotNull(result);
        assertTrue(env.effects().stream().anyMatch(e -> e instanceof FluidEnvironment.Log log && log.level().equals("warn")));
    }
    @Test void builderGuardsAndDoubleConversionsKeepJvmEdgeCases() {
        assertThrows(IllegalArgumentException.class, () -> new FluidBuilder().temperature(0));
        assertThrows(IllegalArgumentException.class, () -> new FluidBuilder().luminosity(16));
        assertThrows(IllegalArgumentException.class, () -> new FluidBuilder().viscosity(-1));
        assertEquals(0, new FluidBuilder().name("nan").density(Double.NaN).viscosity(Double.NaN).build("susy",null,null).getDensity());
        assertEquals(Integer.MIN_VALUE, new FluidBuilder().name("zero").density(0.0).build("susy",null,null).getDensity());
        assertEquals(Integer.MAX_VALUE, new FluidBuilder().name("neg_zero").density(-0.0).build("susy",null,null).getDensity());
        assertEquals(0, new FluidBuilder().name("air").density(0.001225).build("susy",null,null).getDensity());
    }
    @Test void whiteAndCustomTexturesDisableColoring() {
        var value = material(1, "colored").liquid().color(0x123456).build();
        var white = new FluidBuilder().color(0xffffff).build("susy",value,env.storageKeys().LIQUID);
        assertEquals(0xffffffff, white.getColor());
        var custom = new FluidBuilder().name("custom").customStill().customFlow().build("susy",value,env.storageKeys().LIQUID);
        assertEquals("susy:blocks/fluids/fluid.custom", custom.getStill().toString());
        assertEquals("susy:blocks/fluids/fluid.custom_flow", custom.getFlowing().toString());
        assertEquals(0xffffffff, custom.getColor());
    }
    @Test void duplicateRegistrationKeepsMasterAndDelegateSideEffects() {
        var first = bare("same");
        env.registry().registerFluid(first);
        runtime.activeMod("foreign");
        var second = bare("same");
        assertFalse(env.registry().registerFluid(second));
        assertSame(first, env.registry().getFluid("same"));
        assertSame(second, env.registry().masterFluidReference.get("foreign:same"));
        assertTrue(env.registry().delegates.containsKey(second));
        assertFalse(env.registry().isFluidDefault(second));
        assertTrue(env.registry().isFluidRegistered(second)); // Name-based, not identity-based.
        assertEquals(1, env.registry().getMaxID());
    }
    @Test void bucketSnapshotIsNotInvalidatedByLaterAdditions() {
        var first = new FluidBuilder().name("first").build("susy",null,null);
        var snapshot = env.registry().getBucketFluids();
        var second = new FluidBuilder().name("second").build("susy",null,null);
        assertTrue(snapshot.contains(first));
        assertFalse(snapshot.contains(second));
        assertSame(snapshot, env.registry().getBucketFluids());
        assertTrue(env.registry().hasBucket(second));
    }
    @Test void unifierUsesFluidNamesNotObjectIdentity() {
        var value = material(1,"material").liquid().build();
        var a = bare("same"); var b = bare("same");
        env.unifier().registerFluid(a,value);
        assertSame(value, env.unifier().getMaterialFromFluid(b));
    }
    @Test void eventOccursBeforeOwnershipRepairBucketsUnifierAndTooltip() {
        env.close();
        env = FluidEnvironment.eventProbe(runtime, event -> {
            var current = FluidEnvironment.current();
            var fluid = current.registry().getFluid(event.name());
            assertEquals("gregtech:event", current.registry().getDefaultFluidName(fluid));
            assertFalse(current.registry().hasBucket(fluid));
            assertNull(current.unifier().getMaterialFromFluid(fluid));
            assertTrue(current.tooltipBindings(fluid).isEmpty());
        });
        var result = new FluidBuilder().name("event").build("susy",null,null);
        assertEquals("susy:event", env.registry().getDefaultFluidName(result));
    }
    @Test void missingPackListenersStopWithoutRollingBackEarlierRegistryEffects() {
        env.close(); env = FluidEnvironment.unresolvedPack(runtime);
        var failure = assertThrows(Failure.class, () -> new FluidBuilder().name("event").build("susy",null,null));
        assertEquals("incomplete", failure.kind);
        var fluid = env.registry().getFluid("event");
        assertNotNull(fluid);
        assertEquals("gregtech:event", env.registry().getDefaultFluidName(fluid));
        assertTrue(env.sprites().isEmpty());
        assertFalse(env.registry().hasBucket(fluid));
    }
    @Test void worldBlockFailureRetainsEarlierConstructionAndLeavesQueueUnfinished() {
        var value = material(1,"blocked").liquid(new FluidBuilder().block()).build();
        var property = value.getProperty(FluidDomain.FLUID);
        runtime.materials().closeRegistries();
        assertThrows(Failure.class, env::registerMaterialFluids);
        var fluid = env.registry().getFluid("blocked");
        assertNotNull(fluid);
        assertTrue(env.registry().hasBucket(fluid));
        assertSame(value, env.unifier().getMaterialFromFluid(fluid));
        assertEquals(1, env.tooltipBindings(fluid).size());
        assertNull(value.getFluid()); // Queue stores only after build returns.
        assertNotNull(property.getQueuedBuilder(env.storageKeys().LIQUID));
    }
    @Test void primaryAndSolidificationSemanticsComeFromTheProperty() {
        var property = new FluidProperty();
        assertThrows(IllegalStateException.class, () -> property.verifyProperty(new MaterialProperties()));
        property.enqueueRegistration(env.storageKeys().GAS,new FluidBuilder());
        assertSame(env.storageKeys().GAS,property.getPrimaryKey());
        assertThrows(IllegalArgumentException.class, () -> property.enqueueRegistration(env.storageKeys().GAS,new FluidBuilder()));
        var liquid = bare("liquid"); property.store(env.storageKeys().LIQUID,liquid);
        assertSame(env.storageKeys().GAS,property.getPrimaryKey());
        assertSame(liquid,property.solidifiesFrom());
        var override = bare("override"); property.setSolidifyingFluid(override);
        assertSame(override,property.solidifiesFrom());
        property.setSolidifyingFluid(null);
        assertSame(liquid,property.solidifiesFrom());
    }
    @Test void missingNameAndInvalidMaterialNamesFailBeforeRegistration() {
        assertThrows(IllegalArgumentException.class, () -> new FluidBuilder().build("susy",null,null));
        assertThrows(IllegalArgumentException.class, () -> material(1,"bad_"));
        assertThrows(StringIndexOutOfBoundsException.class, () -> material(1,""));
        assertTrue(env.registry().fluids.isEmpty());
    }
    @Test void nestedEnvironmentsAreRejectedAndClosedEnvironmentCannotBeReused() {
        assertThrows(IllegalStateException.class, () -> FluidEnvironment.isolatedProducer(runtime));
        var old = env; old.close(); env = null;
        assertThrows(IllegalStateException.class, old::registry);
        assertThrows(IllegalStateException.class, FluidEnvironment::current);
    }
    public static class Worker {
        public static void main(String[] args) throws Exception {
            NativeRuntime.require(); WorkerIsolation.install();
            try (var runtime = RegistryRuntime.open(Path.of(args[0]));
                 var env = FluidEnvironment.isolatedProducer(runtime)) {
                runtime.materials().createRegistry("susy"); runtime.activeMod("gregtech");
                runtime.materials().unfreezeRegistries();
                var value = new FluidMaterial.Builder(1,new NativeLocation("susy","probe")).liquid().build();
                runtime.materials().closeRegistries(); runtime.materials().freezeRegistries();
                env.registerMaterialFluids();
                if (env.registry().getMaxID()!=1 || value.getFluid()!=env.registry().getFluid("probe"))
                    throw new AssertionError("Fluid identity or isolation differs");
                System.out.println(Json.write(Map.of("schema","axiom.result.v1","status","accepted","coverage",env.coverage())));
            }
        }
    }
    @Test void constructionRunsInsideFreshProductionSandboxWorkers() throws Exception {
        Path root = Path.of(System.getProperty("axiom.test.registryRoot"));
        for (int i=0;i<2;i++) {
            try (var worker = WorkerSandbox.launch(List.of(root),
                    Arrays.asList(System.getProperty("axiom.test.runtimeClasspath").split(File.pathSeparator)),
                    Worker.class.getName(), List.of(root.toString()))) {
                var result = Main.observe(worker.process(),new byte[0],65536,65536,20000);
                assertEquals("accepted",result.get("status"),result.toString());
            }
        }
    }
}
