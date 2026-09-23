package dev.workbench.crucible.runtimegraph;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.nio.file.StandardOpenOption;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Create-new publication for a category-local capture bundle. */
public final class CapturePublisher {
    private final CaptureConfiguration configuration;
    private final Map<String, CategoryResult> results =
        new LinkedHashMap<String, CategoryResult>();
    private final Map<String, FileRecord> files = new LinkedHashMap<String, FileRecord>();
    private boolean committed;

    public CapturePublisher(CaptureConfiguration configuration) {
        this.configuration = configuration;
        try {
            Files.createDirectory(configuration.getStaging());
            JsonObject opened = new JsonObject();
            opened.addProperty("format", "workbench-runtime-graph-capture-open-v1");
            opened.addProperty("schema_version", 1);
            opened.addProperty("capture_id", configuration.getCaptureId());
            opened.addProperty("launch_id", configuration.getLaunchId());
            opened.addProperty("input_manifest_sha256", configuration.getInputManifestSha256());
            opened.addProperty("candidate_lock_sha256", configuration.getCandidateLockSha256());
            opened.addProperty("adapter_profile_sha256", configuration.getAdapterProfileSha256());
            write("capture-open.json", CanonicalJson.bytes(opened));
        } catch (IOException exception) {
            throw new IllegalStateException("cannot reserve runtime graph staging", exception);
        }
    }

    public synchronized void writeCategory(CategoryResult result) {
        if (committed) throw new IllegalStateException("capture bundle is already committed");
        if (results.containsKey(result.adapterId())) {
            throw new IllegalStateException("category result already written: " + result.adapterId());
        }
        String filename = result.adapterId() + ".json";
        try {
            write(filename, CanonicalJson.bytes(result.value()));
        } catch (IOException exception) {
            throw new IllegalStateException("cannot write category result " + filename, exception);
        }
        results.put(result.adapterId(), result);
    }

    public synchronized void commit(List<String> requiredAdapters) {
        if (committed) throw new IllegalStateException("capture bundle is already committed");
        if (!results.keySet().containsAll(requiredAdapters)) {
            throw new IllegalStateException("capture bundle lacks required category results");
        }
        try {
            JsonObject manifest = new JsonObject();
            manifest.addProperty("format", "workbench-runtime-graph-raw-bundle-v1");
            manifest.addProperty("schema_version", 1);
            manifest.addProperty("capture_id", configuration.getCaptureId());
            manifest.addProperty("launch_id", configuration.getLaunchId());
            manifest.addProperty("input_manifest_sha256", configuration.getInputManifestSha256());
            manifest.addProperty("candidate_lock_sha256", configuration.getCandidateLockSha256());
            manifest.addProperty("adapter_profile_sha256", configuration.getAdapterProfileSha256());
            manifest.addProperty("physical_side", configuration.getPhysicalSide());
            manifest.addProperty("producer", "dev.workbench.crucible.runtimegraph");

            JsonArray categories = new JsonArray();
            List<String> adapterIds = new ArrayList<String>(results.keySet());
            Collections.sort(adapterIds);
            for (String adapterId : adapterIds) {
                CategoryResult category = results.get(adapterId);
                JsonObject row = new JsonObject();
                row.addProperty("adapter_id", adapterId);
                row.addProperty("status", category.status());
                row.addProperty("file", adapterId + ".json");
                categories.add(row);
            }
            manifest.add("categories", categories);

            JsonArray payloads = new JsonArray();
            List<String> filenames = new ArrayList<String>(files.keySet());
            Collections.sort(filenames);
            for (String filename : filenames) {
                FileRecord file = files.get(filename);
                JsonObject row = new JsonObject();
                row.addProperty("file", filename);
                row.addProperty("sha256", file.sha256);
                row.addProperty("size", file.size);
                payloads.add(row);
            }
            manifest.add("payloads", payloads);
            manifest.addProperty("manifest_sha256", CanonicalJson.sha256(manifest));
            write("manifest.json", CanonicalJson.bytes(manifest));

            Path marker = configuration.getStaging().resolve(".capture-complete");
            FileChannel markerChannel = FileChannel.open(
                marker,
                StandardOpenOption.CREATE_NEW,
                StandardOpenOption.WRITE
            );
            try {
                markerChannel.force(true);
            } finally {
                markerChannel.close();
            }
            try {
                Files.move(
                    configuration.getStaging(),
                    configuration.getOutput(),
                    StandardCopyOption.ATOMIC_MOVE
                );
            } catch (AtomicMoveNotSupportedException exception) {
                throw new IOException("runtime graph publication requires atomic directory move", exception);
            }
            committed = true;
        } catch (IOException exception) {
            throw new IllegalStateException("cannot commit runtime graph capture", exception);
        }
    }

    private void write(String filename, byte[] value) throws IOException {
        Path target = configuration.getStaging().resolve(filename);
        FileChannel channel = FileChannel.open(
            target,
            StandardOpenOption.CREATE_NEW,
            StandardOpenOption.WRITE
        );
        try {
            ByteBuffer buffer = ByteBuffer.wrap(value);
            while (buffer.hasRemaining()) channel.write(buffer);
            channel.force(true);
        } finally {
            channel.close();
        }
        files.put(filename, new FileRecord(value.length, Hashing.sha256(value)));
    }

    private static final class FileRecord {
        private final long size;
        private final String sha256;

        private FileRecord(long size, String sha256) {
            this.size = size;
            this.sha256 = sha256;
        }
    }
}
