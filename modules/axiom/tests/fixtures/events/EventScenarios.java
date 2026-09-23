package fixtureevents;

import research.orthrus.axiom.nativeevents.*;
import java.util.*;

/** Controlled contract probes; no material producer or pack qualification. */
public class EventScenarios {
    static final List<String> trace = new ArrayList<>();
    static final EventContext.Owner FIRST = new EventContext.Owner("first", "First");
    static final EventContext.Owner SECOND = new EventContext.Owner("second", "Second");
    static EventBus bus;
    static Object added;
    public static class Root extends Event {}
    public static class Child extends Root {}
    public static class Sibling extends Event {}
    @Cancelable @Event.HasResult public static class Cancel extends Event {}
    public static class Typed<T> extends GenericEvent<T> {
        public Typed() { super(null); }
        public Typed(Class<T> type) { super(type); }
    }
    public static class Context extends Event implements IContextSetter {
        public EventContext.Owner owner;
        public void setModContainer(EventContext.Owner owner) { this.owner = owner; }
    }
    public static class NoDefault extends Event { public NoDefault(int ignored) {} }
    public static class BrokenConstructor extends Event { public BrokenConstructor() { throw new IllegalStateException("constructor"); } }
    public static class RootHandler {
        @SubscribeEvent(priority=EventPriority.HIGH) public void high(Root e) { trace.add("root-high:"+e.getPhase()); }
        @SubscribeEvent public void normal(Root e) { trace.add("root-normal:"+e.getPhase()); }
    }
    public static class ChildHandler {
        @SubscribeEvent(priority=EventPriority.HIGH) public void high(Child e) { trace.add("child-high:"+e.getPhase()); }
        @SubscribeEvent public void normal(Child e) { trace.add("child-normal:"+e.getPhase()); }
    }
    public static class CancelHandler {
        @SubscribeEvent(priority=EventPriority.HIGHEST) public void cancel(Cancel e) { trace.add("cancel"); e.setCanceled(true); e.setResult(Event.Result.ALLOW); }
        @SubscribeEvent public void skipped(Cancel e) { trace.add("must-not-run"); }
        @SubscribeEvent(priority=EventPriority.LOWEST,receiveCanceled=true) public void received(Cancel e) { trace.add("received:"+e.getResult()); }
    }
    public static class GenericHandler {
        @SubscribeEvent(priority=EventPriority.HIGH) public void string(GenericEvent<String> e) { trace.add("string"); }
        @SubscribeEvent public void integer(GenericEvent<Integer> e) { trace.add("integer"); }
    }
    public static class Added {
        @SubscribeEvent(priority=EventPriority.LOWEST) public void call(Root e) { trace.add("added"); }
    }
    public static class Mutator {
        @SubscribeEvent(priority=EventPriority.HIGHEST) public void call(Root e) { trace.add("mutator"); bus.register(added); bus.unregister(this); }
    }
    public static class ContextHandler {
        @SubscribeEvent public void call(Context e) {
            trace.add("owner:"+EventContext.instance().activeModContainer().getModId()+":"+e.owner.getModId());
            throw new IllegalArgumentException("listener");
        }
    }
    public static class SuccessfulContextHandler {
        @SubscribeEvent public void call(Context e) { trace.add("owner:"+e.owner.getModId()); }
    }
    public static class WrongSignature { @SubscribeEvent public void call(Root one, Root two) {} }
    public static class WrongType { @SubscribeEvent public void call(String one) {} }
    public static class BrokenHandler { @SubscribeEvent public void call(BrokenConstructor event) { trace.add("must-not-run"); } }
    public static class ConstructorHandler { @SubscribeEvent public void call(NoDefault event) { trace.add("constructed"); } }
    public static class StaticHandler { @SubscribeEvent public static void call(Root event) { trace.add("static"); } }
    public static class PrivateHandler { @SubscribeEvent private void call(Root event) {} }
    static class ProtectedHandler { @SubscribeEvent protected void call(Root event) { trace.add("publicized"); } }
    public interface Inherited { @SubscribeEvent void call(Root event); }
    public static class Implemented implements Inherited { public void call(Root event) { trace.add("inherited"); } }

    private static void require(boolean condition, String message) { if (!condition) throw new AssertionError(message); }
    private static void expect(Class<? extends Throwable> type, Runnable action) {
        try { action.run(); } catch (Throwable failure) {
            if (type.isInstance(failure)) { trace.add(type.getSimpleName()); return; }
            throw new AssertionError(failure);
        }
        throw new AssertionError("Missing " + type.getName());
    }

    public static String run(String mode) throws Exception {
        trace.clear();
        EventContext.instance().setMinecraftModContainer(new EventContext.Owner("minecraft", "Minecraft"));
        EventContext.instance().setActiveModContainer(FIRST);
        bus = new EventBus((b,e,list,index,failure) -> trace.add("exception:"+index+":"+failure.getMessage()));
        switch (mode) {
            case "inheritance" -> {
                bus.register(new RootHandler()); bus.register(new ChildHandler());
                var child = new Child();
                require(child.getListenerList() != new Root().getListenerList(), "shared child list");
                require(child.getListenerList() != new Sibling().getListenerList(), "shared sibling list");
                bus.post(child); bus.post(new Sibling());
                require(trace.equals(List.of("child-high:HIGH","root-high:HIGH","child-normal:NORMAL","root-normal:NORMAL")), trace.toString());
            }
            case "cancel" -> {
                bus.register(new CancelHandler()); var event = new Cancel();
                require(event.isCancelable() && event.hasResult(), "missing annotation transform");
                require(bus.post(event), "cancellation return");
                require(trace.equals(List.of("cancel","received:ALLOW")), trace.toString());
                expect(UnsupportedOperationException.class, () -> new Root().setCanceled(true));
            }
            case "generic" -> {
                bus.register(new GenericHandler());
                bus.post(new Typed<>(String.class)); bus.post(new Typed<>(Integer.class)); bus.post(new Typed<>(Object.class));
                require(trace.equals(List.of("string","integer")), trace.toString());
            }
            case "mutation" -> {
                added = new Added(); bus.register(new Mutator()); bus.post(new Root()); bus.post(new Root());
                require(trace.equals(List.of("mutator","added")), trace.toString());
                bus.register(added); bus.post(new Root());
                require(trace.equals(List.of("mutator","added","added")), "duplicate registration");
                bus.unregister(added); bus.post(new Root()); require(trace.size()==3, "unregister");
            }
            case "context-failure" -> {
                bus.register(new ContextHandler()); EventContext.instance().setActiveModContainer(SECOND);
                expect(IllegalArgumentException.class, () -> bus.post(new Context()));
                require(EventContext.instance().activeModContainer()==FIRST, "upstream does not restore after throw");
                require(trace.equals(List.of("owner:first:first","exception:1:listener","IllegalArgumentException")), trace.toString());
            }
            case "context-success" -> {
                bus.register(new SuccessfulContextHandler()); EventContext.instance().setActiveModContainer(SECOND);
                bus.post(new Context()); require(EventContext.instance().activeModContainer()==SECOND, "normal owner restoration");
            }
            case "registration-errors" -> {
                expect(IllegalArgumentException.class, () -> bus.register(new WrongSignature()));
                expect(IllegalArgumentException.class, () -> bus.register(new WrongType()));
                bus.register(new BrokenHandler()); // Constructor Exception is logged, not thrown.
                bus.register(new ConstructorHandler());
                bus.post(NoDefault.class.getConstructor().newInstance());
                require(trace.equals(List.of("IllegalArgumentException","IllegalArgumentException","constructed")), trace.toString());
            }
            case "subscriber-transform" -> {
                bus.register(new ProtectedHandler()); bus.register(new Implemented());
                bus.post(new Root());
                require(trace.equals(List.of("publicized","inherited")), trace.toString());
                expect(RuntimeException.class, () -> {
                    try { Class.forName("fixtureevents.EventScenarios$PrivateHandler", true, EventScenarios.class.getClassLoader()); }
                    catch (ClassNotFoundException failure) { throw new AssertionError(failure); }
                });
            }
            case "static-and-shutdown" -> {
                bus.register(new StaticHandler()); bus.post(new Root()); require(trace.isEmpty(), "instance must not register static");
                bus.register(StaticHandler.class); bus.post(new Root()); require(trace.equals(List.of("static")), trace.toString());
                bus.shutdown(); require(!bus.post(new Root()), "shutdown return"); require(trace.size()==1, "shutdown invocation");
            }
            case "phase" -> {
                var root = new Root(); root.setPhase(EventPriority.HIGH);
                expect(IllegalArgumentException.class, () -> root.setPhase(EventPriority.HIGH));
                expect(IllegalArgumentException.class, () -> root.setPhase(EventPriority.HIGHEST));
                expect(NullPointerException.class, () -> root.setPhase(null));
                root.setPhase(EventPriority.LOWEST); trace.add(root.getPhase().name());
            }
            case "listener-cache" -> {
                var parent = new ListenerList(); var child = new ListenerList(parent);
                ListenerList.resize(3);
                IEventListener one=e->trace.add("one"), two=e->trace.add("two");
                parent.register(2,EventPriority.HIGH,one); child.register(2,EventPriority.HIGH,two);
                var snapshot=child.getListeners(2); require(snapshot.length==3,"sentinel");
                parent.unregister(2,one); require(snapshot.length==3,"snapshot changed");
                var next=child.getListeners(2); require(next.length==2,"child cache not rebuilt");
                for (var listener:snapshot) listener.invoke(new Root());
                require(trace.equals(List.of("two","one")),trace.toString());
                ListenerList.clearBusID(2); require(child.getListeners(2)==null,"source dispose leaves null cache");
            }
            case "randomized-cache" -> {
                var digest=java.security.MessageDigest.getInstance("SHA-256");
                for(int seed=0;seed<16;seed++) {
                    var random=new Random(1709+seed);
                    var root=new ListenerList();var child=new ListenerList(root);var leaf=new ListenerList(child);
                    var lists=List.of(root,child,leaf);ListenerList.resize(4);
                    IEventListener[] listeners=new IEventListener[24];
                    for(int n=0;n<listeners.length;n++){final int id=n;listeners[n]=e->trace.add(Integer.toString(id));}
                    for(int step=0;step<500;step++) {
                        var list=lists.get(random.nextInt(3));int id=2+random.nextInt(2);
                        var listener=listeners[random.nextInt(listeners.length)];
                        if(random.nextInt(3)==0)list.unregister(id,listener);
                        else list.register(id,EventPriority.values()[random.nextInt(EventPriority.values().length)],listener);
                        for(var observed:lists) {
                            trace.clear();var event=new Root();var snapshot=observed.getListeners(id);
                            for(var value:snapshot)value.invoke(event);
                            digest.update((snapshot.length+":"+event.getPhase()+":"+String.join(",",trace)+"\n")
                                    .getBytes(java.nio.charset.StandardCharsets.UTF_8));
                        }
                    }
                }
                trace.clear();trace.add("8000-mutations:"+java.util.HexFormat.of().formatHex(digest.digest()));
            }
            default -> throw new AssertionError(mode);
        }
        return String.join("|",trace);
    }
}
