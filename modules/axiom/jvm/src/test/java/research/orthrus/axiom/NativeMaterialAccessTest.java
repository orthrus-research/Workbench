package research.orthrus.axiom;

import java.net.URL;
import java.util.Set;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class NativeMaterialAccessTest {
    @Test void oreAdmissionRequiresPinnedProgramRuleCarrier() {
        assertThrows(IllegalArgumentException.class, () -> new NativeMaterialClassLoader(new URL[0], Set.of(), true));
    }
    @Test void ordinaryContextDoesNotApplyOreTransforms() throws Exception {
        try (var loader = new NativeMaterialClassLoader(new URL[0], Set.of("research/orthrus/axiom/nativeconstruction/OreAccessRules.class"), false)) {
            assertTrue(loader.accessTransformations().isEmpty());
            ClassNotFoundException failure = assertThrows(ClassNotFoundException.class, () -> loader.loadClass("net.minecraft.block.Block"));
            assertEquals("net.minecraft.block.Block", failure.getMessage());
            assertTrue(loader.accessTransformations().isEmpty());
        }
    }
    @Test void unusedOreContextDoesNotClaimATransformation() throws Exception {
        try (var loader = new NativeMaterialClassLoader(new URL[0], Set.of("research/orthrus/axiom/nativeconstruction/OreAccessRules.class"), true)) {
            assertTrue(loader.accessTransformations().isEmpty());
        }
    }
}
