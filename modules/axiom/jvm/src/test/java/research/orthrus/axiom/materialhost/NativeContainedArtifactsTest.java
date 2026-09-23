package research.orthrus.axiom.materialhost;

import org.junit.jupiter.api.*;
import org.junit.jupiter.api.io.TempDir;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;
import java.util.zip.*;
import static org.junit.jupiter.api.Assertions.*;

class NativeContainedArtifactsTest {
    @TempDir Path temporary;
    private final byte[] content="original contained artifact".getBytes(java.nio.charset.StandardCharsets.UTF_8);
    private String digest() throws Exception {return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(content));}
    private Path parent() throws Exception {
        Path parent=temporary.resolve("parent.jar");
        try(var zip=new ZipOutputStream(Files.newOutputStream(parent))) {
            zip.putNextEntry(new ZipEntry("contained.jar"));zip.write(content);zip.closeEntry();
        }
        return parent;
    }
    @Test void verifiesOriginalExtractionWithoutWritingOrRecreatingIt() throws Exception {
        Path parent=parent(),home=Files.createDirectory(temporary.resolve("home")),extracted=home.resolve("child.jar");
        Files.write(extracted,content);var before=Files.getLastModifiedTime(extracted);
        assertEquals(extracted.toRealPath(),NativeContainedArtifacts.verify(parent,"contained.jar",home,"child.jar",digest(),content.length));
        assertEquals(before,Files.getLastModifiedTime(extracted));assertArrayEquals(content,Files.readAllBytes(extracted));
        Files.delete(extracted);
        assertThrows(NoSuchFileException.class,()->NativeContainedArtifacts.verify(parent,"contained.jar",home,"child.jar",digest(),content.length));
        assertFalse(Files.exists(extracted));
    }
    @Test void rejectsMissingMemberWrongPinsChangedBytesAndEscapingPaths() throws Exception {
        Path parent=parent(),home=Files.createDirectory(temporary.resolve("home")),extracted=home.resolve("child.jar");Files.write(extracted,content);
        assertThrows(IllegalStateException.class,()->NativeContainedArtifacts.verify(parent,"absent.jar",home,"child.jar",digest(),content.length));
        assertThrows(IllegalStateException.class,()->NativeContainedArtifacts.verify(parent,"contained.jar",home,"child.jar","0".repeat(64),content.length));
        assertThrows(IllegalStateException.class,()->NativeContainedArtifacts.verify(parent,"contained.jar",home,"child.jar",digest(),content.length+1));
        for(String path:List.of("../child.jar","a/../child.jar",extracted.toString(),"a\\child.jar",".")) {
            assertThrows(IllegalStateException.class,()->NativeContainedArtifacts.verify(parent,"contained.jar",home,path,digest(),content.length));
            assertThrows(IllegalStateException.class,()->NativeContainedArtifacts.verify(parent,path,home,"child.jar",digest(),content.length));
        }
        byte[] changed=content.clone();changed[0]^=1;Files.write(extracted,changed);
        assertThrows(IllegalStateException.class,()->NativeContainedArtifacts.verify(parent,"contained.jar",home,"child.jar",digest(),content.length));
        assertArrayEquals(changed,Files.readAllBytes(extracted));Files.delete(extracted);
        Path outside=temporary.resolve("outside.jar");Files.write(outside,content);Files.createSymbolicLink(extracted,outside);
        assertThrows(IllegalStateException.class,()->NativeContainedArtifacts.verify(parent,"contained.jar",home,"child.jar",digest(),content.length));
    }
}
