package research.orthrus.axiom;

import java.util.List;
import org.codehaus.groovy.runtime.InvokerInvocationException;
import org.junit.jupiter.api.Test;
import research.orthrus.axiom.materialhost.MaterialCallGate;
import static org.junit.jupiter.api.Assertions.*;

/** Constructs trusted exception data only; never exhausts the test coordinator. */
class MaterialResourceObservationTest {
    static final class GetterTrap extends RuntimeException {
        @Override public synchronized Throwable getCause() {
            throw new AssertionError("Observer must not invoke arbitrary throwable getters");
        }
    }

    @Test void trustedWrappedVmFailureIsStickyWithoutFormattingOrReplacingTheCause() {
        MaterialCallGate.caught(new GetterTrap());
        assertFalse(MaterialCallGate.resourceFailure());
        assertEquals(List.of(GetterTrap.class.getName()),MaterialCallGate.caughtCause());

        var vm=new StackOverflowError();
        var loading=new ClassNotFoundException("unresolved while unwinding",vm);
        var bootstrap=new BootstrapMethodError(loading);
        var original=new InvokerInvocationException(bootstrap);
        MaterialCallGate.caught(original);
        var expected=List.of(InvokerInvocationException.class.getName(),BootstrapMethodError.class.getName(),
                ClassNotFoundException.class.getName(),StackOverflowError.class.getName());
        assertTrue(MaterialCallGate.resourceFailure());
        assertTrue(MaterialCallGate.linkageFailure());
        assertEquals(expected,MaterialCallGate.resourceCause());
        assertSame(bootstrap,original.getCause());
        assertSame(loading,bootstrap.getCause());
        assertSame(vm,loading.getCause());

        MaterialCallGate.caught(new IllegalArgumentException("later caught error"));
        assertEquals(expected,MaterialCallGate.resourceCause(),"Later catches cannot erase the resource failure");
    }
}
