package research.orthrus.axiom.materialhost;

import org.apache.logging.log4j.*;
import org.apache.logging.log4j.core.LogEvent;
import org.apache.logging.log4j.core.appender.AbstractAppender;
import org.apache.logging.log4j.core.config.Property;
import java.lang.reflect.*;
import java.io.*;
import java.util.*;
import java.util.regex.Pattern;

/** Observe both original log channels without changing native continuation. */
public final class NativeProgramObservations extends AbstractAppender implements AutoCloseable {
    private static final Pattern GROOVY_LEVEL=Pattern.compile("^\\[[^]]+] \\[(?:SERVER|CLIENT)/(ERROR|FATAL|WARN)]");
    private final Map<String,String> sources;
    private final List<Map<String,Object>> diagnostics=new ArrayList<>();
    private final org.apache.logging.log4j.core.Logger root;
    private final Field writerField;
    private final Object nativeLog;
    private final PrintWriter originalWriter;
    private final PrintWriter observedWriter;
    private boolean compilationFailure;

    public NativeProgramObservations(Map<String,String> sources) throws Exception {
        super("axiom-material-program-observation",null,null,true,Property.EMPTY_ARRAY);
        this.sources=Map.copyOf(sources);
        // GroovyLog.log(Msg) normally writes only to this writer. A Log4j
        // appender alone loses its components(Object...) errors and submessages.
        var logType=Class.forName("com.cleanroommc.groovyscript.sandbox.GroovyLogImpl",false,getClass().getClassLoader());
        nativeLog=logType.getField("LOG").get(null);
        writerField=logType.getDeclaredField("printWriter");writerField.setAccessible(true);
        originalWriter=(PrintWriter)writerField.get(nativeLog);
        observedWriter=new PrintWriter(originalWriter,true) {
            @Override public void println(String line) {
                originalWriter.println(line);
                var match=GROOVY_LEVEL.matcher(line);
                if(match.find()) record("groovy-log",match.group(1),line,null,Thread.currentThread().getStackTrace(),null);
            }
        };
        writerField.set(nativeLog,observedWriter);
        root=(org.apache.logging.log4j.core.Logger)LogManager.getRootLogger();
        start();root.addAppender(this);
    }
    @Override public void append(LogEvent event) {
        if(event.getLevel().isMoreSpecificThan(Level.WARN))
            record("log4j",event.getLevel().name(),event.getMessage().getFormattedMessage(),
                    event.getThrown(),Thread.currentThread().getStackTrace(),event.getLoggerName());
    }
    public void exception(Throwable failure) {
        record("native-exception","ERROR",failure.getClass().getName()+": "+failure.getMessage(),failure,new StackTraceElement[0],null);
    }
    private synchronized void record(String channel,String level,String message,Throwable failure,StackTraceElement[] current,String logger) {
        var row=new LinkedHashMap<String,Object>();
        row.put("channel",channel);row.put("severity",level.equals("WARN")?"warning":"error");row.put("message",message);
        if(logger!=null)row.put("logger",logger);
        if(!level.equals("WARN")&&!channel.equals("native-exception")) {
            try {row.put("nativeOrigins",NativeDiagnosticOrigins.capture());}
            catch(RuntimeException|LinkageError originFailure) {row.put("nativeOriginFailure",originFailure.getClass().getName()+": "+originFailure.getMessage());}
        }
        var locations=new LinkedHashSet<Map<String,Object>>();
        var causes=MaterialDiagnosticCauses.observe(failure,sources);
        var compilerFindings=new ArrayList<Map<String,Object>>();
        for(int exceptionIndex=0;exceptionIndex<causes.exceptions().size();exceptionIndex++) {
            Throwable cause=causes.exceptions().get(exceptionIndex);
            locations.addAll(MaterialDiagnosticCauses.locations(cause.getStackTrace(),sources));
            if(cause instanceof org.codehaus.groovy.control.CompilationFailedException)compilationFailure=true;
            if(cause instanceof org.codehaus.groovy.control.MultipleCompilationErrorsException compilation)
                for(Object error:compilation.getErrorCollector().getErrors())
                    if(error instanceof org.codehaus.groovy.control.messages.SyntaxErrorMessage syntax) {
                        var problem=syntax.getCause();var finding=new LinkedHashMap<String,Object>();
                        finding.put("message",problem.getOriginalMessage());
                        finding.put("exceptionIndex",exceptionIndex);
                        String locator=problem.getSourceLocator();
                        var matching=new LinkedHashSet<String>();
                        if(locator!=null)for(String path:sources.values())
                            if(locator.equals(path)||locator.equals(path.substring("groovy/".length()))||locator.endsWith("/"+path))matching.add(path);
                        if(matching.size()==1&&problem.getStartLine()>0) {
                            var location=new LinkedHashMap<String,Object>();location.put("path",matching.iterator().next());location.put("line",problem.getStartLine());
                            if(problem.getStartColumn()>0)location.put("column",problem.getStartColumn());
                            location.put("precision","native-compiler");locations.add(location);finding.put("location",location);
                        }
                        compilerFindings.add(finding);
                    }
        }
        var observationLocations=MaterialDiagnosticCauses.locations(current,sources);
        locations.addAll(observationLocations);
        row.put("locations",List.copyOf(locations));
        row.put("locationStatus",locations.isEmpty()?"unlocated":"located");
        if(failure!=null)row.put("trace",trace(failure));
        row.put("causality",causes.graph());row.put("observationLocations",observationLocations);
        if(!compilerFindings.isEmpty())row.put("compilerFindings",compilerFindings);
        diagnostics.add(Collections.unmodifiableMap(row));
    }
    public synchronized List<Map<String,Object>> diagnostics() {return MaterialDiagnosticGroups.group(diagnostics);}
    public synchronized boolean hasErrors() {return diagnostics.stream().anyMatch(row->row.get("severity").equals("error"));}
    public synchronized boolean compilationFailure() {return compilationFailure;}
    /** Passive snapshot; collectErrors() would drain original native state. */
    public List<?> nativeErrors() throws Exception {
        return List.copyOf((List<?>)field(nativeLog.getClass(),nativeLog,"errors"));
    }
    @Override public void close() throws IllegalAccessException {
        root.removeAppender(this);stop();
        observedWriter.flush();writerField.set(nativeLog,originalWriter);
    }
    public static String trace(Throwable failure) {
        var out=new StringWriter();failure.printStackTrace(new PrintWriter(out));return out.toString();
    }
    /** A host/observation failure may happen outside the log observer's lifetime.
     * Preserve its original throwable graph without opening or replaying native state. */
    public static Map<String,Object> detachedFailure(Throwable failure,Map<String,String> sources) {
        var causes=MaterialDiagnosticCauses.observe(failure,sources);
        var locations=new LinkedHashSet<Map<String,Object>>();
        for(Throwable cause:causes.exceptions())locations.addAll(MaterialDiagnosticCauses.locations(cause.getStackTrace(),sources));
        var row=new LinkedHashMap<String,Object>();
        row.put("channel","native-host-exception");row.put("severity","error");
        row.put("message",failure.getClass().getName()+": "+failure.getMessage());
        row.put("trace",trace(failure));row.put("causality",causes.graph());
        row.put("locations",List.copyOf(locations));row.put("locationStatus",locations.isEmpty()?"unlocated":"located");
        row.put("observationLocations",List.of());
        return row;
    }
    private static Object field(Class<?> type,Object object,String name) throws Exception {
        Field field=type.getDeclaredField(name);field.setAccessible(true);return field.get(object);
    }
    public static List<Map<String,Object>> scriptIndex(Object engine) throws Exception {
        var index=(Map<?,?>)field(engine.getClass(),engine,"index");
        var result=new ArrayList<Map<String,Object>>();
        for(var entry:index.entrySet()) {
            Object script=entry.getValue();Class<?> type=script.getClass();
            var preprocessors=field(type,script,"preprocessors");
            result.add(Map.of("path",entry.getKey(),"preprocessors",preprocessors==null?List.of():List.copyOf((List<?>)preprocessors),
                    "preprocessorCheckFailed",field(type,script,"preprocessorCheckFailed"),
                    "classDefined",field(type.getSuperclass(),script,"clazz")!=null));
        }
        return result;
    }
}
