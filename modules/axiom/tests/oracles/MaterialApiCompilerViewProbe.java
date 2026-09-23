package research.orthrus.axiom.tooling;

import java.nio.file.*;
import java.util.*;
import java.util.jar.*;
import org.objectweb.asm.*;
import org.objectweb.asm.tree.*;

/** Tests only compiler metadata in trusted, temporary artifacts; executes no game class. */
public final class MaterialApiCompilerViewProbe {
    private static final String REGISTRY = "net/minecraft/util/registry/RegistryNamespaced";
    private static final String ITERABLE = "net/minecraft/util/IObjectIntIterable";
    private static ClassNode read(Path path, String name) throws Exception {
        try (var jar = new JarFile(path.toFile()); var input = jar.getInputStream(jar.getJarEntry(name+".class"))) {
            var node = new ClassNode(); new ClassReader(input.readAllBytes()).accept(node,0); return node;
        }
    }
    private static byte[] bytes(ClassNode node) {
        var writer = new ClassWriter(0); node.accept(writer); return writer.toByteArray();
    }
    private static void input(Path path, ClassNode... nodes) throws Exception {
        try (var out = new JarOutputStream(Files.newOutputStream(path, StandardOpenOption.CREATE_NEW))) {
            for (var node : nodes) {
                out.putNextEntry(new JarEntry(node.name+".class")); out.write(bytes(node)); out.closeEntry();
            }
        }
    }
    public static void main(String[] args) throws Exception {
        Path nativeImage = Path.of(args[0]), directory = Path.of(args[1]);
        Files.createDirectory(directory);
        Path first = directory.resolve("first.jar"), second = directory.resolve("second.jar");
        MaterialApiCompilerView.main(new String[]{nativeImage.toString(),first.toString()});
        MaterialApiCompilerView.main(new String[]{nativeImage.toString(),second.toString()});
        if (!Arrays.equals(Files.readAllBytes(first), Files.readAllBytes(second))) throw new AssertionError("non-deterministic compiler metadata view");
        var original = read(nativeImage, ITERABLE); var generated = read(first, ITERABLE);
        if (!"<T:Ljava/lang/Object;>Ljava/lang/Object;Ljava/lang/Iterable<TT;>;".equals(generated.signature))
            throw new AssertionError("compiler signature differs");
        generated.signature = null;
        if (!Arrays.equals(bytes(original),bytes(generated))) throw new AssertionError("view changes more than generic metadata");
        int negatives = 0;
        for (String mutation : List.of("registry-signature", "iterable-signature", "field", "method", "interface", "access", "missing")) {
            var registry = read(nativeImage, REGISTRY); var iterable = read(nativeImage, ITERABLE);
            switch (mutation) {
                case "registry-signature" -> registry.signature = null;
                case "iterable-signature" -> iterable.signature = "Ljava/lang/Object;";
                case "field" -> iterable.fields.add(new FieldNode(Opcodes.ACC_PUBLIC | Opcodes.ACC_STATIC, "changed", "I", null, null));
                case "method" -> iterable.methods.add(new MethodNode(Opcodes.ACC_PUBLIC | Opcodes.ACC_ABSTRACT, "changed", "()V", null, null));
                case "interface" -> iterable.interfaces.add("java/io/Serializable");
                case "access" -> iterable.access |= Opcodes.ACC_SYNTHETIC;
                case "missing" -> { }
                default -> throw new AssertionError(mutation);
            }
            Path changed = directory.resolve(mutation+".jar"), output = directory.resolve(mutation+"-view.jar");
            if (mutation.equals("missing")) input(changed, registry); else input(changed, registry, iterable);
            try { MaterialApiCompilerView.main(new String[]{changed.toString(),output.toString()});
                  throw new AssertionError("compiler-view drift accepted: "+mutation); }
            catch (IllegalArgumentException expected) { negatives++; }
            if (Files.exists(output)) throw new AssertionError("rejected compiler view left an artifact");
        }
        System.out.println("{\"deterministicView\":true,\"onlySignatureChanged\":true,\"negativeInputs\":"+negatives+"}");
    }
}
