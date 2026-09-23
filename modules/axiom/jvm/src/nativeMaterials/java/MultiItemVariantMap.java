// Retained pinned GTCEu source; LGPL-3.0. See sources/native-items.lock.json and spec/native-items.md.
package research.orthrus.axiom.nativeconstruction;


import it.unimi.dsi.fastutil.shorts.Short2ObjectMap;
import it.unimi.dsi.fastutil.shorts.Short2ObjectOpenHashMap;

/**
 * {@link ItemVariantMap} implementation backed by a hashmap. Each metadata is
 * treated as separate entry in hashmap, and get/set accesses treat each metadata
 * as a unique item variant.
 *
 * @param <E> type of the elements
 */
public final class MultiItemVariantMap<E> implements ItemVariantMap.Mutable<E> {

    
    private Short2ObjectOpenHashMap<E> itemDamageEntries;
    
    private E wildcardEntry;

    @Override
    public boolean hasNonWildcardEntry() {
        return this.itemDamageEntries != null && !this.itemDamageEntries.isEmpty();
    }

    @Override
    public boolean has(short meta) {
        if (meta == ItemConstants.W) {
            return this.wildcardEntry != null;
        } else {
            return this.itemDamageEntries != null && this.itemDamageEntries.containsKey(meta);
        }
    }

    
    @Override
    public E get(short meta) {
        if (meta == ItemConstants.W) {
            return this.wildcardEntry;
        } else if (this.itemDamageEntries != null) {
            return this.itemDamageEntries.get(meta);
        } else {
            return null;
        }
    }

    
    @Override
    public E put(short meta,  E e) {
        if (meta == ItemConstants.W) {
            E cache = this.wildcardEntry;
            this.wildcardEntry = e;
            return cache;
        } else if (e != null) {
            if (this.itemDamageEntries == null) {
                this.itemDamageEntries = new Short2ObjectOpenHashMap<>();
            }
            return this.itemDamageEntries.put(meta, e);
        } else {
            if (this.itemDamageEntries != null) {
                return this.itemDamageEntries.remove(meta);
            } else {
                return null;
            }
        }
    }

    @Override
    public void clear() {
        this.itemDamageEntries = null;
        this.wildcardEntry = null;
    }

    @Override
    public String toString() {
        StringBuilder stb = new StringBuilder().append("MultiItemVariantMap[");
        boolean first = true;
        if (itemDamageEntries != null) {
            for (Short2ObjectMap.Entry<E> e : itemDamageEntries.short2ObjectEntrySet()) {
                if (first) first = false;
                else stb.append(',');
                stb.append(e.getShortKey()).append("=").append(e.getValue());
            }
        }
        if (wildcardEntry != null) {
            if (!first) stb.append(',');
            stb.append("*=").append(wildcardEntry);
        }
        return stb.append(']').toString();
    }
}
