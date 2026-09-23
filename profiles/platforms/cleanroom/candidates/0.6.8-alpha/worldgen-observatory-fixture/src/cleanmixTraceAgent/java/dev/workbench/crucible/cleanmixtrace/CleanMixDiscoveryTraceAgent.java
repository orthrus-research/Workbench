package dev.workbench.crucible.cleanmixtrace;

import java.io.IOException;
import java.io.InputStream;
import java.lang.instrument.ClassFileTransformer;
import java.lang.instrument.Instrumentation;
import java.security.MessageDigest;
import java.security.ProtectionDomain;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;

import org.objectweb.asm.ClassReader;
import org.objectweb.asm.ClassVisitor;
import org.objectweb.asm.ClassWriter;
import org.objectweb.asm.MethodVisitor;
import org.objectweb.asm.Opcodes;
import org.objectweb.asm.Label;
import org.objectweb.asm.Type;
import org.objectweb.asm.commons.AdviceAdapter;

/**
 * Opt-in, exact-byte guarded observer for CleanMix's own service-selection seam.
 *
 * <p>The agent does not install a provider, set a Mixin bypass property, alter a
 * service descriptor, or enumerate a service.  It only substitutes an
 * observationally instrumented copy of the exact published CleanMix 0.7.0
 * {@code MixinService} before that class is defined. Discovery tracing and
 * selected-service component capture remain independently enabled outputs.</p>
 */
public final class CleanMixDiscoveryTraceAgent {

    public static final String ENABLE_PROPERTY =
        "workbench.cleanmix.discovery_trace.enabled";
    public static final String TARGET_CLASS =
        "org/spongepowered/asm/service/MixinService";
    public static final String EXPECTED_TARGET_SHA256 =
        "3e36757484a0b03ac70952b72696a8989506b84c60fb64e5154d26f53895c09b";
    private static final String PAYLOAD_RESOURCE =
        "/workbench/cleanmix-discovery-trace/instrumented/"
            + TARGET_CLASS + ".class";
    private static final String LIFECYCLE_RUNTIME =
        "dev/workbench/crucible/cleanmixtrace/CleanMixConfigLifecycleRuntime";
    private static final Map<String, String> LIFECYCLE_TARGETS = lifecycleTargets();
    private static final String CHAIN_RUNTIME =
        "dev/workbench/crucible/cleanmixtrace/CleanMixTransformerChainRuntime";
    private static final Map<String, String> CHAIN_TARGETS = chainTargets();
    static final String FINAL_DEFINITION_TARGET_CLASS =
        "top/outlands/foundation/boot/ActualClassLoader";
    static final String FINAL_DEFINITION_TARGET_SHA256 =
        "41cc84afadb4155ea5d15f5132f08b23327e6bf51edfb5615d1a6f5c6eb7706f";
    static final String LEGACY_FINAL_DEFINITION_TARGET_SHA256 =
        "e91b78341e98b7141666c128df0cc4917591430042bc0cb762858f7c0c0e46a2";
    static final String DIRECT_APPLICATION_TARGET_CLASS =
        "org/spongepowered/asm/mixin/transformer/MixinApplicatorStandard";
    static final String DIRECT_APPLICATION_TARGET_SHA256 =
        "b0bd92ba11df86ec1eb3027a665729877e87fe3ca4c3574995d18b0fd210cc46";
    private static final String DIRECT_APPLICATION_RUNTIME =
        "dev/workbench/crucible/cleanmixtrace/CleanMixDirectApplicationRuntime";

    private CleanMixDiscoveryTraceAgent() {
    }

    public static void premain(String ignoredArguments, Instrumentation instrumentation) {
        boolean discoveryTraceEnabled =
            Boolean.parseBoolean(System.getProperty(ENABLE_PROPERTY, "false"));
        boolean serviceComponentsEnabled = CleanMixServiceComponentRuntime.enabled();
        boolean configLifecycleEnabled = CleanMixConfigLifecycleRuntime.enabled();
        boolean transformerChainEnabled = CleanMixTransformerChainRuntime.enabled();
        boolean finalDefinitionEnabled = FoundationFinalDefinitionRuntime.enabled();
        boolean directApplicationEnabled = CleanMixDirectApplicationRuntime.enabled();
        if (!discoveryTraceEnabled && !serviceComponentsEnabled && !configLifecycleEnabled
                && !transformerChainEnabled && !finalDefinitionEnabled
                && !directApplicationEnabled) {
            return;
        }
        if (discoveryTraceEnabled) {
            CleanMixDiscoveryTraceRuntime.start(instrumentation);
        }
        if (serviceComponentsEnabled) {
            CleanMixServiceComponentRuntime.start(instrumentation);
        }
        if (configLifecycleEnabled) {
            CleanMixConfigLifecycleRuntime.start(instrumentation);
        }
        if (transformerChainEnabled) {
            CleanMixTransformerChainRuntime.start(instrumentation);
        }
        if (finalDefinitionEnabled) {
            FoundationFinalDefinitionRuntime.start(instrumentation);
        }
        if (directApplicationEnabled) {
            CleanMixDirectApplicationRuntime.start(instrumentation);
        }
        instrumentation.addTransformer(new ExactCleanMixTransformer(), false);
        if (discoveryTraceEnabled) {
            CleanMixDiscoveryTraceRuntime.transformerInstalled();
        }
        if (serviceComponentsEnabled) {
            CleanMixServiceComponentRuntime.transformerInstalled();
        }
        if (configLifecycleEnabled) {
            CleanMixConfigLifecycleRuntime.transformerInstalled();
        }
        if (transformerChainEnabled) {
            CleanMixTransformerChainRuntime.transformerInstalled();
        }
        if (finalDefinitionEnabled) {
            FoundationFinalDefinitionRuntime.transformerInstalled();
        }
        if (directApplicationEnabled) {
            CleanMixDirectApplicationRuntime.transformerInstalled();
        }
    }

    private static final class ExactCleanMixTransformer
        implements ClassFileTransformer {

        @Override
        public byte[] transform(
            ClassLoader loader,
            String className,
            Class<?> classBeingRedefined,
            ProtectionDomain protectionDomain,
            byte[] classfileBuffer
        ) {
            boolean mixinServiceTarget = TARGET_CLASS.equals(className);
            String lifecycleExpected = LIFECYCLE_TARGETS.get(className);
            String chainExpected = CHAIN_TARGETS.get(className);
            boolean finalDefinitionTarget =
                FINAL_DEFINITION_TARGET_CLASS.equals(className);
            boolean directApplicationTarget =
                DIRECT_APPLICATION_TARGET_CLASS.equals(className);
            if (!mixinServiceTarget && lifecycleExpected == null
                    && chainExpected == null && !finalDefinitionTarget
                    && !directApplicationTarget) {
                return null;
            }

            // CleanMix configuration state for this profile is owned by the
            // LaunchClassLoader definition.  The same CleanMix classes can be
            // resolved through the application loader while an apply failure
            // is being reported; weaving those diagnostic copies would make
            // one capture cross runtime owners and invalidate its exact target
            // cardinality.
            if ((lifecycleExpected != null || directApplicationTarget)
                    && (loader == null || !"net.minecraft.launchwrapper.LaunchClassLoader"
                        .equals(loader.getClass().getName()))) {
                return null;
            }

            String inputSha256 = sha256(classfileBuffer);
            String expected = mixinServiceTarget
                    ? EXPECTED_TARGET_SHA256
                : lifecycleExpected != null
                    ? lifecycleExpected
                    : chainExpected != null
                        ? chainExpected : finalDefinitionTarget
                            ? FoundationFinalDefinitionRuntime.expectedTargetSha256()
                            : DIRECT_APPLICATION_TARGET_SHA256;
            if (!expected.equals(inputSha256)) {
                if (mixinServiceTarget) {
                    CleanMixDiscoveryTraceRuntime.transformRejected(
                        loader, protectionDomain, inputSha256,
                        "target_class_sha256_mismatch"
                    );
                    CleanMixServiceComponentRuntime.transformRejected(
                        loader, protectionDomain, inputSha256,
                        "target_class_sha256_mismatch"
                    );
                }
                if (lifecycleExpected != null) {
                    CleanMixConfigLifecycleRuntime.transformRejected(
                        className, loader, protectionDomain, inputSha256,
                        "target_class_sha256_mismatch"
                    );
                }
                if (chainExpected != null) {
                    CleanMixTransformerChainRuntime.transformRejected(
                        className, loader, protectionDomain, inputSha256,
                        "target_class_sha256_mismatch"
                    );
                }
                if (finalDefinitionTarget) {
                    FoundationFinalDefinitionRuntime.transformRejected(
                        className, loader, protectionDomain, inputSha256,
                        "target_class_sha256_mismatch"
                    );
                }
                if (directApplicationTarget) {
                    CleanMixDirectApplicationRuntime.transformRejected(
                        className, loader, protectionDomain, inputSha256,
                        "target_class_sha256_mismatch"
                    );
                }
                return null;
            }

            try {
                byte[] output = mixinServiceTarget
                    ? readPayload()
                    : lifecycleExpected != null
                        ? weaveLifecycle(className, classfileBuffer)
                        : chainExpected != null
                            ? weaveTransformerChain(className, classfileBuffer)
                            : finalDefinitionTarget
                                ? weaveFinalDefinition(classfileBuffer)
                                : weaveDirectApplication(classfileBuffer);
                String outputSha256 = sha256(output);
                if (mixinServiceTarget) {
                    CleanMixDiscoveryTraceRuntime.transformApplied(
                        loader, protectionDomain, inputSha256, outputSha256
                    );
                    CleanMixServiceComponentRuntime.transformApplied(
                        loader, protectionDomain, inputSha256, outputSha256
                    );
                }
                if (lifecycleExpected != null) {
                    CleanMixConfigLifecycleRuntime.transformApplied(
                        className, loader, protectionDomain, inputSha256, outputSha256
                    );
                }
                if (chainExpected != null) {
                    CleanMixTransformerChainRuntime.transformApplied(
                        className, loader, protectionDomain, inputSha256, outputSha256
                    );
                }
                if (finalDefinitionTarget) {
                    FoundationFinalDefinitionRuntime.transformApplied(
                        className, loader, protectionDomain, inputSha256, outputSha256
                    );
                }
                if (directApplicationTarget) {
                    CleanMixDirectApplicationRuntime.transformApplied(
                        className, loader, protectionDomain, inputSha256, outputSha256
                    );
                }
                return output;
            } catch (Throwable failure) {
                if (mixinServiceTarget) {
                    CleanMixDiscoveryTraceRuntime.transformFailed(
                        loader, protectionDomain, inputSha256, failure
                    );
                    CleanMixServiceComponentRuntime.transformFailed(
                        loader, protectionDomain, inputSha256, failure
                    );
                }
                if (lifecycleExpected != null) {
                    CleanMixConfigLifecycleRuntime.transformFailed(
                        className, loader, protectionDomain, inputSha256, failure
                    );
                }
                if (chainExpected != null) {
                    CleanMixTransformerChainRuntime.transformFailed(
                        className, loader, protectionDomain, inputSha256, failure
                    );
                }
                if (finalDefinitionTarget) {
                    FoundationFinalDefinitionRuntime.transformFailed(
                        className, loader, protectionDomain, inputSha256, failure
                    );
                }
                if (directApplicationTarget) {
                    CleanMixDirectApplicationRuntime.transformFailed(
                        className, loader, protectionDomain, inputSha256, failure
                    );
                }
                return null;
            }
        }

        private static byte[] readPayload() throws IOException {
            try (InputStream input =
                    CleanMixDiscoveryTraceAgent.class.getResourceAsStream(PAYLOAD_RESOURCE)) {
                if (input == null) {
                    throw new IOException("instrumented MixinService payload is absent");
                }
                return input.readAllBytes();
            }
        }
    }

    private static Map<String, String> lifecycleTargets() {
        Map<String, String> result = new LinkedHashMap<>();
        result.put(
            "org/spongepowered/asm/mixin/Mixins",
            "7d2ff2c3cb1780c2cb6273ab5426869104b6e8ac7db961033bc1156c11038d74"
        );
        result.put(
            "org/spongepowered/asm/mixin/transformer/Config",
            "7ad7a697935397fff8dc8fe63012615e6b28a9ec3d9df88c2bdcfcea313ee7ca"
        );
        result.put(
            "org/spongepowered/asm/mixin/transformer/MixinConfig",
            "9e0c60d18374facbc6ca6ab429792f81d1ebfb54765fdb32c3b26f6c3d623f47"
        );
        result.put(
            "org/spongepowered/asm/mixin/transformer/MixinProcessor",
            "426ea93ecb32d50f5ca7d4dfcbea9a20f964c6723d6cc89f1e802902942d1b4c"
        );
        return Collections.unmodifiableMap(result);
    }

    static Map<String, String> lifecycleTargetsForReceipt() {
        return LIFECYCLE_TARGETS;
    }

    private static Map<String, String> chainTargets() {
        Map<String, String> result = new LinkedHashMap<>();
        result.put(
            "com/cleanroommc/cleanmix/service/FoundationTransformerProvider",
            "7c44263e004ecf0647a1789bd210c42b1a148647ce39768ed7f31395d0d23185"
        );
        result.put(
            "com/cleanroommc/cleanmix/service/CleanMixService",
            "6acbc3726fc8b5d1087ced2e21c63329666fc2fd48fda13fb0a249cb15f72c29"
        );
        return Collections.unmodifiableMap(result);
    }

    static Map<String, String> chainTargetsForReceipt() {
        return CHAIN_TARGETS;
    }

    private static byte[] weaveLifecycle(String className, byte[] input) {
        ClassReader reader = new ClassReader(input);
        ClassWriter writer = new ClassWriter(reader, ClassWriter.COMPUTE_MAXS);
        LifecycleClassVisitor visitor = new LifecycleClassVisitor(writer, className);
        reader.accept(visitor, ClassReader.EXPAND_FRAMES);
        visitor.verify();
        return writer.toByteArray();
    }

    private static byte[] weaveTransformerChain(String className, byte[] input) {
        ClassReader reader = new ClassReader(input);
        ClassWriter writer = new ClassWriter(reader, ClassWriter.COMPUTE_MAXS);
        TransformerChainClassVisitor visitor =
            new TransformerChainClassVisitor(writer, className);
        reader.accept(visitor, ClassReader.EXPAND_FRAMES);
        visitor.verify();
        return writer.toByteArray();
    }

    private static byte[] weaveFinalDefinition(byte[] input) {
        ClassReader reader = new ClassReader(input);
        ClassWriter writer = new ClassWriter(
            reader, ClassWriter.COMPUTE_FRAMES | ClassWriter.COMPUTE_MAXS
        );
        FinalDefinitionClassVisitor visitor =
            new FinalDefinitionClassVisitor(writer);
        reader.accept(visitor, ClassReader.EXPAND_FRAMES);
        visitor.verify();
        return writer.toByteArray();
    }

    private static byte[] weaveDirectApplication(byte[] input) {
        ClassReader reader = new ClassReader(input);
        ClassWriter writer = new ClassWriter(
            reader, ClassWriter.COMPUTE_FRAMES | ClassWriter.COMPUTE_MAXS
        );
        DirectApplicationClassVisitor visitor =
            new DirectApplicationClassVisitor(writer);
        reader.accept(visitor, ClassReader.EXPAND_FRAMES);
        visitor.verify();
        return writer.toByteArray();
    }

    private static final class DirectApplicationClassVisitor extends ClassVisitor {
        private int hookCount;

        DirectApplicationClassVisitor(ClassVisitor delegate) {
            super(Opcodes.ASM9, delegate);
        }

        @Override
        public MethodVisitor visitMethod(
            int access, String name, String descriptor, String signature,
            String[] exceptions
        ) {
            MethodVisitor delegate = super.visitMethod(
                access, name, descriptor, signature, exceptions
            );
            if (!name.equals("apply") || !descriptor.equals("(Ljava/util/SortedSet;)V")) {
                return delegate;
            }
            hookCount++;
            return new DirectApplicationAdvice(
                delegate, access, name, descriptor
            );
        }

        void verify() {
            if (hookCount != 1) {
                throw new IllegalStateException(
                    "expected one direct-application hook but found " + hookCount
                );
            }
        }
    }

    private static final class DirectApplicationAdvice extends AdviceAdapter {
        private final Label observedBodyStart = new Label();
        private final Label observedBodyEnd = new Label();
        private boolean emittingCatchHandler;

        DirectApplicationAdvice(
            MethodVisitor delegate, int access, String name, String descriptor
        ) {
            super(Opcodes.ASM9, delegate, access, name, descriptor);
        }

        @Override
        protected void onMethodEnter() {
            loadTargetName();
            loadArg(0);
            call("applicationStarted", "(Ljava/lang/String;Ljava/lang/Object;)V");
            mark(observedBodyStart);
        }

        @Override
        protected void onMethodExit(int opcode) {
            if (!emittingCatchHandler && opcode == RETURN) {
                loadTargetName();
                loadArg(0);
                call("applicationCompleted", "(Ljava/lang/String;Ljava/lang/Object;)V");
            }
        }

        @Override
        public void visitMaxs(int maxStack, int maxLocals) {
            mark(observedBodyEnd);
            catchException(observedBodyStart, observedBodyEnd, Type.getType(Throwable.class));
            emittingCatchHandler = true;
            dup();
            loadTargetName();
            loadArg(0);
            call(
                "applicationThrew",
                "(Ljava/lang/Throwable;Ljava/lang/String;Ljava/lang/Object;)V"
            );
            throwException();
            emittingCatchHandler = false;
            super.visitMaxs(maxStack, maxLocals);
        }

        private void loadTargetName() {
            loadThis();
            visitFieldInsn(
                GETFIELD,
                DIRECT_APPLICATION_TARGET_CLASS,
                "targetName",
                "Ljava/lang/String;"
            );
        }

        private void call(String name, String descriptor) {
            visitMethodInsn(
                INVOKESTATIC, DIRECT_APPLICATION_RUNTIME, name, descriptor, false
            );
        }
    }

    private static final class FinalDefinitionClassVisitor extends ClassVisitor {
        private int hookCount;

        FinalDefinitionClassVisitor(ClassVisitor delegate) {
            super(Opcodes.ASM9, delegate);
        }

        @Override
        public MethodVisitor visitMethod(
            int access, String name, String descriptor, String signature,
            String[] exceptions
        ) {
            MethodVisitor delegate = super.visitMethod(
                access, name, descriptor, signature, exceptions
            );
            if (!name.equals("findClass")
                    || !descriptor.equals("(Ljava/lang/String;)Ljava/lang/Class;")) {
                return delegate;
            }
            hookCount++;
            return new FinalDefinitionAdvice(delegate, access, name, descriptor);
        }

        void verify() {
            if (hookCount != 1) {
                throw new IllegalStateException(
                    "expected one final-definition hook but found " + hookCount
                );
            }
        }
    }

    private static final class FinalDefinitionAdvice extends AdviceAdapter {
        private final Label observedBodyStart = new Label();
        private final Label observedBodyEnd = new Label();
        private boolean emittingCatchHandler;

        FinalDefinitionAdvice(
            MethodVisitor delegate, int access, String name, String descriptor
        ) {
            super(Opcodes.ASM9, delegate, access, name, descriptor);
        }

        @Override
        protected void onMethodEnter() {
            loadThis();
            loadArg(0);
            call("findStarted", "(Ljava/lang/Object;Ljava/lang/String;)V");
            mark(observedBodyStart);
        }

        @Override
        protected void onMethodExit(int opcode) {
            if (emittingCatchHandler) {
                return;
            }
            if (opcode == ARETURN) {
                dup();
                loadThis();
                swap();
                call("findReturned", "(Ljava/lang/Object;Ljava/lang/Class;)V");
            }
        }

        @Override
        public void visitMaxs(int maxStack, int maxLocals) {
            mark(observedBodyEnd);
            catchException(
                observedBodyStart, observedBodyEnd,
                Type.getType(Throwable.class)
            );
            emittingCatchHandler = true;
            dup();
            loadThis();
            call("findThrew", "(Ljava/lang/Throwable;Ljava/lang/Object;)V");
            throwException();
            emittingCatchHandler = false;
            super.visitMaxs(maxStack, maxLocals);
        }

        private void call(String name, String descriptor) {
            visitMethodInsn(
                INVOKESTATIC,
                "dev/workbench/crucible/cleanmixtrace/FoundationFinalDefinitionRuntime",
                name,
                descriptor,
                false
            );
        }
    }

    private static final class TransformerChainClassVisitor extends ClassVisitor {
        private final String target;
        private int hookCount;

        TransformerChainClassVisitor(ClassVisitor delegate, String target) {
            super(Opcodes.ASM9, delegate);
            this.target = target;
        }

        @Override
        public MethodVisitor visitMethod(
            int access, String name, String descriptor, String signature,
            String[] exceptions
        ) {
            MethodVisitor delegate = super.visitMethod(
                access, name, descriptor, signature, exceptions
            );
            if (!selectedMethod(name, descriptor)) {
                return delegate;
            }
            hookCount++;
            return new TransformerChainAdvice(
                delegate, access, name, descriptor, target
            );
        }

        private boolean selectedMethod(String name, String descriptor) {
            if (target.endsWith("/FoundationTransformerProvider")) {
                return (name.equals("<init>") && descriptor.equals("()V"))
                    || (name.equals("refreshDelegatedTransformers")
                        && descriptor.equals("()V"))
                    || (name.equals("addTransformerExclusion")
                        && descriptor.equals("(Ljava/lang/String;)V"))
                    || (name.equals("getDelegatedLegacyTransformers")
                        && descriptor.equals("()Ljava/util/List;"));
            }
            return target.endsWith("/CleanMixService")
                && name.equals("onRefresh") && descriptor.equals("()V");
        }

        void verify() {
            int expected = target.endsWith("/FoundationTransformerProvider") ? 4 : 1;
            if (hookCount != expected) {
                throw new IllegalStateException(
                    "expected " + expected + " transformer-chain hooks in " + target
                        + " but found " + hookCount
                );
            }
        }
    }

    private static final class TransformerChainAdvice extends AdviceAdapter {
        private final String owner;
        private final String methodName;

        TransformerChainAdvice(
            MethodVisitor delegate, int access, String name, String descriptor,
            String owner
        ) {
            super(Opcodes.ASM9, delegate, access, name, descriptor);
            this.owner = owner;
            this.methodName = name;
        }

        @Override
        protected void onMethodEnter() {
            if (owner.endsWith("/CleanMixService")) {
                loadThis();
                call("serviceRefreshStarted", "(Ljava/lang/Object;)V");
            } else if (methodName.equals("refreshDelegatedTransformers")) {
                loadThis();
                call("providerRefreshStarted", "(Ljava/lang/Object;)V");
            } else if (methodName.equals("addTransformerExclusion")) {
                loadThis();
                loadArg(0);
                call(
                    "exclusionChangeStarted",
                    "(Ljava/lang/Object;Ljava/lang/String;)V"
                );
            } else if (methodName.equals("getDelegatedLegacyTransformers")) {
                loadThis();
                call("delegationAccessStarted", "(Ljava/lang/Object;)V");
            }
        }

        @Override
        protected void onMethodExit(int opcode) {
            if (owner.endsWith("/CleanMixService")) {
                if (opcode == RETURN) {
                    loadThis();
                    call("serviceRefreshCompleted", "(Ljava/lang/Object;)V");
                } else if (opcode == ATHROW) {
                    dup();
                    loadThis();
                    call(
                        "serviceRefreshThrew",
                        "(Ljava/lang/Throwable;Ljava/lang/Object;)V"
                    );
                }
            } else if (methodName.equals("<init>") && opcode == RETURN) {
                loadThis();
                call("providerConstructed", "(Ljava/lang/Object;)V");
            } else if (methodName.equals("addTransformerExclusion")
                    && opcode == RETURN) {
                loadThis();
                loadArg(0);
                call(
                    "exclusionChangeCompleted",
                    "(Ljava/lang/Object;Ljava/lang/String;)V"
                );
            } else if (methodName.equals("getDelegatedLegacyTransformers")) {
                if (opcode == ARETURN) {
                    dup();
                    loadThis();
                    swap();
                    call(
                        "delegationAccessCompleted",
                        "(Ljava/lang/Object;Ljava/lang/Object;)V"
                    );
                } else if (opcode == ATHROW) {
                    dup();
                    loadThis();
                    call(
                        "delegationAccessThrew",
                        "(Ljava/lang/Throwable;Ljava/lang/Object;)V"
                    );
                }
            }
        }

        private void call(String name, String descriptor) {
            visitMethodInsn(
                INVOKESTATIC, CHAIN_RUNTIME, name, descriptor, false
            );
        }
    }

    private static final class LifecycleClassVisitor extends ClassVisitor {
        private final String target;
        private int hookCount;

        LifecycleClassVisitor(ClassVisitor delegate, String target) {
            super(Opcodes.ASM9, delegate);
            this.target = target;
        }

        @Override
        public MethodVisitor visitMethod(
            int access, String name, String descriptor, String signature,
            String[] exceptions
        ) {
            MethodVisitor delegate = super.visitMethod(
                access, name, descriptor, signature, exceptions
            );
            boolean selected = selectedMethod(name, descriptor);
            if (selected) {
                hookCount++;
                return new LifecycleAdvice(
                    delegate, access, name, descriptor, target
                );
            }
            return delegate;
        }

        private boolean selectedMethod(String name, String descriptor) {
            if (target.endsWith("/Mixins")) {
                return name.equals("registerConfiguration")
                    && descriptor.equals(
                        "(Lorg/spongepowered/asm/mixin/transformer/Config;)V"
                    );
            }
            if (target.endsWith("/Config")) {
                return name.equals("create") && descriptor.equals(
                    "(Ljava/lang/String;Lorg/spongepowered/asm/mixin/MixinEnvironment;"
                        + "Lorg/spongepowered/asm/mixin/extensibility/IMixinConfigSource;)"
                        + "Lorg/spongepowered/asm/mixin/transformer/Config;"
                );
            }
            if (target.endsWith("/MixinConfig")) {
                return (name.equals("findSource")
                        && descriptor.equals(
                            "(Lorg/spongepowered/asm/mixin/extensibility/IMixinConfigSource;"
                                + "Ljava/lang/String;)"
                                + "Lorg/spongepowered/asm/mixin/extensibility/IMixinConfigSource;"
                        ))
                    || (name.equals("checkFeatures") && descriptor.equals("()Z"))
                    || (name.equals("onSelect") && descriptor.equals("()V"))
                    || ((name.equals("prepare") || name.equals("postInitialise"))
                        && descriptor.equals(
                            "(Lorg/spongepowered/asm/mixin/transformer/ext/Extensions;)V"
                        ));
            }
            if (target.endsWith("/MixinProcessor")) {
                return (name.equals("prepareNewConfigs")
                        && descriptor.equals(
                            "(Lorg/spongepowered/asm/mixin/MixinEnvironment;)V"
                        ))
                    || (name.equals("consumeConfigs")
                        && descriptor.equals("()Ljava/util/List;"))
                    || (name.equals("prepareBatch")
                        && descriptor.equals(
                            "(Ljava/util/List;Lorg/spongepowered/asm/mixin/MixinEnvironment;)I"
                        ));
            }
            return false;
        }

        void verify() {
            int expected = target.endsWith("/Mixins") || target.endsWith("/Config")
                ? 1 : target.endsWith("/MixinConfig") ? 5 : 3;
            if (hookCount != expected) {
                throw new IllegalStateException(
                    "expected " + expected + " lifecycle hooks in " + target
                        + " but found " + hookCount
                );
            }
        }
    }

    private static final class LifecycleAdvice extends AdviceAdapter {
        private final String owner;
        private final String methodName;

        LifecycleAdvice(
            MethodVisitor delegate, int access, String name, String descriptor,
            String owner
        ) {
            super(Opcodes.ASM9, delegate, access, name, descriptor);
            this.owner = owner;
            this.methodName = name;
        }

        @Override
        protected void onMethodEnter() {
            if (owner.endsWith("/Config") && methodName.equals("create")) {
                loadArg(0);
                loadArg(1);
                loadArg(2);
                call("configCreateStarted", "(Ljava/lang/String;Ljava/lang/Object;Ljava/lang/Object;)V");
            } else if (owner.endsWith("/Mixins")) {
                loadArg(0);
                call("registrationStarted", "(Ljava/lang/Object;)V");
            } else if (owner.endsWith("/MixinConfig") && methodName.equals("checkFeatures")) {
                loadThis();
                call("featureCheckStarted", "(Ljava/lang/Object;)V");
            } else if (owner.endsWith("/MixinConfig") && isStage()) {
                loadThis();
                push(stageName());
                call("stageStarted", "(Ljava/lang/Object;Ljava/lang/String;)V");
            } else if (owner.endsWith("/MixinProcessor")
                    && methodName.equals("prepareNewConfigs")) {
                loadArg(0);
                call("phasePassStarted", "(Ljava/lang/Object;)V");
            }
        }

        @Override
        protected void onMethodExit(int opcode) {
            if (owner.endsWith("/Config") && methodName.equals("create")) {
                if (opcode == ARETURN) {
                    dup();
                    loadArg(0);
                    call("configCreateReturned", "(Ljava/lang/Object;Ljava/lang/String;)V");
                } else if (opcode == ATHROW) {
                    dup();
                    loadArg(0);
                    call("configCreateThrew", "(Ljava/lang/Throwable;Ljava/lang/String;)V");
                }
            } else if (owner.endsWith("/Mixins")) {
                if (opcode == RETURN) {
                    loadArg(0);
                    call("registrationReturned", "(Ljava/lang/Object;)V");
                } else if (opcode == ATHROW) {
                    dup();
                    loadArg(0);
                    call("registrationThrew", "(Ljava/lang/Throwable;Ljava/lang/Object;)V");
                }
            } else if (owner.endsWith("/MixinConfig") && methodName.equals("checkFeatures")) {
                if (opcode == IRETURN) {
                    dup();
                    loadThis();
                    call("featureCheckReturned", "(ZLjava/lang/Object;)V");
                } else if (opcode == ATHROW) {
                    dup();
                    loadThis();
                    call("featureCheckThrew", "(Ljava/lang/Throwable;Ljava/lang/Object;)V");
                }
            } else if (owner.endsWith("/MixinConfig") && isStage()) {
                if (opcode == RETURN) {
                    loadThis();
                    push(stageName());
                    call("stageReturned", "(Ljava/lang/Object;Ljava/lang/String;)V");
                } else if (opcode == ATHROW) {
                    dup();
                    loadThis();
                    push(stageName());
                    call("stageThrew", "(Ljava/lang/Throwable;Ljava/lang/Object;Ljava/lang/String;)V");
                }
            } else if (owner.endsWith("/MixinProcessor")
                    && methodName.equals("prepareNewConfigs")) {
                if (opcode == RETURN) {
                    loadArg(0);
                    call("phasePassReturned", "(Ljava/lang/Object;)V");
                } else if (opcode == ATHROW) {
                    dup();
                    loadArg(0);
                    call("phasePassThrew", "(Ljava/lang/Throwable;Ljava/lang/Object;)V");
                }
            } else if (owner.endsWith("/MixinProcessor")
                    && methodName.equals("prepareBatch")) {
                if (opcode == IRETURN) {
                    loadArg(0);
                    loadArg(1);
                    call("batchPromoted", "(Ljava/lang/Object;Ljava/lang/Object;)V");
                } else if (opcode == ATHROW) {
                    dup();
                    loadArg(0);
                    loadArg(1);
                    call("batchThrew", "(Ljava/lang/Throwable;Ljava/lang/Object;Ljava/lang/Object;)V");
                }
            }
        }

        @Override
        public void visitMethodInsn(
            int opcode, String invocationOwner, String name, String descriptor,
            boolean isInterface
        ) {
            super.visitMethodInsn(opcode, invocationOwner, name, descriptor, isInterface);
            if (owner.endsWith("/MixinConfig") && methodName.equals("findSource")
                    && invocationOwner.equals(
                        "org/spongepowered/asm/service/clean/ICleanMixinService"
                    )
                    && name.equals("getResource")
                    && descriptor.equals("(Ljava/lang/String;)Ljava/net/URL;")) {
                dup();
                loadArg(1);
                call("resourceLocated", "(Ljava/lang/Object;Ljava/lang/String;)V");
            } else if (owner.endsWith("/MixinProcessor")
                    && methodName.equals("consumeConfigs")
                    && invocationOwner.equals(
                        "org/spongepowered/asm/mixin/MixinEnvironment$Phase"
                    )
                    && name.equals("hasReached") && descriptor.equals("()Z")) {
                dup();
                visitVarInsn(ALOAD, 4);
                call("phaseEligibility", "(ZLjava/lang/Object;)V");
            }
        }

        private boolean isStage() {
            return methodName.equals("onSelect") || methodName.equals("prepare")
                || methodName.equals("postInitialise");
        }

        private String stageName() {
            return methodName.equals("postInitialise") ? "post_initialise" : methodName;
        }

        private void call(String name, String descriptor) {
            visitMethodInsn(
                INVOKESTATIC, LIFECYCLE_RUNTIME, name, descriptor, false
            );
        }
    }

    static String sha256(byte[] value) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] encoded = digest.digest(value);
            StringBuilder result = new StringBuilder(encoded.length * 2);
            for (byte item : encoded) {
                result.append(Character.forDigit((item >>> 4) & 0x0f, 16));
                result.append(Character.forDigit(item & 0x0f, 16));
            }
            return result.toString();
        } catch (Throwable failure) {
            throw new IllegalStateException("SHA-256 is unavailable", failure);
        }
    }
}
