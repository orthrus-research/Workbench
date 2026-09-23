package research.orthrus.axiom;

import org.junit.jupiter.api.*;
import org.junit.jupiter.api.io.TempDir;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import javax.tools.ToolProvider;
import java.nio.file.*;
import java.util.*;
import java.util.function.Consumer;
import static org.junit.jupiter.api.Assertions.*;

class MaterialLifecycleTest {
    @TempDir Path temporary;
    RegistryRuntime runtime;
    MaterialEvents events;
    MaterialState aluminium;
    List<String> trace=new ArrayList<>();
    String failAt="";
    @BeforeEach void setup() throws Exception {
        String root=System.getProperty("axiom.test.registryRoot");
        Assumptions.assumeTrue(root!=null,"Use tools/build_axiom.py for pinned registry inputs");
        runtime=RegistryRuntime.open(Path.of(root));runtime.activeMod("gregtech");
        Path source=temporary.resolve("MaterialListeners.java");
        try(var input=getClass().getResourceAsStream("/events/MaterialListeners.java")) {Files.write(source,input.readAllBytes());}
        assertEquals(0,ToolProvider.getSystemJavaCompiler().run(null,null,null,"--release","25","-cp",
                System.getProperty("axiom.test.runtimeClasspath"),"-d",temporary.toString(),source.toString()));
        byte[] code=Files.readAllBytes(temporary.resolve("fixtureevents/MaterialListeners.class"));
        events=new MaterialEvents(Map.of("fixtureevents.MaterialListeners",code));events.owner("gregtech","GregTech");
        var listener=events.space.loadClass("fixtureevents.MaterialListeners").getConstructor(Consumer.class)
                .newInstance((Consumer<String>)this::effect);
        events.register(listener);
    }
    @AfterEach void close() throws Exception {if(runtime!=null)runtime.close();}
    void effect(String stage) {
        trace.add(stage+":"+runtime.materials().getPhase());
        if(stage.equals(failAt))throw new IllegalArgumentException("fail:"+stage);
        switch(stage) {
            case "registry" -> runtime.materials().createRegistry("susy");
            case "base" -> {
                aluminium=new MaterialState(13,"gregtech","aluminium",runtime.materials()::canModifyMaterials);
                aluminium.setProperty(PropertyKey.DUST,new DustProperty());
                runtime.materials().getRegistry("gregtech").register(aluminium);
            }
            case "material-high" -> assertSame(aluminium,runtime.materials().getDefaultFallback());
            case "post-high" -> {
                assertEquals(List.of(aluminium),new ArrayList<>(runtime.materials().getRegisteredMaterials()));
                aluminium.setProperty(PropertyKey.INGOT,new IngotProperty());
            }
        }
    }
    MaterialLifecycle lifecycle() {
        return new MaterialLifecycle(runtime,events,new MaterialLifecycle.Dependencies() {
            public void initializeMarkers(){effect("markers");}
            public void registerMaterials(){effect("base");}
            public MaterialState aluminium(){effect("fallback");return aluminium;}
        });
    }
    @Test void realEventDispatchWrapsNativeRegistryTransitions() {
        var lifecycle=lifecycle();lifecycle.execute();
        assertEquals(List.of("markers:PRE","registry:PRE","base:OPEN","fallback:OPEN","material-high:OPEN",
                "material-low:OPEN","post-high:CLOSED","post-normal:CLOSED"),trace);
        assertEquals(MaterialPhase.FROZEN,runtime.materials().getPhase());
        assertTrue(aluminium.getProperties().hasProperty(PropertyKey.INGOT));
        assertThrows(IllegalStateException.class,()->aluminium.setProperty(PropertyKey.GEM,new GemProperty()));
        assertThrows(IllegalStateException.class,lifecycle::execute);
    }
    @ParameterizedTest @ValueSource(strings={"markers","registry","base","fallback","material-high","material-low","post-high","post-normal"})
    void failuresKeepPriorEffectsAndDoNotAdvanceOrRetry(String failure) {
        failAt=failure;var lifecycle=lifecycle();
        assertEquals("fail:"+failure,assertThrows(IllegalArgumentException.class,lifecycle::execute).getMessage());
        MaterialPhase expected=switch(failure) {
            case "markers","registry"->MaterialPhase.PRE;
            case "post-high","post-normal"->MaterialPhase.CLOSED;
            default->MaterialPhase.OPEN;
        };
        assertEquals(expected,runtime.materials().getPhase());
        assertTrue(trace.getLast().startsWith(failure+":"));
        if(Set.of("fallback","material-high","material-low","post-high","post-normal").contains(failure))
            assertSame(aluminium,runtime.materials().getMaterial("aluminium"));
        assertThrows(IllegalStateException.class,lifecycle::execute);
    }
    @Test void freshMaterialEventsHaveTheActualGenericTypeAndIndependentLists() throws Exception {
        var material=events.construct("research.orthrus.axiom.materialevents.MaterialEvent");
        var post=events.construct("research.orthrus.axiom.materialevents.PostMaterialEvent");
        assertSame(MaterialState.class,material.getClass().getMethod("getGenericType").invoke(material));
        assertNotSame(material.getClass().getMethod("getListenerList").invoke(material),
                post.getClass().getMethod("getListenerList").invoke(post));
    }
}
