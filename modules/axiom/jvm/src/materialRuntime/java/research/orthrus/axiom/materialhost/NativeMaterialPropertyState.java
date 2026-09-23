package research.orthrus.axiom.materialhost;

import static research.orthrus.axiom.materialhost.NativeObservationAccess.*;
import java.lang.reflect.*;
import java.util.*;

/** Passive state of properties already attached by native code. Does not create
 * property keys, initialize addon catalogs, call verification or generate fluids.
 */
public final class NativeMaterialPropertyState {
    private NativeMaterialPropertyState() {}

    private static Object field(Class<?> owner,Object receiver,String name) throws ReflectiveOperationException {
        return NativeObservationAccess.field(owner,receiver,name);
    }
    private static Map<String,Object> fields(Object receiver,Class<?> owner,String... names) throws ReflectiveOperationException {
        var result=new LinkedHashMap<String,Object>();
        for(String name:names) {
            Object value=field(owner,receiver,name);
            if(value instanceof Double number) {
                // Preserve exact bits, including non-finite values. Native
                // setters do not all validate their input; do not invent rules.
                result.put(name,Map.of("type","float64","value",Double.isFinite(number)?number:Double.toString(number),
                        "rawBits",HexFormat.of().toHexDigits(Double.doubleToRawLongBits(number))));
            } else result.put(name,value);
        }
        return result;
    }
    public static Map<String,Object> observe(Object material) throws ReflectiveOperationException {
        var properties=new TreeMap<String,Object>();
        var attached=(Map<?,?>)field(type("gregtech.api.unification.material.properties.MaterialProperties"),call(material,"getProperties"),"propertyMap");
        for(var entry:attached.entrySet()) {
            Object value=entry.getValue();
            if(value==null) {
                // PropertyKey.constructDefault returns null when no no-arg
                // constructor exists. Keep the native failed assignment rather
                // than losing its original verification error in this observer.
                var row=new LinkedHashMap<String,Object>();row.put("class",null);row.put("valuesObserved",false);
                properties.put(entry.getKey().toString(),row);continue;
            }
            Class<?> type=value.getClass();
            var row=new LinkedHashMap<String,Object>();row.put("class",type.getName());
            Map<String,Object> values=switch(type.getName()) {
                case "gregtech.api.unification.material.properties.FluidProperty" -> NativeFluidObservations.storage(value);
                case "gregtech.api.unification.material.properties.DustProperty" ->
                    fields(value,type,"harvestLevel","burnTime");
                case "gregtech.api.unification.material.properties.BlastProperty" -> {
                    var state=fields(value,type,"blastTemperature","eutOverride","durationOverride",
                            "vacuumEUtOverride","vacuumDurationOverride");
                    var tier=(Enum<?>)field(type,value,"gasTier");
                    state.put("gasTier",tier==null?null:tier.name());yield state;
                }
                case "gregtech.api.unification.material.properties.WireProperties" ->
                    fields(value,type,"voltage","amperage","lossPerBlock","superconductorCriticalTemperature","isSuperconductor");
                case "gregtech.api.unification.material.properties.OreProperty" -> {
                    var state=fields(value,type,"oreMultiplier","byProductMultiplier","emissive","washedAmount");
                    var products=new ArrayList<Object>();
                    for(Object materialValue:(List<?>)field(type,value,"oreByProducts"))
                        products.add(materialValue==null?null:materialName(materialValue));
                    state.put("oreByProducts",products);
                    for(String name:List.of("directSmeltResult","washedIn")) {
                        var linked=field(type,value,name);
                        state.put(name,linked==null?null:materialName(linked));
                    }
                    yield state;
                }
                case "gregtech.api.unification.material.properties.FluidPipeProperties" -> {
                    var state=fields(value,type,"throughput","tanks","maxFluidTemperature","gasProof","cryoProof","plasmaProof");
                    var containment=new TreeMap<String,Object>();
                    var predicates=(Map<?,?>)field(type,value,"containmentPredicate");
                    for(var predicate:predicates.entrySet()) {
                        var attribute=predicate.getKey();
                        containment.put(call(attribute,"getResourceLocation").toString(),predicate.getValue());
                    }
                    // Preserve explicit false entries; key membership alone is
                    // not the native containment predicate's boolean result.
                    state.put("containmentPredicate",containment);yield state;
                }
                case "supersymmetry.api.unification.material.properties.FiberProperty" ->
                    fields(value,type,"solutionSpun","meltSpun","weaving");
                case "supersymmetry.api.unification.material.properties.MillBallProperty" ->
                    fields(value,type,"durability");
                case "supersymmetry.api.unification.material.properties.DummyABSProperty",
                        "gregicality.multiblocks.api.unification.properties.AlloyBlastProperty" -> {
                    Class<?> parent=type.getName().endsWith("DummyABSProperty")?type.getSuperclass():type;
                    var state=fields(value,parent,"temperature","canGenerateMolten","forceGenerateMolten");
                    Object producer=field(parent,value,"recipeProducer");
                    state.put("recipeProducerClass",producer==null?null:producer.getClass().getName());
                    yield state;
                }
                case "supercritical.api.unification.material.properties.FissionFuelProperty" -> {
                    var state=fields(value,type,"maxTemperature","duration","slowNeutronCaptureCrossSection",
                            "fastNeutronCaptureCrossSection","slowNeutronFissionCrossSection","fastNeutronFissionCrossSection",
                            "neutronGenerationTime","releasedNeutrons","requiredNeutrons","releasedHeatEnergy","decayRate","id");
                    // Presence is not execution or qualification of later item
                    // callbacks. Never call the supplier getters from reporting.
                    state.put("depletedFuelSupplierPresent",field(type,value,"depletedFuelSupplier")!=null);
                    state.put("allDepletedFuelsPresent",field(type,value,"allDepletedFuels")!=null);
                    yield state;
                }
                case "supercritical.api.unification.material.properties.ModeratorProperty" ->
                    fields(value,type,"maxTemperature","moderationFactor","absorptionFactor");
                case "supercritical.api.unification.material.properties.CoolantProperty" -> {
                    var state=fields(value,type,"moderatorFactor","coolingFactor","boilingPoint","heatOfVaporization",
                            "specificHeatCapacity","accumulatesHydrogen","slowAbsorptionFactor","fastAbsorptionFactor","mass");
                    var hot=field(type,value,"hotHPCoolant");
                    state.put("hotMaterial",hot==null?null:materialName(hot));
                    var key=field(type,value,"key");
                    state.put("storageKey",key==null?null:call(key,"getResourceLocation").toString());
                    yield state;
                }
                default -> null;
            };
            row.put("valuesObserved",values!=null);
            if(values!=null)row.put("values",values);
            properties.put(entry.getKey().toString(),row);
        }
        Object flags=field(type("gregtech.api.unification.material.Material"),material,"flags");
        var names=((Set<?>)field(type("gregtech.api.unification.material.info.MaterialFlags"),flags,"flags")).stream().map(Object::toString).sorted().toList();
        return Map.of("schema","axiom.native-material-property-state.v1","properties",properties,"flags",names,
                "scope","attached-native-properties-and-flags-selected-field-values",
                "verificationInvokedByObserver",false,"recipeProducerInvokedByObserver",false);
    }
}
