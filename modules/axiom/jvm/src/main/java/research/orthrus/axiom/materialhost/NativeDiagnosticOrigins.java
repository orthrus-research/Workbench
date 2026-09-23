package research.orthrus.axiom.materialhost;

import java.util.*;

/** Observe the classes actually emitting a diagnostic. StackWalker retains
 * existing class identities; this never loads a class named by a log message. */
public final class NativeDiagnosticOrigins {
    private NativeDiagnosticOrigins() {}
    public static List<Map<String,Object>> capture() {
        return StackWalker.getInstance(StackWalker.Option.RETAIN_CLASS_REFERENCE).walk(frames -> frames
                .filter(frame -> !frame.getClassName().startsWith("research.orthrus.axiom.")
                        && !frame.getClassName().startsWith("org.apache.logging.log4j."))
                .map(frame -> {
                    var row=new LinkedHashMap<String,Object>();Class<?> owner=frame.getDeclaringClass();
                    row.put("class",owner.getName());row.put("method",frame.getMethodName());
                    if(frame.getFileName()!=null)row.put("file",frame.getFileName());
                    if(frame.getLineNumber()>0)row.put("line",frame.getLineNumber());
                    var domain=owner.getProtectionDomain();var source=domain==null?null:domain.getCodeSource();
                    if(source!=null&&source.getLocation()!=null)row.put("codeSource",source.getLocation().toExternalForm());
                    row.put("bootstrapClass",owner.getClassLoader()==null);
                    return Collections.unmodifiableMap(row);
                }).toList());
    }
}
