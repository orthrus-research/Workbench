package research.orthrus.axiom.materialhost;

import java.io.IOException;
import java.nio.file.*;
import java.security.*;
import java.util.*;
import java.util.zip.ZipFile;

/** Verifies an artifact already extracted by the original native loader.
 * Never extracts, registers or loads a contained dependency. */
public final class NativeContainedArtifacts {
    private NativeContainedArtifacts() {}

    public static Path verify(Path parent,String entry,Path home,String output,String sha256,long size) throws IOException {
        relative(entry);Path relative=relative(output),root=home.toRealPath();
        Path source=root.resolve(relative).toRealPath();
        require(source.startsWith(root)&&Files.isRegularFile(source),"Contained native artifact escaped its worker home");
        byte[] original;
        try(var archive=new ZipFile(parent.toFile())) {
            var member=archive.getEntry(entry);
            require(member!=null&&!member.isDirectory()&&member.getSize()==size,"Contained native artifact entry differs");
            try(var stream=archive.getInputStream(member)) {original=stream.readAllBytes();}
        }
        require(size>=0&&original.length==size&&digest(original).equals(sha256),"Contained native artifact pin differs");
        require(Files.size(source)==size&&Arrays.equals(original,Files.readAllBytes(source)),
                "Original extracted native artifact bytes differ");
        return source;
    }
    private static Path relative(String value) {
        Path path=Path.of(value);
        require(!value.isBlank()&&!path.isAbsolute()&&!value.contains("\\")
                &&!path.startsWith("..")&&path.equals(path.normalize())&&!path.toString().equals("."),
                "Contained native artifact path is not a normalized relative path");
        return path;
    }
    private static String digest(byte[] bytes) {
        try {return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));}
        catch(NoSuchAlgorithmException impossible) {throw new AssertionError(impossible);}
    }
    private static void require(boolean value,String message) {if(!value)throw new IllegalStateException(message);}
}
