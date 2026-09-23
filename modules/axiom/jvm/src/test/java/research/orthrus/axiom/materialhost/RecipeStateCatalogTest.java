package research.orthrus.axiom.materialhost;

import org.junit.jupiter.api.Test;
import java.util.*;
import java.util.concurrent.atomic.AtomicInteger;
import static org.junit.jupiter.api.Assertions.*;

class RecipeStateCatalogTest {
    static final class NativeIdentity {
        @Override public int hashCode() {throw new AssertionError("Native hashCode was invoked");}
        @Override public boolean equals(Object other) {throw new AssertionError("Native equals was invoked");}
    }
    @Test void membershipUsesIdentityAndRetainsDuplicateValues() throws Exception {
        var first=new NativeIdentity();var second=new NativeIdentity();var calls=new AtomicInteger();
        var catalog=new RecipeStateCatalog();
        RecipeStateCatalog.State state=ignored->{calls.incrementAndGet();return Map.of("inputs",List.of("iron"),"output","steel");};
        String a=catalog.add(first,state);assertEquals(a,catalog.add(first,state));assertEquals(a,catalog.add(second,state));
        assertEquals(2,calls.get());var result=catalog.snapshot();
        assertEquals(3,result.get("lookupReferences"));assertEquals(2,result.get("distinctNativeObjects"));
        assertEquals(2,((Map<?,?>)((Map<?,?>)result.get("entries")).get(a)).get("multiplicity"));
        assertNull(catalog.identity(new NativeIdentity()));
    }
    @Test void effectsWithIdenticalInputsHaveDifferentContentIdentities() throws Exception {
        var base=new LinkedHashMap<String,Object>();
        base.put("inputs",List.of("iron"));base.put("outputs",List.of("steel"));base.put("duration",120);base.put("EUt",30);
        base.put("hidden",false);base.put("chance",1000);base.put("properties",Map.of("temperature",1200));
        String original=RecipeStateCatalog.digest(base);
        var edits=Map.of("outputs",List.of("nickel"),"duration",121,"EUt",31,"hidden",true,"chance",1001,
                "properties",Map.of("temperature",1201));
        for(var entry:edits.entrySet()) {
            var changed=new LinkedHashMap<>(base);changed.put(entry.getKey(),entry.getValue());
            assertNotEquals(original,RecipeStateCatalog.digest(changed),entry.getKey());
        }
    }
    @Test void orderOfTraversalDoesNotChangeMultisetButMultiplicityDoes() throws Exception {
        var one=new RecipeStateCatalog();var two=new RecipeStateCatalog();
        one.add(new NativeIdentity(),ignored->Map.of("result","a"));one.add(new NativeIdentity(),ignored->Map.of("result","b"));
        two.add(new NativeIdentity(),ignored->Map.of("result","b"));two.add(new NativeIdentity(),ignored->Map.of("result","a"));
        assertEquals(one.snapshot(),two.snapshot());
        two.add(new NativeIdentity(),ignored->Map.of("result","a"));
        assertNotEquals(one.snapshot().get("multisetSha256"),two.snapshot().get("multisetSha256"));
    }
    @Test void canonicalStatePreservesTypesOrderAndNullAndDetachesInput() throws Exception {
        var left=new LinkedHashMap<String,Object>();left.put("b",1);left.put("a",null);
        var right=new LinkedHashMap<String,Object>();right.put("a",null);right.put("b",1);
        assertEquals(RecipeStateCatalog.digest(left),RecipeStateCatalog.digest(right));
        assertNotEquals(RecipeStateCatalog.digest(1),RecipeStateCatalog.digest("1"));
        assertNotEquals(RecipeStateCatalog.digest(List.of(1,2)),RecipeStateCatalog.digest(List.of(2,1)));
        var catalog=new RecipeStateCatalog();catalog.add(new NativeIdentity(),ignored->left);
        var before=catalog.snapshot();left.put("b",2);assertEquals(before,catalog.snapshot());
        assertThrows(IllegalArgumentException.class,()->RecipeStateCatalog.digest(new NativeIdentity()));
    }
}
