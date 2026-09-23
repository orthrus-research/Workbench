package research.orthrus.axiom;

import java.math.BigDecimal;
import java.util.*;

/** Developer intent compared with native observations, never a material rule engine. */
final class MaterialExpectations {
    private static final Map<String,String> FAMILIES=Map.of("gt-prefix-items","prefixItems",
            "gt-material-blocks","materialBlocks","gt-ore-blocks","materialOres");
    private MaterialExpectations() {}

    static List<Map<String,Object>> parse(Object input) {
        if(input==null)return List.of();
        var checks=new ArrayList<Map<String,Object>>();var ids=new HashSet<String>();
        for(Object item:Json.array(input)) {
            var value=Json.object(item);String kind=Json.string(value.get("kind"));
            var fields=new ArrayList<>(List.of("id","material","kind","equals"));
            switch(kind) {
                case "registration" -> {}
                case "value", "property", "flag" -> fields.add("key");
                case "property-value" -> fields.addAll(List.of("key","field"));
                case "form" -> fields.addAll(List.of("family","prefix","fact","stone"));
                case "fluid" -> fields.addAll(List.of("storageKey","fact"));
                case "processing" -> fields.addAll(List.of("prefix","fact"));
                default -> throw Failure.request("Unknown material expectation kind: "+kind);
            }
            Json.keys(value,fields.toArray(String[]::new));
            if(!value.containsKey("equals"))throw Failure.request("Material expectation requires an explicit equals value");
            String id=Json.string(value.get("id"));
            if(!id.matches("[A-Za-z0-9][A-Za-z0-9._-]{0,79}")||!ids.add(id))throw Failure.request("Expectation IDs must be ordinary and unique");
            materialName(value.get("material"));
            if(kind.equals("value")) {
                String key=Json.string(value.get("key"));
                if(Set.of("id","color").contains(key)) {
                    if(!(value.get("equals") instanceof Number number))throw Failure.request("Expected integer material value");
                    try {new BigDecimal(number.toString()).intValueExact();}catch(ArithmeticException error){throw Failure.request("Expected bounded integer material value");}
                } else if(key.equals("formula")) {
                    if(value.get("equals")!=null)Json.string(value.get("equals"));
                } else if(key.equals("storageRegistry"))Json.string(value.get("equals"));
                else throw Failure.request("Unknown material value: "+key);
            } else if(kind.equals("property-value")) {
                for(String selector:List.of("key","field"))if(!Json.string(value.get(selector)).matches("[A-Za-z][A-Za-z0-9_]*"))
                    throw Failure.request("Expected literal native property/field selector");
                Object expected=value.get("equals");
                if(expected instanceof Number number) {
                    try {new BigDecimal(number.toString());}catch(NumberFormatException error) {throw Failure.request("Expected finite JSON number");}
                } else if(!(expected instanceof Boolean)&&!(expected instanceof String text&&Set.of("NaN","Infinity","-Infinity").contains(text)))
                    throw Failure.request("Expected numeric/boolean property value or explicit non-finite float token");
            } else if(!(value.get("equals") instanceof Boolean))throw Failure.request("Expected boolean material intent");
            if(Set.of("property","flag").contains(kind))Json.string(value.get("key"));
            if(kind.equals("fluid")) {
                materialName(value.get("storageKey"));
                if(!Set.of("queued","stored").contains(Json.string(value.get("fact"))))throw Failure.request("Unknown native fluid state fact");
            }
            if(kind.equals("processing")) {
                Json.string(value.get("prefix"));
                if(!"queued".equals(Json.string(value.get("fact"))))throw Failure.request("Only pending material membership is observed; handlers are not executed");
            }
            if(kind.equals("form")) {
                String family=Json.string(value.get("family"));Json.string(value.get("prefix"));
                if(!Set.of("generated","selected","eligible","ordinaryDrop").contains(Json.string(value.get("fact"))))
                    throw Failure.request("Unknown native form fact");
                if(family.equals("gt-ore-blocks"))Json.string(value.get("stone"));
                else if(value.containsKey("stone"))throw Failure.request("Stone identity is only meaningful for ore forms");
            }
            checks.add(Collections.unmodifiableMap(new LinkedHashMap<>(value)));
            if(checks.size()>256)throw Failure.request("At most 256 material expectations are supported");
        }
        return List.copyOf(checks);
    }

    static String materialName(Object value) {
        String name=Json.string(value);
        if(!name.matches("[a-z0-9_]+:[a-z0-9_]+"))throw Failure.request("Material identities require namespace:name");
        return name;
    }

    record Observation(String status,Object value,String reason,String pointer) {
        static Observation known(Object value,String pointer) {return new Observation("observed",value,"",pointer);}
        static Observation absent(String reason) {return new Observation("not-evaluated",null,reason,"");}
        static Observation unsupported(String reason) {return new Observation("unsupported",null,reason,"");}
    }

    static Map<String,Object> evaluate(List<Map<String,Object>> checks,Map<String,Object> execution,boolean usable) {
        var results=new ArrayList<Map<String,Object>>();
        for(int index=0;index<checks.size();index++) {
            var check=checks.get(index);
            Observation observed=usable?observe(check,execution):Observation.absent("Native program did not complete with usable coverage and without errors");
            var result=new LinkedHashMap<String,Object>();result.put("id",check.get("id"));result.put("material",check.get("material"));
            result.put("kind",check.get("kind"));result.put("expected",check.get("equals"));result.put("requestPointer","/expectations/"+index);
            if(observed.status().equals("observed")) {
                result.put("status",same(check.get("equals"),observed.value())?"matched":"mismatch");
                result.put("observed",observed.value());result.put("evidencePointer",observed.pointer());
            } else {result.put("status",observed.status());result.put("reason",observed.reason());}
            results.add(result);
        }
        String status=results.isEmpty()?"not-requested":results.stream().anyMatch(row->Set.of("unsupported","not-evaluated").contains(row.get("status")))?"incomplete"
                :results.stream().anyMatch(row->row.get("status").equals("mismatch"))?"mismatch":"matched";
        return Map.of("schema","axiom.material-intent-result.v1","status",status,"checks",results,
                "requestSha256",Json.digest(checks),"meaning","developer-intent-not-native-validity");
    }

    private static boolean same(Object expected,Object actual) {
        if(expected instanceof Number left&&actual instanceof Number right)
            return new BigDecimal(left.toString()).compareTo(new BigDecimal(right.toString()))==0;
        return Objects.equals(expected,actual);
    }

    private static Observation observe(Map<String,Object> check,Map<String,Object> execution) {
        String kind=Json.string(check.get("kind")),name=Json.string(check.get("material"));
        if(!(execution.get("materials") instanceof List<?>)) {
            // The original startup route reports a complete current registry,
            // not the prepared route's selected-material projection. Missing
            // selected values must not become false membership or a parse error.
            if(kind.equals("registration")&&execution.get("registrationEffects") instanceof Map<?,?> effects
                    &&effects.get("materials") instanceof Map<?,?> catalog
                    &&"observed".equals(catalog.get("status"))&&Boolean.TRUE.equals(catalog.get("inventoryComplete"))
                    &&"native-material-registry".equals(catalog.get("membership"))&&catalog.get("entries") instanceof Map<?,?> entries)
                return Observation.known(entries.containsKey(name),"/execution/registrationEffects/materials/entries");
            return Observation.absent("This startup result does not contain the selected native values required by the optional expectation");
        }
        var materials=Json.array(execution.get("materials"));Map<String,Object> material=null;int materialIndex=-1;
        for(int i=0;i<materials.size();i++)if(name.equals(Json.object(materials.get(i)).get("name"))) {
            material=Json.object(materials.get(i));materialIndex=i;break;
        }
        if(kind.equals("registration"))return Observation.known(material!=null&&Boolean.TRUE.equals(material.get("registryIdentity")),
                material==null?"/execution/lookups":"/execution/materials/"+materialIndex+"/registryIdentity");
        if(kind.equals("fluid")||kind.equals("processing"))return deferred(check,execution,material!=null);
        var vocabulary=Json.object(execution.get("vocabulary"));
        if(kind.equals("property-value")) {
            if(!(vocabulary.get("propertyValues") instanceof Map<?,?> supported))
                return Observation.unsupported("Native scalar property observations are unavailable in this context");
            String property=Json.string(check.get("key")),field=Json.string(check.get("field"));
            if(!(supported.get(property) instanceof Map<?,?> fields)||!fields.containsKey(field))
                return Observation.unsupported("Property field is not in the selected native getter vocabulary");
            if(material==null)return Observation.absent("Requested material identity was not registered");
            if(!(material.get("propertyValues") instanceof Map<?,?> values)||!(values.get(property) instanceof Map<?,?> selected)
                    ||!(selected.get(field) instanceof Map<?,?> scalar)||!scalar.containsKey("value"))
                return Observation.absent("Native property field was not observed; property absence is a separate membership fact");
            if(!Objects.equals(scalar.get("type"),fields.get(field)))return Observation.absent("Native property field type differs from its observation vocabulary");
            return Observation.known(scalar.get("value"),"/execution/materials/"+materialIndex+"/propertyValues/"+property+"/"+field+"/value");
        }
        if(kind.equals("property")||kind.equals("flag")) {
            String field=kind.equals("property")?"properties":"flags";
            if(!Json.array(vocabulary.get(field)).contains(check.get("key")))return Observation.unsupported("Name is not in the native GT "+field+" observation vocabulary");
            if(material==null)return Observation.absent("Requested material identity was not registered");
            return Observation.known(Json.array(material.get(field)).contains(check.get("key")),"/execution/materials/"+materialIndex+"/"+field);
        }
        if(kind.equals("value")) {
            if(material==null)return Observation.absent("Requested material identity was not registered");
            String key=Json.string(check.get("key"));
            return material.containsKey(key)?Observation.known(material.get(key),"/execution/materials/"+materialIndex+"/"+key)
                    :Observation.absent("Native material value was not observed");
        }
        String family=Json.string(check.get("family")),fact=Json.string(check.get("fact"));
        if(!FAMILIES.containsKey(family))return Observation.unsupported("Generated family is outside this native context");
        if(fact.equals("eligible")&&!family.equals("gt-prefix-items")||fact.equals("ordinaryDrop")&&!family.equals("gt-ore-blocks"))
            return Observation.unsupported("This family does not observe the requested native fact");
        if(!vocabulary.containsKey(family))return Observation.absent("Native content lifecycle did not complete");
        boolean covered=family.equals("gt-ore-blocks")?Json.array(vocabulary.get(family)).stream().map(Json::object)
                .anyMatch(row->Objects.equals(row.get("prefix"),check.get("prefix"))&&Objects.equals(row.get("stone"),check.get("stone")))
                :Json.array(vocabulary.get(family)).contains(check.get("prefix"));
        if(!covered)return Observation.unsupported("Requested prefix/stone is not in this family's native observation vocabulary");
        if(material==null)return Observation.absent("Requested material identity was not registered");
        String field=FAMILIES.get(family);var familyResult=Json.object(execution.get(field));
        if(!"COMPLETE".equals(familyResult.get("phase")))return Observation.absent("Native content lifecycle did not complete");
        var forms=Json.array(familyResult.get("forms"));
        for(int index=0;index<forms.size();index++) {
            var row=Json.object(forms.get(index));
            if(!name.equals(row.get("material"))||!Objects.equals(check.get("prefix"),row.get("prefix"))
                    ||family.equals("gt-ore-blocks")&&!Objects.equals(check.get("stone"),row.get("stone")))continue;
            String pointer="/execution/"+field+"/forms/"+index+"/"+fact;
            if(fact.equals("generated"))return Observation.known(!Json.array(row.get("generated")).isEmpty(),pointer);
            if(fact.equals("eligible"))return row.get("eligible") instanceof Boolean?Observation.known(row.get("eligible"),pointer)
                    :Observation.absent("Native eligibility was not observed");
            if(fact.equals("selected"))return stackPresence(row.get("selected"),pointer);
            var generated=Json.array(row.get("generated"));
            if(generated.size()!=1)return Observation.absent("Ordinary drop requires an observed generated ore variant");
            return stackPresence(Json.object(generated.get(0)).get("ordinaryDrop"),
                    "/execution/"+field+"/forms/"+index+"/generated/0/ordinaryDrop");
        }
        return Observation.absent("Native form row was not observed; absence was not inferred");
    }
    private static Observation deferred(Map<String,Object> check,Map<String,Object> execution,boolean registered) {
        if(!(execution.get("deferredWork") instanceof Map<?,?>))return Observation.absent("Native deferred-work checkpoint was not observed");
        var work=Json.object(execution.get("deferredWork"));
        if(!"axiom.native-deferred-material-work.v1".equals(work.get("schema"))||!"FROZEN".equals(work.get("phase")))
            return Observation.absent("Native deferred-work checkpoint is not covered");
        String name=Json.string(check.get("material"));
        if(check.get("kind").equals("fluid")) {
            if(!Json.array(work.get("fluidStorageKeys")).contains(check.get("storageKey")))
                return Observation.unsupported("Storage key is not in the native fluid observation vocabulary");
            if(!registered)return Observation.absent("Requested material identity was not registered");
            var rows=Json.array(work.get("fluids"));
            for(int index=0;index<rows.size();index++) {
                var row=Json.object(rows.get(index));if(!name.equals(row.get("material")))continue;
                String pointer="/execution/deferredWork/fluids/"+index;
                if(Boolean.FALSE.equals(row.get("hasFluidProperty")))return Observation.known(false,pointer+"/hasFluidProperty");
                String fact=Json.string(check.get("fact"));
                if(!Boolean.TRUE.equals(row.get("hasFluidProperty"))||!(row.get(fact) instanceof List<?>))
                    return Observation.absent("Native fluid membership was not observed");
                return Observation.known(Json.array(row.get(fact)).stream().map(Json::object)
                        .anyMatch(value->Objects.equals(value.get("key"),check.get("storageKey"))),pointer+"/"+fact);
            }
        } else {
            if(!(work.get("prefixProcessing") instanceof List<?>))return Observation.absent("Native prefix-processing checkpoint was not observed");
            var rows=Json.array(work.get("prefixProcessing"));
            for(int index=0;index<rows.size();index++) {
                var row=Json.object(rows.get(index));if(!Objects.equals(check.get("prefix"),row.get("prefix")))continue;
                if(!registered)return Observation.absent("Requested material identity was not registered");
                if(!(row.get("pendingMaterials") instanceof List<?>))return Observation.absent("Native processing membership was not observed");
                return Observation.known(Json.array(row.get("pendingMaterials")).contains(name),
                        "/execution/deferredWork/prefixProcessing/"+index+"/pendingMaterials");
            }
            return Observation.unsupported("Prefix is not in the native processing observation vocabulary");
        }
        return Observation.absent("Native deferred-work row was not observed; absence was not inferred");
    }
    private static Observation stackPresence(Object value,String pointer) {
        return value instanceof Map<?,?> stack&&stack.get("empty") instanceof Boolean empty?Observation.known(!empty,pointer)
                :Observation.absent("Native stack presence was not observed");
    }
}
