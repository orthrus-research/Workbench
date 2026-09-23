package research.orthrus.axiom.materialhost;

import java.util.*;
import java.util.function.LongSupplier;

/** Passive host observations. This neither schedules native hooks nor infers
 * success from a callback returning: native logged errors remain separate. */
public final class NativeInitializationTrace {
    private final LinkedHashMap<String,Map<String,Object>> steps=new LinkedHashMap<>();
    private final Map<String,Long> starts=new HashMap<>();
    private final LongSupplier clock;
    private int sequence;

    public NativeInitializationTrace() {this(System::nanoTime);}
    NativeInitializationTrace(LongSupplier clock) {this.clock=Objects.requireNonNull(clock);}
    public void declare(String id,String kind,String owner) {
        if(!Set.of("stage","hook").contains(kind)||id.isBlank()||owner.isBlank()||steps.containsKey(id))
            throw new IllegalArgumentException("Invalid or duplicate initialization step: "+id);
        var row=new LinkedHashMap<String,Object>();
        row.put("id",id);row.put("kind",kind);row.put("owner",owner);row.put("status","not-reached");
        steps.put(id,row);
    }
    public void defer(String id,String owner,String reason) {
        if(reason.isBlank())throw new IllegalArgumentException("Deferred initialization needs a reason");
        declare(id,"stage",owner);steps.get(id).put("status","deferred");steps.get(id).put("reason",reason);
    }
    public void begin(String id) {
        var row=step(id);
        if(!row.get("status").equals("not-reached"))throw new IllegalStateException("Initialization step already visited or deferred: "+id);
        row.put("status","running");row.put("sequence",++sequence);starts.put(id,clock.getAsLong());
    }
    public void returned(String id) {finish(id,null);}
    private void finish(String id,Throwable failure) {
        var row=step(id);
        if(!row.get("status").equals("running"))throw new IllegalStateException("Initialization step is not running: "+id);
        row.put("elapsedNanos",Math.max(0,clock.getAsLong()-starts.remove(id)));
        row.put("status",failure==null?"returned":"threw");
        if(failure!=null)row.put("exceptionType",failure.getClass().getName());
    }
    public void failedRunning(Throwable failure) {
        for(String id:List.copyOf(starts.keySet()))finish(id,Objects.requireNonNull(failure));
    }
    public Map<String,Object> snapshot() {
        return Map.of("schema","axiom.native-initialization-trace.v1",
                "scope","explicit-host-stages-and-invoked-hooks-not-per-listener-trace",
                "returnMeaning","returned-to-host-not-proof-of-error-free-or-qualified-initialization",
                "steps",steps.values().stream().map(row->Collections.unmodifiableMap(new LinkedHashMap<>(row))).toList());
    }
    private Map<String,Object> step(String id) {
        var row=steps.get(id);if(row==null)throw new IllegalArgumentException("Undeclared initialization step: "+id);return row;
    }
}
