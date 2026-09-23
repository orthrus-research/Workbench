package research.orthrus.axiom.materialhost;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.*;

/** Identity membership and a multiset of canonical stored values. Native recipe
 * equals/hashCode need not describe outputs and are never used by this catalog. */
public final class RecipeStateCatalog {
    private final IdentityHashMap<Object,String> identities=new IdentityHashMap<>();
    private final SortedMap<String,Map<String,Object>> entries=new TreeMap<>();
    private int references;

    @FunctionalInterface public interface State { Object read(Object member) throws ReflectiveOperationException; }

    public String add(Object member,State state) throws ReflectiveOperationException {
        Objects.requireNonNull(member);references++;
        String existing=identities.get(member);if(existing!=null)return existing;
        Object value=canonical(state.read(member));String hash=digest(value);
        var entry=entries.get(hash);
        if(entry==null) {entry=new TreeMap<>();entry.put("value",value);entry.put("multiplicity",1);entries.put(hash,entry);}
        else {
            if(!Objects.equals(entry.get("value"),value))throw new IllegalStateException("Recipe state digest collision");
            entry.put("multiplicity",(Integer)entry.get("multiplicity")+1);
        }
        identities.put(member,hash);return hash;
    }
    public String identity(Object member) {return identities.get(member);}
    public Map<String,Object> snapshot() {
        var counts=new TreeMap<String,Integer>();entries.forEach((key,value)->counts.put(key,(Integer)value.get("multiplicity")));
        return Map.of("lookupReferences",references,"distinctNativeObjects",identities.size(),
                "entries",canonical(entries),"multisetSha256",digest(counts));
    }
    public static String digest(Object value) {
        try {
            var hash=MessageDigest.getInstance("SHA-256");hash(hash,canonical(value));
            return HexFormat.of().formatHex(hash.digest());
        }
        catch(NoSuchAlgorithmException impossible) {throw new IllegalStateException(impossible);}
    }
    private static void token(MessageDigest hash,String text) {
        byte[] bytes=text.getBytes(StandardCharsets.UTF_8);
        hash.update(Integer.toString(bytes.length).getBytes(StandardCharsets.US_ASCII));hash.update((byte)':');hash.update(bytes);
    }
    private static void hash(MessageDigest hash,Object value) {
        if(value==null)token(hash,"null");
        else if(value instanceof Map<?,?> map) {
            token(hash,"map");token(hash,Integer.toString(map.size()));
            for(var entry:map.entrySet()) {token(hash,(String)entry.getKey());hash(hash,entry.getValue());}
        } else if(value instanceof List<?> list) {
            token(hash,"list");token(hash,Integer.toString(list.size()));for(Object item:list)hash(hash,item);
        } else {token(hash,value.getClass().getName());token(hash,value.toString());}
    }
    private static Object canonical(Object value) {
        if(value==null||value instanceof String||value instanceof Boolean||value instanceof Number)return value;
        if(value instanceof Map<?,?> map) {
            var result=new TreeMap<String,Object>();
            for(var entry:map.entrySet()) {
                if(!(entry.getKey() instanceof String key))throw new IllegalArgumentException("Recipe state key is not text");
                result.put(key,canonical(entry.getValue()));
            }
            return result;
        }
        if(value instanceof List<?> list) {var result=new ArrayList<Object>();for(Object item:list)result.add(canonical(item));return result;}
        throw new IllegalArgumentException("Recipe state contains an unencoded native value: "+value.getClass().getName());
    }
}
