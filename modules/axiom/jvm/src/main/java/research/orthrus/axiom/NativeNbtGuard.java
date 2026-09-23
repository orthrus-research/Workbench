// Extracted from pinned GTCEu/Cleanroom source. See spec/native-fluids.md and sources/NOTICE.md.
package research.orthrus.axiom;
// Selected Cleanroom NBTTagCompound patch, Minecraft Forge LGPL-2.1.
// Original license is distributed in sources/licenses/forge/LGPL-2.1.txt.
final class NativeNbtGuard {
    static void requireTag(String key, NativeNbtValue value) {
        if (value == null) throw new IllegalArgumentException("Invalid null NBT value with key " + key);
    }
}
