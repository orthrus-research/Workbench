package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.net.URI;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.List;

/** Source-only transport and exact byte/UTF-16 location checks; no domain parser. */
final class SourceNavigationClient {
    private SourceNavigationClient() { }

    static JsonObject invoke(CoreLaunch launch, Path workspace, String session, List<String> action) throws Exception {
        if (!launch.host().equals("native")) throw new IllegalArgumentException("Source navigation requires a native Linux Workbench host.");
        if (session.isBlank() || session.startsWith("-") || session.length() > 256 || session.matches(".*\\s.*")) {
            throw new IllegalArgumentException("Enter one exact Work Session ID.");
        }
        var arguments = new java.util.ArrayList<>(List.of("context", "run", session, "--", "source"));
        arguments.addAll(action);
        String raw = CommandProcess.capture(launch, arguments, 16 * 1024 * 1024, 120, workspace.toString());
        JsonObject envelope = JsonParser.parseString(raw).getAsJsonObject();
        JsonObject result = envelope.getAsJsonObject("result");
        Path root = workspace.toRealPath();
        if (!envelope.get("format").getAsString().equals("workbench-developer-action-v1")
                || envelope.get("exit_code").getAsInt() != 0
                || !Path.of(URI.create(envelope.getAsJsonObject("context").getAsJsonObject("selection").get("pack_uri").getAsString())).equals(root)
                || !Path.of(URI.create(result.getAsJsonObject("context").getAsJsonObject("binding").getAsJsonObject("source_observation").get("root_uri").getAsString())).equals(root)
                || !result.getAsJsonObject("context").getAsJsonObject("authority").get("runtime_authority").getAsString().equals("none")) {
            throw new IllegalArgumentException("Source navigation changed the workspace or source-only authority.");
        }
        return result;
    }

    static Target verify(Path workspace, JsonObject location) throws Exception {
        String relative = location.get("path").getAsString();
        if (!location.get("coordinate_system").getAsString().equals("one-based-utf16")
                || !location.get("interval").getAsString().equals("half-open")
                || relative.isEmpty() || relative.contains("\\") || relative.contains(":") || relative.indexOf('\0') >= 0) {
            throw new IllegalArgumentException("Invalid source location.");
        }
        Path target = workspace.toRealPath();
        for (String part : relative.split("/", -1)) {
            if (part.isEmpty() || part.equals(".") || part.equals("..")) throw new IllegalArgumentException("Unsafe source path.");
            target = target.resolve(part);
            if (Files.isSymbolicLink(target)) throw new IllegalArgumentException("Source path traverses a symbolic link.");
        }
        if (!Files.isRegularFile(target) || Files.size(target) > 64 * 1024 * 1024) throw new IllegalArgumentException("Source file exceeds its bound.");
        byte[] raw = Files.readAllBytes(target);
        if (!HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(raw)).equals(location.get("sha256").getAsString())) {
            throw new IllegalArgumentException("Source selection is stale; search again.");
        }
        int start = location.get("byte_start").getAsBigDecimal().intValueExact();
        int end = location.get("byte_end").getAsBigDecimal().intValueExact();
        if (start < 0 || start > end || end > raw.length) throw new IllegalArgumentException("Invalid source interval.");
        for (String name : List.of("start", "end")) {
            String prefix = StandardCharsets.UTF_8.newDecoder().decode(ByteBuffer.wrap(raw, 0, name.equals("start") ? start : end)).toString();
            JsonObject point = location.getAsJsonObject(name);
            if (point.get("line").getAsBigDecimal().intValueExact() != prefix.chars().filter(c -> c == '\n').count() + 1
                    || point.get("column").getAsBigDecimal().intValueExact() != prefix.length() - prefix.lastIndexOf('\n')) {
                throw new IllegalArgumentException("Editor coordinates differ from exact source bytes.");
            }
        }
        String text = StandardCharsets.UTF_8.newDecoder().decode(ByteBuffer.wrap(raw)).toString().replace("\r\n", "\n");
        return new Target(target, text, location.deepCopy());
    }

    record Target(Path path, String text, JsonObject location) { }
}
