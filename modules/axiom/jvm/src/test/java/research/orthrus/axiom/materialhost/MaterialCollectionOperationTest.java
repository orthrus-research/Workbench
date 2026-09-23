package research.orthrus.axiom.materialhost;

import groovy.lang.IntRange;
import org.codehaus.groovy.runtime.DefaultGroovyMethods;
import org.junit.jupiter.api.Test;
import research.orthrus.axiom.Json;
import java.nio.charset.StandardCharsets;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;

class MaterialCollectionOperationTest {
    @Test void originalCombinationsPreserveOrderIdentityAndEmptyDimensionShortCircuit() {
        Object first=new Object(),second=new Object();
        var dimension=new ArrayList<>(List.of(first,second));
        var source=new ArrayList<>(List.of(dimension,dimension));
        assertTrue(MaterialCallGate.collectionCombinationsOperand(source));
        assertEquals(List.of(List.of(first,first),List.of(second,first),List.of(first,second),List.of(second,second)),
                DefaultGroovyMethods.combinations(source));
        assertSame(dimension,source.getFirst());assertSame(first,dimension.getFirst());
        var hostile=new ArrayList<Object>() {
            public Iterator<Object> iterator(){throw new AssertionError("Unselected iterator invoked");}
            public Object[] toArray(){throw new AssertionError("Unselected array conversion invoked");}
        };
        assertFalse(MaterialCallGate.collectionCombinationsOperand(hostile));
        assertFalse(MaterialCallGate.collectionCombinationsOperand(new ArrayList<>(List.of(hostile))));
        for(Object empty:Arrays.asList(new ArrayList<>(),null)) {
            var stopped=new ArrayList<>(Arrays.asList(empty,hostile));
            assertTrue(MaterialCallGate.collectionCombinationsOperand(stopped));
            assertEquals(List.of(),DefaultGroovyMethods.combinations(stopped));
        }
        assertFalse(MaterialCallGate.collectionCombinationsOperand(new ArrayList<>(List.of("unselected dimension"))));
        assertFalse(MaterialCallGate.collectionCombinationsOperand(null));
    }
    @SuppressWarnings("unchecked") @Test void originalListMultiplyPreservesReferencesAndNativeFailures() throws Exception {
        Map<String,Object> raw;
        try(var stream=getClass().getResourceAsStream("/axiom-material-admission.json")) {
            assertNotNull(stream);raw=(Map<String,Object>)Json.parse(new String(stream.readAllBytes(),StandardCharsets.UTF_8));
        }
        var policy=new MaterialAdmissionPolicy(raw);Object token=new Object();
        var source=new ArrayList<>(Arrays.asList(token,null));
        assertTrue(MaterialCallGate.collectionMultiplyOperand(policy,source,2));
        var result=DefaultGroovyMethods.multiply(source,2);
        assertEquals(Arrays.asList(token,null,token,null),result);
        assertSame(token,result.getFirst());assertSame(token,result.get(2));
        assertEquals(Arrays.asList(token,null),source);
        assertTrue(MaterialCallGate.collectionMultiplyOperand(policy,source,-1));
        assertThrows(IllegalArgumentException.class,()->DefaultGroovyMethods.multiply(source,-1));
        assertTrue(MaterialCallGate.collectionMultiplyOperand(policy,source,null));
        assertThrows(NullPointerException.class,()->DefaultGroovyMethods.multiply(source,null));
        Number hostile=new Number() {
            public int intValue(){throw new AssertionError("Unselected numeric conversion invoked");}
            public long longValue(){throw new AssertionError("Unselected numeric conversion invoked");}
            public float floatValue(){throw new AssertionError("Unselected numeric conversion invoked");}
            public double doubleValue(){throw new AssertionError("Unselected numeric conversion invoked");}
        };
        assertFalse(MaterialCallGate.collectionMultiplyOperand(policy,source,hostile));
        assertFalse(MaterialCallGate.collectionMultiplyOperand(policy,new ArrayList<>() {
            public Object[] toArray(){throw new AssertionError("Unselected array conversion invoked");}
        },2));
    }
    @Test void joinPrecheckRefusesUnselectedFormattingAndIteration() {
        assertTrue(MaterialCallGate.collectionJoinOperand(new ArrayList<>(Arrays.asList("one",null,"two"))));
        assertFalse(MaterialCallGate.collectionJoinOperand(new ArrayList<>(List.of(new Object(){
            public String toString(){throw new AssertionError("Unselected formatter invoked");}
        }))));
        assertFalse(MaterialCallGate.collectionJoinOperand(new ArrayList<>() {
            public Object[] toArray(){throw new AssertionError("Unselected array conversion invoked");}
        }));
        assertFalse(MaterialCallGate.collectionJoinOperand(new ArrayList<>(List.of(List.of("nested")))));
        assertFalse(MaterialCallGate.collectionJoinOperand(null));
    }
    @Test void flattenPrecheckCannotInvokeNestedCollectionCallbacks() {
        var token=new Object();var nested=new ArrayList<>(Arrays.asList(token,null,new int[]{2,3}));
        var input=new ArrayList<>(List.of(1,nested));
        assertTrue(MaterialCallGate.collectionFlattenOperand(input));
        assertEquals(Arrays.asList(1,token,null,2,3),DefaultGroovyMethods.flatten(input));
        assertSame(nested,input.get(1));assertSame(token,nested.getFirst());
        var hostile=new ArrayList<Object>() {
            @Override public Iterator<Object> iterator(){throw new AssertionError("Unselected iterator invoked");}
            @Override public Object[] toArray(){throw new AssertionError("Unselected array conversion invoked");}
        };
        assertFalse(MaterialCallGate.collectionFlattenOperand(hostile));
        assertFalse(MaterialCallGate.collectionFlattenOperand(new ArrayList<>(List.of(hostile))));
        assertFalse(MaterialCallGate.collectionFlattenOperand(new ArrayList<>(Collections.singletonList(new Object[]{hostile}))));
        assertFalse(MaterialCallGate.collectionFlattenOperand(null));
    }
    @SuppressWarnings("unchecked") @Test void originalListAdditionRetainsInputsAndRefusesUnselectedIteration() throws Exception {
        Map<String,Object> raw;
        try (var stream = getClass().getResourceAsStream("/axiom-material-admission.json")) {
            assertNotNull(stream); raw = (Map<String,Object>)Json.parse(new String(stream.readAllBytes(), StandardCharsets.UTF_8));
        }
        raw.put("listOperations", Map.of(ArrayList.class.getName(), List.of("plus", "iterator"), IntRange.class.getName(), List.of("iterator")));
        var policy = new MaterialAdmissionPolicy(raw);
        var left = new ArrayList<>(List.of(340, 393)); var right = new IntRange(27200, 27202);
        assertTrue(MaterialCallGate.collectionAdditionOperand(policy, right));
        assertEquals(List.of(340, 393, 27200, 27201, 27202), DefaultGroovyMethods.plus(left, right));
        assertEquals(List.of(340, 393), left); assertEquals(List.of(27200, 27201, 27202), right);
        var hostile = new ArrayList<Object>() {
            @Override public Iterator<Object> iterator() { throw new AssertionError("Unselected iterator invoked"); }
            @Override public Object[] toArray() { throw new AssertionError("Unselected array conversion invoked"); }
        };
        assertFalse(MaterialCallGate.collectionAdditionOperand(policy, hostile));
        assertFalse(MaterialCallGate.collectionAdditionOperand(policy, (Iterable<?>)() -> { throw new AssertionError("Unselected iterable invoked"); }));
        assertFalse(MaterialCallGate.collectionAdditionOperand(policy, null));
    }
}
