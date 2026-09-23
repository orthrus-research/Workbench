package dev.workbench.crucible.forgerecipes;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collection;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import static dev.workbench.crucible.forgerecipes.ForgeReflection.*;

/** Synthetic protocol checks only; these classes do not replace Minecraft owners. */
public final class ForgeBlockStateValueTest {
    static final class Property {
        final String name;
        Property(String name) { this.name=name; }
    }
    static final class State {
        final Object block;
        final Map<Property,Integer> properties;
        State(Object block,Property a,Property b,int av,int bv) {
            this.block=block; properties=new LinkedHashMap<Property,Integer>();
            properties.put(b,bv); properties.put(a,av);
        }
    }
    static final class Access implements ForgeBlockStateValue.Access {
        final Object block=new Object();
        final Property a=new Property("age"), b=new Property("facing");
        final List<State> states=new ArrayList<State>();
        boolean unlisted,badParse,dropProperty,wrongRestore,invalidRegistry;
        Access() { for(int av=0;av<2;av++) for(int bv=0;bv<2;bv++) states.add(new State(block,a,b,av,bv)); }
        State state(int a,int b) { return states.get(a*2+b); }
        public Object block(Object state) { return ((State)state).block; }
        public Map<?,?> properties(Object state) { return ((State)state).properties; }
        public Collection<?> declaredProperties(Object ignored) { return dropProperty?Arrays.asList(a):Arrays.asList(a,b); }
        public Collection<?> validStates(Object ignored) { return states; }
        public Object defaultState(Object ignored) { return state(0,0); }
        public boolean hasUnlistedProperties(Object ignored) { return unlisted; }
        public Map<String,Object> registeredBlock(Object ignored) {
            if(invalidRegistry)throw new IllegalStateException("Original block registry mismatch");
            return row("projection_kind","registry-reference","registry_kind","block","registry_name","fixture:listed","numeric_id",101);
        }
        public String name(Object property) { return ((Property)property).name; }
        public String serialized(Object property,Object value) { return "v"+value; }
        public Object parsed(Object property,String value) { return badParse?null:Integer.valueOf(value.substring(1)); }
        public Collection<?> allowed(Object property) { return Arrays.asList(0,1); }
        public Object withProperty(Object original,Object property,Object value) {
            if(wrongRestore)return state(0,0);
            State old=(State)original;
            return state(property==a?(Integer)value:old.properties.get(a),property==b?(Integer)value:old.properties.get(b));
        }
    }
    public interface SyntheticSrgProperty {
        String func_177702_a(Comparable<?> value);
    }
    public static final class SrgProperty implements SyntheticSrgProperty {
        public String func_177702_a(Comparable<?> value) { return "native-style-"+value; }
        public String unrelated(Object ignored) { throw new AssertionError("Unrelated method invoked"); }
    }
    private static void check(boolean value,String message) { if(!value)throw new AssertionError(message); }
    private static void refuse(Runnable action,String message) {
        try { action.run(); throw new AssertionError(message); }
        catch(IllegalStateException expected) { }
    }
    public static void main(String[] args) {
        Access access=new Access(); State state=access.state(1,0);
        Map<String,Object> result=ForgeBlockStateValue.encode(state,access);
        check(result.get("projection_kind").equals("registry-listed-block-state"),"Explicit structural projection kind");
        check(new ArrayList<Object>(((Map<?,?>)result.get("properties")).keySet()).equals(Arrays.asList("age","facing")),"Every property sorted by original name");
        check(((Map<?,?>)result.get("properties")).get("age").equals("v1"),"Original serialized property value retained");
        check(((Map<?,?>)result.get("block")).get("numeric_id").equals(101),"Registered numeric identity retained");
        check(hash(result).equals(hash(ForgeBlockStateValue.encode(state,access))),"Repeated observation is deterministic");
        check(state.properties.get(access.a)==1 && state.properties.get(access.b)==0,"Input state is unchanged");
        access.unlisted=true;refuse(()->ForgeBlockStateValue.encode(state,access),"Unlisted data must refuse");access.unlisted=false;
        access.dropProperty=true;refuse(()->ForgeBlockStateValue.encode(state,access),"Incomplete property inventory must refuse");access.dropProperty=false;
        access.badParse=true;refuse(()->ForgeBlockStateValue.encode(state,access),"Unparseable property must refuse");access.badParse=false;
        access.wrongRestore=true;refuse(()->ForgeBlockStateValue.encode(state,access),"Wrong reconstructed state must refuse");access.wrongRestore=false;
        access.invalidRegistry=true;refuse(()->ForgeBlockStateValue.encode(state,access),"Registry roundtrip failures propagate");access.invalidRegistry=false;
        State unretained=new State(access.block,access.a,access.b,1,0);
        refuse(()->ForgeBlockStateValue.encode(unretained,access),"Unretained state must refuse");
        state.properties.put(access.a,2);
        refuse(()->ForgeBlockStateValue.encode(state,access),"Out-of-domain property must refuse");
        state.properties.put(access.a,1);
        Object encoded=ForgeBlockStateValue.invoke(new SrgProperty(),SyntheticSrgProperty.class.getName(),
            new String[]{"getName","func_177702_a"},String.class,new Class<?>[]{Comparable.class},1);
        check(encoded.equals("native-style-1"),"Typed SRG fallback with erased Comparable preserves virtual dispatch");
        System.out.println("ForgeBlockStateValueTest: listed-state protocol and refusal checks passed; no native initialization performed");
    }
}
