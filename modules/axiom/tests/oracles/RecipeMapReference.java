package research.orthrus.axiom.materialhost;

import gregtech.api.recipes.RecipeMap;
import gregtech.api.recipes.RecipeBuilder;
import gregtech.api.recipes.category.GTRecipeCategory;
import gregtech.integration.groovy.GroovyScriptModule;
import java.util.*;

/** Fixed-corpus observation, separate from the installed observer. */
public final class RecipeMapReference {
    private static Object read(Object object,Class<?> type,String name) throws Exception {
        var field=type.getDeclaredField(name);field.setAccessible(true);return field.get(object);
    }
    public static List<Map<String,Object>> observe() throws Exception {
        var rows=new TreeMap<String,Map<String,Object>>();
        for(RecipeMap<?> map:RecipeMap.getRecipeMaps()) {
            var row=new LinkedHashMap<String,Object>();
            row.put("name",map.getUnlocalizedName());
            var limits=new ArrayList<Object>();
            for(String name:List.of("maxInputs","maxOutputs","maxFluidInputs","maxFluidOutputs"))limits.add(read(map,RecipeMap.class,name));
            row.put("limits",limits);
            var flags=new ArrayList<Object>();
            for(String name:List.of("modifyItemInputs","modifyItemOutputs","modifyFluidInputs","modifyFluidOutputs"))flags.add(read(map,RecipeMap.class,name));
            row.put("modifiable",flags);
            var builder=read(map,RecipeMap.class,"recipeBuilderSample");
            var category=(GTRecipeCategory)read(builder,RecipeBuilder.class,"category");
            row.put("builderLinked",read(builder,RecipeBuilder.class,"recipeMap")==map);
            row.put("categoryLinked",category.getRecipeMap()==map&&GTRecipeCategory.getByName(map.getUnlocalizedName())==category);
            var virtual=read(map,RecipeMap.class,"grsVirtualizedRecipeMap");
            boolean registered=false;
            for(var entry:GroovyScriptModule.getInstance().get().getRegistries())if(entry==virtual)registered=true;
            row.put("virtualizedRegistryLinked",virtual!=null&&registered);
            row.put("nativeClassSpace",map.getClass().getClassLoader()==net.minecraft.launchwrapper.Launch.classLoader
                &&builder.getClass().getClassLoader()==net.minecraft.launchwrapper.Launch.classLoader);
            row.put("hidden",map.isHidden);
            rows.put(map.getUnlocalizedName(),row);
        }
        return new ArrayList<>(rows.values());
    }
}
