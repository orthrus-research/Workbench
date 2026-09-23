package research.orthrus.axiom.materialtest;

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
    public static Object start(String mode) throws Exception {
        Method fill = TransformerDelegate.class.getDeclaredMethod("fillTransformerHolder",TransformerHolder.class);
        fill.setAccessible(true); fill.invoke(null,ActualClassLoader.getTransformerHolder());
        if (!TransformerDelegate.getTransformers().isEmpty()) throw new IllegalStateException("Warm transformer list");
        // These are the actual Foundation 0.19.11 LaunchHandler prefix operations.
        TransformerDelegate.registerExplicitTransformer(new ASMVisitorTransformer(),
                "org.objectweb.asm.FieldVisitor","org.objectweb.asm.ClassVisitor","org.objectweb.asm.MethodVisitor");
        TransformerDelegate.registerExplicitTransformer(new ASMClassWriterTransformer(),"org.objectweb.asm.ClassWriter");
        for (String name : List.of("net.minecraft.launchwrapper.IClassTransformer","net.minecraft.launchwrapper.ITweaker",
                "net.minecraft.launchwrapper.IClassNameTransformer","org.objectweb.asm.FieldVisitor",
                "org.objectweb.asm.ClassVisitor","org.objectweb.asm.MethodVisitor","org.objectweb.asm.ClassWriter"))
            Launch.classLoader.findClass(name);
        for (String name : List.of("org.objectweb.asm.","net.minecraftforge.fml.relauncher.",
                "net.minecraftforge.fml.common.asm.transformers.","net.minecraftforge.fml.common.patcher.",
                "com.cleanroommc.groovyscript.core.")) Launch.classLoader.addTransformerExclusion(name);
        CleanroomEnvironment.setSide(Side.SERVER);
        Launch.classLoader.registerTransformer("net.minecraftforge.fml.common.asm.transformers.SideTransformer");
        Launch.classLoader.registerTransformer("net.minecraftforge.fml.common.asm.transformers.EventSubscriptionTransformer");
        research.orthrus.axiom.materialhost.NativeRecipeContext.installOptionalTransformer();
        TransformerDelegate.registerTransformer(NativeMaterialBlockAccess.create());
        if(!mode.equals("missing-core")) Launch.classLoader.registerTransformer("com.cleanroommc.groovyscript.core.GroovyScriptTransformer");
        ContextMixinService.bindSources(Launch.classLoader.getSources());
        MixinBootstrap.init();
        com.llamalad7.mixinextras.MixinExtrasBootstrap.init();
        MixinEnvironment.getDefaultEnvironment().setSide(MixinEnvironment.Side.SERVER);
        if (!mode.equals("missing")) Mixins.addConfiguration("axiom-native-groovy-language.json");
        Class<?> tweaker=Class.forName("org.spongepowered.asm.mixin.EnvironmentStateTweaker",true,Launch.classLoader);
        tweaker.getMethod("getLaunchArguments").invoke(tweaker.getConstructor().newInstance());
        if(!mode.equals("no-audit")) TransformerDelegate.registerTransformer(new NativeTransformAudit());
        var transformers=TransformerDelegate.getTransformers().stream().map(t->t.getClass().getName()).toList();
        for (String required : List.of("org.spongepowered.asm.mixin.transformer.Proxy",
                "net.minecraftforge.fml.common.asm.transformers.SideTransformer",
                "net.minecraftforge.fml.common.asm.transformers.EventSubscriptionTransformer",
                "net.minecraftforge.fml.common.asm.transformers.AccessTransformer",
                "com.cleanroommc.groovyscript.core.GroovyScriptTransformer"))
            if (!transformers.contains(required)&&!(mode.equals("missing-core")&&required.equals("com.cleanroommc.groovyscript.core.GroovyScriptTransformer")))
                throw new AssertionError("Required native transformer absent: "+required);
        Class<?> module=Class.forName("org.codehaus.groovy.ast.ModuleNode",false,Launch.classLoader);
        Class<?> accessor=Class.forName("com.cleanroommc.groovyscript.core.mixin.groovy.ModuleNodeAccessor",false,Launch.classLoader);
        if (mode.equals("missing")) {
            if (accessor.isAssignableFrom(module)) throw new AssertionError("Unconfigured accessor applied");
            return Map.of("requiredTransformAbsent",true,"admitted",false,"compilerInvoked",false,"transformers",transformers);
        }
        if (!accessor.isAssignableFrom(module)) throw new AssertionError("Required original accessor absent");
        for (String target : List.of("org.codehaus.groovy.ast.decompiled.AsmDecompiler",
                "org.codehaus.groovy.control.ClassNodeResolver","groovy.lang.Closure",
                "org.codehaus.groovy.control.CompilationUnit$2","org.codehaus.groovy.vmplugin.v8.Java8",
                "groovy.lang.MetaClassImpl","org.codehaus.groovy.control.ResolveVisitor",
                "net.minecraftforge.fml.common.eventhandler.EventBus"))
            if (Class.forName(target,false,Launch.classLoader).getClassLoader()!=Launch.classLoader)
                throw new AssertionError("Target escaped native class space: "+target);
        // Behavioral witnesses are separate from successful target definition.
        return Map.of("requiredTargetDefinitions",true,"compilerInvoked",false,"transformers",transformers,
                "phase",MixinEnvironment.getCurrentEnvironment().getPhase().toString());
    }
}
