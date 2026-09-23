package research.orthrus.axiom;

import java.io.*;
import java.net.*;
import java.nio.file.*;
import java.util.*;
import java.util.jar.*;
import java.util.zip.Adler32;
import net.minecraft.launchwrapper.LaunchClassLoader;
import net.minecraftforge.fml.common.patcher.ClassPatchManager;
import net.minecraftforge.fml.common.asm.transformers.AccessTransformer;
import net.minecraftforge.fml.common.asm.transformers.EventSubscriptionTransformer;
import net.minecraftforge.fml.common.asm.transformers.deobf.*;
import net.minecraftforge.fml.relauncher.Side;
import org.objectweb.asm.*;
import org.objectweb.asm.tree.*;

/** Offline input construction using original Cleanroom transformations, not a launcher. */
public final class NativeIdentityInputs {
    private static final String BOOTSTRAP = "net/minecraft/init/Bootstrap";
    private static final String FLUID_EVENT = "net/minecraftforge/fluids/FluidRegistry$FluidRegisterEvent";

    private static final class Bytes extends LaunchClassLoader {
        final JarFile minecraft, cleanroom;
        Bytes(JarFile minecraft, JarFile cleanroom) {
            super(new URL[0]); this.minecraft = minecraft; this.cleanroom = cleanroom;
        }
        @Override public byte[] getClassBytes(String name) throws IOException {
            String path = name.replace('.', '/') + ".class";
            for (var jar : List.of(minecraft, cleanroom)) {
                var entry = jar.getJarEntry(path);
                if (entry != null) try (var stream = jar.getInputStream(entry)) { return stream.readAllBytes(); }
            }
            // Remapper superclass queries can reach selected libraries and JDK types.
            try (var stream = NativeIdentityInputs.class.getClassLoader().getResourceAsStream(path)) {
                return stream == null ? null : stream.readAllBytes();
            }
        }
        @Override public Class<?> loadClass(String name) throws ClassNotFoundException {
            throw new ClassNotFoundException("Byte provider must not define classes: " + name);
        }
    }

    /** Exact straight-line constructor prefix; the full upstream method stays untouched. */
    static byte[] prefix(byte[] bytes) {
        ClassNode node = new ClassNode(); new ClassReader(bytes).accept(node, 0);
        if (!node.name.equals(BOOTSTRAP)) throw new IllegalArgumentException("Wrong bootstrap class");
        var methods = node.methods.stream().filter(m -> m.name.equals("func_151354_b") && m.desc.equals("()V")).toList();
        if (methods.size() != 1 || node.methods.stream().anyMatch(m -> m.name.equals("axiom$materialIdentities")))
            throw new IllegalArgumentException("Ambiguous bootstrap method");
        MethodNode original = methods.getFirst();
        if (!original.tryCatchBlocks.isEmpty()) throw new IllegalArgumentException("Changed bootstrap exception boundary");
        var prefix = new MethodNode(Opcodes.ACC_PUBLIC | Opcodes.ACC_STATIC, "axiom$materialIdentities", "()V", null, null);
        original.accept(prefix);
        List<String> observed = new ArrayList<>(); MethodInsnNode stop = null; JumpInsnNode guard = null;
        for (var ins : prefix.instructions) {
            if (ins.getOpcode() < 0) continue;
            if (ins instanceof FieldInsnNode f) observed.add(f.getOpcode() + ":" + f.owner + "." + f.name + f.desc);
            else if (ins instanceof MethodInsnNode m) {
                observed.add(m.getOpcode() + ":" + m.owner + "." + m.name + m.desc);
                if (m.owner.equals("net/minecraft/item/Item") && m.name.equals("func_150900_l")) { stop = m; break; }
            } else if (ins instanceof JumpInsnNode jump) { observed.add("jump:" + ins.getOpcode()); guard = jump; }
            else observed.add("opcode:" + ins.getOpcode());
        }
        var expected = List.of(
                "178:" + BOOTSTRAP + ".field_151355_aZ", "jump:154", "opcode:4",
                "179:" + BOOTSTRAP + ".field_151355_aZ",
                "184:net/minecraft/util/SoundEvent.func_187504_b()V",
                "184:net/minecraft/block/Block.func_149671_p()V",
                "184:net/minecraft/block/BlockFire.func_149843_e()V",
                "184:net/minecraft/potion/Potion.func_188411_k()V",
                "184:net/minecraft/enchantment/Enchantment.func_185257_f()V",
                "184:net/minecraft/item/Item.func_150900_l()V");
        if (!observed.equals(expected) || stop == null || guard == null)
            throw new IllegalArgumentException("Native construction prefix differs: " + observed);
        // Only the original already-registered guard may jump beyond the prefix.
        var destination = guard.label;
        var tail = destination.getNext();
        while (tail != null && tail.getOpcode() < 0) tail = tail.getNext();
        if (tail == null || tail.getOpcode() != Opcodes.RETURN)
            throw new IllegalArgumentException("Bootstrap guard no longer targets return");
        while (stop.getNext() != null) prefix.instructions.remove(stop.getNext());
        var end = new LabelNode(); guard.label = end;
        prefix.instructions.add(end);
        prefix.instructions.add(new FrameNode(Opcodes.F_SAME, 0, null, 0, null));
        prefix.instructions.add(new InsnNode(Opcodes.RETURN));
        prefix.localVariables = null; node.methods.add(prefix);
        var writer = new ClassWriter(0); node.accept(writer); return writer.toByteArray();
    }

    private static void entry(JarOutputStream output, String name, byte[] bytes) throws IOException {
        var entry = new JarEntry(name); entry.setTime(0);
        output.putNextEntry(entry); output.write(bytes); output.closeEntry();
    }

    /** Drift must reject before any constructor code is executed. */
    private static void rejectPrefixDrift(byte[] bytes) {
        for (int mutation = 0; mutation < 11; mutation++) {
            ClassNode node = new ClassNode(); new ClassReader(bytes).accept(node, 0);
            var method = node.methods.stream().filter(m -> m.name.equals("func_151354_b")).findFirst().orElseThrow();
            if (mutation == 0) node.name += "Changed";
            else if (mutation == 1) method.name += "Changed";
            else if (mutation == 2) node.methods.add(new MethodNode(Opcodes.ACC_STATIC, "axiom$materialIdentities", "()V", null, null));
            else if (mutation == 3) {
                LabelNode label = new LabelNode(); method.instructions.insert(label);
                method.tryCatchBlocks.add(new TryCatchBlockNode(label, label, label, "java/lang/Exception"));
            } else if (mutation == 4) {
                for (var ins : method.instructions) if (ins instanceof JumpInsnNode jump) {
                    method.instructions.insert(jump.label, new InsnNode(Opcodes.IRETURN)); break;
                }
            } else {
                int call = 0;
                for (var ins : method.instructions) if (ins instanceof MethodInsnNode invoke && call++ == mutation - 5) {
                    invoke.name += "Changed"; break;
                }
            }
            var writer = new ClassWriter(0); node.accept(writer);
            try { prefix(writer.toByteArray()); throw new AssertionError("Bootstrap drift admitted: " + mutation); }
            catch (IllegalArgumentException expected) { /* Explicitly rejected, never executed. */ }
        }
    }

    private static void verifyPatch(Object patch, byte[] raw) throws ReflectiveOperationException {
        Class<?> type = patch.getClass();
        boolean exists = type.getField("existsAtTarget").getBoolean(patch);
        if (exists != (raw != null && raw.length > 0)) throw new IllegalArgumentException("Binary patch existence differs");
        if (exists) {
            var checksum = new Adler32(); checksum.update(raw);
            if ((int) checksum.getValue() != type.getField("inputChecksum").getInt(patch))
                throw new IllegalArgumentException("Binary patch input checksum differs");
        }
    }

    public static void main(String[] args) throws Exception {
        NativeRuntime.require(); WorkerIsolation.install();
        if (args.length == 4 && "server-root".equals(args[0])) {
            serverRoot(Path.of(args[1]), Path.of(args[2]), Path.of(args[3]));
            return;
        }
        if (args.length != 3) throw new IllegalArgumentException("minecraft.jar cleanroom.jar new-output-root required");
        Path output = Path.of(args[2]); Files.createDirectory(output);
        ClassPatchManager.INSTANCE.setup(Side.CLIENT);
        var patchField = ClassPatchManager.class.getDeclaredField("patches"); patchField.setAccessible(true);
        var patches = (com.google.common.collect.ListMultimap<?, ?>) patchField.get(ClassPatchManager.INSTANCE);
        // The container has 1,187 records; native setup excludes 22 zero-length patches.
        if (patches == null || patches.size() != 1165) throw new IllegalArgumentException("Selected client patch inventory differs");
        var traces = new TreeMap<String, Object>();
        try (var minecraft = new JarFile(args[0]); var cleanroom = new JarFile(args[1]); var input = new Bytes(minecraft, cleanroom);
             var image = new JarOutputStream(Files.newOutputStream(output.resolve("cleanroom-classes.jar"), StandardOpenOption.CREATE_NEW));
             var checkpoint = new JarOutputStream(Files.newOutputStream(output.resolve("construction-prefix.jar"), StandardOpenOption.CREATE_NEW))) {
            FMLDeobfuscatingRemapper.INSTANCE.setup(null, input, "/deobf_data-1.12.2.tsrg", true);
            var access = new AccessTransformer();
            var events = new EventSubscriptionTransformer();
            Set<String> names = new TreeSet<>();
            for (var jar : List.of(minecraft, cleanroom)) jar.stream().filter(e -> e.getName().endsWith(".class"))
                    .forEach(e -> { if (!names.add(e.getName().substring(0, e.getName().length() - 6))) throw new IllegalArgumentException("Duplicate input class"); });
            for (Object name : patches.keySet()) names.add(name.toString().replace('.', '/'));
            Set<String> mappedNames = new HashSet<>(); int newClasses = 0;
            for (String name : names) {
                String mapped = FMLDeobfuscatingRemapper.INSTANCE.map(name);
                if (!mappedNames.add(mapped)) throw new IllegalArgumentException("Duplicate remapped class: " + mapped);
                byte[] raw = input.getClassBytes(name);
                var group = patches.asMap().get(name.replace('/', '.'));
                if (group != null) {
                    if (group.size() != 1) throw new IllegalArgumentException("Unqualified chained patches");
                    verifyPatch(group.iterator().next(), raw);
                }
                if (raw == null) newClasses++;
                byte[] patched = ClassPatchManager.INSTANCE.applyPatch(name.replace('/', '.'), mapped.replace('/', '.'), raw);
                var writer = new ClassWriter(0); new ClassReader(patched).accept(new FMLRemappingAdapter(writer), 0);
                byte[] transformed = access.transform(name, mapped.replace('/', '.'), writer.toByteArray());
                if (mapped.equals(FLUID_EVENT)) transformed = events.transform(mapped.replace('/', '.'), mapped.replace('/', '.'), transformed);
                entry(image, mapped + ".class", transformed);
                if (mapped.equals(BOOTSTRAP)) {
                    rejectPrefixDrift(transformed);
                    entry(checkpoint, mapped + ".class", prefix(transformed));
                }
                traces.put(mapped, Map.of("source", name, "inputSha256", raw == null ? "absent" : Json.bytesDigest(raw),
                        "patchedSha256", Json.bytesDigest(patched), "outputSha256", Json.bytesDigest(transformed)));
            }
            for (String resource : List.of("mcpmod.info")) {
                var source = cleanroom.getJarEntry(resource);
                if (source == null) throw new IllegalArgumentException("Missing native metadata: " + resource);
                try (var stream = cleanroom.getInputStream(source)) { entry(image, resource, stream.readAllBytes()); }
            }
            if (traces.size() != 5185 || newClasses != 22) throw new IllegalArgumentException("Native class inventory differs");
            Files.writeString(output.resolve("transforms.json"), Json.write(Map.of("schema", "axiom.native-identity-transforms.v1",
                    "classes", traces, "newClasses", newClasses, "binaryPatches", patches.size(),
                    "minecraftLaunched", false, "wholePackParity", false)), StandardOpenOption.CREATE_NEW);
        }
        System.out.println(Json.write(Map.of("classes", traces.size(), "binaryPatches", patches.size(), "prefixDriftRejections", 11, "minecraftLaunched", false)));
    }

    /** A separate raw SERVER contract; does not replace the bounded CLIENT images. */
    private static void serverRoot(Path minecraftPath, Path cleanroomPath, Path output) throws Exception {
        Files.createDirectory(output);
        ClassPatchManager.INSTANCE.setup(Side.SERVER);
        var patchField = ClassPatchManager.class.getDeclaredField("patches"); patchField.setAccessible(true);
        var patches = (com.google.common.collect.ListMultimap<?, ?>) patchField.get(ClassPatchManager.INSTANCE);
        if (patches == null || patches.isEmpty()) throw new IllegalArgumentException("Original SERVER binary patch set unavailable");
        var patchInventory = new TreeMap<String,Object>();
        var witnesses = new TreeMap<String,Object>();
        var resources = new TreeMap<String,Object>();
        try (var minecraft = new JarFile(minecraftPath.toFile()); var cleanroom = new JarFile(cleanroomPath.toFile());
             var input = new Bytes(minecraft, cleanroom)) {
            // Validate every selected original patch before remapper superclass/field queries.
            for (var group : patches.asMap().entrySet()) {
                if (group.getValue().size() != 1) throw new IllegalArgumentException("Unqualified chained SERVER patch");
                Object patch = group.getValue().iterator().next();
                byte[] raw = input.getClassBytes(group.getKey().toString());
                verifyPatch(patch, raw);
                var type = patch.getClass();
                patchInventory.put(group.getKey().toString(), Map.of("target", type.getField("targetClassName").get(patch),
                        "inputSha256", raw == null ? "absent" : Json.bytesDigest(raw),
                        "inputChecksum", type.getField("inputChecksum").get(patch),
                        "patchSha256", Json.bytesDigest((byte[])type.getField("patch").get(patch))));
            }
            for (String resource : List.of("binpatches.pack.lzma", "deobf_data-1.12.2.tsrg", "forge_at.cfg", "mcpmod.info")) {
                var entry = cleanroom.getJarEntry(resource);
                if (entry == null) throw new IllegalArgumentException("Original root resource missing: " + resource);
                try (var stream = cleanroom.getInputStream(entry)) {
                    byte[] raw = stream.readAllBytes();
                    resources.put(resource, Map.of("sha256", Json.bytesDigest(raw), "size", raw.length));
                }
            }
            FMLDeobfuscatingRemapper.INSTANCE.setup(null, input, "/deobf_data-1.12.2.tsrg", true);
            var access = new AccessTransformer();
            for (String mapped : List.of("net/minecraft/block/Block", "net/minecraft/item/ItemStack",
                    "net/minecraft/util/EnumFacing", "net/minecraft/util/math/Vec3d", "net/minecraft/enchantment/Enchantment")) {
                String name = FMLDeobfuscatingRemapper.INSTANCE.unmap(mapped);
                if (name.equals(mapped)) throw new IllegalArgumentException("Original SERVER remapping unavailable: " + mapped);
                byte[] raw = input.getClassBytes(name);
                if (raw == null || !patchInventory.containsKey(name.replace('/', '.')))
                    throw new IllegalArgumentException("Witness lacks original raw SERVER patch: " + mapped);
                byte[] patched = ClassPatchManager.INSTANCE.applyPatch(name.replace('/', '.'), mapped.replace('/', '.'), raw);
                var writer = new ClassWriter(0); new ClassReader(patched).accept(new FMLRemappingAdapter(writer), 0);
                byte[] remapped = writer.toByteArray();
                byte[] transformed = access.transform(name, mapped.replace('/', '.'), remapped);
                witnesses.put(mapped, Map.of("source", name, "inputSha256", Json.bytesDigest(raw),
                        "patchedSha256", Json.bytesDigest(patched), "remappedSha256", Json.bytesDigest(remapped),
                        "accessSha256", Json.bytesDigest(transformed), "classInternalName", new ClassReader(transformed).getClassName()));
            }
        }
        var descriptor = new LinkedHashMap<String,Object>();
        descriptor.put("schema", "axiom.native-root-class-space.v1"); descriptor.put("side", "SERVER");
        descriptor.put("inputStage", "original-obfuscated-artifacts");
        descriptor.put("witnessStage", "original-server-patch-remap-access-byte-probe");
        descriptor.put("minecraft", artifact(minecraftPath)); descriptor.put("cleanroom", artifact(cleanroomPath));
        descriptor.put("resources", resources); descriptor.put("binaryPatches", patches.size());
        descriptor.put("patchInventory", patchInventory); descriptor.put("witnesses", witnesses);
        descriptor.put("targetClassesDefined", false); descriptor.put("minecraftLaunched", false);
        descriptor.put("materialInitializationComplete", false);
        Files.writeString(output.resolve("root-class-space.json"), Json.write(descriptor), StandardOpenOption.CREATE_NEW);
        System.out.println(Json.write(Map.of("schema", "axiom.native-root-input-build.v1", "side", "SERVER",
                "binaryPatches", patches.size(), "witnesses", witnesses.size(), "minecraftLaunched", false)));
    }

    private static Map<String,Object> artifact(Path path) throws IOException {
        return Map.of("name", path.getFileName().toString(), "sha256", Json.bytesDigest(Files.readAllBytes(path)), "size", Files.size(path));
    }
}
