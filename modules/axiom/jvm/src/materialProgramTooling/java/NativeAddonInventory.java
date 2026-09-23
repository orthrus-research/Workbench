package research.orthrus.axiom.tooling;

import com.google.gson.Gson;
import net.minecraftforge.fml.common.discovery.asm.ASMModParser;
import net.minecraftforge.fml.common.discovery.ITypeDiscoverer;
import java.io.*;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;
import java.util.zip.*;

/** Original Cleanroom annotation parsing only. Never define a supplied mod class. */
public final class NativeAddonInventory {
    private static String hash(byte[] raw) throws Exception {
        return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(raw));
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 2) throw new IllegalArgumentException("input JAR directory and output JSON required");
        var artifacts = new ArrayList<Object>();
        try (var paths = Files.list(Path.of(args[0]))) {
            for (Path path : paths.sorted().toList()) {
                var declarations = new ArrayList<Object>();
                var apiDeclarations = new ArrayList<Object>();
                var packages = new TreeMap<String,Object>();
                var entries = new HashSet<String>();
                int count = 0;
                long expanded = 0;
                var manifest = new TreeMap<String,String>();
                try (var jar = new java.util.jar.JarFile(path.toFile())) {
                    for (var it = jar.entries(); it.hasMoreElements();) {
                        var entry = it.nextElement();
                        if (!entries.add(entry.getName()) || entries.size() > 100000)
                            throw new IllegalArgumentException("Duplicate/excessive JAR entries: " + path);
                        if (entry.getName().equalsIgnoreCase("META-INF/MANIFEST.MF") && entry.getSize() > 1 << 20)
                            throw new IllegalArgumentException("Oversized JAR manifest");
                        if (!entry.getName().endsWith(".class") || entry.isDirectory()) continue;
                        byte[] raw;
                        try (var input = jar.getInputStream(entry)) { raw = input.readNBytes((4 << 20) + 1); }
                        expanded += raw.length;
                        if (raw.length > 4 << 20 || expanded > 1L << 30)
                            throw new IllegalArgumentException("Class inventory exceeds bound: " + path);
                        var parser = new ASMModParser(new ByteArrayInputStream(raw));
                        parser.validate();
                        count++;
                        // Witness original JarDiscoverer/addClassEntry package membership
                        // with one unchanged eligible class; never synthesize package data.
                        String entryName = entry.getName();
                        if (!entryName.startsWith("__MACOSX") && ITypeDiscoverer.classFile.matcher(entryName).matches()) {
                            String classEntry = entryName.substring(0,entryName.lastIndexOf('.')).replace('/','.');
                            int separator = classEntry.lastIndexOf('.');
                            if (separator >= 0) packages.putIfAbsent(classEntry.substring(0,separator),
                                    Map.of("entry",entryName,"sha256",hash(raw)));
                        }
                        for (var annotation : parser.getAnnotations()) {
                            String annotationName = annotation.getASMType().getClassName();
                            if (!Set.of("net.minecraftforge.fml.common.Mod","net.minecraftforge.fml.common.API").contains(annotationName)) continue;
                            String name = parser.getASMType().getClassName();
                            if (!entry.getName().equals(name.replace('.', '/') + ".class"))
                                throw new IllegalArgumentException("Mod class entry/name mismatch: " + entry.getName());
                            (annotationName.endsWith(".Mod") ? declarations : apiDeclarations).add(Map.of("class", name, "entry", entry.getName(), "sha256", hash(raw),
                                    "annotation", new TreeMap<>(annotation.getValues())));
                        }
                    }
                    // Match the original LibraryManager's JDK manifest reader,
                    // including its case-insensitive entry lookup and continuations.
                    var mf = jar.getManifest();
                    if (mf != null) mf.getMainAttributes().forEach(
                            (key, value) -> manifest.put(key.toString(), value.toString()));
                }
                artifacts.add(Map.of("file", path.getFileName().toString(), "classFiles", count,
                        "declarations", declarations, "apiDeclarations",apiDeclarations,
                        "packageEntries",packages,"manifest", manifest));
            }
        }
        Files.writeString(Path.of(args[1]), new Gson().toJson(Map.of("artifacts", artifacts,
                "parser", "net.minecraftforge.fml.common.discovery.asm.ASMModParser",
                "modClassesDefined", false, "modCallbacksExecuted", false)), StandardOpenOption.CREATE_NEW);
    }
}
