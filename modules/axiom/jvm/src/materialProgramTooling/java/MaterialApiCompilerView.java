package research.orthrus.axiom.tooling;

import java.nio.file.*;
import java.util.*;
import java.util.jar.*;
import org.objectweb.asm.*;
import org.objectweb.asm.tree.*;

/** Compiler-only metadata. Never put this view on the execution classpath. */
public final class MaterialApiCompilerView {
    public static void main(String[] args) throws Exception {
        if (args.length != 3) throw new IllegalArgumentException("native-image.jar native-mods.jar new-compiler-view.jar");
        String name = "net/minecraft/util/IObjectIntIterable";
        try (var input = new JarFile(args[0])) {
            ClassNode registry = read(input, "net/minecraft/util/registry/RegistryNamespaced");
            ClassNode iterable = read(input, name);
            if (!"<K:Ljava/lang/Object;V:Ljava/lang/Object;>Lnet/minecraft/util/registry/RegistrySimple<TK;TV;>;Lnet/minecraft/util/IObjectIntIterable<TV;>;".equals(registry.signature))
                throw new IllegalArgumentException("Native registry generic usage changed");
            if (!iterable.name.equals(name) || iterable.signature != null || !iterable.superName.equals("java/lang/Object")
                    || !iterable.interfaces.equals(List.of("java/lang/Iterable")) || !iterable.fields.isEmpty() || !iterable.methods.isEmpty()
                    || iterable.access != (Opcodes.ACC_PUBLIC | Opcodes.ACC_ABSTRACT | Opcodes.ACC_INTERFACE))
                throw new IllegalArgumentException("Native raw iterable declaration changed");
            // Complete the generic arity required by the unchanged native registry
            // signature for javac. No methods, fields, interfaces or executable
            // behavior are supplied. The raw runtime class remains untouched.
            iterable.signature = "<T:Ljava/lang/Object;>Ljava/lang/Object;Ljava/lang/Iterable<TT;>;";
            var writer = new ClassWriter(0); iterable.accept(writer);
            try (var output = new JarOutputStream(Files.newOutputStream(Path.of(args[2]), StandardOpenOption.CREATE_NEW));
                 var mods = new JarFile(args[1])) {
                var entry = new JarEntry(name + ".class"); entry.setTime(0);
                output.putNextEntry(entry); output.write(writer.toByteArray()); output.closeEntry();
                // javac does not run Cleanroom's Optional transformer. Hide only
                // this annotation-declared absent optional interface in its view.
                // Runtime retains the original class and original transformer.
                ClassNode meta = read(mods,"gregtech/api/items/metaitem/MetaItem");
                String iface="com.enderio.core.common.interfaces.IOverlayRenderAware";
                var optional=meta.visibleAnnotations.stream().filter(a->a.desc.equals("Lnet/minecraftforge/fml/common/Optional$Interface;")).toList();
                if(optional.size()!=1||!optional.getFirst().values.equals(List.of("modid","endercore","iface",iface))
                        ||!meta.interfaces.remove(iface.replace('.','/')))
                    throw new IllegalArgumentException("Native MetaItem optional interface declaration changed");
                String optionalSignature="L"+iface.replace('.','/')+";";
                if(meta.signature==null||!meta.signature.endsWith(optionalSignature))
                    throw new IllegalArgumentException("Native MetaItem optional generic signature changed");
                meta.signature=meta.signature.substring(0,meta.signature.length()-optionalSignature.length());
                var metaWriter=new ClassWriter(0);meta.accept(metaWriter);
                var metaEntry=new JarEntry(meta.name+".class");metaEntry.setTime(0);
                output.putNextEntry(metaEntry);output.write(metaWriter.toByteArray());output.closeEntry();
            }
        }
    }
    private static ClassNode read(JarFile file, String name) throws Exception {
        var entry = file.getJarEntry(name + ".class");
        if (entry == null) throw new IllegalArgumentException("Native compiler-view input missing: " + name);
        try (var stream = file.getInputStream(entry)) {
            var node = new ClassNode(); new ClassReader(stream).accept(node,0); return node;
        }
    }
}
