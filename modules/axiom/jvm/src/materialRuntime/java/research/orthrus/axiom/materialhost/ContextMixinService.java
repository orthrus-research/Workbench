package research.orthrus.axiom.materialhost;

import com.cleanroommc.cleanmix.service.CleanMixService;
import java.net.*;
import java.nio.file.*;
import java.security.*;
import java.util.*;

/** Native CleanMix services with explicit artifact audit IDs, not installed-mod discovery. */
public final class ContextMixinService extends CleanMixService {
    private static Map<URI,String> sources;
    private static final Map<String,String> reached = new TreeMap<>();
    public static void bindSources(List<URL> urls) throws Exception {
        if (sources != null) throw new IllegalStateException("Mixin audit sources already bound");
        var values = new HashMap<URI,String>();
        for (URL url : urls) {
            URI uri = url.toURI().normalize();
            if (!uri.getScheme().equals("file")) throw new IllegalArgumentException("Unqualified mixin source URI");
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            try (var input = Files.newInputStream(Path.of(uri))) {
                byte[] buffer = new byte[65536];
                for (int size; (size=input.read(buffer))!=-1;) digest.update(buffer,0,size);
            }
            if (values.put(uri,"axiom-artifact:sha256:"+HexFormat.of().formatHex(digest.digest()))!=null)
                throw new IllegalArgumentException("Duplicate mixin audit source");
        }
        sources = Map.copyOf(values);
    }
    @Override protected String resolveSourceId(URI source) {
        if (sources == null) throw new IllegalStateException("Mixin audit sources not bound");
        String identity = sources.get(source.normalize());
        if (identity == null) throw new IllegalArgumentException("Unqualified mixin audit source: "+source);
        reached.put(source.getPath(),identity);
        return identity;
    }
    public static Map<String,String> observations() { return Map.copyOf(reached); }
}
