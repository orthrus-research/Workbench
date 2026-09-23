package research.orthrus.axiom.materialhost;

import org.junit.jupiter.api.Assumptions;
import org.junit.jupiter.api.Test;
import org.objectweb.asm.Opcodes;
import org.objectweb.asm.ClassReader;
import org.objectweb.asm.tree.*;

import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.*;
import java.util.jar.Manifest;
import java.util.zip.ZipFile;

import static org.junit.jupiter.api.Assertions.*;

/** Static original-artifact assertions only: no native classes or callbacks are executed. */
class NativePackEarlyArtifactTest {
    private record Artifact(String file, String sha256, String plugin, String name, int order,
                            String container, String transformer, String accessTransformer, String accessResource) {}

    // Original LibraryManager sorts candidate file names case-insensitively.
    private static final List<Artifact> INPUTS = List.of(
            new Artifact("Bubbles-2.4.10.jar", "8a54cb9abafb14ce66bbc61fbfe5846fdeb3a4cf6ca6ff3bbd6bfe7b16f595f2",
                    "baubles.core.BubblesCore", "Bubbles", 100, null, null, "baubles.core.BubblesTransformer", "baubles_at.cfg"),
            new Artifact("CodeChickenLib-1.12.2-3.2.3.358-universal.jar", "5f8499f7ee2590e45110bbf296b2bf101aec15897e28d5e9759cf8f2387d8a74",
                    null, null, 0, null, null, null, "ccl_at.cfg"),
            new Artifact("geckolib-forge-1.12.2-3.0.31.jar", "b00bc9de4e3e94cea0455f8d58b295c1464272135e842bdf3170c2c955c871a5",
                    null, null, 0, null, null, null, null),
            new Artifact("GregicalityMultiblocks-1.2.11.jar", "dc250750b86c4d95bb68fe1681d48ce912efa1472ec0355c30e625d41a0591da",
                    null, null, 0, null, null, null, null),
            new Artifact("gregtech-1.12.2-2.8.10-beta.jar", "54744bb11ea4679df4b55846d8073e9bb2ef8c1c53aae8aee41cda3fc22d927e",
                    "gregtech.asm.GregTechLoadingPlugin", "GregTechLoadingPlugin", 1001, null,
                    "gregtech.asm.GregTechTransformer", null, "gregtech_at.cfg"),
            new Artifact("gregtechfoodoption-1.12.2-1.12.10.jar", "2d57d4313f02bf05ef411f063b5ab93c988cdda2dfef1f0ff6789ae68aaa72ba",
                    "gregtechfoodoption.mixins.GTFOEarlyMixinPlugin", null, 0, null, null, null, "gtfo_at.cfg"),
            new Artifact("groovyscript-1.4.3.jar", "07617b7ce9170a857199bd61d730a0db3af2685cf83d31734a2d4b628fda7533",
                    "com.cleanroommc.groovyscript.core.GroovyScriptCore", "GroovyScript-Core", Integer.MIN_VALUE + 10,
                    "com.cleanroommc.groovyscript.sandbox.ScriptModContainer", null,
                    "com.cleanroommc.groovyscript.core.GroovyScriptTransformer", null),
            new Artifact("ImmersiveRailroading-1.12.2-forge-1.10.0.jar", "de5483f7d06642ca8de38bbb56852559cafe5bcbebe38f680c391fd766d36022",
                    null, null, 0, null, null, null, null),
            new Artifact("IvToolkit-1.3.3-1.12.jar", "ffb745111790e27cb7810a2e03d5270265dee7ebd3ef98fde897c409d58b1e59",
                    "ivorius.ivtoolkit.IvToolkitLoadingPlugin", "IvToolkit", 0,
                    "ivorius.ivtoolkit.IvToolkitCoreContainer", null, null, null),
            new Artifact("modularui-3.1.6.jar", "4c16829c70b322aab735e73f53608fcabbf1d14956d5365c0c2b911c4d4dca54",
                    "com.cleanroommc.modularui.core.ModularUICore", "ModularUI-Core", 2000,
                    null, "com.cleanroommc.modularui.core.ClassTransformer", null, null),
            new Artifact("OpenComputers-MC1.12.2-1.8.9a+8ca336f.jar", "a5d0fc97a809314529b55cac5ef5484b960cb364fc7f944148bcc0ac8694238f",
                    "li.cil.oc.common.launch.TransformerLoader", null, 1001,
                    "li.cil.oc.common.launch.CoreModContainer", "li.cil.oc.common.asm.ClassTransformer", null, "oc_at.cfg"),
            new Artifact("Scalar Legacy-1.0.1.jar", "350d1c378f3b0ac8e7b3df0432482bbb5341c79d07df843c747be1d5fdc23cfe",
                    "com.cleanroommc.scalar.ScalarLoadingPlugin", null, 0,
                    "com.cleanroommc.scalar.ScalarModContainer", null, null, null),
            new Artifact("Supercritical-0.2.7.jar", "a75383ebb268d561864abc81d5bef8000363ec059fb7d3f348364c4e56fc2559",
                    null, null, 0, null, null, null, null),
            new Artifact("SussyPatches-1.11.6.jar", "96d2f2865bcffaa0d066304c2340412eafc126e0e9885d680d00ca76190c5153",
                    "dev.tianmi.sussypatches.core.LoadingPlugin", "SussyPatchesPlugin", 0,
                    null, null, null, "sussypatches_at.cfg"),
            new Artifact("Susy-Core-0.1.118.jar", "967d98d318604dd4e5d82277787973d2ee345dd1c088a83ed8e14a502aedbec2",
                    "supersymmetry.asm.SusyLoadingPlugin", "SusyLoadingPlugin", 2001,
                    null, "supersymmetry.asm.SusyTransformer", null, "susy_at.cfg"),
            new Artifact("TrackAPI-1.2.jar", "5fd930eb6afb769f3c806a5356fb09f842fd6679a5a04c53d3be934152c482bc",
                    null, null, 0, null, null, null, null),
            new Artifact("UniversalModCore-1.12.2-forge-1.2.2-3d12767.jar", "c2f71ad972320457cc37bacc999a46900964597df00ecc922f7649628560d823",
                    null, null, 0, null, null, null, "UniversalModCore_at.cfg"));
    private static final String GROOVY_TARGET = "org/codehaus/groovy/reflection/CachedClass$1";
    private static final String GROOVY_OWNER = "org/codehaus/groovy/reflection/CachedClass";
    private static final String HOOK_OWNER = "com/cleanroommc/groovyscript/sandbox/transformer/GroovyCodeFactory";
    private static final String HOOK_DESC = "(L" + GROOVY_OWNER + ";)Ljava/security/PrivilegedAction;";

    private static Artifact artifact(String filename) {
        return INPUTS.stream().filter(row -> row.file.equals(filename)).findFirst().orElseThrow();
    }

    private static Path home() throws Exception {
        String input = System.getenv("AXIOM_EARLY_ADDON_HOME");
        Assumptions.assumeTrue(input != null && !input.isBlank(), "Optional selected original addon home is not configured");
        Path home = Path.of(input);
        assertTrue(Files.isDirectory(home.resolve("mods")), "AXIOM_EARLY_ADDON_HOME must contain mods/");
        for (Artifact artifact : INPUTS) {
            Path jar = home.resolve("mods").resolve(artifact.file);
            assertTrue(Files.isRegularFile(jar), artifact.file);
            assertEquals(artifact.sha256, digest(Files.readAllBytes(jar)), artifact.file);
        }
        return home;
    }

    private static String digest(byte[] bytes) throws Exception {
        return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
    }

    private static byte[] entry(Path home, Artifact artifact, String name) throws Exception {
        try (var jar = new ZipFile(home.resolve("mods").resolve(artifact.file).toFile())) {
            var entry = jar.getEntry(name);
            assertNotNull(entry, artifact.file + "!" + name);
            try (var input = jar.getInputStream(entry)) { return input.readAllBytes(); }
        }
    }

    private static ClassNode type(Path home, Artifact artifact, String name) throws Exception {
        var node = new ClassNode();
        new ClassReader(entry(home, artifact, name.replace('.', '/') + ".class")).accept(node, 0);
        return node;
    }

    private static MethodNode method(ClassNode node, String name) {
        return node.methods.stream().filter(m -> m.name.equals(name)).findFirst().orElseThrow();
    }

    private static List<AbstractInsnNode> code(MethodNode method) {
        return Arrays.stream(method.instructions.toArray()).filter(i -> i.getOpcode() >= 0).toList();
    }

    private static List<String> strings(MethodNode method) {
        return code(method).stream().filter(i -> i instanceof LdcInsnNode ldc && ldc.cst instanceof String)
                .map(i -> (String)((LdcInsnNode)i).cst).toList();
    }

    private static Object annotationValue(ClassNode node, String name, Object absent) {
        if (node.visibleAnnotations != null) for (var annotation : node.visibleAnnotations) {
            if (annotation.desc.equals("Lnet/minecraftforge/fml/relauncher/IFMLLoadingPlugin$" + name + ";"))
                return annotation.values.get(annotation.values.indexOf("value") + 1);
        }
        return absent;
    }

    private static void constantReturn(MethodNode method, String expected) {
        var instructions = code(method);
        assertEquals(2, instructions.size(), method.name);
        if (expected == null) assertEquals(Opcodes.ACONST_NULL, instructions.getFirst().getOpcode(), method.name);
        else assertEquals(expected, assertInstanceOf(LdcInsnNode.class, instructions.getFirst()).cst, method.name);
        assertEquals(Opcodes.ARETURN, instructions.getLast().getOpcode(), method.name);
    }

    @Test void originalManifestsAndAnnotationsDetermineCoremodCandidatesAndTheirOrder() throws Exception {
        Path home = home();
        assertEquals(INPUTS.stream().map(Artifact::file).toList(), INPUTS.stream().map(Artifact::file)
                .sorted(Comparator.comparing(s -> s.toLowerCase(Locale.ENGLISH))).toList());
        var plugins = new ArrayList<Artifact>();
        for (Artifact artifact : INPUTS) {
            var attributes = new Manifest(new java.io.ByteArrayInputStream(entry(home, artifact, "META-INF/MANIFEST.MF"))).getMainAttributes();
            assertEquals(artifact.plugin, attributes.getValue("FMLCorePlugin"), artifact.file);
            assertEquals(artifact.accessResource, attributes.getValue("FMLAT"), artifact.file);
            for (String absent : List.of("TweakClass", "MixinConfigs", "MixinConnector", "ModSide"))
                assertNull(attributes.getValue(absent), artifact.file + ": " + absent);
            assertEquals(artifact.file.equals("Scalar Legacy-1.0.1.jar") ? "true" : null,
                    attributes.getValue("NonModDeps"));
            if (artifact.accessResource != null)
                assertTrue(entry(home, artifact, "META-INF/" + artifact.accessResource).length > 0);
            if (artifact.plugin == null) continue;
            plugins.add(artifact);
            ClassNode plugin = type(home, artifact, artifact.plugin);
            assertEquals(artifact.name, annotationValue(plugin, "Name", null));
            assertEquals(artifact.order, annotationValue(plugin, "SortingIndex", 0));
            assertEquals(List.of(), annotationValue(plugin, "DependsOn", List.of()));
            assertEquals(Set.of("IvToolkit-1.3.3-1.12.jar", "Scalar Legacy-1.0.1.jar").contains(artifact.file) ? null : "true",
                    attributes.getValue("FMLCorePluginContainsFMLMod"));
        }
        // Original CoreModManager keeps discovery order for ties; roots precede these candidates.
        plugins.sort(Comparator.comparingInt(Artifact::order));
        assertEquals(List.of("GroovyScript-Core", "GTFOEarlyMixinPlugin", "IvToolkit", "ScalarLoadingPlugin", "SussyPatchesPlugin", "Bubbles",
                        "GregTechLoadingPlugin", "TransformerLoader", "ModularUI-Core", "SusyLoadingPlugin"),
                plugins.stream().map(a -> a.name == null ? a.plugin.substring(a.plugin.lastIndexOf('.') + 1) : a.name).toList());
        assertTrue(artifact("gregtech-1.12.2-2.8.10-beta.jar").order > 1000 && artifact("Susy-Core-0.1.118.jar").order > 1000,
                "GT and Susy wrappers follow the original FMLDeobfTweaker transition");
    }

    @Test void originalCallbackDeclarationsSupplyContainersAndAccessOrWrappedTransformers() throws Exception {
        Path home = home();
        for (Artifact artifact : INPUTS) {
            if (artifact.plugin == null) continue;
            ClassNode plugin = type(home, artifact, artifact.plugin);
            if ("SussyPatchesPlugin".equals(artifact.name)) {
                assertTrue(plugin.interfaces.contains("dev/tianmi/sussypatches/api/core/ILoadingPlugin"));
                plugin = type(home, artifact, "dev.tianmi.sussypatches.api.core.ILoadingPlugin");
                assertTrue(plugin.interfaces.contains("net/minecraftforge/fml/relauncher/IFMLLoadingPlugin"));
            }
            constantReturn(method(plugin, "getSetupClass"), null);
            constantReturn(method(plugin, "getModContainerClass"), artifact.container);
            constantReturn(method(plugin, "getAccessTransformerClass"), artifact.accessTransformer);
            var transformers = method(plugin, "getASMTransformerClass");
            if (artifact.plugin.equals("li.cil.oc.common.launch.TransformerLoader")) {
                assertEquals(List.of(artifact.transformer), code(transformers).stream()
                        .filter(i -> i instanceof LdcInsnNode ldc && ldc.cst instanceof org.objectweb.asm.Type)
                        .map(i -> ((org.objectweb.asm.Type)((LdcInsnNode)i).cst).getClassName()).toList());
                var nameCall = code(transformers).stream().filter(i -> i instanceof MethodInsnNode).toList();
                assertEquals(1, nameCall.size());
                var invoke = (MethodInsnNode)nameCall.getFirst();
                assertEquals("java/lang/Class.getName()Ljava/lang/String;", invoke.owner + "." + invoke.name + invoke.desc);
            } else {
                assertEquals(artifact.transformer == null ? List.of() : List.of(artifact.transformer), strings(transformers));
            }
            assertEquals(Opcodes.ARETURN, code(transformers).getLast().getOpcode());
            if (artifact.transformer != null) {
                var transformer = type(home, artifact, artifact.transformer);
                assertEquals("java/lang/Object", transformer.superName);
                assertFalse(transformer.methods.stream().anyMatch(m -> m.name.equals("getPriority")));
            }
            if ("IvToolkit".equals(artifact.name)) {
                var call = code(transformers).stream().filter(i -> i instanceof MethodInsnNode).findFirst().orElseThrow();
                var invoke = assertInstanceOf(MethodInsnNode.class, call);
                assertEquals("ivorius/ivtoolkit/tools/JavaCompatibility", invoke.owner);
                assertEquals("requireJava8", invoke.name); assertEquals("(Z)V", invoke.desc);
            }
        }
        assertEquals(List.of("baubles_at.cfg", "ccl_at.cfg", "gregtech_at.cfg", "gtfo_at.cfg", "oc_at.cfg", "sussypatches_at.cfg", "susy_at.cfg", "UniversalModCore_at.cfg"),
                INPUTS.stream().map(Artifact::accessResource).filter(Objects::nonNull).toList());
    }

    @Test void earlySelectorsDeclareDefaultPhaseAndSussyUsesTheOriginalThreeGuards() throws Exception {
        Path home = home();
        var groovy = artifact("groovyscript-1.4.3.jar"); var sussy = artifact("SussyPatches-1.11.6.jar"); var susy = artifact("Susy-Core-0.1.118.jar");
        assertEquals(List.of("mixin.groovyscript.json"), strings(method(type(home, groovy, groovy.plugin), "getMixinConfigs")));
        assertEquals(List.of("mixins.susy.early.minecraft.json", "mixins.susy.early.forge.json"),
                strings(method(type(home, susy, susy.plugin), "getMixinConfigs")));
        var initializer = method(type(home, sussy, sussy.plugin), "<clinit>");
        assertEquals(List.of("itemoverlayevent", "statechangenotifier", "realtimeshadercheck"), strings(initializer));
        assertEquals(List.of("itemOverlayEvent", "passiveStructureChecking", "realTimeShaderCheck"), code(initializer).stream()
                .filter(i -> i instanceof FieldInsnNode f && f.desc.equals("Z"))
                .map(i -> ((FieldInsnNode)i).name).toList());
        for (var config : Map.of(groovy, List.of("mixin.groovyscript.json"),
                sussy, List.of("sussypatches/api/mixins.itemoverlayevent.json", "sussypatches/api/mixins.statechangenotifier.json",
                        "sussypatches/compat/mixins.realtimeshadercheck.json"),
                susy, List.of("mixins.susy.early.minecraft.json", "mixins.susy.early.forge.json")).entrySet()) {
            for (String name : config.getValue()) {
                String text = new String(entry(home, config.getKey(), name), java.nio.charset.StandardCharsets.UTF_8);
                assertTrue(text.matches("(?s).*\\\"target\\\"\\s*:\\s*\\\"@env\\(DEFAULT\\)\\\".*"), name);
            }
        }
        for (String suffix : List.of("Api", "Compat")) {
            String fieldName = suffix.equals("Api") ? "itemOverlayEvent" : "realTimeShaderCheck";
            var node = type(home, sussy, "dev.tianmi.sussypatches.common.SusConfig$" + suffix);
            var field = node.fields.stream().filter(f -> f.name.equals(fieldName)).findFirst().orElseThrow();
            assertTrue(field.visibleAnnotations.stream().anyMatch(a -> a.desc.equals("Lnet/minecraftforge/common/config/Config$Ignore;")));
            var write = code(method(node, "<init>")).stream().filter(i -> i instanceof FieldInsnNode f && f.name.equals(fieldName))
                    .findFirst().orElseThrow();
            assertEquals(Opcodes.ICONST_1, write.getPrevious().getOpcode());
        }
    }

    @Test void originalCachedFieldsDefinitionAndVisitorRecipeBindTheNativeHookWitness() throws Exception {
        Path home = home(); var groovy = artifact("groovyscript-1.4.3.jar");
        byte[] bytes = entry(home, groovy, GROOVY_TARGET + ".class");
        assertEquals("b267555bbd0a565e4691af448fc48ad71997e6511cd5b27c5d7d0ef5bb123f41", digest(bytes));
        var target = type(home, groovy, GROOVY_TARGET);
        assertEquals("org/codehaus/groovy/util/LazyReference", target.superName);
        assertEquals("org/codehaus/groovy/util/LockableObject", type(home, groovy, target.superName).superName);
        assertEquals("java/util/concurrent/locks/AbstractQueuedSynchronizer",
                type(home, groovy, "org/codehaus/groovy/util/LockableObject").superName);
        assertFalse(target.methods.stream().anyMatch(m -> m.name.equals("<clinit>")));
        var init = target.methods.stream().filter(m -> m.name.equals("initValue")
                && m.desc.equals("()[Lorg/codehaus/groovy/reflection/CachedField;")).findFirst().orElseThrow();
        var dynamics = code(init).stream().filter(i -> i instanceof InvokeDynamicInsnNode).toList();
        assertEquals(1, dynamics.size());
        var dynamic = (InvokeDynamicInsnNode)dynamics.getFirst();
        assertEquals("run", dynamic.name);
        assertEquals("(L" + GROOVY_TARGET + ";)Ljava/security/PrivilegedAction;", dynamic.desc);
        assertFalse(code(init).stream().anyMatch(i -> i instanceof MethodInsnNode c && c.name.equals("makeFieldsHook")));
        String visitorName = "com/cleanroommc/groovyscript/core/visitors/CachedClassFieldsVisitor";
        var visitor = type(home, groovy, visitorName);
        assertEquals(GROOVY_TARGET.replace('/', '.'), visitor.fields.stream().filter(f -> f.name.equals("CLASS_NAME")).findFirst().orElseThrow().value);
        assertEquals(List.of("initValue"), strings(method(visitor, "visitMethod")));
        byte[] replacementBytes = entry(home, groovy, visitorName + "$InitFieldVisitor.class");
        assertEquals("9f2703104637b8485d36ef072a636c9cb56b85f7e086db4f6a1f9151c4b3de78", digest(replacementBytes));
        var replacement = method(type(home, groovy, visitorName + "$InitFieldVisitor"), "visitInvokeDynamicInsn");
        assertEquals(List.of(GROOVY_TARGET, "this$0", "L" + GROOVY_OWNER + ";", HOOK_OWNER, "makeFieldsHook", HOOK_DESC), strings(replacement));
        var recipe = code(replacement);
        assertEquals(List.of(Opcodes.GETFIELD, Opcodes.INVOKESTATIC), recipe.stream().filter(i -> i instanceof IntInsnNode)
                .map(i -> ((IntInsnNode)i).operand).toList());
        var calls = recipe.stream().filter(i -> i instanceof MethodInsnNode).map(i -> (MethodInsnNode)i).toList();
        assertEquals(List.of("visitFieldInsn", "visitMethodInsn"), calls.stream().map(c -> c.name).toList());
        assertTrue(calls.stream().allMatch(c -> c.owner.equals("org/objectweb/asm/MethodVisitor")));
        assertEquals("(ILjava/lang/String;Ljava/lang/String;Ljava/lang/String;)V", calls.getFirst().desc);
        assertEquals("(ILjava/lang/String;Ljava/lang/String;Ljava/lang/String;Z)V", calls.getLast().desc);
        assertEquals(Opcodes.ICONST_0, calls.getLast().getPrevious().getOpcode());
        assertEquals(Opcodes.RETURN, recipe.getLast().getOpcode());
    }
}
