package research.orthrus.axiom.materialhost;

import org.junit.jupiter.api.Test;
import org.objectweb.asm.ClassWriter;
import org.objectweb.asm.Opcodes;
import java.util.Arrays;
import java.util.function.UnaryOperator;
import static org.junit.jupiter.api.Assertions.*;

/** Operand containment only; native research effects require the installed pack lane. */
class AssemblyLineAdmissionTest {
    private static final String BUILDER = "gregtech.api.recipes.builders.AssemblyLineRecipeBuilder";
    private static final String STACK = "net.minecraft.item.ItemStack";

    private static class Definitions extends ClassLoader {
        Class<?> type(String name, String parent) {
            var writer = new ClassWriter(0);
            String owner = name.replace('.', '/'), base = parent.replace('.', '/');
            writer.visit(Opcodes.V17, Opcodes.ACC_PUBLIC, owner, null, base, null);
            var init = writer.visitMethod(Opcodes.ACC_PUBLIC, "<init>", "()V", null, null);
            init.visitCode(); init.visitVarInsn(Opcodes.ALOAD, 0);
            init.visitMethodInsn(Opcodes.INVOKESPECIAL, base, "<init>", "()V", false);
            init.visitInsn(Opcodes.RETURN); init.visitMaxs(1, 1); init.visitEnd(); writer.visitEnd();
            byte[] bytes = writer.toByteArray(); return defineClass(name, bytes, 0, bytes.length);
        }
    }

    @Test void stackFormChecksExactNativeClassWithoutCoercingOrCallingCallbacks() throws Exception {
        var loader = new Definitions();
        Class<?> stack = loader.type(STACK, "java.lang.Object");
        Class<?> builder = loader.type(BUILDER, "java.lang.Object");
        Object receiver = builder.getConstructor().newInstance(), item = stack.getConstructor().newInstance();
        var guard = MaterialCallGate.class.getDeclaredMethod("nativeMethodOperands",
                MaterialAdmissionPolicy.class, Object.class, String.class, Object[].class);
        guard.setAccessible(true);
        Object[] call = {receiver, item};
        assertEquals(true, guard.invoke(null, null, receiver, "scannerResearch", call));
        assertSame(item, call[1]);
        assertEquals(true, guard.invoke(null, null, receiver, "scannerResearch", new Object[]{receiver, null}));
        int[] calls = {0};
        UnaryOperator<Object> callback = value -> {calls[0]++; return value;};
        var closure = new groovy.lang.Closure<Object>(null) {
            public Object doCall(Object value) {calls[0]++; return value;}
        };
        var hostile = new Object() {public String toString() {calls[0]++; return "item";}};
        Object child = loader.type("fixture.StackChild", STACK).getConstructor().newInstance();
        Object foreign = new Definitions().type(STACK, "java.lang.Object").getConstructor().newInstance();
        for (Object value : Arrays.asList(callback, closure, hostile, child, foreign, stack, "item"))
            assertEquals(false, guard.invoke(null, null, receiver, "scannerResearch", new Object[]{receiver, value}));
        assertEquals(false, guard.invoke(null, null, builder, "scannerResearch", new Object[]{builder, item}));
        assertEquals(false, guard.invoke(null, null, receiver, "scannerResearch", new Object[]{receiver}));
        assertEquals(false, guard.invoke(null, null, receiver, "scannerResearch", new Object[]{receiver, item, item}));
        assertEquals(0, calls[0]);
    }
}
