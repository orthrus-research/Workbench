package research.orthrus.axiom;

import static org.junit.jupiter.api.Assertions.*;
import java.nio.file.*;
import java.util.*;
import java.util.zip.*;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class NativeMaterialProgramTest {
    @TempDir Path root;
    private Path jar(String file, String... entries) throws Exception {
        Path path = root.resolve(file);
        try (var out = new ZipOutputStream(Files.newOutputStream(path))) {
            for (String name : entries) { out.putNextEntry(new ZipEntry(name)); out.write(new byte[]{1,2,3}); out.closeEntry(); }
        }
        return path;
    }
    private String digest(Path path) throws Exception { return Json.bytesDigest(Files.readAllBytes(path)); }
    @Test void admitsOnlyDeclaredProgramNamespace() throws Exception {
        var p = jar("valid.jar", "research/orthrus/axiom/nativeconstruction/GTFluid.class", "research/orthrus/axiom/nativeconstruction/GTFluid$GTMaterialFluid.class");
        var entries = new HashSet<String>();
        assertEquals(p.toUri().toURL(), NativeVanillaIdentities.materialProgram(p,digest(p),entries));
        assertEquals(2, entries.size());
    }
    @Test void digestMustMatchBeforeAdmission() throws Exception {
        var p = jar("changed.jar", "research/orthrus/axiom/nativeconstruction/Changed.class");
        var entries = new HashSet<String>();
        assertThrows(IllegalArgumentException.class, () -> NativeVanillaIdentities.materialProgram(p,"0".repeat(64),entries));
        assertTrue(entries.isEmpty());
    }
    @Test void duplicateProgramClassesReject() throws Exception {
        var p = jar("duplicate.jar", "research/orthrus/axiom/nativeconstruction/FluidBuilder.class");
        var entries = new HashSet<String>();
        NativeVanillaIdentities.materialProgram(p,digest(p),entries);
        assertThrows(IllegalArgumentException.class, () -> NativeVanillaIdentities.materialProgram(p,digest(p),entries));
    }
    @Test void rejectsResourcesTraversalAmbientClassesAndEmptyArchives() throws Exception {
        int i = 0;
        for (var names : List.of(List.<String>of(), List.of("META-INF/MANIFEST.MF"), List.of("net/minecraft/Fluid.class"),
                List.of("research/orthrus/axiom/nativeconstruction/../Main.class"), List.of("research/orthrus/axiom/Main.class"),
                List.of("research/orthrus/axiom/nativeconstruction/"), List.of("research/orthrus/axiom/nativeconstruction/nested/Class.class"))) {
            var p = jar("bad" + i++ + ".jar", names.toArray(String[]::new));
            var entries = new HashSet<String>();
            assertThrows(IllegalArgumentException.class, () -> NativeVanillaIdentities.materialProgram(p,digest(p),entries));
            assertTrue(entries.isEmpty());
        }
    }
    @Test void indirectProgramRejects() throws Exception {
        var p = jar("real.jar", "research/orthrus/axiom/nativeconstruction/FluidBuilder.class");
        Path link = root.resolve("link.jar"); Files.createSymbolicLink(link,p);
        assertThrows(IllegalArgumentException.class, () -> NativeVanillaIdentities.materialProgram(link,digest(p),new HashSet<>()));
    }
    @Test void invalidProgramDoesNotPartiallyReserveClasses() throws Exception {
        var p = jar("partial.jar", "research/orthrus/axiom/nativeconstruction/FluidBuilder.class", "unexpected.txt");
        var entries = new HashSet<String>();
        assertThrows(IllegalArgumentException.class, () -> NativeVanillaIdentities.materialProgram(p,digest(p),entries));
        assertTrue(entries.isEmpty());
    }
}
