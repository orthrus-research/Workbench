package research.orthrus.axiom;

import com.google.common.collect.ImmutableList;
import org.junit.jupiter.api.*;
import java.io.IOException;
import java.nio.file.Path;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;

/** Native construction fixtures; none is a supplied pack registry. */
class MaterialConstructionTest {
    private RegistryRuntime runtime;
    private FluidEnvironment env;
    private int nextId;
    @BeforeEach void open() {
        String root = System.getProperty("axiom.test.registryRoot");
        Assumptions.assumeTrue(root != null, "Use the profile-selected registry inputs");
        runtime = RegistryRuntime.open(Path.of(root));
        env = FluidEnvironment.isolatedProducer(runtime);
        runtime.activeMod("gregtech");
        runtime.materials().unfreezeRegistries();
    }
    @AfterEach void close() throws IOException {
        if (env != null) env.close();
        if (runtime != null) runtime.close();
    }
    private FluidMaterial.Builder builder(String name) {
        return new FluidMaterial.Builder(++nextId, new NativeLocation("gregtech", name));
    }
    private FluidMaterial element(String name, Element e, int color) {
        return builder(name).dust().element(e).color(color).build();
    }
    @Test void actualElementCatalogAndFormulaFormattingAreRetained() {
        assertNull(Elements.get("HELIUM_3"));
        assertSame(Elements.He3, Elements.get("HELIUM-3"));
        assertEquals("He-3", Elements.He3.getSymbol());
        assertEquals(98, Elements.Tc.getMass());
        assertEquals("U-235", SmallDigits.toSmallDownNumbers("U-235"));
        assertEquals("C₆H₄-12N₂", SmallDigits.toSmallDownNumbers("C6H4-12N2"));
        // This apparently odd upstream up-number constant also uses subscripts.
        assertEquals("H₂", SmallDigits.toSmallUpNumbers("H2"));
        assertThrows(UnsupportedOperationException.class, () -> Elements.getAllElements().clear());
    }
    @Test void nestedCompositionFormulaMassRadioactivityAndGroovyMultiplyAreNative() {
        var h = element("hydrogen", Elements.H, 0x112233);
        var o = element("oxygen", Elements.O, 0x445566);
        var water = builder("water").liquid().components(h, 2, o, 1).build();
        assertEquals("H₂O", water.getChemicalFormula());
        assertEquals("(H₂O)₃", water.multiply(3).toFormatted());
        assertEquals(6, water.getMass());
        assertEquals(3, water.getProtons());
        assertEquals(2, water.getNeutrons());
        assertEquals("H₂O", builder("same_formula").components(water, 1).build().getChemicalFormula());
        assertEquals("(H₂O)₂", builder("nested_formula").components(water.multiply(2)).build().getChemicalFormula());
        assertFalse(water.isRadioactive());
        var radioactive = element("radioactive", Elements.Nq, 0);
        assertTrue(builder("radioactive_composite").components(water, 1, radioactive, 1).build().isRadioactive());
        assertSame(water, water.setFormula("H2O", true));
        assertEquals("H₂O", water.getChemicalFormula());
        assertEquals(h.multiply(4), h.multiply(4).copy());
        assertEquals(h.multiply(4).hashCode(), h.multiply(2).hashCode());
        assertThrows(UnsupportedOperationException.class, () -> water.getMaterialComponents().clear());
    }
    @Test void weightedPackedColorAndZeroTotalsAreNotNormalized() {
        var a = element("alpha", Elements.H, 0xff0000);
        var b = element("beta", Elements.O, 0x0000ff);
        var c = builder("average").components(a, 1, b, 1).colorAverage().build();
        assertEquals((0xff0000L + 0x0000ffL) / 2, c.getMaterialRGB());
        assertEquals(0xffffff, builder("not_average").components(a, 1, b, 1).build().getMaterialRGB());
        assertEquals(7, builder("explicit").components(a, 1, b, 1).colorAverage().color(7).build().getMaterialRGB());
        assertThrows(ArithmeticException.class, () -> builder("zero_color").components(a, 1, b, -1).colorAverage().build());
        assertNull(runtime.materials().getMaterial("gregtech:zero_color"));
        var zero = builder("zero_mass").components(a, 1, b, -1).build();
        assertThrows(ArithmeticException.class, zero::getMass);
    }
    @Test void compositionOverloadsPreserveFixedListsCopyingAndPartialEffects() {
        var a = element("alpha", Elements.H, 0);
        var b = element("beta", Elements.O, 0);
        var fixed = builder("fixed").components(a.multiply(1));
        assertThrows(UnsupportedOperationException.class, () -> fixed.components(b, 1));
        assertEquals(List.of(a.multiply(1)), fixed.build().getMaterialComponents());
        var immutable = builder("immutable").components(ImmutableList.of(a.multiply(1)));
        assertThrows(UnsupportedOperationException.class, () -> immutable.components(b, 1));
        var partial = builder("partial");
        assertThrows(IllegalArgumentException.class, () -> partial.components(a, 1, null, 2));
        assertEquals(List.of(a.multiply(1)), partial.build().getMaterialComponents());
        assertThrows(ClassCastException.class, () -> builder("long_amount").components(a, 1L));
        assertThrows(IllegalArgumentException.class, () -> builder("odd").components((Object) a));
        assertThrows(NullPointerException.class, () -> builder("null_stack").components(new MaterialStack[]{null}).build());
    }
    @Test void ingotAndGemDefaultsAreConditionalAndSetterFailuresRemainVisible() {
        var defaulted = builder("defaulted").dust().ingot(4, 600).build().getProperty(PropertyKey.DUST);
        assertEquals(4, defaulted.getHarvestLevel()); assertEquals(600, defaulted.getBurnTime());
        var explicit = builder("explicit").dust(1, 300).ingot(4, 600).build().getProperty(PropertyKey.DUST);
        assertEquals(1, explicit.getHarvestLevel()); assertEquals(300, explicit.getBurnTime());
        assertThrows(IllegalArgumentException.class, () -> builder("setter_failure").dust().ingot(0));
        assertEquals(0, builder("constructor_allows_zero").ingot(0).build().getProperty(PropertyKey.DUST).getHarvestLevel());
        assertEquals(MaterialIconSet.GEM_VERTICAL, builder("gem").gem().build().getMaterialIconSet());
        assertThrows(IllegalStateException.class, () -> builder("gem_and_ingot").gem().ingot().build());
    }
    @Test void polymerVerificationAndFlagDependencyClosureAreReal() {
        var polymer = builder("polymer").polymer(1).flags(MaterialFlags.GENERATE_FINE_WIRE).build();
        assertTrue(polymer.hasProperty(PropertyKey.DUST)); assertTrue(polymer.hasProperty(PropertyKey.INGOT));
        assertTrue(polymer.hasFlags(MaterialFlags.FLAMMABLE, MaterialFlags.NO_SMASHING,
                MaterialFlags.DISABLE_DECOMPOSITION, MaterialFlags.GENERATE_FOIL, MaterialFlags.GENERATE_PLATE));
        assertEquals(1, polymer.getProperty(PropertyKey.DUST).getHarvestLevel());
        var metal = builder("metal").ingot().build();
        var alloy = builder("alloy").components(metal, 2).build();
        assertTrue(alloy.hasFlag(MaterialFlags.DECOMPOSITION_BY_CENTRIFUGING));
        var mixed = builder("mixed").components(metal, 1, builder("dust").dust().build(), 1).build();
        assertTrue(mixed.hasFlag(MaterialFlags.DECOMPOSITION_BY_ELECTROLYZING));
        metal.addFlags("generate_long_rod", "unknown_flag");
        assertTrue(metal.hasFlag(MaterialFlags.GENERATE_ROD));
        runtime.materials().closeRegistries(); metal.addFlags(MaterialFlags.GENERATE_GEAR);
        runtime.materials().freezeRegistries();
        assertThrows(IllegalStateException.class, () -> metal.addFlags(MaterialFlags.GLOWING));
    }
    @Test void flagPropertyWarningsDoNotInventRequiredProperties() {
        var fluid = builder("fluid_only").liquid().flags(MaterialFlags.GENERATE_FINE_WIRE).build();
        assertFalse(fluid.hasProperty(PropertyKey.INGOT));
        assertFalse(fluid.hasProperty(PropertyKey.DUST));
        assertTrue(fluid.hasFlag(MaterialFlags.GENERATE_PLATE));
        assertTrue(env.effects().stream().anyMatch(e -> e instanceof FluidEnvironment.Log log && log.level().equals("warn")));
    }
    @Test void oreVerificationMutatesOtherMaterialsAndUsesOriginalClamp() {
        var byproduct = builder("byproduct").build();
        var separated = builder("separated").build();
        var smelted = builder("smelted").build();
        var ore = builder("ore").ore(3, 2, true).addOreByproducts(byproduct).separatedInto(separated).oreSmeltInto(smelted).build();
        var p = ore.getProperty(PropertyKey.ORE);
        for (var m : List.of(ore, byproduct, separated, smelted)) assertTrue(m.hasProperty(PropertyKey.DUST));
        assertSame(byproduct, p.getOreByProduct(Integer.MIN_VALUE));
        assertSame(byproduct, p.getOreByProduct(Integer.MAX_VALUE));
        p.setWashedIn(smelted, -7); // Setter does not validate the amount or trigger verification.
        assertEquals(-7, p.getWashedIn().getRight());
        assertSame(smelted, p.getWashedIn().getLeft());
        assertThrows(IllegalStateException.class, () -> p.verifyProperty(ore.getProperties()));
        assertTrue(smelted.hasProperty(PropertyKey.FLUID)); // Failing verification keeps prior mutation.
    }
    @Test void pipeConflictsDoNotRollbackEarlierPropertyDependencies() {
        var material = builder("pipe").fluidPipeProperties(300, 10, true, true, false, false).build();
        assertTrue(material.hasProperty(PropertyKey.INGOT));
        var pipe = material.getProperty(PropertyKey.FLUID_PIPE);
        assertTrue(pipe.isAcidProof()); assertTrue(pipe.canContain(FluidState.GAS));
        assertFalse(pipe.canContain(FluidState.PLASMA));
        assertThrows(IllegalStateException.class, () -> material.setProperty(PropertyKey.ITEM_PIPE, new ItemPipeProperties()));
        assertTrue(material.hasProperty(PropertyKey.ITEM_PIPE));
    }
    @Test void woodPipeCannotHideMissingPrefixEffects() {
        var wood = builder("wood").wood().build();
        assertTrue(wood.hasFlag(MaterialFlags.FLAMMABLE));
        Failure failure = assertThrows(Failure.class, () -> wood.setProperty(PropertyKey.FLUID_PIPE, new FluidPipeProperties()));
        assertEquals("incomplete", failure.kind);
        assertEquals("material.ore-prefix", failure.rule);
        assertTrue(wood.hasProperty(PropertyKey.FLUID_PIPE));
    }
    @Test void cableNarrowingAndFoilPolicyUseNativeVoltageConstants() {
        var normal = builder("wire").ingot().cableProperties(MaterialVoltages.V[MaterialVoltages.IV], 1, 5).build();
        assertTrue(normal.hasFlag(MaterialFlags.GENERATE_FOIL));
        var superconductor = builder("superconductor").ingot().cableProperties(Long.MAX_VALUE, 2, 100, true, 9).build();
        var p = superconductor.getProperty(PropertyKey.WIRE);
        assertEquals(-1, p.getVoltage()); assertEquals(0, p.getLossPerBlock());
        assertEquals(9, p.getSuperconductorCriticalTemperature());
        assertFalse(superconductor.hasFlag(MaterialFlags.GENERATE_FOIL));
    }
    @Test void toolAndRotorNativeConstructionRetainsOpaqueKeysButCreatesNoEnchantments() {
        EnchantmentIdentity fixtureKey = new EnchantmentIdentity() {}; // Test identity, not a vanilla registry claim.
        var p = ToolProperty.Builder.of(4, 2, 600, 3).enchantment(fixtureKey, 3).magnetic().build();
        var tool = builder("tool").toolStats(p).rotorStats(1, 2, 3).build();
        assertTrue(tool.hasProperty(PropertyKey.INGOT));
        assertSame(p, tool.getProperty(PropertyKey.TOOL));
        assertEquals(3, p.getEnchantments().get(fixtureKey).getLevel(2));
        p.addEnchantmentForTools(fixtureKey, 2, 0.5);
        assertEquals(3, p.getEnchantments().get(fixtureKey).getLevel(3));
        assertEquals(127, new EnchantmentLevel(1000, 0).getLevel(0));
        assertEquals(-11, new EnchantmentLevel(-10.5, 0).getLevel(0));
        assertThrows(IllegalArgumentException.class, () -> tool.getProperty(PropertyKey.ROTOR).setSpeed(0));
        assertNull(PropertyKey.ROTOR.constructDefault()); // No default constructor upstream.
    }
    @Test void existingFluidsKeepIdentityAndPostRegistrationTooltipBinding() {
        var existing = new NativeFluid("external", new NativeLocation("test:still"), new NativeLocation("test:flow"));
        var material = builder("external").fluid(existing, env.storageKeys().LIQUID, FluidState.LIQUID).build();
        assertSame(existing, material.getFluid());
        assertSame(material, runtime.materials().getMaterial("gregtech:external"));
        assertEquals(1, env.tooltipBindings(existing).size());
        assertNull(env.registry().getFluid("external")); // Storing is not Forge registration.
    }
    @Test void markerInterningIsSeparateAndLeavesNativeUninitializedMetadata() {
        var marker = MarkerMaterial.create("null");
        assertSame(marker, MarkerMaterial.create("null"));
        assertSame(marker, env.markers().getMarkerMaterial("null"));
        assertNull(runtime.materials().getMaterial("gregtech:null"));
        assertNull(marker.getChemicalFormula());
        assertNull(marker.getMaterialComponents());
        assertNull(marker.getProperties().getMaterial());
        assertThrows(NullPointerException.class, marker::getMass);
        assertThrows(UnsupportedOperationException.class, () -> env.markers().getAll().clear());
    }
}
