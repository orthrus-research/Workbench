package research.orthrus.axiom.nativeconstruction;

import java.lang.reflect.*;
import java.nio.file.*;
import java.util.*;
import net.minecraft.enchantment.Enchantment;
import net.minecraft.util.ResourceLocation;
import net.minecraftforge.common.MinecraftForge;
import net.minecraftforge.fluids.*;
import net.minecraftforge.fml.common.*;
import net.minecraftforge.fml.common.eventhandler.*;

/** Explicit native owner/listeners. No mod discovery, generated content or pack registration. */
public final class NativeCatalogProbe {
    public static final class UninitializedCatalog { public static FluidMaterial Later; }
    private static void check(boolean value, String message) { if (!value) throw new AssertionError(message); }
    private static Object field(Object object, String name) throws Exception {
        var field = object.getClass().getDeclaredField(name); field.setAccessible(true); return field.get(object);
    }
    private static Object capture(Object value) throws Exception {
        if (value == null || value instanceof String || value instanceof Number || value instanceof Boolean) return value;
        if (value instanceof Enum<?> e) return e.name();
        if (value instanceof FluidMaterial m) return m.getRegistryName();
        if (value instanceof Fluid f) return f.getName();
        if (value instanceof Enchantment e) return e.getRegistryName().toString();
        if (value instanceof ResourceLocation || value instanceof FluidStorageKey || value instanceof FluidAttribute || value instanceof MaterialFlag || value instanceof PropertyKey<?>) return value.toString();
        if (value instanceof Map<?,?> m) {
            var result = new TreeMap<String,Object>();
            for (var entry : m.entrySet()) {
                String key = Objects.toString(capture(entry.getKey()));
                check(!result.containsKey(key), "unique captured map key"); result.put(key, capture(entry.getValue()));
            }
            return result;
        }
        if (value instanceof Collection<?> c) {
            var result = new ArrayList<Object>(); for (Object item : c) result.add(capture(item));
            if (value instanceof Set<?>) result.sort(Comparator.comparing(Objects::toString));
            return result;
        }
        if (value.getClass().isArray()) {
            var result = new ArrayList<Object>(); for (int i=0; i<Array.getLength(value); i++) result.add(capture(Array.get(value,i))); return result;
        }
        if (!value.getClass().getPackageName().equals(NativeCatalogProbe.class.getPackageName()))
            throw new AssertionError("Unqualified state capture: " + value.getClass());
        var result = new TreeMap<String,Object>();
        for (Class<?> type=value.getClass(); type!=Object.class; type=type.getSuperclass())
            for (var f : type.getDeclaredFields()) {
                if (Modifier.isStatic(f.getModifiers())) continue;
                // Host dependency callbacks are not material state. Their reached algorithms
                // are separately source-qualified; never serialize lambda identity as evidence.
                if (value instanceof FluidRegistration<?,?> && Set.of("defaultBuilder", "collision").contains(f.getName())) continue;
                f.setAccessible(true); check(!result.containsKey(f.getName()), "unique captured field"); result.put(f.getName(), capture(f.get(value)));
            }
        return result;
    }
    public static final class Observer {
        final String failure;
        final List<Object> phases = new ArrayList<>();
        final List<String> generic = new ArrayList<>();
        Observer(String failure) { this.failure=failure; }
        private void reached(String phase) {
            var manager = FluidEnvironment.current().runtime().materials();
            check(manager.getPhase().name().equals(phase), "native material event phase " + phase);
            phases.add(List.of(phase, manager.getDefaultRegistry().getAllMaterials().size()));
            if (failure.equals(phase)) throw new IllegalStateException("catalog-listener-" + phase);
        }
        @SubscribeEvent public void registry(MaterialRegistryEvent event) {
            check(!FluidEnvironment.current().markers().getAll().isEmpty(), "markers precede registry event");
            FluidEnvironment.current().runtime().materials().createRegistry("fixture"); reached("PRE");
        }
        @SubscribeEvent public void material(MaterialEvent event) {
            check(FluidEnvironment.current().runtime().materials().getDefaultFallback() == SourceMaterialCatalog.Aluminium,
                    "actual fallback installed before addon material event"); reached("OPEN");
        }
        @SubscribeEvent public void post(PostMaterialEvent event) {
            var manager = FluidEnvironment.current().runtime().materials();
            int before=manager.getDefaultRegistry().getAllMaterials().size();
            // Actual closed-registry behavior logs and skips; it does not throw.
            manager.getDefaultRegistry().register(32000,"late_fixture",SourceMaterialCatalog.Aluminium);
            check(manager.getDefaultRegistry().getAllMaterials().size()==before && manager.getDefaultRegistry().getObject("late_fixture")==null,"closed registration skipped");
            check(manager.canModifyMaterials(),"post-material property mutation is allowed before freeze");
            reached("CLOSED");
        }
        @SubscribeEvent public void materialGeneric(GenericEvent<FluidMaterial> event) { generic.add(event.getClass().getSimpleName()); }
        @SubscribeEvent public void registryGeneric(GenericEvent<MaterialRegistry> event) { generic.add(event.getClass().getSimpleName()); }
    }
    private static Map<String,Object> snapshot(FluidEnvironment env, CatalogInputs inputs) throws Exception {
        var materials = new TreeMap<String,Object>(); var blocks = new ArrayList<String>();
        for (var state : env.runtime().materials().getRegisteredMaterials()) {
            var m = FluidMaterial.require(state); var row = new TreeMap<String,Object>();
            row.put("id",m.getId()); row.put("color",m.getMaterialRGB()); row.put("icon",m.getMaterialIconSet().toString());
            row.put("formula",m.getChemicalFormula()); row.put("components",capture(m.getMaterialComponents()));
            row.put("flags",capture(field(m,"flags"))); row.put("properties",capture(field(m.getProperties(),"propertyMap")));
            materials.put(m.getRegistryName(),row);
            if (m.hasProperty(FluidDomain.FLUID)) for (var key : List.of(env.storageKeys().LIQUID,env.storageKeys().GAS,env.storageKeys().PLASMA)) {
                var builder = m.getProperty(FluidDomain.FLUID).getQueuedBuilder(key);
                if (builder != null && (boolean)field(builder,"hasFluidBlock")) blocks.add(m.getRegistryName());
            }
        }
        Collections.sort(blocks);
        check(blocks.equals(List.of("gregtech:natural_gas","gregtech:oil","gregtech:oil_heavy","gregtech:oil_light","gregtech:oil_medium")), "all five deferred world blocks retained");
        var prefixes = new TreeMap<String,Object>();
        for (var prefix : OrePrefix.values()) {
            var rows = new TreeMap<String,Object>();
            for (var state : env.runtime().materials().getRegisteredMaterials()) {
                var m=FluidMaterial.require(state); rows.put(m.getRegistryName(),List.of(prefix.doGenerateItem(m),prefix.isIgnored(m),prefix.getMaterialAmount(m)));
            }
            check(((Collection<?>)field(prefix,"generatedMaterials")).isEmpty(), "no ore registration processed");
            prefixes.put(prefix.name,Arrays.asList(capture(prefix.materialType),capture(prefix.secondaryMaterials),prefix.maxStackSize,rows));
        }
        var fields = new TreeMap<String,Object>();
        for (var f : SourceMaterialCatalog.class.getFields()) if (Modifier.isStatic(f.getModifiers()) && f.getType()==FluidMaterial.class) fields.put(f.getName(),capture(f.get(null)));
        var markers = new TreeSet<String>(); for (var marker : env.markers().getAll()) markers.add(marker.getRegistryName());
        return Map.of("materials",materials,"prefixes",prefixes,"markers",new ArrayList<>(markers),"catalogFields",fields,
                "dyes",capture(SourceMaterialCatalog.CHEMICAL_DYES),"deferredWorldBlocks",blocks,"liveCatalogReads",inputs.reads());
    }
    public static Object run(String request) throws Exception {
        var properties = new Properties(); try (var reader=Files.newBufferedReader(Path.of(request))) { properties.load(reader); }
        // Configuration's original constructor reads the launch-supplied home solely
        // to relativize the configuration filename. Do not invoke global early config discovery.
        var injectionHome = net.minecraftforge.fml.relauncher.FMLInjectionData.class.getDeclaredField("minecraftHome");
        injectionHome.setAccessible(true); check(injectionHome.get(null)==null,"fresh explicit configuration home");
        injectionHome.set(null,Path.of(request).getParent().toFile());
        var configuration = CatalogConfiguration.load(Path.of(properties.getProperty("config")),SourceCatalogConfiguration.class);
        var loader = Loader.instance(); var controller=Loader.class.getDeclaredField("modController"); controller.setAccessible(true);
        Object previous=controller.get(loader); check(previous==null,"no initialized mod loader"); controller.set(loader,new LoadController(loader));
        var metadata=new ModMetadata(); metadata.modId="gregtech"; metadata.name="gregtech"; loader.setActiveModContainer(new DummyModContainer(metadata));
        var observer=new Observer(properties.getProperty("failure","none"));
        try (var env=new FluidEnvironment()) {
            var coldInputs=new CatalogInputs(UninitializedCatalog.class,configuration);
            check(coldInputs.material("Later")==null,"declared but unassigned field remains null");
            var inputs=new CatalogInputs(SourceMaterialCatalog.class,configuration); env.bindCatalog(inputs);
            try { inputs.material("NoSuchMaterial"); throw new AssertionError("missing catalog field became null"); } catch (Failure expected) { }
            try { env.bindCatalog(inputs); throw new AssertionError("catalog rebound"); } catch (IllegalStateException expected) { }
            MinecraftForge.EVENT_BUS.register(observer);
            var lifecycle=new MaterialLifecycle(env.runtime(),new MaterialEvents(),new MaterialLifecycle.Dependencies() {
                public void initializeMarkers() { MarkerMaterials.register(); }
                public void registerMaterials() { SourceMaterialCatalog.register(); }
                public FluidMaterial aluminium() { return SourceMaterialCatalog.Aluminium; }
            });
            boolean failed=false;
            try { lifecycle.execute(); } catch (ExceptionInInitializerError failure) {
                if (!properties.getProperty("localeFailure","false").equals("true")) throw failure;
                check(failure.getCause() instanceof IllegalArgumentException && failure.getCause().getMessage().contains("Cannot add materials with names"),"original locale-sensitive marker rejection");
                check(env.runtime().materials().getPhase()==MaterialPhase.PRE && observer.phases.isEmpty(),"marker failure precedes first material event");
                try { lifecycle.execute(); throw new AssertionError("failed marker lifecycle retried"); }
                catch (IllegalStateException expected) { check(expected.getMessage().contains("one fresh"),"failed initialization stays one-shot"); }
                return Map.of("phase","PRE","materialCount",0,"markersBeforeFailure",capture(env.markers().getAll()),
                        "failure",List.of(failure.getClass().getName(),failure.getCause().getClass().getName(),failure.getCause().getMessage()),"phases",observer.phases);
            } catch (IllegalStateException failure) {
                if (!failure.getMessage().equals("catalog-listener-"+observer.failure)) throw failure; failed=true;
            }
            check(failed==!observer.failure.equals("none"),"expected listener outcome");
            try { lifecycle.execute(); throw new AssertionError("material lifecycle retried"); } catch (IllegalStateException expected) { check(expected.getMessage().contains("one fresh"),"one-shot lifecycle guard"); }
            check(FluidRegistry.getRegisteredFluids().keySet().equals(Set.of("water","lava")),"catalog does not register fluids");
            var result=new TreeMap<String,Object>();
            result.put("configuration",configuration.evidence()); result.put("phases",observer.phases); result.put("genericEvents",observer.generic);
            result.put("phase",env.runtime().materials().getPhase().name()); result.put("listenerFailed",failed);
            result.put("materialCount",env.runtime().materials().getDefaultRegistry().getAllMaterials().size());
            if (!failed) {
                check(observer.phases.equals(List.of(List.of("PRE",0),List.of("OPEN",602),List.of("CLOSED",602))),"complete catalog phase order");
                check(observer.generic.equals(List.of("MaterialRegistryEvent","MaterialEvent","PostMaterialEvent")),"original native generic filtering and event inheritance");
                check(new MaterialRegistryEvent().getListenerList()!=new MaterialEvent().getListenerList() && new MaterialEvent().getListenerList()!=new PostMaterialEvent().getListenerList(),"distinct transformed native listener lists");
                int before=env.runtime().materials().getDefaultRegistry().getAllMaterials().size(); SourceMaterialCatalog.register();
                check(before==env.runtime().materials().getDefaultRegistry().getAllMaterials().size(),"original catalog registration guard");
                check(env.runtime().materials().getPhase()==MaterialPhase.FROZEN,"frozen material checkpoint");
                check(!env.runtime().materials().canModifyMaterials(),"frozen mutation boundary");
                try { SourceMaterialCatalog.Aluminium.setProperty(PropertyKey.DUST,new DustProperty()); throw new AssertionError("frozen property addition admitted"); }
                catch (IllegalStateException expected) { check(expected.getMessage().contains("registry is frozen"),"original frozen property guard"); }
                UninitializedCatalog.Later=SourceMaterialCatalog.Aluminium;
                check(coldInputs.material("Later")==SourceMaterialCatalog.Aluminium,"live binding observes later assignment without copying material");
                result.put("snapshot",snapshot(env,inputs));
            }
            return result;
        } finally { MinecraftForge.EVENT_BUS.unregister(observer); controller.set(loader,previous); }
    }
}
