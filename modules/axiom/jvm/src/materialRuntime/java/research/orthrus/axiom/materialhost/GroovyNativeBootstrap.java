package research.orthrus.axiom.materialhost;

import net.minecraft.launchwrapper.Launch;
import net.minecraftforge.fml.relauncher.Side;
import com.cleanroommc.common.CleanroomEnvironment;
import top.outlands.foundation.TransformerDelegate;
import top.outlands.foundation.boot.ActualClassLoader;
import top.outlands.foundation.boot.TransformerHolder;
import top.outlands.foundation.transformer.ASMVisitorTransformer;
import top.outlands.foundation.transformer.ASMClassWriterTransformer;
import org.spongepowered.asm.launch.MixinBootstrap;
import org.spongepowered.asm.mixin.MixinEnvironment;
import org.spongepowered.asm.mixin.Mixins;
import java.lang.reflect.*;
import java.util.*;
import research.orthrus.axiom.materialhost.NativeMaterialBlockAccess;

/** Reviewed native loader prefix only. Never invokes LaunchHandler.launch or a game target. */
public final class GroovyNativeBootstrap {
    public static Object start(String context) throws Exception {
        boolean pack=NativePackMaterialContext.selected(context);
        Method fill = TransformerDelegate.class.getDeclaredMethod("fillTransformerHolder",TransformerHolder.class);
        fill.setAccessible(true); fill.invoke(null,ActualClassLoader.getTransformerHolder());
        if (!TransformerDelegate.getTransformers().isEmpty()) throw new IllegalStateException("Warm transformer list");
        // These are the actual Foundation 0.19.11 LaunchHandler prefix operations.
        TransformerDelegate.registerExplicitTransformer(new ASMVisitorTransformer(),
                "org.objectweb.asm.FieldVisitor","org.objectweb.asm.ClassVisitor","org.objectweb.asm.MethodVisitor");
        TransformerDelegate.registerExplicitTransformer(new ASMClassWriterTransformer(),"org.objectweb.asm.ClassWriter");
        if (pack) TransformerDelegate.registerExplicitTransformer(NativeCoremodPrefix::apply, NativeCoremodPrefix.TARGET);
        for (String name : List.of("net.minecraft.launchwrapper.IClassTransformer","net.minecraft.launchwrapper.ITweaker",
                "net.minecraft.launchwrapper.IClassNameTransformer","org.objectweb.asm.FieldVisitor",
                "org.objectweb.asm.ClassVisitor","org.objectweb.asm.MethodVisitor","org.objectweb.asm.ClassWriter"))
            Launch.classLoader.findClass(name);
        if (pack) {
            // Complete original FMLLaunchHandler constructor exclusion list, in
            // native order, before PatchingTransformer registration. This is
            // transformation filtering, not classloader ownership. In particular,
            // the patch manager must not transform its own shaded dependencies
            // while ClassPatchManager.INSTANCE is still being initialized.
            for (String name : List.of("org.spongepowered.asm.launch.","org.spongepowered.asm.service.",
                    "org.spongepowered.asm.mixin.","org.spongepowered.asm.logging.","org.spongepowered.asm.util.",
                    "org.spongepowered.asm.lib.","org.objectweb.asm.","com.cleanroommc.loader.",
                    "net.minecraftforge.fml.relauncher.","net.minecraftforge.classloading.",
                    "net.minecraftforge.fml.common.asm.transformers.","net.minecraftforge.fml.common.patcher.",
                    "net.minecraftforge.fml.repackage.","LZMA.","scala.","it.unimi.dsi.","oshi."))
                Launch.classLoader.addTransformerExclusion(name);
        } else {
            for (String name : List.of("org.objectweb.asm.","net.minecraftforge.fml.relauncher.",
                    "net.minecraftforge.fml.common.asm.transformers.","net.minecraftforge.fml.common.patcher."))
                Launch.classLoader.addTransformerExclusion(name);
        }
        // Existing original GroovyScript core exclusion remains an independent
        // part of the bounded language bootstrap, not an FML constructor rule.
        Launch.classLoader.addTransformerExclusion("com.cleanroommc.groovyscript.core.");
        CleanroomEnvironment.setSide(Side.SERVER);
        Map<String,Object> platform = Map.of("status", "not-selected");
        if (pack) {
            try { platform = NativePlatformBootstrap.initialize(); }
            catch (Exception | LinkageError failure) {
                // A failed native platform prerequisite is an incomplete check,
                // not invalid developer Groovy or an opaque reflection error.
                return Map.of("requiredTargetDefinitions", false, "admitted", false, "compilerInvoked", false,
                        "platformInitialization", Map.of("status", "threw", "causes", causes(failure),
                                "launcherCompositionQualified", false));
            }
        }
        Launch.classLoader.registerTransformer("net.minecraftforge.fml.common.asm.transformers.SideTransformer");
        Launch.classLoader.registerTransformer("net.minecraftforge.fml.common.asm.transformers.EventSubscriptionTransformer");
        NativeRecipeContext.installOptionalTransformer();
        TransformerDelegate.registerTransformer(NativeMaterialBlockAccess.create());
        TransformerDelegate.registerTransformer(new NativeTraitDefinitionTransformer());
        Launch.classLoader.registerTransformer("com.cleanroommc.groovyscript.core.GroovyScriptTransformer");
        ContextMixinService.bindSources(Launch.classLoader.getSources());
        MixinBootstrap.init();
        com.llamalad7.mixinextras.MixinExtrasBootstrap.init();
        MixinEnvironment.getDefaultEnvironment().setSide(MixinEnvironment.Side.SERVER);
        Mixins.addConfiguration("axiom-native-groovy-language.json");
        if(pack) {
            Mixins.addConfiguration("axiom-native-pack-materials.json");
            Mixins.addConfiguration("axiom-native-pack-recipes.json");
            Mixins.addConfiguration("axiom-native-pack-gcym.json");
        }
        Class<?> tweaker=Class.forName("org.spongepowered.asm.mixin.EnvironmentStateTweaker",true,Launch.classLoader);
        tweaker.getMethod("getLaunchArguments").invoke(tweaker.getConstructor().newInstance());
        TransformerDelegate.registerTransformer(new NativeTransformAudit());
        var transformers=TransformerDelegate.getTransformers().stream().map(t->t.getClass().getName()).toList();
        for (String required : List.of("org.spongepowered.asm.mixin.transformer.Proxy",
                "net.minecraftforge.fml.common.asm.transformers.SideTransformer",
                "net.minecraftforge.fml.common.asm.transformers.EventSubscriptionTransformer",
                "net.minecraftforge.fml.common.asm.transformers.AccessTransformer",
                "com.cleanroommc.groovyscript.core.GroovyScriptTransformer"))
            if (!transformers.contains(required))
                throw new AssertionError("Required native transformer absent: "+required);
        var linkage=NativeCompilerLinkage.inspect();
        Map<String,Object> packLinkage = Map.of("selected", false);
        if (pack) {
            try { packLinkage = NativePackMaterialContext.inspectElement(); }
            catch (Exception | LinkageError failure) {
                packLinkage = Map.of("selected", true, "status", "incomplete", "causes", causes(failure),
                        "wholeInstalledMixinSet", false, "materialCallbacksExecuted", false);
            }
        }
        boolean admitted="observed".equals(linkage.get("status")) && (!pack ||
                Boolean.TRUE.equals(packLinkage.get("elementMixinObserved")) && Boolean.TRUE.equals(packLinkage.get("orePrefixMixinObserved")));
        // Behavioral witnesses are separate from successful target definition.
        return Map.of("requiredTargetDefinitions",admitted,"admitted",admitted,"compilerInvoked",false,"transformers",transformers,
                "compilerLinkage",linkage,"packMaterialLinkage",packLinkage,"platformInitialization",platform,
                "phase",MixinEnvironment.getCurrentEnvironment().getPhase().toString());
    }

    private static List<Map<String,Object>> causes(Throwable failure) {
        var causes = new ArrayList<Map<String,Object>>();
        var seen = Collections.newSetFromMap(new IdentityHashMap<Throwable,Boolean>());
        for (Throwable current = failure; current != null && causes.size() < 12 && seen.add(current); current = current.getCause()) {
            String message = Objects.toString(current.getMessage(), "");
            causes.add(Map.of("class", current.getClass().getName(), "message", message.substring(0, Math.min(2048,message.length()))));
        }
        return causes;
    }
}
