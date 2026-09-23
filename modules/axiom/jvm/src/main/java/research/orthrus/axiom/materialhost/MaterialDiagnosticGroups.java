package research.orthrus.axiom.materialhost;

import java.util.*;

/** Lossless grouping of adjacent native warning lines with identical context. */
public final class MaterialDiagnosticGroups {
    private MaterialDiagnosticGroups() {}
    public static List<Map<String,Object>> group(List<Map<String,Object>> input) {
        var output=new ArrayList<Map<String,Object>>();
        for(var row:input) {
            if(!output.isEmpty()&&"warning".equals(row.get("severity"))&&!row.containsKey("trace")) {
                var previous=output.getLast();
                var left=new LinkedHashMap<>(previous);var right=new LinkedHashMap<>(row);
                left.remove("message");left.remove("nativeLogEvents");right.remove("message");right.remove("nativeLogEvents");
                if(left.equals(right)) {
                    var combined=new LinkedHashMap<>(previous);
                    combined.put("message",previous.get("message")+"\n"+row.get("message"));
                    combined.put("nativeLogEvents",((Number)previous.getOrDefault("nativeLogEvents",1)).intValue()+1);
                    output.set(output.size()-1,Collections.unmodifiableMap(combined));continue;
                }
            }
            output.add(row);
        }
        return List.copyOf(output);
    }
}
