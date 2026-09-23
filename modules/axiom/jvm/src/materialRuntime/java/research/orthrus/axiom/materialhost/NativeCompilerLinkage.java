package research.orthrus.axiom.materialhost;

import net.minecraft.launchwrapper.Launch;
import java.util.*;

/** Required GroovyScript 1.4.3 compiler linkage, not behavioral or context qualification. */
public final class NativeCompilerLinkage {
    private static final String MIXIN="com.cleanroommc.groovyscript.core.mixin.";
    private static final String FACTORY="com/cleanroommc/groovyscript/sandbox/transformer/GroovyCodeFactory#";
    private static final Map<String,List<String>> MIXINS=Map.of(
            "org.codehaus.groovy.ast.ModuleNode",List.of("groovy.ModuleNodeAccessor","groovy.ModuleNodeMixin"),
            "org.codehaus.groovy.ast.decompiled.AsmDecompiler",List.of("groovy.AsmDecompilerMixin"),
            "org.codehaus.groovy.control.ClassNodeResolver",List.of("groovy.ClassNodeResolverMixin"),
            "groovy.lang.Closure",List.of("groovy.ClosureMixin"),
            "org.codehaus.groovy.control.CompilationUnit$2",List.of("groovy.CompUnitClassGenMixin"),
            "org.codehaus.groovy.vmplugin.v8.Java8",List.of("groovy.Java8Mixin"),
            "groovy.lang.MetaClassImpl",List.of("groovy.MetaClassImplMixin"),
            "org.codehaus.groovy.control.ResolveVisitor",List.of("groovy.ResolveVisitorMixin"),
            "net.minecraftforge.fml.common.eventhandler.EventBus",List.of("EventBusMixin"));
    private static final Map<String,String> CORE=Map.of(
            "org.codehaus.groovy.runtime.InvokerHelper","createMap:it/unimi/dsi/fastutil/objects/Object2ObjectLinkedOpenHashMap#<init>",
            "org.codehaus.groovy.reflection.CachedClass$1","initValue:"+FACTORY+"makeFieldsHook",
            "org.codehaus.groovy.reflection.CachedClass$2","initValue:"+FACTORY+"makeConstructorsHook",
            "org.codehaus.groovy.reflection.CachedClass$3","initValue:"+FACTORY+"makeMethodsHook",
            "org.codehaus.groovy.control.StaticVerifier","visitVariableExpression:com/cleanroommc/groovyscript/sandbox/GroovyScriptSandbox#getBindings");
    private static final Map<String,String> INTERFACES=Map.of(
            "org.codehaus.groovy.ast.ModuleNode",MIXIN+"groovy.ModuleNodeAccessor",
            "net.minecraftforge.fml.common.eventhandler.EventBus","com.cleanroommc.groovyscript.event.EventBusExtended");

    private NativeCompilerLinkage() {}

    public static boolean observesCoreCalls(String name) {return CORE.containsKey(name);}

    public static Map<String,Object> inspect() {
        var missing=new ArrayList<Map<String,Object>>();
        var targets=new TreeSet<>(MIXINS.keySet());targets.addAll(CORE.keySet());
        for(String target:targets) {
            try {
                Class<?> defined=Class.forName(target,false,Launch.classLoader);
                if(defined.getClassLoader()!=Launch.classLoader)
                    missing.add(Map.of("kind","class-definition","target",target,"reason","different-class-loader"));
                if(INTERFACES.containsKey(target)&&!Class.forName(INTERFACES.get(target),false,Launch.classLoader).isAssignableFrom(defined))
                    missing.add(Map.of("kind","interface","target",target,"required",INTERFACES.get(target),"reason","native-interface-not-implemented"));
            } catch(ClassNotFoundException|LinkageError|RuntimeException failure) {
                missing.add(Map.of("kind","class-definition","target",target,"reason",failure.getClass().getName()));
            }
        }
        var observed=NativeTransformAudit.observations();
        for(String target:targets) {
            var rows=observed.getOrDefault(target,List.of());
            // Earlier reentrant transformation observations are not the final
            // bytecode observation obtained after resolving the native class.
            Map<String,Object> last=rows.isEmpty()?Map.of():rows.getLast();
            for(String mixin:MIXINS.getOrDefault(target,List.of())) {
                String required=MIXIN+mixin;
                if(!((List<?>)last.getOrDefault("mergedMixins",List.of())).contains(required))
                    missing.add(Map.of("kind","mixin","target",target,"required",required,"reason","not-observed-on-defined-target"));
            }
            if(CORE.containsKey(target)) {
                String required=CORE.get(target);
                boolean present=((List<?>)last.getOrDefault("coreCalls",List.of())).stream()
                        .anyMatch(call->call.toString().startsWith(required+"("));
                if(!present||Objects.equals(last.get("inputSha256"),last.get("outputSha256")))
                    missing.add(Map.of("kind","core-hook","target",target,"required",required,"reason","native-compiler-hook-not-observed"));
            }
        }
        return Map.of("schema","axiom.native-compiler-linkage.v1","status",missing.isEmpty()?"observed":"incomplete",
                "missing",missing,"requiredMixins",MIXINS,"requiredCoreHooks",CORE,"requiredInterfaces",INTERFACES,
                "meaning","applied-linkage-evidence-not-behavioral-qualification");
    }
}
