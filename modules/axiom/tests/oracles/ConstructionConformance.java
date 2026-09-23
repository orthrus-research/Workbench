package research.orthrus.axiom;

import java.nio.file.Path;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;

/** Separate-process comparison driver. Fixture vectors are not pack membership. */
public final class ConstructionConformance {
    private static final int VECTORS = 4096;
    private static int nextId = 4000;
    private static FluidMaterial.Builder builder(String name) {
        return new FluidMaterial.Builder(++nextId, new NativeLocation("gregtech", name));
    }
    private static String letters(int value) {
        StringBuilder result = new StringBuilder();
        do { result.append((char) ('a' + value % 26)); value /= 26; } while (value != 0);
        return result.toString();
    }
    private static Object capture(FluidMaterial m) {
        var result = new TreeMap<String,Object>();
        result.put("id",m.getId()); result.put("name",m.getRegistryName());
        result.put("formula",m.getChemicalFormula()); result.put("color",m.getMaterialRGB());
        result.put("mass",safe(m::getMass)); result.put("protons",safe(m::getProtons));
        result.put("neutrons",safe(m::getNeutrons)); result.put("radioactive",m.isRadioactive());
        result.put("icon",m.getMaterialIconSet().name);
        result.put("components",m.getMaterialComponents().stream().map(s -> List.of(s.material.getRegistryName(),s.amount)).toList());
        var flags = new ArrayList<String>();
        for (var f : MaterialFlags.class.getDeclaredFields()) {
            if (java.lang.reflect.Modifier.isStatic(f.getModifiers()) && f.getType()==MaterialFlag.class) {
                try { var flag=(MaterialFlag)f.get(null); if(m.hasFlag(flag)) flags.add(flag.toString()); }
                catch(ReflectiveOperationException failure) {throw new AssertionError(failure);}
            }
        }
        Collections.sort(flags); result.put("flags",flags);
        var keys = new TreeMap<String,Object>();
        for (var field : PropertyKey.class.getDeclaredFields()) {
            if (!java.lang.reflect.Modifier.isStatic(field.getModifiers()) || field.getType()!=PropertyKey.class) continue;
            try {
                var key=(PropertyKey<?>)field.get(null);
                var p=m.getProperties().getProperty(key);
                if(p==null)continue;
                var values=new TreeMap<String,Object>();
                for (var method : p.getClass().getMethods()) {
                    if(method.getParameterCount()!=0 || !(method.getName().startsWith("get") || method.getName().startsWith("is")))continue;
                    var type=method.getReturnType();
                    if(type==int.class || type==long.class || type==boolean.class || type==float.class || type==double.class)
                        values.put(method.getName(),method.invoke(p));
                }
                if(p instanceof IngotProperty ingot) values.put("macerateInto",String.valueOf(ingot.getMacerateInto()));
                if(p instanceof OreProperty ore) values.put("byproducts",ore.getOreByProducts().stream().map(Object::toString).toList());
                keys.put(key.toString(),values);
            } catch(ReflectiveOperationException failure) {throw new AssertionError(failure);}
        }
        result.put("properties",keys);
        return result;
    }
    @FunctionalInterface private interface Operation {Object get();}
    private static Object safe(Operation action) {
        try {return action.get();}
        catch(Throwable failure) {return List.of(failure.getClass().getName(),String.valueOf(failure.getMessage()));}
    }
    private static String digest(Object object) throws Exception {
        return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(Json.write(object).getBytes(StandardCharsets.UTF_8)));
    }
    public static void main(String[] args) throws Exception {
        NativeRuntime.require();
        // This developer probe runs under the same post-start kernel restrictions
        // before evaluating any selected producer or vector.
        WorkerIsolation.install();
        try(var runtime=RegistryRuntime.open(Path.of(args[0]));var env=FluidEnvironment.isolatedProducer(runtime)) {
            runtime.activeMod("gregtech");runtime.materials().unfreezeRegistries();
            SourceElementProducer.register();
            var producer=new TreeMap<String,Object>();
            for(var state:runtime.materials().getDefaultRegistry().getAllMaterials()) {
                var m=FluidMaterial.require(state);producer.put(m.getRegistryName(),capture(m));
            }
            if(producer.size()!=Integer.parseInt(args[1]))throw new AssertionError("Not all element declarations registered");
            if(runtime.materials().getMaterial("borax")!=null)throw new AssertionError("Unexpected later producer");
            var carbon=ConstructionMaterialCatalog.Carbon;
            var oxygen=ConstructionMaterialCatalog.Oxygen;
            var iron=ConstructionMaterialCatalog.Iron;
            var random=new Random(0x4158494f4dL);
            var trace=new ArrayList<Object>();
            for(int i=0;i<VECTORS;i++) {
                String name="vector_"+letters(i); var b=builder(name);
                int n=random.nextInt(8)-2, burn=random.nextInt(5)*100, mode=i%16;
                Object operation=safe(()->{
                    switch(mode) {
                        case 0 -> b.dust(n,burn);
                        case 1 -> b.dust().ingot(n,burn);
                        case 2 -> b.polymer(n).flags(MaterialFlags.GENERATE_FINE_WIRE);
                        case 3 -> b.components(carbon,1,oxygen,n).colorAverage();
                        case 4 -> b.components(carbon.multiply(n)).components(oxygen,1);
                        case 5 -> b.components(carbon,1,null,n);
                        case 6 -> b.ore(n,2,true).addOreByproducts(iron,carbon).separatedInto(iron).oreSmeltInto(iron);
                        case 7 -> b.fluidPipeProperties(n*100,10,true,true,false,false).itemPipeProperties(n,2);
                        case 8 -> b.toolStats(ToolProperty.Builder.of(n,2,burn,3).enchantment(null,n).build());
                        case 9 -> b.rotorStats(n,2,burn);
                        case 10 -> b.ingot().cableProperties(MaterialVoltages.V[MaterialVoltages.IV],n,3,n%2==0,n*100);
                        case 11 -> b.gem().flags(MaterialFlags.GENERATE_LENS,MaterialFlags.GENERATE_GEAR);
                        case 12 -> b.components(iron,1,carbon,n).flags(MaterialFlags.DISABLE_DECOMPOSITION);
                        case 13 -> b.liquid().blast(n*100);
                        case 14 -> b.wood().fluidPipeProperties(300,1,false);
                        case 15 -> b.dust(n,burn).gem(n,burn);
                        default -> throw new AssertionError();
                    }
                    return "ok";
                });
                Object built=safe(()->capture(b.build()));
                var registered=runtime.materials().getMaterial("gregtech:"+name);
                trace.add(Arrays.asList(operation,built,registered==null?null:registered.getId()));
            }
            runtime.materials().closeRegistries();runtime.materials().freezeRegistries();
            System.out.println(Json.write(Map.of("vectors",VECTORS,"vectorDigest",digest(trace),
                    "producerDeclarations",producer.size(),"producerDigest",digest(producer),
                    "aluminiumColor",ConstructionMaterialCatalog.Aluminium.getMaterialRGB(),
                    "markers",env.markers().getAll().stream().map(Object::toString).sorted().toList(),
                    "phase",runtime.materials().getPhase().name(),"wholePackParity",false,
                    "minecraftLaunched",false,"kernelIsolation",true)));
        }
    }
}
