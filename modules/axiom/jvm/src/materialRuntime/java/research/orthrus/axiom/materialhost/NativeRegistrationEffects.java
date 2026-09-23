package research.orthrus.axiom.materialhost;

import static research.orthrus.axiom.materialhost.NativeObservationAccess.*;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;

/** Passive complete membership catalogs with compact selected-state fingerprints.
 * Completeness refers to current native collections, never to initialization.
 * No builder, registration, supplier, verification or queue processing is invoked.
 */
public final class NativeRegistrationEffects {
    private static final int MAX_ENTRIES=8192;
    private NativeRegistrationEffects() {}

    public static Map<String,Object> registries() throws ReflectiveOperationException {
        Object manager=materialManager();String phase=((Enum<?>)call(manager,"getPhase")).name();
        if(phase.equals("PRE"))return Map.of("status","not-observed-in-PRE","phase","PRE");
        Object defaultRegistry=call(manager,"getDefaultRegistry");
        var registries=new ArrayList<Map<String,Object>>();int total=0;
        for(Object registry:(Collection<?>)call(manager,"getRegistries")) {
            int count=((Collection<?>)call(registry,"getAllMaterials")).size();total+=count;
            registries.add(Map.of("modId",call(registry,"getModid"),"networkId",call(registry,"getNetworkId"),
                    "registeredMaterials",count,"defaultRegistry",registry==defaultRegistry));
        }
        registries.sort(Comparator.comparing(row->(String)row.get("modId")));
        return Map.of("status","observed","phase",phase,"registries",List.copyOf(registries),
                "totalRegisteredMaterials",total,"scope","all-current-native-registries-not-complete-pack");
    }

    public static Map<String,Object> collect() {
        // Retain complete inventories while MVP resource targets are suspended.
        return observedCatalogs(-1,-1);
    }

    public static Map<String,Object> collect(int materialBudgetBytes,int fluidBudgetBytes) {
        if(materialBudgetBytes<0||fluidBudgetBytes<0||(long)materialBudgetBytes+fluidBudgetBytes>512*1024)
            throw new IllegalArgumentException("Native effect catalog allocation exceeds bounded protocol budget");
        return observedCatalogs(materialBudgetBytes,fluidBudgetBytes);
    }

    /** Complete preInit observes native fluid registration without a catalog budget. */
    public static Map<String,Object> collectPreInit() {
        return observedCatalogs(-1,-1);
    }

    private static Map<String,Object> observedCatalogs(int materialBudgetBytes,int fluidBudgetBytes) {
        try { return collectNative(materialBudgetBytes,fluidBudgetBytes); }
        catch(ReflectiveOperationException failure) { throw new IllegalStateException("Original registry observation failed",failure); }
    }

    private static Map<String,Object> collectNative(int materialBudgetBytes,int fluidBudgetBytes) throws ReflectiveOperationException {
        Object manager=materialManager();
        String phase=((Enum<?>)call(manager,"getPhase")).name();
        var result=new LinkedHashMap<String,Object>();
        result.put("schema","axiom.native-registration-effects.v1");result.put("phase",phase);
        result.put("meaning","all-current-native-memberships-not-complete-initialization");
        result.put("materialFluidBindings",Map.of("status","unavailable","bindingsComplete",false));
        result.put("materials",observe("native-material-registry", "native-material-observation-selected-property-values-v2",()-> {
            if(phase.equals("PRE"))throw new IllegalStateException("Material registries not yet available in PRE");
            var entries=new Catalog(materialBudgetBytes);
            int fluidMaterials=0;var bindingGaps=new ArrayList<String>();
            for(Object registry:(Collection<?>)call(manager,"getRegistries"))for(Object material:(Collection<?>)call(registry,"getAllMaterials")) {
                var state=new LinkedHashMap<>(NativeMaterialObservations.material(material));
                // A generated lambda class name can contain a VM-specific suffix.
                // Report producer presence, not a false implementation fingerprint.
                var properties=new TreeMap<String,Object>();
                var nativeState=map(state.get("nativePropertyState"));
                for(var entry:map(nativeState.get("properties")).entrySet()) {
                    var property=new LinkedHashMap<>(map(entry.getValue()));
                    if(property.get("values") instanceof Map<?,?>) {
                        var values=new LinkedHashMap<>(map(property.get("values")));
                        if(values.containsKey("recipeProducerClass"))values.put("recipeProducerPresent",values.remove("recipeProducerClass")!=null);
                        property.put("values",values);
                    }
                    properties.put(entry.getKey(),property);
                }
                var selected=new LinkedHashMap<>(nativeState);selected.put("properties",properties);state.put("nativePropertyState",selected);
                for(Object rawProperty:properties.values()) {
                    var property=map(rawProperty);
                    if("gregtech.api.unification.material.properties.FluidProperty".equals(property.get("class"))) {
                        fluidMaterials++;
                        if(!NativeFluidObservations.completed(map(property.get("values"))))bindingGaps.add(materialName(material));
                    }
                }
                add(entries,materialName(material),state);
            }
            result.put("materialFluidBindings",Map.of("status","observed","bindingsComplete",bindingGaps.isEmpty(),
                    "materialsWithFluidProperty",fluidMaterials,"affectingGaps",bindingGaps,
                    "stateScope","native-storage-registration-primary-key-and-stored-fluid-registry-identities",
                    "fingerprintedIn","materials"));
            return entries;
        }));
        // NativeMetaItemObservations is already retained by the execution owner.
        // Reuse its exact records; do not double the output or call it again.
        result.put("customItems",Map.of("status","reference","sourcePointer","/execution/customMetaItems"));
        result.put("fluids",observe("native-forge-fluid-registry", "native-fluid-default-scalars-v1",()-> {
            var entries=new Catalog(fluidBudgetBytes);
            for(var entry:((Map<?,?>)callStatic("net.minecraftforge.fluids.FluidRegistry","getRegisteredFluids")).entrySet()) {
                var fluid=entry.getValue();
                add(entries,(String)entry.getKey(),Map.of("class",fluid.getClass().getName(),"name",call(fluid,"getName"),
                        "temperature",call(fluid,"getTemperature"),"density",call(fluid,"getDensity"),"viscosity",call(fluid,"getViscosity"),
                        "luminosity",call(fluid,"getLuminosity"),"gaseous",call(fluid,"isGaseous")));
            }
            return entries;
        }));
        return result;
    }

    @FunctionalInterface interface Inventory {Map<String,String> collect() throws Exception;}
    static Map<String,Object> observe(String membership,String scope,Inventory inventory) {
        try {
            var entries=inventory.collect();
            return Map.of("status","observed","inventoryComplete",true,"membership",membership,"stateScope",scope,"entries",entries);
        } catch(Exception|LinkageError failure) {
            return Map.of("status","unavailable","inventoryComplete",false,"membership",membership,"stateScope",scope,
                    "failure",Map.of("type",failure.getClass().getName(),"message",String.valueOf(failure.getMessage()),
                            "causes",NativeProgramObservations.trace(failure)));
        }
    }
    static final class Catalog extends TreeMap<String,String> {
        final int budget;
        int bytes=2;
        Catalog() {this(-1);}
        Catalog(int budget) {this.budget=budget;}
    }
    static void add(Catalog entries,String identity,Object state) throws Exception {
        if(identity==null||identity.isEmpty()||entries.budget>=0&&entries.size()>=MAX_ENTRIES||entries.containsKey(identity))
            throw new IllegalStateException("Native effect identity is missing, duplicated, or exceeds bounded inventory");
        var digest=MessageDigest.getInstance("SHA-256");hash(digest,state);
        entries.put(identity,HexFormat.of().formatHex(digest.digest()));
        entries.bytes+=escapedUtf8Length(identity)+64+6;
        if(entries.budget>=0&&entries.bytes>entries.budget)throw new IllegalStateException("Complete native effect inventory exceeds its "+entries.budget+" byte output allocation; no partial inventory returned");
    }
    private static int escapedUtf8Length(String value) {
        int bytes=0;
        // The protocol writer escapes each UTF16 surrogate. Count its actual
        // serialized bytes, rather than undercounting supplementary identities.
        for(char c:value.toCharArray()) {
            if(c=='"'||c=='\\')bytes+=2;
            else if(c<32||Character.isSurrogate(c))bytes+=6;
            else bytes+=c<128?1:c<2048?2:3;
        }
        return bytes;
    }
    // Canonical typed tree encoding; maps sorted, native list order retained.
    // Length-prefixed tokens prevent delimiter collisions without an extra JSON library.
    private static void token(MessageDigest digest,String value) {
        byte[] bytes=value.getBytes(StandardCharsets.UTF_8);
        digest.update(Integer.toString(bytes.length).getBytes(StandardCharsets.US_ASCII));digest.update((byte)':');digest.update(bytes);
    }
    private static void hash(MessageDigest digest,Object value) {
        if(value==null) {token(digest,"null");return;}
        if(value instanceof Map<?,?> values) {
            token(digest,"map");token(digest,Integer.toString(values.size()));
            for(String key:new TreeSet<>(map(values).keySet())) {token(digest,key);hash(digest,values.get(key));}
        } else if(value instanceof Collection<?> values) {
            token(digest,"list");token(digest,Integer.toString(values.size()));for(Object child:values)hash(digest,child);
        } else if(value instanceof String text) {token(digest,"string");token(digest,text);}
        else if(value instanceof Boolean bool) {token(digest,"boolean");token(digest,bool.toString());}
        else if(value instanceof Number number) {token(digest,"number");token(digest,number.toString());}
        else throw new IllegalArgumentException("Unsupported native effect state: "+value.getClass().getName());
    }
    @SuppressWarnings("unchecked") private static Map<String,Object> map(Object value) {return (Map<String,Object>)value;}
}
