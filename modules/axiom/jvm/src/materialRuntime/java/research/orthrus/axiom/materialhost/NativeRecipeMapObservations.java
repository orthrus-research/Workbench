package research.orthrus.axiom.materialhost;

import java.lang.reflect.Field;
import java.util.*;
import gregtech.api.recipes.*;
import gregtech.api.recipes.category.GTRecipeCategory;
import gregtech.integration.groovy.GroovyScriptModule;
import net.minecraft.launchwrapper.Launch;

/** Read actual constructor/limit state after execution; never evaluate recipes. */
public final class NativeRecipeMapObservations {
    private NativeRecipeMapObservations() {}
    private static Object field(Class<?> owner,Object object,String name) throws Exception {
        Field field=owner.getDeclaredField(name);field.setAccessible(true);return field.get(object);
    }
    public static List<Map<String,Object>> collect() throws Exception {
        // Do not initialize an unused registry solely to populate observations.
        if(MaterialCallGate.observations().keySet().stream().noneMatch(key->key.contains("gregtech.api.recipes.RecipeMap#")))return List.of();
        var result=new ArrayList<Map<String,Object>>();
        for(var map:RecipeMap.getRecipeMaps()) {
            var row=new LinkedHashMap<String,Object>();
            row.put("name",map.unlocalizedName);
            row.put("limits",List.of(map.getMaxInputs(),map.getMaxOutputs(),map.getMaxFluidInputs(),map.getMaxFluidOutputs()));
            row.put("modifiable",List.of(field(RecipeMap.class,map,"modifyItemInputs"),field(RecipeMap.class,map,"modifyItemOutputs"),
                field(RecipeMap.class,map,"modifyFluidInputs"),field(RecipeMap.class,map,"modifyFluidOutputs")));
            Object builder=field(RecipeMap.class,map,"recipeBuilderSample");
            Object callback=field(RecipeMap.class,map,"onRecipeBuildAction");
            // Read identity only; never invoke a recipe hook to populate a report.
            row.put("onRecipeBuildOwner",callback==null?null:callback.getClass().getNestHost().getName());
            Object category=field(RecipeBuilder.class,builder,"category");
            row.put("builderLinked",field(RecipeBuilder.class,builder,"recipeMap")==map);
            row.put("categoryLinked",category==GTRecipeCategory.getByName(map.unlocalizedName)&&((GTRecipeCategory)category).getRecipeMap()==map);
            Object virtual=field(RecipeMap.class,map,"grsVirtualizedRecipeMap");
            row.put("virtualizedRegistryLinked",virtual!=null&&GroovyScriptModule.getInstance().get().getProperties().containsValue(virtual));
            row.put("nativeClassSpace",map.getClass().getClassLoader()==Launch.classLoader&&builder.getClass().getClassLoader()==Launch.classLoader);
            row.put("hidden",map.isHidden);
            result.add(row);
        }
        result.sort(Comparator.comparing(row->(String)row.get("name")));
        return result;
    }
}
