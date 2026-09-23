package research.orthrus.axiom;

import java.util.*;

/** Compare already observed native memberships, never declarations or source text. */
final class MaterialRegistrationEffectComparison {
    private static final List<String> DOMAINS=List.of("materials","customItems","fluids");
    private MaterialRegistrationEffectComparison() {}

    static Map<String,Object> compare(Map<String,Object> before,Map<String,Object> after) {
        before=NativeStageSnapshots.expandExecution(before);after=NativeStageSnapshots.expandExecution(after);
        var left=object(before.get("registrationEffects"));
        var right=object(after.get("registrationEffects"));
        var domains=new LinkedHashMap<String,Object>();
        boolean changed=false,incomplete=false;
        var observedDomains=new ArrayList<>(DOMAINS);
        for(String domain:List.of("prefixItems","materialBlocks","oreBlocks"))
            if(left.containsKey(domain)||right.containsKey(domain))observedDomains.add(domain);
        for(String domain:observedDomains) {
            var a=inventory(before,left,domain);var b=inventory(after,right,domain);
            var reasons=new ArrayList<String>();
            if(!"axiom.native-registration-effects.v1".equals(left.get("schema"))
                    ||!"axiom.native-registration-effects.v1".equals(right.get("schema")))
                reasons.add("Native effect inventory is unavailable or has an unsupported schema");
            if(!Objects.equals(left.get("phase"),right.get("phase"))||!left.containsKey("phase"))
                reasons.add("Native checkpoints differ or are unavailable");
            for(var inventory:List.of(a,b)) {
                if(!"observed".equals(inventory.get("status"))||!Boolean.TRUE.equals(inventory.get("inventoryComplete")))
                    reasons.add("A complete current native membership inventory was not observed");
                else if(!(inventory.get("entries") instanceof Map<?,?> entries)
                        ||entries.entrySet().stream().anyMatch(row->!(row.getKey() instanceof String identity)||identity.isEmpty()
                        ||!(row.getValue() instanceof String digest)||!digest.matches("[0-9a-f]{64}")))
                    reasons.add("Native identity/state inventory is malformed");
            }
            for(String key:List.of("membership","stateScope"))
                if(!a.containsKey(key)||!Objects.equals(a.get(key),b.get(key)))reasons.add("Different or unavailable "+key);
            if(!reasons.isEmpty()) {
                incomplete=true;
                domains.put(domain,Map.of("status","not-comparable","reasons",reasons.stream().distinct().toList()));
                continue;
            }
            var old=object(a.get("entries"));var current=object(b.get("entries"));
            var identities=new TreeSet<>(old.keySet());identities.addAll(current.keySet());
            var added=new ArrayList<Map<String,Object>>();var removed=new ArrayList<Map<String,Object>>();
            var modified=new ArrayList<Map<String,Object>>();
            for(String identity:identities) {
                if(old.containsKey(identity)&&current.containsKey(identity)&&Objects.equals(old.get(identity),current.get(identity)))continue;
                var row=new LinkedHashMap<String,Object>();row.put("identity",identity);
                if(old.containsKey(identity)) {row.put("baselineStateSha256",old.get(identity));row.put("baselinePointer","/baseline"+pointer(a,domain,identity));}
                if(current.containsKey(identity)) {row.put("candidateStateSha256",current.get(identity));row.put("candidatePointer","/candidate"+pointer(b,domain,identity));}
                if(old.containsKey(identity)&&a.containsKey("ownerPointers"))row.put("baselineOwnerPointer","/baseline"+object(a.get("ownerPointers")).get(identity));
                if(current.containsKey(identity)&&b.containsKey("ownerPointers"))row.put("candidateOwnerPointer","/candidate"+object(b.get("ownerPointers")).get(identity));
                (old.containsKey(identity)?current.containsKey(identity)?modified:removed:added).add(row);
            }
            boolean domainChanged=!added.isEmpty()||!removed.isEmpty()||!modified.isEmpty();changed|=domainChanged;
            domains.put(domain,Map.of("status",domainChanged?"changed":"unchanged","membership",a.get("membership"),
                    "stateScope",a.get("stateScope"),"added",added,"removed",removed,"modified",modified));
        }
        return Map.of("schema","axiom.registration-effect-comparison.v1","status",incomplete?"incomplete":changed?"changed":"unchanged",
                "domains",domains,"meaning","current-native-membership-and-selected-state-not-source-causation-or-initialization-completion",
                "recipeEffectsChecked",false);
    }

    private static Map<String,Object> inventory(Map<String,Object> execution,Map<String,Object> effects,String domain) {
        var value=object(effects.get(domain));
        if(!domain.equals("customItems")||!"reference".equals(value.get("status")))return value;
        if(!"/execution/customMetaItems".equals(value.get("sourcePointer")))return Map.of();
        var observed=object(execution.get("customMetaItems"));
        if(!"observed".equals(observed.get("status"))||!(observed.get("items") instanceof List<?> items))return Map.of();
        var entries=new TreeMap<String,Object>();var pointers=new TreeMap<String,Object>();var ownerPointers=new TreeMap<String,Object>();
        for(int itemIndex=0;itemIndex<items.size();itemIndex++) {
            var item=object(items.get(itemIndex));
            if(!(item.get("registryName") instanceof String owner)||owner.isEmpty()||!(item.get("variants") instanceof List<?> variants))return Map.of();
            for(int variantIndex=0;variantIndex<variants.size();variantIndex++) {
                var variant=object(variants.get(variantIndex));
                if(!(variant.get("meta") instanceof Number)||!(variant.get("name") instanceof String)
                        ||!(variant.get("ownerIdentity") instanceof Boolean)||!(variant.get("nameLookupIdentity") instanceof Boolean))return Map.of();
                String identity=owner+"#"+variant.get("meta");
                var state=new LinkedHashMap<>(item);state.remove("variants");state.put("variant",variant);
                if(entries.put(identity,Json.digest(state))!=null)return Map.of();
                pointers.put(identity,"/result/execution/customMetaItems/items/"+itemIndex+"/variants/"+variantIndex);
                ownerPointers.put(identity,"/result/execution/customMetaItems/items/"+itemIndex);
            }
        }
        return Map.of("status","observed","inventoryComplete",true,"membership","native-custom-meta-item-variant-definition",
                "stateScope","native-custom-meta-item-selected-values-and-owner-registration-v1","entries",entries,"pointers",pointers,"ownerPointers",ownerPointers);
    }
    private static String pointer(Map<String,Object> inventory,String domain,String identity) {
        return inventory.containsKey("pointers")?(String)object(inventory.get("pointers")).get(identity)
                :"/result/execution/registrationEffects/"+domain+"/entries/"+escape(identity);
    }
    private static String escape(String value) {return value.replace("~","~0").replace("/","~1");}
    private static Map<String,Object> object(Object value) {return value instanceof Map<?,?>?Json.object(value):Map.of();}
}
