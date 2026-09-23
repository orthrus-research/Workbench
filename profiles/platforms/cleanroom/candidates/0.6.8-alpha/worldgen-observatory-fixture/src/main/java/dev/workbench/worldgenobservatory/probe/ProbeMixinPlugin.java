package dev.workbench.worldgenobservatory.probe;

import org.objectweb.asm.ClassWriter;
import org.objectweb.asm.tree.ClassNode;
import org.objectweb.asm.tree.MethodNode;
import org.spongepowered.asm.mixin.extensibility.IMixinConfigPlugin;
import org.spongepowered.asm.mixin.extensibility.IMixinInfo;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Arrays;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Validates exact transformed targets and publishes install-time probe health.
 * A missing or duplicated handler is a binding failure, not partial success.
 */
public final class ProbeMixinPlugin implements IMixinConfigPlugin {

    private static final Map<String, List<HookSpec>> HOOKS;

    static {
        Map<String, List<HookSpec>> hooks = new LinkedHashMap<>();
        hooks.put(
            "dev.workbench.worldgenobservatory.probe.mixin.MixinChunkProviderServer",
            Arrays.asList(
                new HookSpec("cleanroom-worldgen:cps.provide_chunk", "workbench$observeProvideChunk", "provideChunk", "(II)Lnet/minecraft/world/chunk/Chunk;"),
                new HookSpec("cleanroom-worldgen:cps.load_chunk", "workbench$observeLoadChunk", "loadChunk", "(II)Lnet/minecraft/world/chunk/Chunk;"),
                new HookSpec("cleanroom-worldgen:cps.load_chunk_callback", "workbench$observeLoadChunkCallback", "loadChunk", "(IILjava/lang/Runnable;)Lnet/minecraft/world/chunk/Chunk;"),
                new HookSpec("cleanroom-worldgen:cps.generate_chunk_call", "workbench$observeGenerateChunkCall", "provideChunk", "(II)Lnet/minecraft/world/chunk/Chunk;")
            )
        );
        hooks.put(
            "dev.workbench.worldgenobservatory.probe.mixin.MixinChunk",
            Arrays.asList(
                new HookSpec("cleanroom-worldgen:chunk.populate_neighbors", "workbench$observePublicPopulate", "populate", "(Lnet/minecraft/world/chunk/IChunkProvider;Lnet/minecraft/world/gen/IChunkGenerator;)V"),
                new HookSpec("cleanroom-worldgen:chunk.populate_owned", "workbench$observeOwnedPopulate", "populate", "(Lnet/minecraft/world/gen/IChunkGenerator;)V"),
                new HookSpec("cleanroom-worldgen:chunk.generator_populate_call", "workbench$observeGeneratorPopulateCall", "populate", "(Lnet/minecraft/world/gen/IChunkGenerator;)V"),
                new HookSpec("cleanroom-worldgen:write.chunk_storage", "workbench$observeChunkSetBlockState", "setBlockState", "(Lnet/minecraft/util/math/BlockPos;Lnet/minecraft/block/state/IBlockState;)Lnet/minecraft/block/state/IBlockState;")
            )
        );
        hooks.put(
            "dev.workbench.worldgenobservatory.probe.mixin.MixinChunkPrimer",
            Collections.singletonList(
                new HookSpec("cleanroom-worldgen:write.chunk_primer", "workbench$observePrimerSetBlockState", "setBlockState", "(IIILnet/minecraft/block/state/IBlockState;)V")
            )
        );
        hooks.put(
            "dev.workbench.worldgenobservatory.probe.mixin.MixinWorld",
            Collections.singletonList(
                new HookSpec("cleanroom-worldgen:write.world_api", "workbench$observeWorldSetBlockState", "setBlockState", "(Lnet/minecraft/util/math/BlockPos;Lnet/minecraft/block/state/IBlockState;I)Z")
            )
        );
        hooks.put(
            "dev.workbench.worldgenobservatory.probe.mixin.MixinEventBus",
            Arrays.asList(
                new HookSpec("cleanroom-worldgen:event_bus.post", "workbench$observePost", "post", "(Lnet/minecraftforge/fml/common/eventhandler/Event;)Z"),
                new HookSpec("cleanroom-worldgen:event_bus.listener_invoke", "workbench$observeListenerInvocation", "post", "(Lnet/minecraftforge/fml/common/eventhandler/Event;)Z")
            )
        );
        hooks.put(
            "dev.workbench.worldgenobservatory.probe.mixin.ASMEventHandlerAccess",
            Arrays.asList(
                new HookSpec("cleanroom-worldgen:event_bus.listener_owner_accessor", "workbench$getOwner", "workbench$getOwner", "()Lnet/minecraftforge/fml/common/ModContainer;"),
                new HookSpec("cleanroom-worldgen:event_bus.listener_readable_accessor", "workbench$getReadable", "workbench$getReadable", "()Ljava/lang/String;")
            )
        );
        HOOKS = Collections.unmodifiableMap(hooks);
    }

    private final Map<String, String> originalDigests = new ConcurrentHashMap<>();

    @Override
    public void onLoad(String mixinPackage) {
        // No late discovery: the config and target set are exact and static.
    }

    @Override
    public String getRefMapperConfig() {
        return null;
    }

    @Override
    public boolean shouldApplyMixin(String targetClassName, String mixinClassName) {
        return true;
    }

    @Override
    public void acceptTargets(Set<String> myTargets, Set<String> otherTargets) {
        // Mixin itself resolves and validates the exact target set.
    }

    @Override
    public List<String> getMixins() {
        return null;
    }

    @Override
    public void preApply(
        String targetClassName,
        ClassNode targetClass,
        String mixinClassName,
        IMixinInfo mixinInfo
    ) {
        originalDigests.put(targetClassName, digest(targetClass));
    }

    @Override
    public void postApply(
        String targetClassName,
        ClassNode targetClass,
        String mixinClassName,
        IMixinInfo mixinInfo
    ) {
        List<HookSpec> hooks = HOOKS.get(mixinClassName);
        if (hooks == null) {
            throw new IllegalStateException("No exact hook specification for " + mixinClassName);
        }

        String originalDigest = originalDigests.get(targetClassName);
        if (originalDigest == null) {
            throw new IllegalStateException("Missing pre-apply digest for " + targetClassName);
        }
        String transformedDigest = digest(targetClass);
        ProbeRuntime.registerTargetBinding(targetClassName, originalDigest, transformedDigest);

        for (HookSpec hook : hooks) {
            int observed = countBindings(targetClass, hook);
            ProbeRuntime.registerHookBinding(
                hook.hookId,
                targetClassName,
                hook.targetMethod,
                hook.targetDescriptor,
                originalDigest,
                transformedDigest,
                1,
                observed
            );
            ProbeRuntime.probeHealth(
                hook.hookId,
                targetClassName,
                hook.targetMethod,
                hook.targetDescriptor,
                originalDigest,
                transformedDigest,
                1,
                observed
            );
            if (observed != 1) {
                throw new IllegalStateException(
                    "Exact probe binding failed for " + hook.hookId
                        + ": expected one merged handler containing " + hook.mergedMethodMarker
                        + " in " + targetClassName + " but found " + observed
                );
            }
        }
    }

    private static int countBindings(ClassNode targetClass, HookSpec hook) {
        if (hook.targetMethod.startsWith("workbench$")) {
            return countExactMethods(targetClass, hook.targetMethod, hook.targetDescriptor);
        }

        String mergedPrefix = isOperationHook(hook.hookId)
            ? "wrapOperation$"
            : "wrapMethod$";
        int count = 0;
        for (MethodNode method : targetClass.methods) {
            if (method.name.startsWith(mergedPrefix)
                && hasExactMarker(method.name, hook.mergedMethodMarker)) {
                count++;
            }
        }
        // MixinExtras emits one exact local-capture bridge beside the one
        // listener operation handler. The injector still enforces a single
        // matched IEventListener.invoke callsite with require/expect/allow=1.
        if ("cleanroom-worldgen:event_bus.listener_invoke".equals(hook.hookId)
            && count == 2) {
            return 1;
        }
        return count;
    }

    private static boolean hasExactMarker(String methodName, String marker) {
        int start = methodName.indexOf(marker);
        if (start < 0) {
            return false;
        }
        int end = start + marker.length();
        return end == methodName.length() || methodName.charAt(end) == '$';
    }

    private static boolean isOperationHook(String hookId) {
        return "cleanroom-worldgen:cps.generate_chunk_call".equals(hookId)
            || "cleanroom-worldgen:chunk.generator_populate_call".equals(hookId)
            || "cleanroom-worldgen:event_bus.listener_invoke".equals(hookId);
    }

    private static int countExactMethods(ClassNode targetClass, String name, String descriptor) {
        int count = 0;
        for (MethodNode method : targetClass.methods) {
            if (method.name.equals(name) && method.desc.equals(descriptor)) {
                count++;
            }
        }
        return count;
    }

    private static String digest(ClassNode node) {
        try {
            ClassWriter writer = new ClassWriter(0);
            node.accept(writer);
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] bytes = digest.digest(writer.toByteArray());
            StringBuilder result = new StringBuilder(64);
            for (byte value : bytes) {
                result.append(Character.forDigit((value >>> 4) & 0x0f, 16));
                result.append(Character.forDigit(value & 0x0f, 16));
            }
            return result.toString();
        } catch (Throwable failure) {
            throw new IllegalStateException("Could not hash transformed class " + node.name, failure);
        }
    }

    private static final class HookSpec {
        private final String hookId;
        private final String mergedMethodMarker;
        private final String targetMethod;
        private final String targetDescriptor;

        private HookSpec(
            String hookId,
            String mergedMethodMarker,
            String targetMethod,
            String targetDescriptor
        ) {
            this.hookId = hookId;
            this.mergedMethodMarker = mergedMethodMarker;
            this.targetMethod = targetMethod;
            this.targetDescriptor = targetDescriptor;
        }
    }
}
