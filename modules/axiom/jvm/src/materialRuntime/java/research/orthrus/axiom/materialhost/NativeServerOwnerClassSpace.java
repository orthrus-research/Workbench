package research.orthrus.axiom.materialhost;

import com.google.gson.JsonObject;
import net.minecraft.launchwrapper.Launch;
import java.io.File;
import java.lang.reflect.Array;
import java.net.Proxy;
import java.util.*;
import java.util.concurrent.ThreadFactory;

/** Required original server ownership without invoking startup or a game loop. */
final class NativeServerOwnerClassSpace {
    static final String MODE = "original-dedicated-server-construction";
    private static final String SERVER = "net.minecraft.server.MinecraftServer";
    private static final String DEDICATED = "net.minecraft.server.dedicated.DedicatedServer";
    private static final String AUTH = "com.mojang.authlib.yggdrasil.YggdrasilAuthenticationService";
    private static final String SESSION = "com.mojang.authlib.minecraft.MinecraftSessionService";
    private static final String REPOSITORY = "com.mojang.authlib.GameProfileRepository";
    private static final String CACHE = "net.minecraft.server.management.PlayerProfileCache";
    private static final String FIXER = "net.minecraft.util.datafix.DataFixer";
    @FunctionalInterface interface Action { void run() throws Exception; }
    private NativeServerOwnerClassSpace() {}

    static Object construct(JsonObject context, Map<String,Object> observed) throws Exception {
        if (!context.has("serverOwner")) return null;
        require(MODE.equals(context.get("serverOwner").getAsString()), "Selected server ownership mode differs");
        require(Boolean.TRUE.equals(observed.get("vanillaRegistrationReturned")), "Server construction precedes vanilla registration");
        require(!Launch.classLoader.isClassLoaded(NativeServerOwnerPrefix.TARGET), "Server handler was initialized before server construction");
        observed.put("stage", "original-server-owner-construction");
        var authType = type(AUTH);
        Object auth = authType.getConstructor(Proxy.class, String.class).newInstance(Proxy.NO_PROXY, UUID.randomUUID().toString());
        Object session = authType.getMethod("createMinecraftSessionService").invoke(auth);
        Object repository = authType.getMethod("createProfileRepository").invoke(auth);
        var serverType = type(SERVER);
        File userCache = (File)NativeObservationAccess.field(serverType, null, "field_152367_a");
        Object cache = type(CACHE).getConstructor(type(REPOSITORY), File.class)
                .newInstance(repository, new File(Launch.minecraftHome, userCache.getName()));
        Object fixer = type("net.minecraft.util.datafix.DataFixesManager").getMethod("func_188279_a").invoke(null);
        Object server = type(DEDICATED).getConstructor(File.class, type(FIXER), authType, type(SESSION), type(REPOSITORY), type(CACHE))
                .newInstance(Launch.minecraftHome, fixer, auth, session, repository, cache);
        require(NativeObservationAccess.field(serverType, server, "field_184112_s") == fixer
                && fixer.getClass() == type("net.minecraftforge.common.util.CompoundDataFixer"), "Original server data fixer identity differs");
        var row = new LinkedHashMap<String,Object>(); observed.put("nativeServerOwner", row);
        row.put("mode", MODE); row.put("constructorReturned", true);
        row.put("serverClass", server.getClass().getName()); row.put("dataFixerClass", fixer.getClass().getName());
        row.put("serverCodeSource", server.getClass().getProtectionDomain().getCodeSource().getLocation().toString());
        row.put("dataFixerCodeSource", fixer.getClass().getProtectionDomain().getCodeSource().getLocation().toString());
        row.put("serverArtifact", NativeEarlyClassSpace.sourceFile(server.getClass().getProtectionDomain().getCodeSource().getLocation()).toString());
        row.put("dataFixerArtifact", NativeEarlyClassSpace.sourceFile(fixer.getClass().getProtectionDomain().getCodeSource().getLocation()).toString());
        row.put("nativeDefiningLoader", server.getClass().getClassLoader() == Launch.classLoader
                && fixer.getClass().getClassLoader() == Launch.classLoader);
        require(Boolean.TRUE.equals(row.get("nativeDefiningLoader")), "Original server owner classloader differs");
        verifyUnstarted(server, row);
        return server;
    }

    static void bind(Object handler, Object server, Map<String,Object> observed) throws Exception {
        if (server == null) return;
        var handlerType = type(NativeServerOwnerPrefix.TARGET);
        require(NativeObservationAccess.field(handlerType, handler, "server") == null, "Native server handler ownership was already assigned");
        handlerType.getMethod(NativeServerOwnerPrefix.METHOD, type(SERVER)).invoke(handler, server);
        Object fixer = NativeObservationAccess.field(type(SERVER), server, "field_184112_s");
        require(NativeObservationAccess.field(handlerType, handler, "server") == server
                && handlerType.getMethod("getDataFixer").invoke(handler) == fixer, "Native handler data fixer ownership differs");
        row(observed).put("originalOwnershipPrefixReturned", true);
    }

    static void initialize(Object server, Map<String,Object> observed, Action action) throws Exception {
        if (server == null) { action.run(); return; }
        // Original startup runs initialization in this native sided group. Its
        // game Runnable, serverThread field and startup methods remain unused.
        var group = (ThreadFactory)type("net.minecraftforge.fml.common.thread.SidedThreadGroups").getField("SERVER").get(null);
        Throwable[] failure = new Throwable[1];
        Thread thread = group.newThread(() -> {
            try {
                row(observed).put("threadGroup", Thread.currentThread().getThreadGroup().getName());
                action.run();
            } catch (Throwable caught) { failure[0] = caught; }
        });
        thread.setName("Axiom native initialization"); thread.start();
        try { thread.join(); }
        catch (InterruptedException interrupted) { thread.interrupt(); Thread.currentThread().interrupt(); throw interrupted; }
        try { verifyUnstarted(server, row(observed)); }
        catch (Exception boundaryFailure) {
            if (failure[0] == null) throw boundaryFailure;
            failure[0].addSuppressed(boundaryFailure);
        }
        if (failure[0] instanceof Exception exception) throw exception;
        if (failure[0] instanceof Error error) throw error;
        if (failure[0] != null) throw new IllegalStateException("Original initialization failed", failure[0]);
    }

    private static void verifyUnstarted(Object server, Map<String,Object> row) throws Exception {
        var owner = type(SERVER);
        Object worlds = NativeObservationAccess.field(owner, server, "field_71305_c");
        Object network = NativeObservationAccess.field(owner, server, "field_147144_o");
        Object snooper = NativeObservationAccess.field(owner, server, "field_71307_n");
        int worldCount = Array.getLength(worlds);
        int endpoints = ((List<?>)NativeObservationAccess.field(network.getClass(), network, "field_151274_e")).size();
        int connections = ((List<?>)NativeObservationAccess.field(network.getClass(), network, "field_151272_f")).size();
        boolean threadAbsent = NativeObservationAccess.field(owner, server, "field_175590_aa") == null;
        boolean snooperStarted = (boolean)NativeObservationAccess.field(snooper.getClass(), snooper, "field_76477_g");
        require(worldCount == 0 && endpoints == 0 && connections == 0 && threadAbsent && !snooperStarted,
                "Native server ownership crossed the construction boundary");
        row.put("worldCount", worldCount); row.put("networkEndpointCount", endpoints); row.put("networkConnectionCount", connections);
        row.put("gameThreadAbsent", threadAbsent); row.put("snooperStarted", snooperStarted);
    }

    @SuppressWarnings("unchecked") private static Map<String,Object> row(Map<String,Object> observed) {
        return (Map<String,Object>)observed.get("nativeServerOwner");
    }
    private static Class<?> type(String name) throws ClassNotFoundException { return Class.forName(name, true, Launch.classLoader); }
    private static void require(boolean value, String message) { if (!value) throw new IllegalStateException(message); }
}
