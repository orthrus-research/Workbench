package dev.workbench.worldgenobservatory.trace;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Locale;
import net.minecraft.block.Block;
import net.minecraft.block.state.IBlockState;
import net.minecraft.util.ResourceLocation;
import net.minecraft.util.math.BlockPos;
import net.minecraft.world.chunk.Chunk;

public final class ChunkCheckpoint {

    private static final long FNV_OFFSET_BASIS = 0xCBF29CE484222325L;
    private static final long FNV_PRIME = 0x100000001B3L;

    private ChunkCheckpoint() {
    }

    public static String blockStateDigest(Chunk chunk) {
        long hash = FNV_OFFSET_BASIS;
        BlockPos.MutableBlockPos position = new BlockPos.MutableBlockPos();

        for (int y = 0; y < 256; y++) {
            for (int z = 0; z < 16; z++) {
                for (int x = 0; x < 16; x++) {
                    position.setPos((chunk.x << 4) + x, y, (chunk.z << 4) + z);
                    IBlockState state = chunk.getBlockState(position);
                    ResourceLocation name = Block.REGISTRY.getNameForObject(state.getBlock());
                    hash = update(hash, name == null ? "unregistered" : name.toString());
                    hash ^= state.getBlock().getMetaFromState(state) & 0xFFL;
                    hash *= FNV_PRIME;
                }
            }
        }

        return String.format(Locale.ROOT, "%016x", hash);
    }

    /**
     * SHA-256 over the fixed y/z/x traversal of registry-name length, UTF-8
     * registry-name bytes, and one unsigned metadata byte for every block.
     */
    public static String blockStateSha256(Chunk chunk) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            BlockPos.MutableBlockPos position = new BlockPos.MutableBlockPos();

            for (int y = 0; y < 256; y++) {
                for (int z = 0; z < 16; z++) {
                    for (int x = 0; x < 16; x++) {
                        position.setPos((chunk.x << 4) + x, y, (chunk.z << 4) + z);
                        IBlockState state = chunk.getBlockState(position);
                        ResourceLocation name = Block.REGISTRY.getNameForObject(state.getBlock());
                        update(digest, name == null ? "unregistered" : name.toString());
                        digest.update((byte) (state.getBlock().getMetaFromState(state) & 0xFF));
                    }
                }
            }

            return hex(digest.digest());
        } catch (RuntimeException failure) {
            throw failure;
        } catch (Exception failure) {
            throw new IllegalStateException("SHA-256 checkpoint unavailable", failure);
        }
    }

    private static long update(long hash, String value) {
        for (int index = 0; index < value.length(); index++) {
            hash ^= value.charAt(index);
            hash *= FNV_PRIME;
        }
        return hash;
    }

    private static void update(MessageDigest digest, String value) {
        byte[] bytes = value.getBytes(StandardCharsets.UTF_8);
        digest.update((byte) (bytes.length >>> 24));
        digest.update((byte) (bytes.length >>> 16));
        digest.update((byte) (bytes.length >>> 8));
        digest.update((byte) bytes.length);
        digest.update(bytes);
    }

    private static String hex(byte[] bytes) {
        StringBuilder value = new StringBuilder(bytes.length * 2);
        for (byte element : bytes) {
            value.append(Character.forDigit((element >>> 4) & 0x0F, 16));
            value.append(Character.forDigit(element & 0x0F, 16));
        }
        return value.toString();
    }
}
