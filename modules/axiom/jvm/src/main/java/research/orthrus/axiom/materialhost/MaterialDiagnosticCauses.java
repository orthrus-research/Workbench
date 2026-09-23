package research.orthrus.axiom.materialhost;

import java.util.*;

/** Native throwable relationships and exact admitted-class source frames.
 * Does not interpret messages, assign blame, invoke material rules or mutate failures.
 */
public final class MaterialDiagnosticCauses {
    private MaterialDiagnosticCauses() {}

    public record Snapshot(Map<String,Object> graph,List<Throwable> exceptions) {}

    public static Snapshot observe(Throwable failure,Map<String,String> sources) {
        var exceptions=new ArrayList<Throwable>();var identities=new IdentityHashMap<Throwable,Integer>();
        var rows=new ArrayList<Map<String,Object>>();
        if(failure!=null)index(failure,exceptions,identities);
        for(int i=0;i<exceptions.size();i++) {
            Throwable value=exceptions.get(i);var row=new LinkedHashMap<String,Object>();
            row.put("type",value.getClass().getName());row.put("message",value.getMessage());
            row.put("locations",locations(value.getStackTrace(),sources));
            Throwable cause=value.getCause();row.put("cause",cause==null?null:index(cause,exceptions,identities));
            var suppressed=new ArrayList<Integer>();
            for(Throwable child:value.getSuppressed())suppressed.add(index(child,exceptions,identities));
            row.put("suppressed",List.copyOf(suppressed));rows.add(Collections.unmodifiableMap(row));
        }
        var graph=new LinkedHashMap<String,Object>();graph.put("schema","axiom.native-diagnostic-causes.v1");
        graph.put("root",failure==null?null:0);graph.put("exceptions",List.copyOf(rows));
        return new Snapshot(Collections.unmodifiableMap(graph),List.copyOf(exceptions));
    }
    private static int index(Throwable value,List<Throwable> exceptions,IdentityHashMap<Throwable,Integer> identities) {
        Integer previous=identities.get(value);if(previous!=null)return previous;
        int index=exceptions.size();identities.put(value,index);exceptions.add(value);return index;
    }
    public static List<Map<String,Object>> locations(StackTraceElement[] frames,Map<String,String> sources) {
        return sourceFrames(frames,sources).stream().filter(row->(int)row.get("line")>0).map(row->{
            var located=new LinkedHashMap<String,Object>(row);located.put("precision","native-stack");
            return Collections.unmodifiableMap(located);
        }).toList();
    }
    /** Retain compiler-generated frames with their actual unknown line (-1).
     * These are evidence, not navigable editor coordinates. */
    public static List<Map<String,Object>> sourceFrames(StackTraceElement[] frames,Map<String,String> sources) {
        var locations=new ArrayList<Map<String,Object>>();
        for(var frame:frames) {
            String name=frame.getClassName();String path=sources.get(name);
            while(path==null&&name.contains("$")) {
                name=name.substring(0,name.lastIndexOf('$'));path=sources.get(name);
            }
            if(path!=null)locations.add(Map.of("path",path,"line",frame.getLineNumber(),
                    "class",frame.getClassName(),"method",frame.getMethodName()));
        }
        // Repeated native frames and their order matter inside each exception.
        return List.copyOf(locations);
    }
}
