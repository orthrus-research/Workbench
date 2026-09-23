package dev.workbench.recipe;

import java.lang.classfile.*;
import java.lang.classfile.instruction.ReturnInstruction;
import java.lang.classfile.attribute.RuntimeVisibleAnnotationsAttribute;
import java.lang.classfile.constantpool.StringEntry;
import java.lang.constant.*;
import java.lang.instrument.*;
import java.nio.file.*;
import java.security.*;
import java.util.*;

/** Current-stack, define-time observer. No retransformation or game dependencies. */
public final class RecipeAgent implements ClassFileTransformer {
    private static final ClassDesc TRACE = ClassDesc.of("dev.workbench.recipe.RecipeTrace");
    private static final ClassDesc OBJECT = ConstantDescs.CD_Object;
    private final Properties configuration;

    private RecipeAgent(Properties configuration) { this.configuration = configuration; }

    public static void premain(String argument, Instrumentation instrumentation) {
        try {
            Properties configuration = new Properties();
            try (var reader = Files.newBufferedReader(Path.of(argument))) { configuration.load(reader); }
            RecipeTrace.configure(configuration);
            // Resolve the standard class-file API before observing application definitions.
            ClassFile.of();
            instrumentation.addTransformer(new RecipeAgent(configuration), false);
        } catch (Throwable failure) {
            // A failed observer cannot become a successful trace. Do not abort the game.
            System.err.println("[WORKBENCH-RECIPE-OBSERVER-UNAVAILABLE] " + failure.getClass().getName());
        }
    }

    @Override public byte[] transform(ClassLoader loader, String name, Class<?> redefined,
                                      ProtectionDomain domain, byte[] bytes) {
        String expected = configuration.getProperty("target." + name);
        if (expected == null || redefined != null) return null;
        try {
            String observed = RecipeTrace.digest(bytes);
            if (loader == null || !loader.getClass().getName().equals("net.minecraft.launchwrapper.LaunchClassLoader")) {
                RecipeTrace.problem("unexpected-target-loader:" + name); return null;
            }
            loader.getClass().getMethod("addClassLoaderExclusion", String.class).invoke(loader, "dev.workbench.recipe.");
            String definition = definitionDigest(bytes);
            if (!expected.equals(definition)) {
                RecipeTrace.problem("target-byte-mismatch:" + name + ":" + observed); return null;
            }
            ClassFile file = ClassFile.of(ClassFile.ClassHierarchyResolverOption.of(
                ClassHierarchyResolver.ofResourceParsing(loader)));
            var model = file.parse(bytes);
            boolean decisionSites = false;
            if (name.endsWith("/RecipeMap")) {
                try { RecipeDecisionHooks.audit(model); decisionSites = true; }
                catch (Throwable unavailable) { RecipeDecisions.problem("decision-site-audit-failed:" + unavailable.getMessage()); }
            }
            final boolean decisions = decisionSites;
            Set<String> installed = new TreeSet<>();
            ClassTransform transform = (builder, element) -> {
                if (!(element instanceof MethodModel method)) { builder.with(element); return; }
                String methodName = method.methodName().stringValue();
                String descriptor = method.methodType().stringValue();
                if (name.endsWith("/AstBuilder") && methodName.equals("createCharStream")
                        && descriptor.equals("(Lorg/codehaus/groovy/control/SourceUnit;)Lgroovyjarjarantlr4/v4/runtime/CharStream;")) {
                    installed.add(methodName);
                    builder.transformMethod(method, MethodTransform.transformingCode((code, instruction) -> {
                        if (instruction instanceof ReturnInstruction) code.dup().aload(1).invokestatic(TRACE, "parsed",
                            MethodTypeDesc.of(ConstantDescs.CD_void, OBJECT, OBJECT));
                        code.with(instruction);
                    }));
                    return;
                }
                if (name.endsWith("/CustomGroovyScriptEngine") && methodName.equals("onCompileClass")
                        && descriptor.equals("(Lorg/codehaus/groovy/control/SourceUnit;Ljava/lang/String;Ljava/lang/Class;[BZ)V")) {
                    installed.add(methodName);
                    builder.transformMethod(method, MethodTransform.transformingCode(new CodeTransform() {
                        @Override public void atStart(CodeBuilder code) {
                            code.aload(1).aload(3).aload(4).invokestatic(TRACE, "compiled",
                                MethodTypeDesc.of(ConstantDescs.CD_void, OBJECT, ConstantDescs.CD_Class, ClassDesc.ofDescriptor("[B")));
                        }
                        @Override public void accept(CodeBuilder code, CodeElement instruction) { code.with(instruction); }
                    }));
                    return;
                }
                String operation = operation(name, methodName, descriptor);
                CodeTransform decision = decisions ? RecipeDecisionHooks.transform(methodName + descriptor) : null;
                if (operation == null && decision == null) { builder.with(element); return; }
                CodeTransform hooks = decision;
                if (operation != null) {
                    installed.add(methodName);
                    hooks = hooks == null ? new Hook(operation, descriptor) : hooks.andThen(new Hook(operation, descriptor));
                }
                builder.transformMethod(method, MethodTransform.transformingCode(hooks));
            };
            Set<String> required = name.endsWith("/RecipeMap")
                ? Set.of("addRecipe", "postValidateRecipe", "compileRecipe", "removeRecipe", "removeAllRecipes")
                : name.endsWith("/RecipeBuilder") ? Set.of("buildAndRegister", "build", "validate")
                : name.endsWith("/AstBuilder") ? Set.of("createCharStream") : Set.of("onCompileClass");
            if (name.endsWith("/RecipeMap")) {
                if (model.methods().stream().anyMatch(method -> method.methodName().stringValue().startsWith("workbenchTrace")))
                    throw new IllegalArgumentException("reserved observer method already exists");
                transform = transform.andThen(ClassTransform.endHandler(builder -> {
                    ClassDesc map = ClassDesc.of("java.util.Map"), set = ClassDesc.of("java.util.Set"), list = ClassDesc.of("java.util.List");
                    builder.withMethod("workbenchTraceRecipes", MethodTypeDesc.of(list), ClassFile.ACC_PUBLIC | ClassFile.ACC_STATIC,
                        method -> method.withCode(code -> code.invokestatic(TRACE, "recipeObjects", MethodTypeDesc.of(list)).areturn()));
                    builder.withMethod("workbenchTraceSnapshot", MethodTypeDesc.of(map, map, set, ConstantDescs.CD_String), ClassFile.ACC_PUBLIC | ClassFile.ACC_STATIC,
                        method -> method.withCode(code -> code.aload(0).aload(1).aload(2).invokestatic(TRACE, "snapshot", MethodTypeDesc.of(map, map, set, ConstantDescs.CD_String)).areturn()));
                    ClassDesc decisionTrace = ClassDesc.of("dev.workbench.recipe.RecipeDecisions");
                    builder.withMethod("workbenchTraceQuery", MethodTypeDesc.of(ConstantDescs.CD_void, ConstantDescs.CD_String), ClassFile.ACC_PUBLIC | ClassFile.ACC_STATIC,
                        method -> method.withCode(code -> code.aload(0).invokestatic(decisionTrace, "query", MethodTypeDesc.of(ConstantDescs.CD_void, ConstantDescs.CD_String)).return_()));
                    builder.withMethod("workbenchTraceDecisions", MethodTypeDesc.of(map, map, map), ClassFile.ACC_PUBLIC | ClassFile.ACC_STATIC,
                        method -> method.withCode(code -> code.aload(0).aload(1).invokestatic(decisionTrace, "snapshot", MethodTypeDesc.of(map, map, map)).areturn()));
                }));
            }
            byte[] changed = file.transformClass(model, transform);
            if (!installed.equals(required)) { RecipeTrace.problem("missing-method:" + name); return null; }
            RecipeTrace.installed(name, observed, definition, RecipeTrace.digest(changed), installed);
            if (decisions) RecipeDecisions.installed(RecipeDecisionHooks.required());
            return changed;
        } catch (Throwable failure) {
            RecipeTrace.problem("transform-failed:" + name + ":" + failure.getClass().getName());
            return null;
        }
    }

    /** Hash every byte except the observed MixinMerged session UUID on two accessors.
     * This only creates a hashing copy. The runtime annotations are never rewritten.
     */
    public static String definitionDigest(byte[] bytes) throws Exception {
        var model = ClassFile.of().parse(bytes);
        if (!model.thisClass().asInternalName().equals("gregtech/api/recipes/RecipeBuilder")) return RecipeTrace.digest(bytes);
        Set<Integer> slots = new HashSet<>(); Set<String> accessors = new HashSet<>();
        for (var method : model.methods()) for (var element : method) {
            if (!(element instanceof RuntimeVisibleAnnotationsAttribute attribute)) continue;
            for (var annotation : attribute.annotations()) {
                if (!annotation.className().equalsString("Lorg/spongepowered/asm/mixin/transformer/meta/MixinMerged;")) continue;
                String methodName = method.methodName().stringValue();
                if (!Set.of("getOnBuildAction", "getRecipeMap").contains(methodName)) throw new IllegalArgumentException("unexpected merged method");
                for (var item : annotation.elements()) if (item.name().equalsString("sessionId")) {
                    if (!(item.value() instanceof AnnotationValue.OfString value) || !value.stringValue().matches("[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")) throw new IllegalArgumentException("unexpected session metadata");
                    slots.add(value.constant().index()); accessors.add(methodName);
                }
            }
        }
        if (slots.isEmpty()) return RecipeTrace.digest(bytes);
        if (slots.size() != 1 || !accessors.equals(Set.of("getOnBuildAction", "getRecipeMap"))) throw new IllegalArgumentException("unexpected session references");
        for (var entry : model.constantPool()) if (entry instanceof StringEntry value && slots.contains(value.utf8().index()))
            throw new IllegalArgumentException("session string is executable data");
        byte[] normalized = bytes.clone();
        var data = java.nio.ByteBuffer.wrap(normalized);
        data.position(8); int count = Short.toUnsignedInt(data.getShort());
        for (int index = 1; index < count; index++) {
            int tag = Byte.toUnsignedInt(data.get());
            switch (tag) {
                case 1 -> {
                    int length = Short.toUnsignedInt(data.getShort()), start = data.position();
                    if (slots.contains(index)) {
                        if (length != 36) throw new IllegalArgumentException("session length changed");
                        Arrays.fill(normalized, start, start + length, (byte) '0');
                    }
                    data.position(start + length);
                }
                case 3, 4, 9, 10, 11, 12, 17, 18 -> data.position(data.position() + 4);
                case 5, 6 -> { data.position(data.position() + 8); index++; }
                case 7, 8, 16, 19, 20 -> data.position(data.position() + 2);
                case 15 -> data.position(data.position() + 3);
                default -> throw new IllegalArgumentException("unexpected constant-pool entry");
            }
        }
        return RecipeTrace.digest(normalized);
    }

    private static String operation(String owner, String name, String descriptor) {
        if (owner.endsWith("/RecipeBuilder")) return switch (name + descriptor) {
            case "buildAndRegister()V" -> "registration";
            case "build()Lgregtech/api/util/ValidationResult;" -> "build";
            case "validate()Lgregtech/api/util/EnumValidationResult;" -> "validation";
            default -> null;
        };
        if (!owner.endsWith("/RecipeMap")) return null;
        return switch (name + descriptor) {
            case "addRecipe(Lgregtech/api/util/ValidationResult;)Z" -> "add";
            case "postValidateRecipe(Lgregtech/api/util/ValidationResult;)Lgregtech/api/util/ValidationResult;" -> "post-validation";
            case "compileRecipe(Lgregtech/api/recipes/Recipe;)Z" -> "insertion";
            case "removeRecipe(Lgregtech/api/recipes/Recipe;)Z" -> "removal";
            case "removeAllRecipes()V" -> "clear";
            default -> null;
        };
    }

    private static final class Hook implements CodeTransform {
        final String operation, descriptor;
        int frame, returned;
        Label start;
        Hook(String operation, String descriptor) { this.operation = operation; this.descriptor = descriptor; }
        @Override public void atStart(CodeBuilder code) {
            frame = code.allocateLocal(TypeKind.REFERENCE);
            returned = code.allocateLocal(TypeKind.REFERENCE);
            code.ldc(operation).aload(0);
            if (descriptor.startsWith("()")) code.aconst_null(); else code.aload(1);
            code.invokestatic(TRACE, "enter", MethodTypeDesc.of(OBJECT, ConstantDescs.CD_String, OBJECT, OBJECT)).astore(frame);
            start = code.newBoundLabel();
        }
        @Override public void accept(CodeBuilder code, CodeElement element) {
            if (element instanceof ReturnInstruction result) {
                if (result.typeKind() == TypeKind.INT) {
                    code.dup().invokestatic(ClassDesc.of("java.lang.Boolean"), "valueOf",
                        MethodTypeDesc.of(ClassDesc.of("java.lang.Boolean"), ConstantDescs.CD_boolean)).astore(returned);
                } else if (result.typeKind() == TypeKind.REFERENCE) code.dup().astore(returned);
                else code.aconst_null().astore(returned);
                code.aload(frame).aload(returned).iconst_0().invokestatic(TRACE, "exit",
                    MethodTypeDesc.of(ConstantDescs.CD_void, OBJECT, OBJECT, ConstantDescs.CD_boolean));
            }
            code.with(element);
        }
        @Override public void atEnd(CodeBuilder code) {
            Label end = code.newBoundLabel(), handler = code.newLabel();
            code.exceptionCatchAll(start, end, handler).labelBinding(handler).astore(returned);
            code.aload(frame).aload(returned).iconst_1().invokestatic(TRACE, "exit",
                MethodTypeDesc.of(ConstantDescs.CD_void, OBJECT, OBJECT, ConstantDescs.CD_boolean));
            code.aload(returned).athrow();
        }
    }
}
