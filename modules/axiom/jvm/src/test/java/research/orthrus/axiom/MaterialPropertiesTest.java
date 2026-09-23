package research.orthrus.axiom;

import org.junit.jupiter.api.Test;
import java.util.concurrent.atomic.AtomicReference;
import static org.junit.jupiter.api.Assertions.*;

class MaterialPropertiesTest {
    private static MaterialState material(String name) {
        return new MaterialState(name, MaterialPhase.OPEN::canModifyMaterials);
    }

    @Test void emptyMaterialGetsNativeEmptyPropertyAndRealPropertyRemovesIt() {
        var properties = material("empty").getProperties();
        assertTrue(properties.isEmpty());
        properties.verify();
        assertFalse(properties.isEmpty());
        assertNotNull(properties.getProperty(PropertyKey.EMPTY));
        properties.ensureSet(PropertyKey.DUST);
        assertFalse(properties.hasProperty(PropertyKey.EMPTY));
        assertEquals(2, properties.getProperty(PropertyKey.DUST).getHarvestLevel());
        assertEquals(0, properties.getProperty(PropertyKey.DUST).getBurnTime());
    }

    @Test void constructorsAndSettersRetainTheirDifferentValidation() {
        var dust = new DustProperty(0, -7);
        assertEquals(0, dust.getHarvestLevel());
        assertEquals(-7, dust.getBurnTime());
        assertEquals("Harvest Level must be greater than zero!", assertThrows(IllegalArgumentException.class,
                () -> dust.setHarvestLevel(0)).getMessage());
        assertEquals("Burn Time cannot be negative!", assertThrows(IllegalArgumentException.class,
                () -> dust.setBurnTime(-1)).getMessage());
        assertEquals(-7, dust.getBurnTime());
        dust.setHarvestLevel(Integer.MAX_VALUE);
        dust.setBurnTime(0);
    }

    @Test void verificationPropagatesIngotDependenciesAndSelfReferences() {
        var source = material("source");
        var target = material("target");
        var magnetic = material("magnetic");
        var ingot = new IngotProperty();
        ingot.setSmeltingInto(target);
        ingot.setArcSmeltingInto(target);
        ingot.setMacerateInto(target);
        ingot.setMagneticMaterial(magnetic);
        source.setProperty(PropertyKey.INGOT, ingot);
        for (var state : new MaterialState[]{source, target, magnetic}) {
            assertNotNull(state.getProperties().getProperty(PropertyKey.DUST));
            assertNotNull(state.getProperties().getProperty(PropertyKey.INGOT));
        }
        var targetIngot = target.getProperties().getProperty(PropertyKey.INGOT);
        assertSame(target, targetIngot.getSmeltingInto());
        assertSame(target, targetIngot.getArcSmeltInto());
        assertSame(target, targetIngot.getMacerateInto());
        assertSame(magnetic, ingot.getMagneticMaterial());
    }

    @Test void cyclicCrossMaterialReferencesTerminateWithoutAReplacementGraphSolver() {
        var a = material("a");
        var b = material("b");
        var aIngot = new IngotProperty();
        var bIngot = new IngotProperty();
        aIngot.setSmeltingInto(b);
        bIngot.setSmeltingInto(a);
        a.getProperties().setProperty(PropertyKey.INGOT, aIngot);
        b.getProperties().setProperty(PropertyKey.INGOT, bIngot);
        a.getProperties().verify();
        assertTrue(a.getProperties().hasProperty(PropertyKey.DUST));
        // ensureSet does not reverify a property already present on b.
        assertFalse(b.getProperties().hasProperty(PropertyKey.DUST));
        b.getProperties().verify();
        assertTrue(b.getProperties().hasProperty(PropertyKey.DUST));
    }

    @Test void conflictingPropertiesAreNotRolledBackAfterFailure() {
        for (boolean gemFirst : new boolean[]{true, false}) {
            var state = material("conflict");
            if (gemFirst) state.setProperty(PropertyKey.GEM, new GemProperty());
            else state.setProperty(PropertyKey.INGOT, new IngotProperty());
            var failure = assertThrows(IllegalStateException.class, () -> {
                if (gemFirst) state.setProperty(PropertyKey.INGOT, new IngotProperty());
                else state.setProperty(PropertyKey.GEM, new GemProperty());
            });
            assertEquals("Material conflict has both Ingot and Gem Property, which is not allowed!", failure.getMessage());
            assertTrue(state.getProperties().hasProperty(PropertyKey.GEM));
            assertTrue(state.getProperties().hasProperty(PropertyKey.INGOT));
            assertTrue(state.getProperties().hasProperty(PropertyKey.DUST));
        }
    }

    @Test void rejectionInReferencedMaterialPreservesEarlierDependencyEffects() {
        var source = material("source");
        var target = material("gem_target");
        target.setProperty(PropertyKey.GEM, new GemProperty());
        var ingot = new IngotProperty();
        ingot.setSmeltingInto(target);
        assertThrows(IllegalStateException.class, () -> source.setProperty(PropertyKey.INGOT, ingot));
        assertTrue(source.getProperties().hasProperty(PropertyKey.DUST));
        assertTrue(target.getProperties().hasProperty(PropertyKey.INGOT));
        assertNull(ingot.getArcSmeltInto());
    }

    @Test void duplicateAndNullRejectionPreserveOriginalValue() {
        var properties = material("dust").getProperties();
        var original = new DustProperty();
        properties.setProperty(PropertyKey.DUST, original);
        assertEquals("Material Property dust already registered!", assertThrows(IllegalArgumentException.class,
                () -> properties.setProperty(PropertyKey.DUST, new DustProperty())).getMessage());
        assertEquals("Material Property must not be null!", assertThrows(IllegalArgumentException.class,
                () -> properties.setProperty(PropertyKey.DUST, null)).getMessage());
        assertSame(original, properties.getProperty(PropertyKey.DUST));
    }

    @Test void keyIdentityIsItsNameNotItsRuntimeTypeAndCastIsDeferred() {
        var properties = material("keys").getProperties();
        var alias = new PropertyKey<GemProperty>("dust", GemProperty.class);
        assertEquals(PropertyKey.DUST, alias);
        assertEquals(PropertyKey.DUST.hashCode(), alias.hashCode());
        properties.setProperty(PropertyKey.DUST, new GemProperty());
        assertTrue(properties.hasProperty(PropertyKey.DUST));
        assertThrows(ClassCastException.class, () -> properties.getProperty(PropertyKey.DUST));
        assertNotNull(properties.getProperty(alias));
    }

    public static class NoDefault implements IMaterialProperty {
        public NoDefault(int ignored) {}
        @Override public void verifyProperty(MaterialProperties properties) {}
    }

    @Test void failedDefaultConstructionRetainsUpstreamNullEntryAndLaterFailure() {
        var properties = material("missing_constructor").getProperties();
        properties.verify();
        var key = new PropertyKey<>("missing_constructor", NoDefault.class);
        properties.ensureSet(key);
        assertFalse(properties.hasProperty(key));
        assertFalse(properties.hasProperty(PropertyKey.EMPTY));
        assertFalse(properties.isEmpty());
        assertThrows(NullPointerException.class, properties::verify);
    }

    @Test void ensureExistingDoesNotReverifyEvenWhenRequested() {
        var properties = material("deferred").getProperties();
        properties.setProperty(PropertyKey.GEM, new GemProperty());
        properties.ensureSet(PropertyKey.GEM, true);
        assertFalse(properties.hasProperty(PropertyKey.DUST));
        properties.verify();
        assertTrue(properties.hasProperty(PropertyKey.DUST));
    }

    @Test void materialApiAndDirectPropertyAccessHaveDifferentPhaseGuards() {
        var phase = new AtomicReference<>(MaterialPhase.PRE);
        var state = new MaterialState("phase", () -> phase.get().canModifyMaterials());
        assertThrows(IllegalStateException.class, () -> state.setProperty(PropertyKey.DUST, new DustProperty()));
        assertTrue(state.getProperties().isEmpty());
        phase.set(MaterialPhase.OPEN);
        state.setProperty(PropertyKey.DUST, new DustProperty());
        phase.set(MaterialPhase.CLOSED);
        state.setProperty(PropertyKey.GEM, new GemProperty());
        phase.set(MaterialPhase.FROZEN);
        assertThrows(IllegalStateException.class, () -> state.setProperty(PropertyKey.INGOT, new IngotProperty()));
        // This upstream escape hatch must not silently acquire a new phase check.
        state.getProperties().setProperty(PropertyKey.INGOT, new IngotProperty());
        assertTrue(state.getProperties().hasProperty(PropertyKey.INGOT));
    }

    public static class CustomBase implements IMaterialProperty {
        @Override public void verifyProperty(MaterialProperties properties) {}
    }

    @Test void addingBaseTypeChangesVerificationForExistingCollections() {
        var properties = material("extension").getProperties();
        var key = new PropertyKey<>("test_only_base", CustomBase.class);
        properties.setProperty(key, new CustomBase());
        assertThrows(IllegalArgumentException.class, properties::verify);
        MaterialProperties.addBaseType(key);
        properties.verify();
        assertSame(properties.getProperty(key).getClass(), CustomBase.class);
    }
}
