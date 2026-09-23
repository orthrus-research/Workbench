package research.orthrus.axiom.materialtest;

import net.minecraft.launchwrapper.Launch;
import net.minecraftforge.fml.relauncher.Side;
import com.cleanroommc.common.CleanroomEnvironment;
import top.outlands.foundation.TransformerDelegate;
import top.outlands.foundation.boot.ActualClassLoader;
import top.outlands.foundation.boot.TransformerHolder;
import org.spongepowered.asm.launch.MixinBootstrap;
import org.spongepowered.asm.mixin.MixinEnvironment;
import org.spongepowered.asm.mixin.Mixins;
import java.lang.reflect.*;
import java.util.*;

/** Native transformer bootstrap witnesses, not a replacement compiler or event implementation. */
public final class GroovyTransformProbe {
    private static void check(boolean value,String message) { if (!value) throw new AssertionError(message); }
    public static Object run(String mode) throws Exception {
        // Invoke the native initialization routine without LaunchHandler.launch,
        // which continues into application/game startup. No custom dispatcher.
        Method fill = TransformerDelegate.class.getDeclaredMethod("fillTransformerHolder",TransformerHolder.class);
        fill.setAccessible(true); fill.invoke(null,ActualClassLoader.getTransformerHolder());
        check(TransformerDelegate.getTransformers().isEmpty(),"fresh native transformer list");
        for (String name : List.of("org.objectweb.asm.","net.minecraftforge.fml.relauncher.",
                "net.minecraftforge.fml.common.asm.transformers.","net.minecraftforge.fml.common.patcher."))
            Launch.classLoader.addTransformerExclusion(name);
        CleanroomEnvironment.setSide(Side.SERVER);
        ContextMixinService.bindSources(Launch.classLoader.getSources());
        MixinBootstrap.init();
        MixinEnvironment.getDefaultEnvironment().setSide(MixinEnvironment.Side.SERVER);
        if (!mode.equals("missing")) Mixins.addConfiguration("axiom-native-groovy-transform-witness.json");
        // Use the original environment state tweaker to enter DEFAULT, no game target.
        Class.forName("org.spongepowered.asm.mixin.EnvironmentStateTweaker",true,Launch.classLoader)
                .getMethod("getLaunchArguments").invoke(Class.forName("org.spongepowered.asm.mixin.EnvironmentStateTweaker",true,Launch.classLoader).getConstructor().newInstance());
        var transformers = TransformerDelegate.getTransformers().stream().map(t->t.getClass().getName()).toList();
        check(transformers.contains("org.spongepowered.asm.mixin.transformer.Proxy"),"native mixin proxy actually registered");
        var module = Class.forName("org.codehaus.groovy.ast.ModuleNode",true,Launch.classLoader);
        var accessor = Class.forName("com.cleanroommc.groovyscript.core.mixin.groovy.ModuleNodeAccessor",true,Launch.classLoader);
        if (mode.equals("missing")) {
            check(!accessor.isAssignableFrom(module),"missing required transformation must not produce accessor");
            return Map.of("requiredTransformAbsent",true,"admitted",false,"transformers",transformers);
        }
        check(accessor.isAssignableFrom(module),"original ModuleNode accessor transformed before definition");
        Object moduleValue = module.getConstructor(Class.forName("org.codehaus.groovy.control.SourceUnit",false,Launch.classLoader)).newInstance((Object)null);
        Object imports = accessor.getMethod("getModifiableImports").invoke(moduleValue);
        check(imports instanceof List<?>,"actual native mutable imports field");
        Class<?> event = Class.forName("net.minecraftforge.fml.common.eventhandler.EventBus",true,Launch.classLoader);
        Class<?> extended = Class.forName("com.cleanroommc.groovyscript.event.EventBusExtended",true,Launch.classLoader);
        check(extended.isAssignableFrom(event),"original EventBusMixin installs native extension");
        check(module.getClassLoader()==Launch.classLoader && event.getClassLoader()==Launch.classLoader,
                "one transformed native class space");
        return Map.of("moduleAccessor",true,"eventBusExtension",true,"singleNativeClassSpace",true,
                "transformers",transformers,"phase",MixinEnvironment.getCurrentEnvironment().getPhase().toString(),
                "compilerInvoked",false,"allGroovyMixinsQualified",false,
                "auditSources",ContextMixinService.observations());
    }
}
