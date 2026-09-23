package research.orthrus.axiom.materialhost;

import net.minecraftforge.fml.common.*;
import net.minecraftforge.fml.common.discovery.ASMDataTable;
import java.io.File;
import java.lang.reflect.Field;
import java.util.*;

/** Native API providers within the witnessed JAR set, not complete loader composition. */
public final class NativeAddonApis {
    private NativeAddonApis() {}

    public static Map<String,Object> inspect(ASMDataTable table, Map<File,String> sources,
                                           NativeInitializationTrace trace) throws Exception {
        trace.declare("addon-api-providers","hook","ModAPIManager.registerDataTableAndParseAPI");
        trace.begin("addon-api-providers");
        var manager=ModAPIManager.INSTANCE;
        var previous=new LinkedHashMap<Field,Object>();
        for(String name:List.of("dataTable","apiContainers")) {
            Field field=ModAPIManager.class.getDeclaredField(name);field.setAccessible(true);
            previous.put(field,field.get(manager));
        }
        var result=new LinkedHashMap<String,Object>();
        try {
            manager.registerDataTableAndParseAPI(table);
            var providers=new TreeMap<String,Object>();
            for(ModContainer container:manager.getAPIList()) {
                var row=new LinkedHashMap<String,Object>();
                row.put("version",container.getVersion());
                row.put("sourceArtifactSha256",source(sources,container.getSource()));
                row.put("after",container.getDependencies().stream().map(String::valueOf).toList());
                row.put("before",container.getDependants().stream().map(String::valueOf).toList());
                row.put("owner",String.valueOf(field(container,"ownerMod")));
                row.put("selfReferenced",field(container,"selfReferenced"));
                @SuppressWarnings("unchecked") var packages=(Set<String>)field(container,"packages");
                row.put("packages",packages.stream().sorted().toList());
                if(container.getMod()!=null)throw new IllegalStateException("API provider constructed a mod instance");
                if(providers.putIfAbsent(container.getModId(),row)!=null)throw new IllegalStateException("Duplicate native API provider");
            }
            var membership=new TreeMap<String,Object>();
            for(var declaration:table.getAll("net.minecraftforge.fml.common.API")) {
                String name=declaration.getClassName();
                String pkg=name.substring(0,name.indexOf(".package-info")); // native parser already accepted this
                var rows=new TreeMap<String,Object>();
                for(var candidate:table.getCandidatesFor(pkg))
                    rows.put(source(sources,candidate.getModContainer()),candidate.getContainedMods().stream().map(ModContainer::getModId).toList());
                membership.put(pkg,rows);
            }
            result.put("scope","discovered-jar-candidates-only-not-complete-pack-api-composition");
            result.put("method","original ModAPIManager.registerDataTableAndParseAPI");
            result.put("declarationCount",table.getAll("net.minecraftforge.fml.common.API").size());
            result.put("providerCount",providers.size());result.put("providers",providers);
            result.put("packageMembership",membership);
            result.put("candidateTransformerInstalled",false);
            result.put("packApiCompositionQualified",false);
        } finally {
            for(var entry:previous.entrySet())entry.getKey().set(manager,entry.getValue());
            for(var entry:previous.entrySet())
                if(entry.getKey().get(manager)!=entry.getValue())throw new IllegalStateException("Native API host state not restored");
        }
        result.put("hostApiStateRestored",true);trace.returned("addon-api-providers");return result;
    }

    private static Object field(Object object,String name) throws Exception {
        Field field=object.getClass().getDeclaredField(name);field.setAccessible(true);return field.get(object);
    }
    private static String source(Map<File,String> sources,File file) {
        String hash=sources.get(file);
        if(hash==null)throw new IllegalStateException("Unbound native API candidate source");
        return hash;
    }
}
