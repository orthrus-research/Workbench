package research.orthrus.axiom;

import java.nio.file.*;
import java.util.*;
import javax.tools.ToolProvider;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.io.TempDir;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import static org.junit.jupiter.api.Assertions.*;

class NativeEventsTest {
    @TempDir Path temporary;
    private Map<String, byte[]> compile() throws Exception {
        Path source = temporary.resolve("EventScenarios.java");
        try (var stream = getClass().getResourceAsStream("/events/EventScenarios.java")) {
            Files.write(source, Objects.requireNonNull(stream).readAllBytes());
        }
        assertEquals(0, ToolProvider.getSystemJavaCompiler().run(null,null,null,"--release","25","-cp",
                System.getProperty("axiom.test.runtimeClasspath"),"-d",temporary.toString(),source.toString()));
        Map<String,byte[]> classes=new LinkedHashMap<>();
        try (var files=Files.walk(temporary)) {
            for (Path path:files.filter(p->p.toString().endsWith(".class")).toList())
                classes.put(temporary.relativize(path).toString().replace('/','.').replace(".class",""),Files.readAllBytes(path));
        }
        return classes;
    }

    @ParameterizedTest @ValueSource(strings={"inheritance","cancel","generic","mutation","context-failure",
            "context-success","registration-errors","subscriber-transform","static-and-shutdown","phase","listener-cache","randomized-cache"})
    void selectedNativeEventSemantics(String scenario) throws Exception {
        var space=new NativeEventSpace(compile());
        var type=space.loadClass("fixtureevents.EventScenarios");
        assertInstanceOf(String.class,type.getMethod("run",String.class).invoke(null,scenario));
        assertFalse(space.transformations().isEmpty());
    }

    @Test void classSpacesDoNotShareEventStatics() throws Exception {
        var classes=compile(); var first=new NativeEventSpace(classes); var second=new NativeEventSpace(classes);
        assertNotSame(first.loadClass(NativeEventSpace.PREFIX+"Event"),second.loadClass(NativeEventSpace.PREFIX+"Event"));
        for (var space:List.of(first,second))
            assertEquals("mutator|added|added",space.loadClass("fixtureevents.EventScenarios").getMethod("run",String.class).invoke(null,"mutation"));
    }

    @Test void callerCannotReplaceEngineOrJdkDefinitions() {
        for (String name:List.of("java.lang.String","research.orthrus.axiom.nativeevents.Event","../bad","missingPackage"))
            assertThrows(Failure.class,()->new NativeEventSpace(Map.of(name,new byte[0])));
    }
}
