package research.orthrus.axiom;

import java.lang.reflect.*;
import java.util.Map;

/** Typed host boundary to a private native event class space. No discovered
 * listener is implied by constructing the space or by registering a fixture. */
final class MaterialEvents {
    final NativeEventSpace space;
    final Object bus;
    MaterialEvents(Map<String,byte[]> classes) {
        space=new NativeEventSpace(classes);
        bus=construct(NativeEventSpace.PREFIX+"EventBus");
    }
    Object construct(String type) {
        try { return space.loadClass(type).getConstructor().newInstance(); }
        catch (InvocationTargetException e) {throw propagate(e.getCause());}
        catch (ReflectiveOperationException e) {throw propagate(e);}
    }
    void register(Object listener) {invoke(bus,"register",new Class<?>[]{Object.class},listener);}
    boolean post(Object event) {
        try {return (boolean)invoke(bus,"post",new Class<?>[]{space.loadClass(NativeEventSpace.PREFIX+"Event")},event);}
        catch(ClassNotFoundException e){throw propagate(e);}
    }
    void owner(String id,String name) {
        try {
            var context=space.loadClass(NativeEventSpace.PREFIX+"EventContext");
            var owner=space.loadClass(NativeEventSpace.PREFIX+"EventContext$Owner");
            Object instance=context.getMethod("instance").invoke(null);
            invoke(instance,"setActiveModContainer",new Class<?>[]{owner},owner.getConstructor(String.class,String.class).newInstance(id,name));
        } catch(InvocationTargetException e){throw propagate(e.getCause());}
        catch(ReflectiveOperationException e){throw propagate(e);}
    }
    private static Object invoke(Object target,String method,Class<?>[] types,Object...arguments) {
        try {return target.getClass().getMethod(method,types).invoke(target,arguments);}
        catch(InvocationTargetException e){throw propagate(e.getCause());}
        catch(ReflectiveOperationException e){throw propagate(e);}
    }
    private static RuntimeException propagate(Throwable failure) {
        if(failure instanceof RuntimeException e)return e;
        if(failure instanceof Error e)throw e;
        return new Failure("execution-error","events.material-binding",failure.toString());
    }
}
