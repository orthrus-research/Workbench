package research.orthrus.axiom;

/** An explicit boundary, never a native recipe rejection. */
public final class Failure extends RuntimeException {
    public final String kind;
    public final String rule;

    public Failure(String kind, String rule, String message) {
        super(message);
        this.kind = kind;
        this.rule = rule;
    }

    static Failure request(String message) { return new Failure("request-error", "request.shape", message); }
    static Failure unsupported(String rule, String message) { return new Failure("unsupported", rule, message); }
    static Failure context(String message) { return new Failure("requires-context", "registry.context", message); }
}
