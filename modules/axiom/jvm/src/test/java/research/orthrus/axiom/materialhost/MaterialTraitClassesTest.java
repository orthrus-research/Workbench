package research.orthrus.axiom.materialhost;

import org.junit.jupiter.api.Test;
import org.objectweb.asm.Opcodes;
import org.objectweb.asm.tree.*;
import java.lang.reflect.Field;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;

class MaterialTraitClassesTest {
    @Test void catalogCopiesAllSignaturesWithoutRetainingCompilerTrees() throws Exception {
        var node=new ClassNode();node.name="catalog/Marker$Trait$Helper";
        node.methods.add(new MethodNode(Opcodes.ACC_PUBLIC,"method","()V",null,null));
        node.methods.add(new MethodNode(Opcodes.ACC_PUBLIC,"method","(I)V",null,null));
        node.methods.add(new MethodNode(Opcodes.ACC_SYNTHETIC,"$init$","()V",null,null));
        var members=new MaterialCallGate.GuestMembers(List.of(),List.of());
        Field source=MaterialTraitClasses.class.getDeclaredField("SOURCE");source.setAccessible(true);
        @SuppressWarnings("unchecked") var catalog=(Map<String,Object>)source.get(null);
        String name=node.name.replace('/','.');assertFalse(catalog.containsKey(name));
        try {
            MaterialTraitClasses.register(node,members,new byte[0]);
            Object entry=catalog.get(name);
            node.methods.clear(); // Catalog must not retain even the mutable method list.
            Field methods=entry.getClass().getDeclaredField("methods");methods.setAccessible(true);
            assertEquals(Set.of("method()V","method(I)V","$init$()V"),methods.get(entry));
            for(Field field:entry.getClass().getDeclaredFields()) {
                field.setAccessible(true);
                Object value=field.get(entry);
                assertTrue(value instanceof String || value instanceof Set<?> || value==members);
                if(value instanceof Set<?> signatures) {
                    assertTrue(signatures.stream().allMatch(String.class::isInstance));
                    assertThrows(UnsupportedOperationException.class,signatures::clear);
                }
            }
        } finally {catalog.remove(name);}
    }
}
