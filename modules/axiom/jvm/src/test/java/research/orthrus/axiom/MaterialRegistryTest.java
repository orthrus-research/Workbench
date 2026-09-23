package research.orthrus.axiom;

import org.junit.jupiter.api.*;
import java.io.*;
import java.nio.file.*;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;

class MaterialRegistryTest {
    private RegistryRuntime runtime;

    @BeforeEach void open() {
        String root = System.getProperty("axiom.test.registryRoot");
        Assumptions.assumeTrue(root != null, "Use tools/build_axiom.py for pinned native registry inputs");
        runtime = RegistryRuntime.open(Path.of(root));
    }
    @AfterEach void close() throws IOException { if (runtime != null) runtime.close(); }
    private MaterialState material(int id, String namespace, String name) {
        return new MaterialState(id, namespace, name, runtime.materials()::canModifyMaterials);
    }

    @Test void missingOrResizedOriginalInputsCannotCreateARegistry(@org.junit.jupiter.api.io.TempDir Path directory) throws IOException {
        assertThrows(Failure.class, () -> RegistryRuntime.open(directory));
        Path jar = directory.resolve("com/mojang/minecraft/1.12.2/minecraft-1.12.2-client.jar");
        Files.createDirectories(jar.getParent()); Files.writeString(jar, "not a registry");
        assertThrows(Failure.class, () -> RegistryRuntime.open(directory));
    }

    @Test void indirectOriginalInputRootsAreRejected(@org.junit.jupiter.api.io.TempDir Path directory) throws IOException {
        Path alias = directory.resolve("alias");
        Files.createSymbolicLink(alias, Path.of(System.getProperty("axiom.test.registryRoot")));
        assertThrows(Failure.class, () -> RegistryRuntime.open(alias));
        assertThrows(Failure.class, () -> RegistryRuntime.open(alias.resolve("com")));
    }

    @Test void identityDoesNotClaimInstalledCompositionOrGameExecution() {
        assertEquals(false, runtime.identity().get("minecraftLaunched"));
        assertEquals(false, runtime.identity().get("installedCompositionQualified"));
        assertEquals("native-untransformed-registry-utilities", runtime.identity().get("execution"));
        assertEquals(5, Json.array(runtime.identity().get("runtimeFiles")).size());
    }

    @Test void registryLifecycleAndClosedMaterialEditsRemainDifferent() {
        var manager = runtime.materials();
        assertEquals(MaterialPhase.PRE, manager.getPhase());
        assertThrows(IllegalStateException.class, manager::getRegistries);
        assertThrows(IllegalStateException.class, manager::getRegisteredMaterials);
        var susy = manager.createRegistry("susy");
        assertEquals(1, susy.getNetworkId());
        assertSame(susy, manager.getRegistry(1));
        runtime.activeMod("gregtech");
        manager.unfreezeRegistries();
        var borax = material(2002, "gregtech", "borax");
        borax.setProperty(PropertyKey.DUST, new DustProperty());
        manager.getDefaultRegistry().register(borax);
        assertThrows(IllegalStateException.class, () -> manager.createRegistry("late"));
        manager.closeRegistries();
        assertEquals(List.of(borax), new ArrayList<>(manager.getRegisteredMaterials()));
        borax.setProperty(PropertyKey.GEM, new GemProperty());
        manager.getDefaultRegistry().register(material(2, "gregtech", "late"));
        assertNull(manager.getMaterial("late"));
        assertEquals(1, runtime.diagnostics().size());
        manager.freezeRegistries();
        assertEquals(MaterialPhase.FROZEN, manager.getPhase());
        assertThrows(IllegalStateException.class, () -> borax.setProperty(PropertyKey.INGOT, new IngotProperty()));
        assertEquals(List.of(borax), new ArrayList<>(manager.getRegisteredMaterials()));
    }

    @Test void unknownRegistryFallsBackButMissingMaterialDoesNot() {
        var manager = runtime.materials();
        var defaultRegistry = manager.getDefaultRegistry();
        var fallback = material(1, "gregtech", "fallback");
        defaultRegistry.register(fallback);
        defaultRegistry.setFallbackMaterial(fallback);
        var addon = manager.createRegistry("addon");
        assertSame(defaultRegistry, manager.getRegistry("unknown"));
        assertSame(defaultRegistry, manager.getRegistry(Integer.MIN_VALUE));
        assertSame(fallback, manager.getMaterial("unknown:fallback"));
        assertNull(manager.getMaterial("addon:fallback"));
        assertNull(manager.getMaterial(""));
        assertNull(addon.getObjectById(1));
        assertSame(fallback, addon.getFallbackMaterial());
        var replacement = material(2, "gregtech", "replacement");
        defaultRegistry.setFallbackMaterial(replacement);
        assertSame(fallback, addon.getFallbackMaterial()); // fallback is cached lazily
    }

    @Test void duplicateIdFailureKeepsNameInsertionAndNoIntegerIdentity() {
        var registry = runtime.materials().getDefaultRegistry();
        var a = material(1, "gregtech", "a");
        var b = material(1, "gregtech", "b");
        registry.register(a);
        assertThrows(IllegalArgumentException.class, () -> registry.register(b));
        assertSame(b, registry.getObject("b"));
        assertSame(a, registry.getObjectById(1));
        assertEquals(-1, registry.getIDForObject(b));
        assertEquals(List.of(a, b), new ArrayList<>(registry.getAllMaterials()));
    }

    @Test void nameReplacementDoesNotErasePreviousIntegerIdentity() {
        var registry = runtime.materials().getDefaultRegistry();
        var a = material(1, "gregtech", "same");
        var b = material(2, "gregtech", "same");
        registry.register(a); registry.register(b);
        assertSame(b, registry.getObject("same"));
        assertSame(a, registry.getObjectById(1));
        assertNull(registry.getNameForObject(a));
        assertEquals(List.of(b), new ArrayList<>(registry.getAllMaterials()));
        assertEquals(List.of(a, b), values(registry));
    }

    @Test void registeringSameObjectAtTwoIdsUsesOriginalIdentityMapBehavior() {
        var registry = runtime.materials().getDefaultRegistry();
        var a = material(1, "gregtech", "same");
        registry.register(a); registry.register(2, "same", a);
        assertSame(a, registry.getObjectById(1));
        assertSame(a, registry.getObjectById(2));
        assertEquals(1, registry.getIDForObject(a)); // not IdentityHashMap last-put-wins
        assertEquals(List.of(a, a), values(registry));
    }

    @Test void nameBimapRejectsOneValueAtTwoNamesBeforeIntegerInsertion() {
        var registry = runtime.materials().getDefaultRegistry();
        var a = material(1, "gregtech", "a"); registry.register(a);
        assertThrows(IllegalArgumentException.class, () -> registry.register(2, "b", a));
        assertNull(registry.getObject("b"));
        assertNull(registry.getObjectById(2));
    }

    @Test void boundsAreCheckedBeforeNamesButCloseSkipsEvenInvalidIds() {
        var registry = runtime.materials().getDefaultRegistry();
        var a = material(-1, "gregtech", "a");
        assertThrows(IndexOutOfBoundsException.class, () -> registry.register(a));
        assertThrows(IndexOutOfBoundsException.class, () -> registry.register(Short.MAX_VALUE, "a", a));
        assertNull(registry.getObject("a"));
        runtime.materials().closeRegistries();
        registry.register(a);
        assertEquals(1, runtime.diagnostics().size());
    }

    @Test void managerPhaseCanMoveWhenActiveModDoesNotUnfreezeBackingRegistries() {
        var manager = runtime.materials();
        runtime.activeMod("addon"); manager.unfreezeRegistries();
        assertEquals(MaterialPhase.OPEN, manager.getPhase());
        assertTrue(manager.getDefaultRegistry().isFrozen());
        assertTrue(manager.canModifyMaterials());
        assertThrows(IllegalStateException.class, manager::freezeRegistries);
        assertEquals(MaterialPhase.OPEN, manager.getPhase());
        runtime.activeMod("gregtech"); manager.unfreezeRegistries();
        assertFalse(manager.getDefaultRegistry().isFrozen());
    }

    @Test void frozenFlagAloneDoesNotBlockDirectRegistration() {
        var registry = runtime.materials().getDefaultRegistry();
        assertTrue(registry.isFrozen());
        registry.register(material(1, "gregtech", "direct"));
        assertNotNull(registry.getObject("direct"));
        assertThrows(UnsupportedOperationException.class, () -> registry.putObject("x", material(2, "gregtech", "x")));
        assertEquals(0, registry.getIdByObjectName("missing"));
    }

    @Test void closeSnapshotsAndLiveRegistryViewsPreserveSourceSemantics() {
        var manager = runtime.materials();
        var registry = manager.getDefaultRegistry();
        var live = registry.getAllMaterials();
        var a = material(1, "gregtech", "a"); registry.register(a);
        assertEquals(List.of(a), new ArrayList<>(live));
        manager.closeRegistries();
        var snapshot = manager.getRegisteredMaterials();
        assertThrows(UnsupportedOperationException.class, snapshot::clear);
        assertThrows(UnsupportedOperationException.class, live::clear);
        assertEquals(List.of(a), new ArrayList<>(snapshot));
    }

    @Test void contextsOwnSeparateNativeRegistriesCountersAndCloseAuthority() throws IOException {
        runtime.materials().createRegistry("addon");
        try (var separate = RegistryRuntime.open(Path.of(System.getProperty("axiom.test.registryRoot")))) {
            assertEquals(0, separate.materials().getDefaultRegistry().getNetworkId());
            assertEquals(1, separate.materials().createRegistry("addon").getNetworkId());
        }
        var registry = runtime.materials().getDefaultRegistry();
        runtime.close();
        assertThrows(IllegalStateException.class, () -> registry.getObject("x"));
    }

    public static class Worker {
        public static void main(String[] args) throws Exception {
            NativeRuntime.require(); WorkerIsolation.install();
            try (var runtime = RegistryRuntime.open(Path.of(args[0]))) {
                var manager = runtime.materials();
                if (manager.getPhase() != MaterialPhase.PRE || manager.getDefaultRegistry().getNetworkId() != 0)
                    throw new AssertionError("Registry state leaked");
                runtime.activeMod("gregtech"); manager.unfreezeRegistries();
                var material = new MaterialState(1, "gregtech", "native", manager::canModifyMaterials);
                material.setProperty(PropertyKey.DUST, new DustProperty());
                manager.getDefaultRegistry().register(material); manager.closeRegistries(); manager.freezeRegistries();
                if (manager.getMaterial("native") != material) throw new AssertionError("Native registry lookup failed");
                System.out.println(Json.write(Map.of("schema", "axiom.result.v1", "status", "accepted", "runtime", runtime.identity())));
            }
        }
    }

    @Test void nativeRegistriesRunInFreshProductionSandboxWorkers() throws Exception {
        Path root = Path.of(System.getProperty("axiom.test.registryRoot"));
        for (int i = 0; i < 2; i++) {
            var command = Main.sandboxCommand(List.of(root), Arrays.asList(System.getProperty("axiom.test.runtimeClasspath").split(File.pathSeparator)),
                    Worker.class.getName(), List.of(root.toString()));
            var process = new ProcessBuilder(command); process.environment().clear();
            assertEquals("accepted", Main.observe(process.start(), new byte[0], 65536, 65536, 20_000).get("status"));
        }
    }
    private static List<MaterialState> values(MaterialRegistry registry) {
        var values = new ArrayList<MaterialState>(); registry.forEach(values::add); return values;
    }
}
