package research.orthrus.axiom;

import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;

/** Strict JSON; duplicate keys and lossy integer coercion are request errors. */
public final class Json {
    private final String text;
    private int at;
    private Json(String text) { this.text = text; }

    public static Object parse(String text) {
        return parse(text, 0);
    }
    static Object parse(String text, int limit) {
        if (limit > 0 && text.length() > limit) throw Failure.request("JSON exceeds its character bound");
        Json parser = new Json(text);
        Object result = parser.value(0);
        parser.space();
        if (parser.at != text.length()) throw Failure.request("Trailing JSON data");
        return result;
    }

    private void space() {
        while (at < text.length() && " \r\n\t".indexOf(text.charAt(at)) >= 0) at++;
    }
    private boolean take(char ch) {
        space();
        if (at < text.length() && text.charAt(at) == ch) { at++; return true; }
        return false;
    }
    private void need(char ch) { if (!take(ch)) throw Failure.request("Expected '" + ch + "' at " + at); }
    private static boolean digit(char ch) { return ch >= '0' && ch <= '9'; }
    private Object value(int depth) {
        if (depth > 48) throw Failure.request("JSON nesting exceeds 48");
        space();
        if (at >= text.length()) throw Failure.request("Incomplete JSON");
        char ch = text.charAt(at);
        if (ch == '"') return string();
        if (take('{')) {
            Map<String, Object> result = new LinkedHashMap<>();
            if (take('}')) return result;
            do {
                String key = string(); need(':');
                if (result.containsKey(key)) throw Failure.request("Duplicate JSON key: " + key);
                result.put(key, value(depth + 1));
            } while (take(','));
            need('}'); return result;
        }
        if (take('[')) {
            List<Object> result = new ArrayList<>();
            if (take(']')) return result;
            do { result.add(value(depth + 1)); } while (take(','));
            need(']'); return result;
        }
        for (String literal : List.of("true", "false", "null")) {
            if (text.startsWith(literal, at)) {
                at += literal.length();
                return literal.equals("null") ? null : Boolean.valueOf(literal);
            }
        }
        int start = at;
        if (at < text.length() && text.charAt(at) == '-') at++;
        if (at >= text.length()) throw Failure.request("Invalid JSON number");
        if (text.charAt(at) == '0') at++;
        else {
            if (text.charAt(at) < '1' || text.charAt(at) > '9') throw Failure.request("Invalid JSON value at " + at);
            while (at < text.length() && digit(text.charAt(at))) at++;
        }
        if (at < text.length() && text.charAt(at) == '.') {
            at++; int digits = at;
            while (at < text.length() && digit(text.charAt(at))) at++;
            if (digits == at) throw Failure.request("Invalid JSON fraction");
        }
        if (at < text.length() && "eE".indexOf(text.charAt(at)) >= 0) {
            at++;
            if (at < text.length() && "+-".indexOf(text.charAt(at)) >= 0) at++;
            int digits = at;
            while (at < text.length() && digit(text.charAt(at))) at++;
            if (digits == at) throw Failure.request("Invalid JSON exponent");
        }
        if (at - start > 128) throw Failure.request("JSON number exceeds bound");
        try { return new BigDecimal(text.substring(start, at)); }
        catch (NumberFormatException exception) { throw Failure.request("Invalid JSON number"); }
    }
    private String string() {
        need('"'); StringBuilder result = new StringBuilder();
        while (at < text.length()) {
            char ch = text.charAt(at++);
            if (ch == '"') {
                for (int i = 0; i < result.length(); i++) {
                    char current = result.charAt(i);
                    if (Character.isHighSurrogate(current)) {
                        if (++i >= result.length() || !Character.isLowSurrogate(result.charAt(i))) throw Failure.request("Unpaired Unicode surrogate");
                    } else if (Character.isLowSurrogate(current)) throw Failure.request("Unpaired Unicode surrogate");
                }
                return result.toString();
            }
            if (ch < 32) throw Failure.request("Control character in JSON string");
            if (ch == '\\') {
                if (at >= text.length()) throw Failure.request("Incomplete JSON escape");
                ch = text.charAt(at++);
                switch (ch) {
                    case '"', '\\', '/' -> result.append(ch);
                    case 'b' -> result.append('\b');
                    case 'f' -> result.append('\f');
                    case 'n' -> result.append('\n');
                    case 'r' -> result.append('\r');
                    case 't' -> result.append('\t');
                    case 'u' -> {
                        if (at + 4 > text.length()) throw Failure.request("Incomplete Unicode escape");
                        if (!text.substring(at, at + 4).matches("[0-9a-fA-F]{4}")) throw Failure.request("Invalid Unicode escape");
                        try { result.append((char) Integer.parseInt(text.substring(at, at + 4), 16)); }
                        catch (NumberFormatException exception) { throw Failure.request("Invalid Unicode escape"); }
                        at += 4;
                    }
                    default -> throw Failure.request("Invalid JSON escape");
                }
            } else result.append(ch);
        }
        throw Failure.request("Unclosed JSON string");
    }

    public static String write(Object value) {
        if (value == null) return "null";
        if (value instanceof String s) {
            StringBuilder result = new StringBuilder("\"");
            for (int i = 0; i < s.length(); i++) {
                char c = s.charAt(i);
                if (c == '"' || c == '\\') result.append('\\').append(c);
                else if (c < 32 || Character.isSurrogate(c)) result.append(String.format("\\u%04x", (int)c));
                else result.append(c);
            }
            return result.append('"').toString();
        }
        if (value instanceof Boolean || value instanceof Number) return value.toString();
        if (value instanceof Map<?, ?> map) {
            List<String> parts = new ArrayList<>();
            // Stable fingerprints do not depend on JSON object property order.
            for (String key : new TreeSet<>(map.keySet().stream().map(Object::toString).toList()))
                parts.add(write(key) + ":" + write(map.get(key)));
            return "{" + String.join(",", parts) + "}";
        }
        if (value instanceof Collection<?> list) return "[" + String.join(",", list.stream().map(Json::write).toList()) + "]";
        throw new IllegalArgumentException("Not JSON: " + value.getClass());
    }
    public static String digest(Object value) { return bytesDigest(write(value).getBytes(StandardCharsets.UTF_8)); }
    public static String bytesDigest(byte[] bytes) {
        try { return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes)); }
        catch (java.security.NoSuchAlgorithmException exception) { throw new AssertionError(exception); }
    }
    @SuppressWarnings("unchecked")
    static Map<String, Object> object(Object value) {
        if (!(value instanceof Map<?, ?>)) throw Failure.request("Expected JSON object");
        return (Map<String, Object>) value;
    }
    @SuppressWarnings("unchecked")
    static List<Object> array(Object value) {
        if (!(value instanceof List<?>)) throw Failure.request("Expected JSON array");
        return (List<Object>) value;
    }
    static String string(Object value) {
        if (!(value instanceof String result)) throw Failure.request("Expected string");
        return result;
    }
    static long integer(Object value) {
        if (!(value instanceof Number)) throw Failure.request("Expected numeric JSON integer, not a coerced string");
        try { return new BigDecimal(value.toString()).longValueExact(); }
        catch (RuntimeException exception) { throw Failure.request("Expected exact signed 64-bit integer"); }
    }
    static int number(Object value) {
        long result = integer(value);
        if (result < Integer.MIN_VALUE || result > Integer.MAX_VALUE) throw Failure.request("Expected signed 32-bit integer");
        return (int) result;
    }
    static boolean bool(Object value) {
        if (!(value instanceof Boolean result)) throw Failure.request("Expected boolean");
        return result;
    }
    static void keys(Map<String, Object> value, String... allowed) {
        Set<String> names = Set.of(allowed);
        for (String key : value.keySet()) if (!names.contains(key)) throw Failure.request("Unknown field: " + key);
    }
}
