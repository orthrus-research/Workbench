package research.orthrus.axiom.materialhost;

import java.io.*;
import java.lang.reflect.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import net.minecraft.launchwrapper.Launch;
import net.minecraft.util.ResourceLocation;
import net.minecraftforge.fml.common.discovery.ASMDataTable;
import net.minecraftforge.fml.common.discovery.asm.ASMModParser;
import net.minecraftforge.fml.common.asm.transformers.ModAPITransformer;
import top.outlands.foundation.TransformerDelegate;
import gregtech.api.GregTechAPI;
import gregtech.api.modules.IGregTechModule;
import gregtech.modules.ModuleManager;
import gregtech.modules.GregTechModules;
import gregtech.integration.groovy.GroovyScriptModule;
import com.cleanroommc.groovyscript.api.GroovyPlugin;
import com.cleanroommc.groovyscript.compat.mods.*;
import com.cleanroommc.groovyscript.mapper.ObjectMapperManager;
import com.cleanroommc.groovyscript.mapper.ObjectMapper;
import com.cleanroommc.groovyscript.GroovyScript;

/** Explicit native context state; not FML discovery or complete compat loading. */
public final class NativeRecipeContext {
    private NativeRecipeContext() {}

    public static void installOptionalTransformer() throws Exception {
        var table = new ASMDataTable();
        var resource = Launch.classLoader.getResourceAsStream("axiom-native-recipe-optionals.txt");
        if (resource == null) throw new IllegalStateException("Pinned recipe input inventory absent");
        try (var reader = new BufferedReader(new InputStreamReader(resource, StandardCharsets.UTF_8))) {
            for (String name : reader.lines().toList()) {
                try (var bytes = Launch.classLoader.getResourceAsStream(name)) {
                    if (bytes == null) throw new IllegalStateException("Native optional input absent: " + name);
                    // Original Cleanroom annotation parser and transformer: no
                    // hand-written approximation of method/interface stripping.
                    new ASMModParser(bytes).sendToTable(table, null);
                }
            }
        }
        var transformer = new ModAPITransformer();
        // No embedded optional APIs are provided by this standalone context.
        // Let the original manager establish its actual (empty) API registry.
        net.minecraftforge.fml.common.ModAPIManager.INSTANCE.registerDataTableAndParseAPI(table);
        transformer.initTable(table);
        TransformerDelegate.registerTransformer(transformer);
    }

    @SuppressWarnings("unchecked")
    public static void prepare() throws Exception {
        if (GregTechAPI.moduleManager != null) throw new IllegalStateException("Warm recipe module state");
        var manager = ModuleManager.getInstance();
        var modules = ModuleManager.class.getDeclaredField("sortedModules");
        modules.setAccessible(true);
        var selected = (Map<ResourceLocation, IGregTechModule>) modules.get(manager);
        if (!selected.isEmpty()) throw new IllegalStateException("Warm native module selection");
        var plugin = new GroovyScriptModule();
        selected.put(new ResourceLocation("gregtech", GregTechModules.MODULE_GRS), plugin);
        GregTechAPI.moduleManager = manager;
        // Supply the actual native container to the original constructor path.
        // The GT-base lane does not execute compatibility loading. The pack lane
        // calls initializeGroovy after establishing its native material manager.
        var constructor = ExternalModContainer.class.getDeclaredConstructor(GroovyPlugin.class, GroovyPropertyContainer.class);
        constructor.setAccessible(true);
        var container = constructor.newInstance(plugin, plugin.createGroovyPropertyContainer());
        Field owner = GroovyScriptModule.class.getDeclaredField("modSupportContainer");
        owner.setAccessible(true);
        if (owner.get(null) != null) throw new IllegalStateException("Warm native Groovy container");
        owner.set(null, container);
        if (!manager.isModuleEnabled(GregTechModules.MODULE_GRS)) throw new AssertionError("Groovy module not selected");
    }

    public static List<String> initializeGroovy(NativeInitializationTrace trace) throws Exception {
        // Use the original container/plugin already selected by prepare(). Its
        // callback registers the complete native mapper/expansion set; do not
        // implement acidic() or cherry-pick callback statements in the host.
        Field owner=GroovyScriptModule.class.getDeclaredField("modSupportContainer");
        owner.setAccessible(true);
        if(!(owner.get(null) instanceof GroovyContainer<?> container))
            throw new IllegalStateException("Native GT Groovy container absent");
        trace.begin("gt-groovy-compatibility");
        container.onCompatLoaded(container);
        trace.returned("gt-groovy-compatibility");
        var names=new ArrayList<String>();
        for(var mapper:ObjectMapperManager.getObjectMappers())if(mapper.getMod()==container)names.add(mapper.getName());
        Collections.sort(names);
        if(!names.equals(List.of("element","material","metaitem","oreprefix","recipemap")))
            throw new IllegalStateException("Original GT Groovy mapper registration differs: "+names);
        return List.copyOf(names);
    }

    public static Map<String,Object> bindGroovyMappers(NativeInitializationTrace trace) {
        trace.begin("native-object-mapper-bindings");
        var names=new ArrayList<String>();
        // The binding loop from original initializeGroovyPreInit, after native
        // compatibility loading. Only this bounded composition's registered
        // mappers exist; general ObjectMapperManager.init/plugin discovery is
        // not claimed. Native sandbox registration preserves aliases/identity.
        for(var mapper:ObjectMapperManager.getObjectMappers()) {
            GroovyScript.getSandbox().registerBinding(mapper);
            names.add(mapper.getName());
        }
        var material=ObjectMapperManager.getObjectMapper("material");
        if(material==null||material.getClass()!=ObjectMapper.class
                ||material.getMod()!=GroovyScriptModule.getInstance()
                ||material.getReturnType()!=gregtech.api.unification.material.Material.class
                ||material.getParamTypes().size()!=1
                ||!Arrays.equals(material.getParamTypes().getFirst(),new Class<?>[]{String.class})
                ||!material.getAliases().equals(Set.of("material"))
                ||GroovyScript.getSandbox().getBindings().get("material")!=material)
            throw new IllegalStateException("Original GT material mapper binding differs");
        MaterialCallGate.bindNativeMappers(GroovyScript.getSandbox().getBindings());
        trace.returned("native-object-mapper-bindings");
        return Map.of("registered",List.copyOf(names),"admitted",List.copyOf(MaterialCallGate.policy().nativeObjectMappers()),
                "materialBindingIdentity",true,"registrationMethod","native-GroovyScriptSandbox.registerBinding",
                "completeMapperInitialization",false);
    }
}
