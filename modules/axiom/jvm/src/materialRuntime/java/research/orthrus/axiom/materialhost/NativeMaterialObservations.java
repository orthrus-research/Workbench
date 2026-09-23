package research.orthrus.axiom.materialhost;

import static research.orthrus.axiom.materialhost.NativeObservationAccess.*;
import java.lang.reflect.*;
import java.util.*;

/** Read native identities and membership; no inferred properties or form rules. */
public final class NativeMaterialObservations {
    private static Map<String,Object> declarations(Class<?> owner,Class<?> type) throws IllegalAccessException {
        var result=new TreeMap<String,Object>();
        for(Field field:owner.getFields())if(Modifier.isStatic(field.getModifiers())&&field.getType()==type) {
            Object value=field.get(null);result.put(value.toString(),value);
        }
        return result;
    }
    public static Map<String,Object> material(Object material) throws ReflectiveOperationException {
        var row=new LinkedHashMap<String,Object>();
        row.put("name",materialName(material));row.put("id",call(material,"getId"));
        Object registry=call(material,"getRegistry");
        row.put("storageRegistry",call(registry,"getModid"));
        row.put("registryIdentity",((Collection<?>)call(registry,"getAllMaterials")).stream().anyMatch(value->value==material));
        row.put("formula",call(material,"getChemicalFormula"));row.put("color",call(material,"getMaterialRGB"));
        var components=new ArrayList<Map<String,Object>>();
        for(Object value:(Collection<?>)call(material,"getMaterialComponents"))
            components.add(Map.of("name",materialName(field(value.getClass(),value,"material")),
                    "amount",field(value.getClass(),value,"amount")));
        row.put("components",components);
        Class<?> propertyKey=type("gregtech.api.unification.material.properties.PropertyKey");
        Class<?> materialFlag=type("gregtech.api.unification.material.info.MaterialFlag");
        var properties=new ArrayList<String>();var flags=new ArrayList<String>();
        for(var entry:declarations(propertyKey,propertyKey).entrySet())
            if(Boolean.TRUE.equals(call(material,"hasProperty",propertyKey,entry.getValue()))) properties.add(entry.getKey());
        for(var entry:declarations(type("gregtech.api.unification.material.info.MaterialFlags"),materialFlag).entrySet())
            if(Boolean.TRUE.equals(call(material,"hasFlag",materialFlag,entry.getValue()))) flags.add(entry.getKey());
        row.put("properties",properties);row.put("flags",flags);
        Object toolKey=field(propertyKey,null,"TOOL"),tool=call(material,"getProperty",propertyKey,toolKey);
        row.put("propertyValues",tool==null?Map.of():Map.of(toolKey.toString(),toolValues(tool)));
        row.put("nativePropertyState",NativeMaterialPropertyState.observe(material));
        return row;
    }
    private static Map<String,Object> scalar(Object value) {
        if(value instanceof Float number)return Map.of("type","float32",
                "value",Float.isFinite(number)?number:Float.toString(number),
                "rawBits",HexFormat.of().toHexDigits(Float.floatToRawIntBits(number)));
        return Map.of("type",value instanceof Integer?"int32":"boolean","value",value);
    }
    private static Map<String,Object> toolValues(Object tool) throws ReflectiveOperationException {
        var result=new LinkedHashMap<String,Object>();
        for(String name:List.of("ToolSpeed","ToolAttackDamage","ToolAttackSpeed","ToolDurability","ToolHarvestLevel",
                "ToolEnchantability","ShouldIgnoreCraftingTools","Unbreakable","DurabilityMultiplier"))
            result.put(Character.toLowerCase(name.charAt(0))+name.substring(1),scalar(call(tool,"get"+name)));
        result.put("magnetic",scalar(call(tool,"isMagnetic")));
        return result;
    }
    public static Map<String,Object> vocabulary(boolean contentCompleted) throws ReflectiveOperationException {
        var result=new LinkedHashMap<String,Object>();
        Class<?> propertyKey=type("gregtech.api.unification.material.properties.PropertyKey");
        result.put("properties",List.copyOf(declarations(propertyKey,propertyKey).keySet()));
        result.put("flags",List.copyOf(declarations(type("gregtech.api.unification.material.info.MaterialFlags"),
                type("gregtech.api.unification.material.info.MaterialFlag")).keySet()));
        result.put("owner","native-gt-declarations");
        result.put("propertyValues",Map.of(field(propertyKey,null,"TOOL").toString(),Map.of(
                "toolSpeed","float32","toolAttackDamage","float32","toolAttackSpeed","float32",
                "toolDurability","int32","toolHarvestLevel","int32","toolEnchantability","int32",
                "shouldIgnoreCraftingTools","boolean","unbreakable","boolean","magnetic","boolean","durabilityMultiplier","int32")));
        result.put("propertyValuesScope","selected-native-getters-not-all-property-fields");
        if(contentCompleted) {
            Class<?> prefixItem=type("gregtech.api.items.materialitem.MetaPrefixItem");
            var prefixes=new ArrayList<String>();
            for(Object item:(Collection<?>)callStatic("gregtech.api.items.metaitem.MetaItem","getMetaItems"))
                if(prefixItem.isInstance(item))prefixes.add((String)call(call(item,"getOrePrefix"),"name"));
            result.put("gt-prefix-items",prefixes);
            String orePrefix="gregtech.api.unification.ore.OrePrefix",stoneType="gregtech.api.unification.ore.StoneType";
            result.put("gt-material-blocks",List.of(call(field(orePrefix,null,"block"),"name"),
                    call(field(orePrefix,null,"frameGt"),"name")));
            var stones=new ArrayList<Map<String,String>>();
            for(Object stone:(Iterable<?>)field(stoneType,null,"STONE_TYPE_REGISTRY"))
                stones.add(Map.of("stone",(String)field(stoneType,stone,"name"),
                        "prefix",(String)call(field(stoneType,stone,"processingPrefix"),"name")));
            result.put("gt-ore-blocks",stones);
        }
        return result;
    }
}
