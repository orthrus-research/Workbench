package research.orthrus.axiom.materialhost;

import java.lang.reflect.*;
import java.util.*;
import static research.orthrus.axiom.materialhost.NativeObservationAccess.*;

/** Passive declared item/component state; never builds stacks or registers items. */
public final class NativeMetaItemObservations {
    private NativeMetaItemObservations() {}
    public static Map<String,Object> collect() throws ReflectiveOperationException {
        // A Class reference/definition is not evidence of initialization. An
        // actual instance passed through native Groovy guarantees it happened.
        if(!MaterialCallGate.observedInstance("gregtech.api.items.metaitem.StandardMetaItem"))
            return Map.of("status","not-observed","scope","declared-custom-meta-items-not-generated-content");
        Class<?> metaItem=type("gregtech.api.items.metaitem.MetaItem"),standard=type("gregtech.api.items.metaitem.StandardMetaItem"),
                metaValue=type("gregtech.api.items.metaitem.MetaItem$MetaValueItem"),electricStats=type("gregtech.api.items.metaitem.ElectricStats");
        Object registry=field("net.minecraftforge.fml.common.registry.ForgeRegistries",null,"ITEMS");
        Class<?> resourceLocation=type("net.minecraft.util.ResourceLocation");
        var rows=new ArrayList<Map<String,Object>>();
        for(Object item:(Collection<?>)callStatic(metaItem.getName(),"getMetaItems")) {
            if(item.getClass()!=standard)continue;
            var row=new LinkedHashMap<String,Object>();
            row.put("class",item.getClass().getName());row.put("nativeClassSpace",item.getClass().getClassLoader()==NativeMetaItemObservations.class.getClassLoader());
            Object name=call(item,"getRegistryName");
            row.put("registryName",name==null?null:name.toString());
            row.put("forgeRegistered",name!=null&&call(registry,"getValue",resourceLocation,name)==item);
            row.put("offset",((Number)field(metaItem,item,"metaItemOffset")).intValue());
            var variants=new ArrayList<Map<String,Object>>();
            for(Object value:(Collection<?>)call(item,"getAllItems")) {
                Object itemName=field(metaValue,value,"unlocalizedName");
                var v=new LinkedHashMap<String,Object>();v.put("name",itemName);v.put("meta",call(value,"getMetaValue"));
                v.put("ownerIdentity",call(value,"getMetaItem")==item);
                v.put("nameLookupIdentity",call(item,"getItem",String.class,itemName)==value);
                v.put("maxStackSize",field(metaValue,value,"maxStackSize"));
                v.put("modelAmount",field(metaValue,value,"modelAmount"));
                v.put("burnValue",call(value,"getBurnValue"));
                var stats=new ArrayList<Map<String,Object>>();
                for(Object stat:(Collection<?>)call(value,"getAllStats")) {
                    var s=new LinkedHashMap<String,Object>();s.put("class",stat==null?null:stat.getClass().getName());
                    if(electricStats.isInstance(stat)) {
                        for(String field:List.of("maxCharge","tier","chargeable","dischargeable"))
                            s.put(field,field(electricStats,stat,field));
                    } else if(stat!=null&&stat.getClass().getName().equals("gregtech.integration.baubles.BaubleBehavior")) {
                        var type=(Enum<?>)field(stat.getClass(),stat,"baubleType");
                        s.put("baubleType",type==null?null:type.name());
                    }
                    stats.add(s);
                }
                v.put("components",stats);variants.add(v);
            }
            row.put("variants",variants);rows.add(row);
        }
        return Map.of("status","observed","scope","declared-custom-meta-items-not-generated-content",
                "stackCreationInvoked",false,"registrationInvoked",false,"items",rows);
    }
}
