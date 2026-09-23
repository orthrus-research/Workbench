package research.orthrus.axiom.materialtest;

import com.cleanroommc.groovyscript.sandbox.CustomGroovyScriptEngine;
import org.apache.logging.log4j.*;
import org.apache.logging.log4j.core.LogEvent;
import org.apache.logging.log4j.core.appender.AbstractAppender;
import org.apache.logging.log4j.core.config.Property;
import java.lang.reflect.*;
import java.io.*;
import java.util.*;

/** Read-only native observations. Neither listener order nor source is rewritten. */
public final class GroovyNativeObservations extends AbstractAppender implements AutoCloseable {
    private final List<Map<String,Object>> messages=new ArrayList<>();
    private final org.apache.logging.log4j.core.Logger root;
    public GroovyNativeObservations() {
        super("axiom-native-observation",null,null,true,Property.EMPTY_ARRAY);
        root=(org.apache.logging.log4j.core.Logger)LogManager.getRootLogger();
        start();root.addAppender(this);
    }
    @Override public synchronized void append(LogEvent event) {
        if(!event.getLevel().isMoreSpecificThan(Level.WARN)) return;
        messages.add(Map.of("logger",event.getLoggerName(),"level",event.getLevel().name(),
                "message",event.getMessage().getFormattedMessage(),"cause",trace(event.getThrown())));
    }
    public synchronized List<Map<String,Object>> messages() {return List.copyOf(messages);}
    @Override public void close() {root.removeAppender(this);stop();}
    public static String trace(Throwable failure) {
        if(failure==null) return "";
        var out=new StringWriter();failure.printStackTrace(new PrintWriter(out));return out.toString();
    }
    private static Object field(Class<?> type,Object object,String name) throws Exception {
        Field field=type.getDeclaredField(name);field.setAccessible(true);return field.get(object);
    }
    public static List<Map<String,Object>> scriptIndex(CustomGroovyScriptEngine engine) throws Exception {
        var index=(Map<?,?>)field(CustomGroovyScriptEngine.class,engine,"index");
        var result=new ArrayList<Map<String,Object>>();
        for(var entry:index.entrySet()) {
            Object script=entry.getValue();Class<?> type=script.getClass();
            var preprocessors=field(type,script,"preprocessors");
            result.add(Map.of("path",entry.getKey(),"preprocessors",preprocessors==null?List.of():List.copyOf((List<?>)preprocessors),
                    "preprocessorCheckFailed",field(type,script,"preprocessorCheckFailed"),
                    "classDefined",field(type.getSuperclass(),script,"clazz")!=null));
        }
        // Native iteration is retained; only input-custody manifests sort paths.
        return result;
    }
}
