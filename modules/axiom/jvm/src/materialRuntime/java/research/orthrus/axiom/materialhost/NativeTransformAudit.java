package research.orthrus.axiom.materialhost;

import net.minecraft.launchwrapper.IClassTransformer;
import net.minecraft.launchwrapper.Launch;
import org.objectweb.asm.*;
import org.objectweb.asm.tree.*;
import java.security.*;
import java.util.*;

/** Observation-only final transformer: returns the identical byte[] reference. */
public final class NativeTransformAudit implements IClassTransformer {
    private static final Set<String> TARGETS=Set.of("org.codehaus.groovy.ast.ModuleNode",
            "org.codehaus.groovy.ast.decompiled.AsmDecompiler","org.codehaus.groovy.control.ClassNodeResolver",
            "groovy.lang.Closure","org.codehaus.groovy.control.CompilationUnit$2","org.codehaus.groovy.vmplugin.v8.Java8",
            "groovy.lang.MetaClassImpl","org.codehaus.groovy.control.ResolveVisitor",
            "org.codehaus.groovy.runtime.InvokerHelper","org.codehaus.groovy.reflection.CachedClass$1",
            "org.codehaus.groovy.reflection.CachedClass$2","org.codehaus.groovy.reflection.CachedClass$3",
            "org.codehaus.groovy.control.StaticVerifier","net.minecraftforge.fml.common.eventhandler.EventBus",
            "gregtech.api.unification.material.event.MaterialRegistryEvent","gregtech.api.unification.material.event.MaterialEvent",
            "gregtech.api.unification.material.event.PostMaterialEvent","net.minecraft.block.Block",
            "gregtech.api.unification.Element","supersymmetry.common.CommonProxy",
            "gregtech.api.unification.ore.OrePrefix","supercritical.common.SCConfigHolder",
            "supercritical.common.CommonProxy","supercritical.common.SCEventHandlers",
            "supercritical.api.unification.material.SCMaterials","supercritical.api.unification.ore.SCOrePrefix",
            "gregtechfoodoption.GTFOConfig","gregtechfoodoption.GTFOEventHandler",
            "gregtechfoodoption.GTFOMaterialHandler","gregtechfoodoption.item.GTFOMetaItems",
            "gregicality.multiblocks.common.GCYMEventHandlers",
            "gregicality.multiblocks.api.unification.properties.AlloyBlastPropertyAddition",
            "gregicality.multiblocks.api.unification.GCYMMaterialFlagAddition",
            "gregicality.multiblocks.api.fluids.GeneratedFluidHandler",
            "gregtech.api.recipes.RecipeMaps","supersymmetry.api.recipes.SuSyRecipeMaps",
            "gregtech.api.GTValues","gregtech.common.ConfigHolder",
            "gregtech.api.items.metaitem.MetaItem","gregtech.api.items.metaitem.MetaItem$MetaValueItem",
            "gregtech.api.items.metaitem.StandardMetaItem","gregtech.api.items.metaitem.ElectricStats",
            "gregtech.integration.baubles.BaubleBehavior","net.minecraftforge.registries.IForgeRegistryEntry$Impl",
            "gregtech.api.unification.material.properties.BlastProperty",
            "gregtech.integration.groovy.MaterialPropertyExpansion","supersymmetry.integration.groovyscript.SuSyExpansions",
            "supercritical.api.unification.material.properties.ModeratorProperty",
            "supercritical.api.unification.material.properties.ModeratorProperty$ModeratorPropertyBuilder",
            "supersymmetry.common.materials.SusyMaterials",
            "com.cleanroommc.groovyscript.mapper.ObjectMapperManager","com.cleanroommc.groovyscript.mapper.ObjectMapper",
            "com.cleanroommc.groovyscript.mapper.AbstractObjectMapper","com.cleanroommc.groovyscript.api.IObjectParser",
            "supersymmetry.common.materials.SuSyElementMaterials","supersymmetry.common.materials.SuSyFirstDegreeMaterials",
            "supersymmetry.common.materials.SuSySecondDegreeMaterials","supersymmetry.common.materials.SuSyOrganicChemistryMaterials",
            "supersymmetry.common.materials.SuSyHighDegreeMaterials","supersymmetry.common.materials.SuSyUnknownCompositionMaterials");
    private static final Map<String,List<Map<String,Object>>> OBSERVED=new TreeMap<>();
    private static String digest(byte[] value) throws Exception {
        return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value));
    }
    @Override public byte[] transform(String name,String transformedName,byte[] bytes) {
        if(bytes==null||!TARGETS.contains(transformedName)) return bytes;
        try {
            boolean core=NativeCompilerLinkage.observesCoreCalls(transformedName);
            var node=new ClassNode();new ClassReader(bytes).accept(node,(core?0:ClassReader.SKIP_CODE)|ClassReader.SKIP_DEBUG|ClassReader.SKIP_FRAMES);
            var mixins=new TreeSet<String>();
            var calls=new TreeSet<String>();
            for(MethodNode method:node.methods) {
                if(core)for(AbstractInsnNode instruction:method.instructions)if(instruction instanceof MethodInsnNode call)
                    calls.add(method.name+":"+call.owner+"#"+call.name+call.desc);
                var annotations=new ArrayList<AnnotationNode>();
                if(method.visibleAnnotations!=null) annotations.addAll(method.visibleAnnotations);
                if(method.invisibleAnnotations!=null) annotations.addAll(method.invisibleAnnotations);
                for(AnnotationNode annotation:annotations) if(annotation.desc.equals("Lorg/spongepowered/asm/mixin/transformer/meta/MixinMerged;"))
                    for(int i=0;i<annotation.values.size();i+=2) if(annotation.values.get(i).equals("mixin")) mixins.add((String)annotation.values.get(i+1));
            }
            var row=Map.<String,Object>of("inputSha256",digest(Launch.classLoader.getClassBytes(name)),
                    "outputSha256",digest(bytes),"mergedMixins",List.copyOf(mixins),"interfaces",List.copyOf(node.interfaces),
                    "coreCalls",List.copyOf(calls),
                    "fieldAccess",node.fields.stream().collect(java.util.stream.Collectors.toMap(f->f.name,f->f.access)),
                    "declaredMethods",node.methods.stream().map(m->m.name+m.desc).toList());
            synchronized(OBSERVED) {
                var rows=OBSERVED.computeIfAbsent(name,k->new ArrayList<>());
                if(!rows.contains(row)) rows.add(row);
            }
            return bytes;
        } catch(Exception failure) {throw new IllegalStateException("Native transformation observation failed",failure);}
    }
    public static Map<String,List<Map<String,Object>>> observations() {
        synchronized(OBSERVED) {
            var copy=new TreeMap<String,List<Map<String,Object>>>();OBSERVED.forEach((key,value)->copy.put(key,List.copyOf(value)));
            return Map.copyOf(copy);
        }
    }
}
