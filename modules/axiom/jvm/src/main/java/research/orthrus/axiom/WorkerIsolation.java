package research.orthrus.axiom;

import java.lang.foreign.*;
import java.lang.invoke.MethodHandle;
import java.nio.ByteOrder;
import java.util.*;

/**
 * Linux x86_64 worker-only kernel limits. Install after the JVM starts, before
 * compilation. Namespace/mount isolation and the deadline remain the supervisor's
 * responsibility. Never install this in the host/library caller's JVM.
 */
final class WorkerIsolation {
    private WorkerIsolation() {}
    private static final int ALLOW = 0x7fff0000, ERRNO = 0x00050000, KILL = 0x80000000;
    private static final ValueLayout.OfLong LONG = ValueLayout.JAVA_LONG;
    private static final ValueLayout.OfInt INT = ValueLayout.JAVA_INT;
    private static final AddressLayout ADDRESS = ValueLayout.ADDRESS;

    static void install() {
        if (!System.getProperty("os.name").equals("Linux") || !System.getProperty("os.arch").equals("amd64")
                || ByteOrder.nativeOrder() != ByteOrder.LITTLE_ENDIAN)
            throw unavailable("Only the selected Linux x86_64 syscall ABI is admitted");
        try (Arena arena = Arena.ofConfined()) {
            Linker linker = Linker.nativeLinker();
            SymbolLookup libc = linker.defaultLookup();
            MethodHandle prctl = linker.downcallHandle(libc.find("prctl").orElseThrow(),
                    FunctionDescriptor.of(INT, INT, LONG, LONG, LONG, LONG));
            if ((int)prctl.invokeExact(38, 1L, 0L, 0L, 0L) != 0)
                throw unavailable("PR_SET_NO_NEW_PRIVS failed");
            MethodHandle setrlimit = linker.downcallHandle(libc.find("setrlimit").orElseThrow(),
                    FunctionDescriptor.of(INT, INT, ADDRESS));
            // MVP resource targets are suspended. Inherit host resource limits;
            // core-dump suppression is independent of execution budgets.
            for (long[] limit : new long[][]{{4, 0}}) {
                MemorySegment value = arena.allocate(16, 8);
                value.set(LONG, 0, limit[1]); value.set(LONG, 8, limit[1]);
                if ((int)setrlimit.invokeExact((int)limit[0], value) != 0)
                    throw unavailable("setrlimit failed for resource " + limit[0]);
            }
            List<int[]> instructions = filter();
            MemorySegment code = arena.allocate(instructions.size() * 8L, 8);
            for (int i = 0; i < instructions.size(); i++) {
                int[] row = instructions.get(i); long offset = i * 8L;
                code.set(ValueLayout.JAVA_SHORT, offset, (short)row[0]);
                code.set(ValueLayout.JAVA_BYTE, offset + 2, (byte)row[1]);
                code.set(ValueLayout.JAVA_BYTE, offset + 3, (byte)row[2]);
                code.set(INT, offset + 4, row[3]);
            }
            MemorySegment program = arena.allocate(16, 8);
            program.set(ValueLayout.JAVA_SHORT, 0, (short)instructions.size());
            program.set(ADDRESS, 8, code);
            MethodHandle syscall = linker.downcallHandle(libc.find("syscall").orElseThrow(),
                    FunctionDescriptor.of(LONG, LONG, LONG, LONG, ADDRESS), Linker.Option.firstVariadicArg(1));
            // seccomp(SECCOMP_SET_MODE_FILTER, TSYNC, program). TSYNC is required:
            // compiler/GC threads already exist and must not retain an unrestricted policy.
            long result = (long)syscall.invokeExact(317L, 1L, 1L, program);
            if (result != 0) throw unavailable("Thread-synchronized seccomp installation failed: " + result);
        } catch (Failure failure) { throw failure; }
        catch (Throwable failure) { throw unavailable(failure.getClass().getSimpleName() + ": " + failure.getMessage()); }
    }

    private static Failure unavailable(String reason) {
        return new Failure("incomplete", "sandbox.kernel-policy", "Worker was not admitted: " + reason);
    }

    private static List<int[]> filter() {
        List<int[]> code = new ArrayList<>();
        code.add(new int[]{0x20, 0, 0, 4}); // seccomp_data.arch
        code.add(new int[]{0x15, 1, 0, 0xc000003e});
        code.add(new int[]{0x06, 0, 0, KILL});
        code.add(new int[]{0x20, 0, 0, 0}); // seccomp_data.nr
        code.add(new int[]{0x35, 0, 1, 0x40000000}); // no x32 ABI escape
        code.add(new int[]{0x06, 0, 0, KILL});
        code.add(new int[]{0x15, 0, 1, 435}); // clone3: libc must use inspectable clone flags
        code.add(new int[]{0x06, 0, 0, ERRNO | 38}); // ENOSYS
        code.add(new int[]{0x15, 0, 4, 56}); // clone
        code.add(new int[]{0x20, 0, 0, 16}); // args[0], low word
        code.add(new int[]{0x45, 1, 0, 0x00010000}); // CLONE_THREAD only
        code.add(new int[]{0x06, 0, 0, ERRNO | 1});
        code.add(new int[]{0x06, 0, 0, ALLOW});
        // The selected JDK's UnixDispatcher.init creates its private pre-close
        // descriptor with socketpair(AF_UNIX, SOCK_STREAM, 0). These unnamed,
        // already-connected endpoints cannot address another process or host.
        // Keep socket/connect/bind/listen and all other socketpair forms denied.
        code.add(new int[]{0x15, 0, 8, 53});
        code.add(new int[]{0x20, 0, 0, 16}); // domain
        code.add(new int[]{0x15, 0, 5, 1});
        code.add(new int[]{0x20, 0, 0, 24}); // type
        code.add(new int[]{0x15, 0, 3, 1});
        code.add(new int[]{0x20, 0, 0, 32}); // protocol
        code.add(new int[]{0x15, 0, 1, 0});
        code.add(new int[]{0x06, 0, 0, ALLOW});
        code.add(new int[]{0x06, 0, 0, ERRNO | 1});
        // Process creation/replacement, network, cross-process memory, namespace
        // changes, filesystem mounts/handles, alternate kernel execution and limit changes.
        for (int syscall : new int[]{41, 42, 43, 49, 50, 57, 58, 59, 101, 155, 157, 160,
                165, 166, 246, 248, 249, 250, 272, 288, 298, 302, 303, 304, 308, 310, 311,
                317, 321, 322, 323, 425, 426, 427, 428, 429, 430, 431, 432, 433, 438, 440, 442}) {
            code.add(new int[]{0x15, 0, 1, syscall});
            code.add(new int[]{0x06, 0, 0, ERRNO | 1});
        }
        code.add(new int[]{0x06, 0, 0, ALLOW});
        return code;
    }
}
