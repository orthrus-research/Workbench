package research.orthrus.axiom;

import java.io.*;
import java.nio.charset.StandardCharsets;
import java.security.*;
import java.util.*;
import java.util.jar.Manifest;
import java.util.zip.*;
import groovyjarjarasm.asm.*;

/** Reads class declarations as data. Never defines, links, initializes, or invokes a mod class. */
final class BinaryInventory {
    interface Classes { void accept(String name, String entry, String sha256); }
    static final int ENTRY_BYTES = 64 * 1024 * 1024, CLASS_BYTES = 4 * 1024 * 1024;
    static final long JAR_EXPANDED = 1024L * 1024 * 1024;
    static final String MOD = "Lnet/minecraftforge/fml/common/Mod;",
            SUBSCRIBER = "Lnet/minecraftforge/fml/common/Mod$EventBusSubscriber;",
            SUBSCRIBE = "Lnet/minecraftforge/fml/common/eventhandler/SubscribeEvent;",
            LIFECYCLE = "Lnet/minecraftforge/fml/common/Mod$EventHandler;",
            SIDE = "Lnet/minecraftforge/fml/relauncher/SideOnly;";
    private BinaryInventory() {}

    static Map<String, Object> read(InputStream source, Classes classes, boolean details, long remainingExpansionBytes) throws IOException {
        if (remainingExpansionBytes < 0) throw Failure.request("Negative artifact expansion allowance");
        long expansionBound = Math.min(JAR_EXPANDED, remainingExpansionBytes);
        Set<String> names = new HashSet<>();
        List<Map<String, Object>> entryPoints = new ArrayList<>(), resources = new ArrayList<>(), diagnostics = new ArrayList<>();
        List<Map<String, Object>> subscribers = new ArrayList<>(), handlers = new ArrayList<>(), annotationTypes = new ArrayList<>();
        Map<String, Object> manifest = new TreeMap<>();
        MessageDigest inventory = digest();
        long total = 0; int classCount = 0, variants = 0, manifests = 0;
        String manifestStatus = "absent";
        int subscriberCount = 0, handlerCount = 0, annotationTypeCount = 0;
        try (ZipInputStream zip = new ZipInputStream(source, StandardCharsets.UTF_8)) {
            ZipEntry entry;
            while ((entry = zip.getNextEntry()) != null) {
                String name = entry.getName();
                try { SourceTarget.path(entry.isDirectory() ? name.substring(0, name.length() - 1) : name); }
                catch (Failure failure) { throw Failure.request("Unsafe JAR entry: " + name); }
                if (!names.add(name) || names.size() > 100000) throw Failure.request("Duplicate or excessive JAR entries");
                if (entry.isDirectory()) {
                    if (zip.read() != -1) throw Failure.request("JAR directory entry contains data");
                    zip.closeEntry(); continue;
                }
                boolean isClass = name.endsWith(".class"), isManifest = name.equalsIgnoreCase("META-INF/MANIFEST.MF");
                boolean isMetadata = name.equals("mcmod.info") || isManifest || name.matches("(?:.*/)?mixins?\\..*\\.json")
                        || name.startsWith("META-INF/services/") || name.endsWith("_at.cfg");
                int bound = isClass ? CLASS_BYTES : isMetadata ? 1_048_576 : ENTRY_BYTES;
                ByteArrayOutputStream content = isClass || isMetadata ? new ByteArrayOutputStream() : null;
                MessageDigest hash = digest(); int size = 0; byte[] buffer = new byte[65536]; int count;
                while ((count = zip.read(buffer)) >= 0) {
                    size += count; total += count;
                    if (size > bound || total > expansionBound) throw new Failure("incomplete", "artifacts.expansion-bound", "JAR expansion exceeds remaining byte policy");
                    hash.update(buffer, 0, count); if (content != null) content.write(buffer, 0, count);
                }
                String sha = HexFormat.of().formatHex(hash.digest());
                inventory.update(name.getBytes(StandardCharsets.UTF_8)); inventory.update((byte)0); inventory.update(HexFormat.of().parseHex(sha));
                if (isClass) {
                    classCount++;
                    boolean variant = name.startsWith("META-INF/versions/"); if (variant) variants++;
                    try {
                        ClassReader reader = new ClassReader(content.toByteArray());
                        String binaryName = reader.getClassName();
                        Map<String, Object> header = header(reader);
                        List<Object> methods = Json.array(header.remove("eventHandlers"));
                        List<Object> annotationMembers = Json.array(header.remove("annotationMembers"));
                        // Header discovery is not JVM verification or classloader resolution.
                        // In particular, a class's declared name need not agree with its ZIP path.
                        String expectedName = variant ? name.replaceFirst("^META-INF/versions/[1-9][0-9]*/", "") : name;
                        boolean matchingPath = expectedName.equals(binaryName + ".class");
                        if (!matchingPath) diagnostics.add(Map.of("entry", name, "class", binaryName, "status", "unsupported",
                                "rule", "artifacts.class-entry-name", "message", "Class declaration differs from its entry path; loader resolution unresolved"));
                        classes.accept(binaryName, name, sha);
                        header.put("entry", name); header.put("sha256", sha); header.put("multiReleaseVariant", variant);
                        header.put("entryNameMatchesClass", matchingPath);
                        if ((Json.number(header.get("access")) & Opcodes.ACC_ANNOTATION) != 0) {
                            annotationTypeCount++;
                            if (details) {
                                Map<String, Object> declaration = new LinkedHashMap<>(header);
                                declaration.put("members", annotationMembers); annotationTypes.add(declaration);
                            }
                        }
                        boolean subscriber = hasAnnotation(Json.array(header.get("annotations")), SUBSCRIBER);
                        if (subscriber) { subscriberCount++; if (details) subscribers.add(header); }
                        handlerCount += methods.size();
                        if (details && !methods.isEmpty()) {
                            Map<String, Object> declaration = new LinkedHashMap<>(header);
                            declaration.put("methods", methods); handlers.add(declaration);
                        }
                        if (Json.array(header.get("annotations")).stream().map(Json::object).anyMatch(annotation ->
                                annotation.get("type").equals(MOD) || annotation.get("type").toString().startsWith("Lnet/minecraftforge/fml/relauncher/IFMLLoadingPlugin$"))
                                || Json.array(header.get("interfaces")).stream().anyMatch(type ->
                                Set.of("net/minecraftforge/fml/relauncher/IFMLLoadingPlugin", "net/minecraft/launchwrapper/IClassTransformer",
                                        "zone/rong/mixinbooter/ILateMixinLoader", "zone/rong/mixinbooter/IEarlyMixinLoader").contains(type))) {
                            entryPoints.add(header);
                        }
                    } catch (IllegalArgumentException | IndexOutOfBoundsException failure) {
                        diagnostics.add(Map.of("entry", name, "status", "unsupported", "rule", "artifacts.class-format",
                                "message", "Class declaration could not be parsed: " + failure.getClass().getSimpleName()));
                    }
                } else if (isMetadata || name.endsWith(".jar")) {
                    Map<String, Object> row = new LinkedHashMap<>(Map.of("entry", name, "sha256", sha, "size", size));
                    if (isManifest) {
                        manifests++; row.put("kind", "jar-manifest");
                        try {
                            Manifest attributes = new Manifest(new ByteArrayInputStream(content.toByteArray()));
                            Map<String, String> values = new TreeMap<>();
                            attributes.getMainAttributes().forEach((key, value) -> values.put(key.toString(), value.toString()));
                            // Named signing sections remain bound by the raw member hash; signatures are not verified.
                            row.put("value", values); row.put("parseStatus", "parsed-main-attributes");
                            if (manifests == 1) { manifest = new LinkedHashMap<>(values); manifestStatus = "parsed-main-attributes"; }
                        } catch (IOException failure) {
                            row.put("parseStatus", "unsupported-manifest-form; raw bytes retained");
                            manifestStatus = "unsupported-manifest-form";
                            diagnostics.add(Map.of("entry", name, "status", "unsupported", "rule", "artifacts.manifest-format",
                                    "message", "JDK manifest parser could not read this declaration; loader acceptance unresolved"));
                        }
                        if (manifests > 1) {
                            manifest = new TreeMap<>(); manifestStatus = "ambiguous-manifests";
                            diagnostics.add(Map.of("entry", name, "status", "unsupported", "rule", "artifacts.manifest-ambiguity",
                                    "message", "Multiple case-insensitive manifest paths; no loader precedence selected"));
                        }
                    } else if (name.equals("mcmod.info") || name.endsWith(".json")) {
                        row.put("kind", "declarative-metadata");
                        try { row.put("value", Json.parse(Main.utf8(content.toByteArray()))); row.put("parseStatus", "parsed-strict-json"); }
                        catch (Failure failure) { row.put("parseStatus", "unsupported-json-form; raw bytes retained"); }
                    } else if (name.endsWith(".jar")) row.put("kind", "embedded-jar; expansion-and-loader-selection-unresolved");
                    else { row.put("kind", "transformation-or-service-declaration"); row.put("text", Main.utf8(content.toByteArray())); }
                    if (!details) { row.remove("value"); row.remove("text"); }
                    resources.add(row);
                }
                if (diagnostics.size() > 256 || resources.size() > 2000 || entryPoints.size() > 2000
                        || subscriberCount > 10000 || handlerCount > 10000 || annotationTypeCount > 10000)
                    throw new Failure("incomplete", "artifacts.inventory-bound", "Binary declaration output exceeds policy");
                zip.closeEntry();
            }
        } catch (ZipException failure) { throw Failure.request("Malformed nested JAR: " + failure.getMessage()); }
        if (names.isEmpty()) throw Failure.request("Artifact contains no readable JAR entries");
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("entries", names.size()); result.put("expandedBytes", total); result.put("classFiles", classCount);
        result.put("multiReleaseClassFiles", variants); result.put("contentInventoryId", HexFormat.of().formatHex(inventory.digest()));
        result.put("manifest", manifest); result.put("manifestStatus", manifestStatus);
        result.put("entryPointDeclarations", entryPoints); result.put("resources", resources);
        result.put("eventSubscriberClasses", subscriberCount); result.put("eventHandlerMethods", handlerCount);
        result.put("annotationTypes", annotationTypeCount);
        if (details) {
            result.put("eventSubscriberDeclarations", subscribers); result.put("eventHandlerDeclarations", handlers);
            result.put("annotationTypeDeclarations", annotationTypes);
        }
        result.put("annotationDefaultsResolved", false);
        result.put("diagnostics", diagnostics); result.put("classesLoaded", false); result.put("signaturesVerified", false);
        result.put("scope", "raw class/method declarations and conventional resources; loader activation, event registration/order and inheritance closure unresolved");
        return result;
    }
    static Map<String, Object> header(ClassReader reader) {
        Map<String, Object> header = new LinkedHashMap<>();
        List<Object> annotations = new ArrayList<>(), methods = new ArrayList<>(), annotationMembers = new ArrayList<>();
        reader.accept(new ClassVisitor(Opcodes.ASM9) {
            @Override public void visit(int version, int access, String name, String signature, String parent, String[] interfaces) {
                header.put("class", name); header.put("classVersion", version); header.put("access", access);
                header.put("superclass", parent); header.put("interfaces", Arrays.asList(interfaces));
            }
            @Override public AnnotationVisitor visitAnnotation(String descriptor, boolean visible) {
                if (!Set.of(MOD, SUBSCRIBER, SIDE).contains(descriptor)
                        && !descriptor.startsWith("Lnet/minecraftforge/fml/relauncher/IFMLLoadingPlugin$")
                        && !((Json.number(header.get("access")) & Opcodes.ACC_ANNOTATION) != 0 && descriptor.startsWith("Ljava/lang/annotation/"))) return null;
                return declarationAnnotation(annotations, descriptor, visible);
            }
            @Override public MethodVisitor visitMethod(int access, String name, String descriptor, String signature, String[] exceptions) {
                List<Object> methodAnnotations = new ArrayList<>(); Map<String, Object> defaults = new LinkedHashMap<>();
                return new MethodVisitor(Opcodes.ASM9) {
                    boolean defaultVisited;
                    @Override public AnnotationVisitor visitAnnotationDefault() {
                        if (defaultVisited) throw Failure.unsupported("artifacts.annotation-default", "Duplicate annotation default attributes require class verification");
                        defaultVisited = true;
                        if ((Json.number(header.get("access")) & Opcodes.ACC_ANNOTATION) == 0)
                            throw Failure.unsupported("artifacts.annotation-default", "Default value on a non-annotation class needs class verification");
                        return new AnnotationVisitor(Opcodes.ASM9, annotation(defaults, 0)) {
                            @Override public void visit(String ignored, Object value) { super.visit("value", value); }
                            @Override public void visitEnum(String ignored, String type, String value) { super.visitEnum("value", type, value); }
                            @Override public AnnotationVisitor visitArray(String ignored) { return super.visitArray("value"); }
                            @Override public AnnotationVisitor visitAnnotation(String ignored, String type) { return super.visitAnnotation("value", type); }
                        };
                    }
                    @Override public AnnotationVisitor visitAnnotation(String type, boolean visible) {
                        return Set.of(SUBSCRIBE, LIFECYCLE, SIDE).contains(type) ? declarationAnnotation(methodAnnotations, type, visible) : null;
                    }
                    @Override public void visitEnd() {
                        Map<String, Object> method = new LinkedHashMap<>();
                        method.put("name", name); method.put("descriptor", descriptor); method.put("access", access);
                        if (signature != null) method.put("signature", signature);
                        if ((Json.number(header.get("access")) & Opcodes.ACC_ANNOTATION) != 0 && !name.startsWith("<")) {
                            Map<String, Object> member = new LinkedHashMap<>(method);
                            member.put("defaultPresent", defaults.containsKey("value"));
                            if (defaults.containsKey("value")) member.put("default", defaults.get("value"));
                            annotationMembers.add(member);
                            if (annotationMembers.size() > 10000) throw new Failure("incomplete", "artifacts.annotation-bound", "Annotation member declarations exceed policy");
                        }
                        if (!hasAnnotation(methodAnnotations, SUBSCRIBE) && !hasAnnotation(methodAnnotations, LIFECYCLE)) return;
                        method.put("annotations", methodAnnotations);
                        methods.add(method);
                        if (methods.size() > 10000) throw new Failure("incomplete", "artifacts.handler-bound", "Class event declarations exceed policy");
                    }
                };
            }
        }, ClassReader.SKIP_CODE | ClassReader.SKIP_DEBUG | ClassReader.SKIP_FRAMES);
        header.put("annotations", annotations); header.put("eventHandlers", methods); header.put("annotationMembers", annotationMembers); return header;
    }
    private static boolean hasAnnotation(List<Object> annotations, String type) {
        return annotations.stream().map(Json::object).anyMatch(row -> type.equals(row.get("type")));
    }
    private static AnnotationVisitor declarationAnnotation(List<Object> annotations, String descriptor, boolean visible) {
        Map<String, Object> values = new TreeMap<>();
        annotations.add(Map.of("type", descriptor, "runtimeVisible", visible, "values", values));
        return annotation(values, 0);
    }
    private static AnnotationVisitor annotation(Map<String, Object> values, int depth) {
        if (depth > 16) throw new Failure("incomplete", "artifacts.annotation-bound", "Annotation nesting exceeds policy");
        return new AnnotationVisitor(Opcodes.ASM9) {
            @Override public void visit(String name, Object value) { values.put(name, scalar(value)); }
            @Override public void visitEnum(String name, String descriptor, String value) { values.put(name, Map.of("enumType", descriptor, "value", value)); }
            @Override public AnnotationVisitor visitAnnotation(String name, String descriptor) {
                Map<String, Object> nested = new TreeMap<>(); values.put(name, Map.of("annotationType", descriptor, "values", nested)); return annotation(nested, depth + 1);
            }
            @Override public AnnotationVisitor visitArray(String name) {
                Map<String, Object> array = new LinkedHashMap<>();
                return new AnnotationVisitor(Opcodes.ASM9, annotation(array, depth + 1)) {
                    int index;
                    @Override public void visit(String ignored, Object value) { super.visit(Integer.toString(index++), value); }
                    @Override public void visitEnum(String ignored, String descriptor, String value) { super.visitEnum(Integer.toString(index++), descriptor, value); }
                    @Override public AnnotationVisitor visitAnnotation(String ignored, String descriptor) { return super.visitAnnotation(Integer.toString(index++), descriptor); }
                    @Override public void visitEnd() { values.put(name, new ArrayList<>(array.values())); }
                };
            }
        };
    }
    private static Object scalar(Object value) {
        if (value instanceof Float number) return Map.of("type", "float", "bits", Integer.toUnsignedString(Float.floatToRawIntBits(number)));
        if (value instanceof Double number) return Map.of("type", "double", "bits", Long.toUnsignedString(Double.doubleToRawLongBits(number)));
        if (value instanceof Type type) return Map.of("classDescriptor", type.getDescriptor());
        if (value instanceof Character character) return Map.of("char", (int)character.charValue());
        if (value instanceof String || value instanceof Boolean || value instanceof Number) return value;
        if (value != null && value.getClass().isArray()) {
            List<Object> result = new ArrayList<>();
            for (int i = 0; i < java.lang.reflect.Array.getLength(value); i++) result.add(scalar(java.lang.reflect.Array.get(value, i)));
            return result;
        }
        throw Failure.unsupported("artifacts.annotation-value", "Unmodeled class annotation value");
    }
    static MessageDigest digest() {
        try { return MessageDigest.getInstance("SHA-256"); }
        catch (NoSuchAlgorithmException impossible) { throw new AssertionError(impossible); }
    }
}
