package research.orthrus.axiom;

import java.lang.reflect.*;
import java.nio.file.*;
import java.util.*;

/** Source comparison over a deliberately isolated producer universe, not a game oracle. */
public final class FluidConformance {
    private static Object call(Object value,String name,Class<?>[] types,Object... args) throws Throwable {
        try { return value.getClass().getMethod(name,types).invoke(value,args); }
        catch (InvocationTargetException failure) { throw failure.getCause(); }
    }
    private static String outcome(Throwable failure) {
        return failure.getClass().getName() + ":" + failure.getMessage();
    }
    private static Map<String,Object> fluid(Object value) throws Throwable {
        if (value == null) return Map.of("missing",true);
        var result = new TreeMap<String,Object>();
        for (String name : List.of("getName","getTranslationKey","getUnlocalizedName","getLuminosity",
                "getDensity","getTemperature","getViscosity","isGaseous","getColor","getStill","getFlowing","getOverlay")) {
            Object got = call(value,name,new Class<?>[0]);
            result.put(name,got instanceof NativeLocation ? got.toString() : got);
        }
        if (value instanceof GTFluid gt) {
            result.put("state",gt.getState().name());
            result.put("attributes",gt.getAttributes().stream().map(Object::toString).toList());
        }
        return result;
    }
    private static List<Object> builders(Path root,Class<?> type) throws Throwable {
        var results = new ArrayList<Object>();
        try (var runtime = RegistryRuntime.open(root); var env = FluidEnvironment.isolatedProducer(runtime)) {
            runtime.materials().createRegistry("susy"); runtime.activeMod("gregtech");
            runtime.materials().unfreezeRegistries();
            var random = new Random(0x4f727468727573L);
            double[] doubles = {0.0,-0.0,Double.NaN,Double.NEGATIVE_INFINITY,Double.POSITIVE_INFINITY,
                    0.001225,Math.nextDown(0.001225),Math.nextUp(0.001225),-0.1,0.1,1,Double.MIN_VALUE,Double.MAX_VALUE};
            for (int i=0;i<4096;i++) {
                int effectStart = env.effects().size();
                var builder = type.getDeclaredConstructor().newInstance();
                int flags=random.nextInt(16);
                var declaration = new FluidMaterial.Builder(i+1,new NativeLocation("susy","material_"+i)).liquid().color(random.nextInt());
                if ((flags&1)!=0) declaration.dust();
                if ((flags&2)!=0) declaration.flags(MaterialFlags.STICKY);
                if ((flags&4)!=0) declaration.flags(MaterialFlags.GLOWING);
                var material = declaration.build();
                if ((flags&8)!=0) material.setProperty(FluidDomain.BLAST,new BlastProperty(Integer.MAX_VALUE-i));
                FluidStorageKey key = switch(i%3) {case 0 -> env.storageKeys().LIQUID;case 1 -> env.storageKeys().GAS;default -> env.storageKeys().PLASMA;};
                if (i%5==0) {
                    var foreign = new NativeFluid("foreign_"+i,new NativeLocation("foreign","still"),new NativeLocation("foreign","flow"));
                    env.registry().registerFluid(foreign);
                    call(builder,"alternativeName",new Class<?>[]{String.class},foreign.getName());
                }
                var record = new TreeMap<String,Object>();
                try {
                    if (i%7==0) call(builder,"name",new Class<?>[]{String.class},"Explicit_"+i);
                    if (i%2==0) call(builder,"state",new Class<?>[]{FluidState.class},FluidState.values()[i%3]);
                    if (i%11==0) call(builder,"temperature",new Class<?>[]{int.class},i%22==0?0:1+random.nextInt(10000));
                    if (i%13==0) call(builder,"color",new Class<?>[]{int.class},i%26==0?-1:random.nextInt());
                    if (i%3==0) call(builder,"density",new Class<?>[]{double.class},doubles[i%doubles.length]);
                    if (i%4==0) call(builder,"viscosity",new Class<?>[]{double.class},doubles[(i/4)%doubles.length]);
                    if (i%17==0) call(builder,"luminosity",new Class<?>[]{int.class},i%32);
                    if (i%19==0) call(builder,"customStill",new Class<?>[]{});
                    if (i%23==0) call(builder,"customFlow",new Class<?>[]{});
                    if (i%29==0) call(builder,"disableBucket",new Class<?>[]{});
                    if (i%31==0) call(builder,"block",new Class<?>[]{});
                    var attribute = new FluidAttribute(new NativeLocation("susy","attribute"),lines -> {throw new AssertionError("eager tooltip");},lines -> {});
                    if (i%37==0) call(builder,"attribute",new Class<?>[]{FluidAttribute.class},attribute);
                    Object value = call(builder,"build",new Class<?>[]{String.class,MaterialState.class,FluidStorageKey.class},
                            "susy",i%41==0?null:material,i%41==0?null:key);
                    record.put("first",fluid(value));
                    if (i%43==0) {
                        Object second = call(builder,"build",new Class<?>[]{String.class,MaterialState.class,FluidStorageKey.class},"susy",material,key);
                        record.put("reuse",fluid(second)); record.put("sameObject",second==value);
                    }
                } catch (Throwable failure) { record.put("failure",outcome(failure)); }
                // Capture prior effects even when build throws: no rollback invented.
                var names = List.of("material_"+i,"gas.material_"+i,"plasma.material_"+i,"explicit_"+i,"foreign_"+i);
                var entries = new TreeMap<String,Object>();
                for (String name:names) {
                    var found=env.registry().getFluid(name);
                    if (found==null) continue;
                    var row=new TreeMap<String,Object>();
                    row.put("fluid",fluid(found));
                    row.put("owner",env.registry().getDefaultFluidName(found));
                    row.put("bucket",env.registry().hasBucket(found));
                    row.put("tooltipBindings",env.tooltipBindings(found).size());
                    var bound=env.unifier().getMaterialFromFluid(found);
                    row.put("material",bound==null?null:bound.getResourceLocation().toString());
                    entries.put(name,row);
                }
                record.put("registry",entries); record.put("maxID",env.registry().getMaxID());
                record.put("effects",env.effects().stream().skip(effectStart).map(effect -> {
                    if (effect instanceof FluidEnvironment.Registration event) return "event:"+event.name()+":"+event.id();
                    if (effect instanceof FluidEnvironment.Tooltip tooltip) return "tooltip:"+tooltip.fluid().getName();
                    var log=(FluidEnvironment.Log)effect; return log.level()+":"+log.template()+":"+log.arguments();
                }).toList());
                results.add(record);
            }
        }
        return results;
    }
    private static List<Object> scalars(Path root,Class<?> type) throws Throwable {
        var results=new ArrayList<Object>();
        try (var runtime=RegistryRuntime.open(root); var env=FluidEnvironment.isolatedProducer(runtime)) {
            var random=new Random(0x4178696f6dL);
            var constructor=type.getDeclaredConstructor(String.class,NativeLocation.class,NativeLocation.class);
            for (int i=0;i<4096;i++) {
                Object value=constructor.newInstance("Fluid_"+i,new NativeLocation("susy","still"),new NativeLocation("susy","flow"));
                for(String method:List.of("setTemperature","setDensity","setViscosity","setLuminosity","setColor"))
                    if(random.nextBoolean()) call(value,method,new Class<?>[]{int.class},random.nextInt());
                if(random.nextBoolean()) call(value,"setGaseous",new Class<?>[]{boolean.class},random.nextBoolean());
                if(random.nextBoolean()) call(value,"setTranslationKey",new Class<?>[]{String.class},"translation_"+i);
                results.add(fluid(value));
            }
        }
        return results;
    }
    private static void equal(List<Object> actual,List<Object> original,String label) {
        if(actual.size()!=original.size()) throw new AssertionError(label+" sizes differ");
        for(int i=0;i<actual.size();i++) if(!actual.get(i).equals(original.get(i)))
            throw new AssertionError(label+" scenario "+i+" differs\n"+actual.get(i)+"\n"+original.get(i));
    }
    public static void main(String[] args) throws Throwable {
        NativeRuntime.require();
        var root=Path.of(args[0]);
        equal(builders(root,FluidBuilder.class),builders(root,OriginalFluidBuilder.class),"builder");
        equal(scalars(root,NativeFluid.class),scalars(root,OriginalNativeFluid.class),"Forge scalar");
        List<Object> producers=new ArrayList<>();
        try(var runtime=RegistryRuntime.open(root);var env=FluidEnvironment.isolatedProducer(runtime)) {
            runtime.materials().createRegistry("susy"); runtime.activeMod("gregtech");
            runtime.materials().unfreezeRegistries();
            LockedSusyProducer.init();
            if(LockedSusyProducer.values().size()!=10) throw new AssertionError("producer declarations missing");
            for(var value:LockedSusyProducer.values()) if(value.getFluid()!=null) throw new AssertionError("fluid created before phase");
            runtime.materials().closeRegistries(); runtime.materials().freezeRegistries(); env.registerMaterialFluids();
            for(var value:LockedSusyProducer.values()) {
                var fluid=value.getFluid();
                if(fluid==null || env.registry().getFluid(value.getName())!=fluid
                        || runtime.materials().getMaterial("susy:"+value.getName())!=value
                        || env.unifier().getMaterialFromFluid(fluid)!=value
                        || !env.registry().hasBucket(fluid))
                    throw new AssertionError("producer identity chain differs: "+value);
                producers.add(Map.of("material",value.getResourceLocation().toString(),"id",value.getId(),"fluid",fluid(fluid)));
            }
            var coolant=LockedSusyProducer.Coolant.getFluid();
            var advanced=LockedSusyProducer.AdvancedCoolant.getFluid();
            if(coolant.getTemperature()!=293 || coolant.getColor()!=0xff46dde8
                    || advanced.getTemperature()!=293 || advanced.getColor()!=0xff33f5ee)
                throw new AssertionError("locked coolant declarations differ");
        }
        System.out.println(Json.write(Map.of("execution","native-jvm","builderScenarios",4096,
                "scalarScenarios",4096,"producerDeclarations",10,"producerResults",producers,
                "wholePackParity",false,"packEventsExecuted",false)));
    }
}
