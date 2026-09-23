package research.orthrus.axiom;

import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.zip.*;
import groovyjarjarasm.asm.*;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class BinaryInventoryTest {
    @Test void annotationTypesRetainRequiredMembersAndExactTypedDefaultsWithoutApplyingThem() throws Exception {
        ClassWriter writer = new ClassWriter(0);
        writer.visit(Opcodes.V1_8, Opcodes.ACC_PUBLIC | Opcodes.ACC_ABSTRACT | Opcodes.ACC_INTERFACE | Opcodes.ACC_ANNOTATION,
                "fixture/Annotation", null, "java/lang/Object", new String[]{"java/lang/annotation/Annotation"});
        int flags = Opcodes.ACC_PUBLIC | Opcodes.ACC_ABSTRACT;
        writer.visitMethod(flags, "required", "()Ljava/lang/String;", null, null).visitEnd();
        MethodVisitor bool = writer.visitMethod(flags, "cancelled", "()Z", null, null);
        AnnotationVisitor b = bool.visitAnnotationDefault(); b.visit(null, false); b.visitEnd(); bool.visitEnd();
        MethodVisitor priority = writer.visitMethod(flags, "priority", "()Lfixture/Priority;", null, null);
        AnnotationVisitor p = priority.visitAnnotationDefault(); p.visitEnum(null, "Lfixture/Priority;", "NORMAL"); p.visitEnd(); priority.visitEnd();
        MethodVisitor array = writer.visitMethod(flags, "sides", "()[Lfixture/Side;", null, null);
        AnnotationVisitor a = array.visitAnnotationDefault(), list = a.visitArray(null);
        list.visitEnum(null, "Lfixture/Side;", "CLIENT"); list.visitEnum(null, "Lfixture/Side;", "SERVER"); list.visitEnd(); a.visitEnd(); array.visitEnd();
        MethodVisitor nested = writer.visitMethod(flags, "nested", "()Lfixture/Nested;", null, null);
        AnnotationVisitor n = nested.visitAnnotationDefault(), value = n.visitAnnotation(null, "Lfixture/Nested;");
        value.visit("key", "value"); value.visitEnd(); n.visitEnd(); nested.visitEnd(); writer.visitEnd();
        byte[] jar = jar(Map.of("fixture/Annotation.class", writer.toByteArray()));
        var result = inspect(jar, true); assertEquals(1, result.get("annotationTypes"));
        List<Object> members = Json.array(Json.object(Json.array(result.get("annotationTypeDeclarations")).get(0)).get("members"));
        assertEquals(5, members.size()); assertEquals(false, Json.object(members.get(0)).get("defaultPresent"));
        assertFalse(Json.object(members.get(0)).containsKey("default"));
        assertEquals(false, Json.object(members.get(1)).get("default"));
        assertEquals(Map.of("enumType", "Lfixture/Priority;", "value", "NORMAL"), Json.object(members.get(2)).get("default"));
        assertEquals(List.of(Map.of("enumType", "Lfixture/Side;", "value", "CLIENT"), Map.of("enumType", "Lfixture/Side;", "value", "SERVER")), Json.object(members.get(3)).get("default"));
        assertEquals(Map.of("annotationType", "Lfixture/Nested;", "values", Map.of("key", "value")), Json.object(members.get(4)).get("default"));
        assertEquals(false, result.get("annotationDefaultsResolved")); assertEquals(false, result.get("classesLoaded"));
        var compact = inspect(jar, false); assertEquals(1, compact.get("annotationTypes")); assertFalse(compact.containsKey("annotationTypeDeclarations"));
        assertEquals(result.get("contentInventoryId"), compact.get("contentInventoryId"));
    }
    static byte[] fixtureClass() {
        ClassWriter writer = new ClassWriter(0);
        writer.visit(Opcodes.V1_8, Opcodes.ACC_PUBLIC, "fixture/Mod", null, "java/lang/Object", new String[]{"zone/rong/mixinbooter/ILateMixinLoader"});
        AnnotationVisitor annotation = writer.visitAnnotation("Lnet/minecraftforge/fml/common/Mod;", true);
        annotation.visit("modid", "fixture_mod"); annotation.visit("version", "2.0"); annotation.visit("dependencies", "required-after:other@[1.0,)"); annotation.visitEnd();
        // If anyone initializes this class instead of reading bytes, the test observes it.
        MethodVisitor method = writer.visitMethod(Opcodes.ACC_STATIC, "<clinit>", "()V", null, null);
        method.visitCode(); method.visitLdcInsn("axiom.mod-initialized"); method.visitLdcInsn("bad");
        method.visitMethodInsn(Opcodes.INVOKESTATIC, "java/lang/System", "setProperty", "(Ljava/lang/String;Ljava/lang/String;)Ljava/lang/String;", false);
        method.visitInsn(Opcodes.POP); method.visitInsn(Opcodes.RETURN); method.visitMaxs(2, 0); method.visitEnd();
        writer.visitEnd(); return writer.toByteArray();
    }
    static byte[] jar(Map<String, byte[]> entries) throws IOException {
        ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        try (ZipOutputStream zip = new ZipOutputStream(bytes)) {
            for (var entry : new TreeMap<>(entries).entrySet()) { zip.putNextEntry(new ZipEntry(entry.getKey())); zip.write(entry.getValue()); zip.closeEntry(); }
        }
        return bytes.toByteArray();
    }
    static byte[] text(String value) { return value.getBytes(StandardCharsets.UTF_8); }
    private Map<String, Object> inspect(byte[] jar, boolean detail) throws IOException {
        return BinaryInventory.read(new ByteArrayInputStream(jar), (name, entry, hash) -> {}, detail, BinaryInventory.JAR_EXPANDED);
    }
    @Test void readsRealClassAnnotationsAndInterfacesWithoutLoadingOrInitialization() throws Exception {
        assertNull(System.getProperty("axiom.mod-initialized"));
        var result = inspect(jar(Map.of("fixture/Mod.class", fixtureClass())), false);
        assertEquals(1, result.get("classFiles")); assertEquals(false, result.get("classesLoaded"));
        var header = Json.object(Json.array(result.get("entryPointDeclarations")).get(0));
        assertEquals("fixture/Mod", header.get("class")); assertEquals(List.of("zone/rong/mixinbooter/ILateMixinLoader"), header.get("interfaces"));
        var annotation = Json.object(Json.array(header.get("annotations")).get(0));
        assertEquals("fixture_mod", Json.object(annotation.get("values")).get("modid"));
        assertNull(System.getProperty("axiom.mod-initialized"));
    }
    @Test void manifestContinuationAndTransformationResourcesArePreservedAsData() throws Exception {
        var result = inspect(jar(Map.of("META-INF/MANIFEST.MF", text("Manifest-Version: 1.0\r\nFMLCorePlugin: fixture.\r\n Plugin\r\n\r\n"),
                "mixins.fixture.json", text("{\"package\":\"fixture\",\"mixins\":[\"Rule\"]}"), "META-INF/fixture_at.cfg", text("public fixture.Mod field\n"))), true);
        assertEquals("fixture.Plugin", Json.object(result.get("manifest")).get("FMLCorePlugin"));
        assertEquals(3, Json.array(result.get("resources")).size()); assertEquals(false, result.get("signaturesVerified"));
        assertTrue(Json.array(result.get("resources")).stream().map(Json::object).anyMatch(row -> row.containsKey("text")));
    }
    @Test void compactInventoryRetainsIdentitiesButDoesNotEmitAllResourceText() throws Exception {
        byte[] jar = jar(Map.of("mcmod.info", text("[{\"modid\":\"fixture\"}]")));
        var compact = inspect(jar, false); var detail = inspect(jar, true);
        assertEquals(compact.get("contentInventoryId"), detail.get("contentInventoryId"));
        assertFalse(Json.object(Json.array(compact.get("resources")).get(0)).containsKey("value"));
        assertTrue(Json.object(Json.array(detail.get("resources")).get(0)).containsKey("value"));
    }
    @Test void malformedMetadataIsNotClaimedAsAModLoaderRejection() throws Exception {
        var result = inspect(jar(Map.of("mcmod.info", text("/* lenient */ [{modid:'fixture'}]"))), true);
        assertEquals("unsupported-json-form; raw bytes retained", Json.object(Json.array(result.get("resources")).get(0)).get("parseStatus"));
    }
    @Test void arbitraryFilesAndTraversalCannotBecomeAJarInventory() {
        assertThrows(Failure.class, () -> inspect(text("not a jar"), false));
        assertThrows(Failure.class, () -> inspect(jar(Map.of("../escape", text("data"))), false));
    }
    @Test void multiReleaseAndEmbeddedJarsRemainExplicitUnresolvedInputs() throws Exception {
        var result = inspect(jar(Map.of("fixture/Mod.class", fixtureClass(), "META-INF/versions/9/fixture/Mod.class", fixtureClass(),
                "META-INF/libraries/embedded.jar", jar(Map.of("inside", text("data"))))), false);
        assertEquals(2, result.get("classFiles")); assertEquals(1, result.get("multiReleaseClassFiles"));
        assertTrue(Json.array(result.get("resources")).stream().map(Json::object).anyMatch(row -> row.get("kind").toString().startsWith("embedded-jar")));
    }
    @Test void brokenClassBytesRemainUnsupportedNotSuccessfulDiscovery() throws Exception {
        var result = inspect(jar(Map.of("Broken.class", new byte[]{0, 1, 2})), false);
        assertEquals(1, Json.array(result.get("diagnostics")).size()); assertEquals(0, Json.array(result.get("entryPointDeclarations")).size());
    }
    @Test void ambiguousManifestNamesDoNotSelectLastZipEntryAsAuthority() throws Exception {
        var result = inspect(jar(Map.of("META-INF/MANIFEST.MF", text("Manifest-Version: 1.0\r\nFMLCorePlugin: First\r\n\r\n"),
                "meta-inf/manifest.mf", text("Manifest-Version: 1.0\r\nFMLCorePlugin: Second\r\n\r\n"))), true);
        assertEquals("ambiguous-manifests", result.get("manifestStatus"));
        assertTrue(Json.object(result.get("manifest")).isEmpty());
        var resources = Json.array(result.get("resources")).stream().map(Json::object).toList();
        assertEquals(Set.of("First", "Second"), new HashSet<>(resources.stream()
                .map(row -> Json.object(row.get("value")).get("FMLCorePlugin")).toList()));
        assertEquals("artifacts.manifest-ambiguity", Json.object(Json.array(result.get("diagnostics")).get(0)).get("rule"));
    }
    @Test void malformedManifestIsInspectableDataNotAnInventedLoaderRejection() throws Exception {
        var result = inspect(jar(Map.of("META-INF/MANIFEST.MF", text("not a manifest header\r\n\r\n"))), true);
        assertEquals("unsupported-manifest-form", result.get("manifestStatus"));
        assertTrue(Json.object(result.get("manifest")).isEmpty());
        assertEquals("artifacts.manifest-format", Json.object(Json.array(result.get("diagnostics")).get(0)).get("rule"));
    }
    @Test void classPathMismatchIsVisibleAndDoesNotBecomeLoaderResolution() throws Exception {
        var result = inspect(jar(Map.of("another/Name.class", fixtureClass())), false);
        var declaration = Json.object(Json.array(result.get("entryPointDeclarations")).get(0));
        assertEquals("fixture/Mod", declaration.get("class"));
        assertEquals(false, declaration.get("entryNameMatchesClass"));
        assertEquals("artifacts.class-entry-name", Json.object(Json.array(result.get("diagnostics")).get(0)).get("rule"));
    }
    @Test void unreadableHeadersDoNotContributeClassProviders() throws Exception {
        List<String> discovered = new ArrayList<>();
        byte[] truncated = Arrays.copyOf(fixtureClass(), fixtureClass().length - 1);
        var result = BinaryInventory.read(new ByteArrayInputStream(jar(Map.of("fixture/Mod.class", truncated))),
                (name, entry, hash) -> discovered.add(name), false, BinaryInventory.JAR_EXPANDED);
        assertTrue(discovered.isEmpty());
        assertEquals(1, Json.array(result.get("diagnostics")).size());
    }
    static byte[] eventClass() {
        ClassWriter writer = new ClassWriter(0);
        writer.visit(Opcodes.V1_8, Opcodes.ACC_PUBLIC, "fixture/Events", null, "fixture/Parent", null);
        AnnotationVisitor subscriber = writer.visitAnnotation(BinaryInventory.SUBSCRIBER, true);
        subscriber.visit("modid", "fixture");
        AnnotationVisitor sides = subscriber.visitArray("value");
        sides.visitEnum(null, "Lnet/minecraftforge/fml/relauncher/Side;", "CLIENT"); sides.visitEnd(); subscriber.visitEnd();
        MethodVisitor material = writer.visitMethod(Opcodes.ACC_PUBLIC | Opcodes.ACC_STATIC, "material", "(Lfixture/MaterialEvent;)V", null, null);
        AnnotationVisitor listener = material.visitAnnotation(BinaryInventory.SUBSCRIBE, true);
        listener.visitEnum("priority", "Lnet/minecraftforge/fml/common/eventhandler/EventPriority;", "HIGH");
        listener.visit("receiveCanceled", true); listener.visitEnd(); material.visitEnd();
        MethodVisitor generic = writer.visitMethod(Opcodes.ACC_PUBLIC | Opcodes.ACC_STATIC, "items", "(Lfixture/Register;)V", "(Lfixture/Register<Lfixture/Item;>;)V", null);
        generic.visitAnnotation(BinaryInventory.SUBSCRIBE, true).visitEnd(); generic.visitEnd();
        MethodVisitor lifecycle = writer.visitMethod(Opcodes.ACC_PUBLIC, "preInit", "(Lfixture/PreInit;)V", null, null);
        lifecycle.visitAnnotation(BinaryInventory.LIFECYCLE, true).visitEnd();
        AnnotationVisitor side = lifecycle.visitAnnotation(BinaryInventory.SIDE, true);
        side.visitEnum("value", "Lnet/minecraftforge/fml/relauncher/Side;", "CLIENT"); side.visitEnd(); lifecycle.visitEnd();
        // A side annotation alone must not invent a registered event handler.
        MethodVisitor unrelated = writer.visitMethod(Opcodes.ACC_PUBLIC, "render", "()V", null, null);
        unrelated.visitAnnotation(BinaryInventory.SIDE, true).visitEnd(); unrelated.visitEnd();
        writer.visitEnd(); return writer.toByteArray();
    }
    @Test void eventDeclarationsRetainExactDescriptorsGenericsPriorityAndSide() throws Exception {
        var result = inspect(jar(Map.of("fixture/Events.class", eventClass())), true);
        assertEquals(1, result.get("eventSubscriberClasses")); assertEquals(3, result.get("eventHandlerMethods"));
        assertEquals(false, result.get("annotationDefaultsResolved"));
        assertTrue(Json.array(result.get("entryPointDeclarations")).isEmpty());
        var owner = Json.object(Json.array(result.get("eventHandlerDeclarations")).get(0));
        assertEquals("fixture/Parent", owner.get("superclass"));
        var methods = Json.array(owner.get("methods")).stream().map(Json::object).toList();
        assertEquals("(Lfixture/MaterialEvent;)V", methods.get(0).get("descriptor"));
        assertEquals(Opcodes.ACC_PUBLIC | Opcodes.ACC_STATIC, methods.get(0).get("access"));
        var values = Json.object(Json.object(Json.array(methods.get(0).get("annotations")).get(0)).get("values"));
        assertEquals("HIGH", Json.object(values.get("priority")).get("value")); assertEquals(true, values.get("receiveCanceled"));
        assertEquals("(Lfixture/Register<Lfixture/Item;>;)V", methods.get(1).get("signature"));
        var classAnnotation = Json.object(Json.array(owner.get("annotations")).get(0));
        var sides = Json.array(Json.object(classAnnotation.get("values")).get("value"));
        assertEquals("CLIENT", Json.object(sides.get(0)).get("value"));
        assertEquals(2, Json.array(methods.get(2).get("annotations")).size());
    }
    @Test void omittedEventDefaultsAndUnregisteredMethodsAreNotInvented() throws Exception {
        byte[] jar = jar(Map.of("fixture/Events.class", eventClass()));
        var compact = inspect(jar, false); var detail = inspect(jar, true);
        assertEquals(compact.get("contentInventoryId"), detail.get("contentInventoryId"));
        assertEquals(3, compact.get("eventHandlerMethods")); assertFalse(compact.containsKey("eventHandlerDeclarations"));
        var methods = Json.array(Json.object(Json.array(detail.get("eventHandlerDeclarations")).get(0)).get("methods"));
        var annotation = Json.object(Json.array(Json.object(methods.get(1)).get("annotations")).get(0));
        assertTrue(Json.object(annotation.get("values")).isEmpty());
        assertEquals(List.of("material", "items", "preInit"), methods.stream().map(Json::object).map(row -> row.get("name")).toList());
    }
    @Test void sharedExpansionBudgetIsEnforcedWhileReadingTheNextJar() throws Exception {
        byte[] jar = jar(Map.of("data", new byte[100]));
        Failure failure = assertThrows(Failure.class, () -> BinaryInventory.read(new ByteArrayInputStream(jar),
                (name, entry, hash) -> fail("No class was present"), false, 99));
        assertEquals("incomplete", failure.kind); assertEquals("artifacts.expansion-bound", failure.rule);
        assertEquals(100L, BinaryInventory.read(new ByteArrayInputStream(jar), (name, entry, hash) -> {}, false, 100).get("expandedBytes"));
    }
}
