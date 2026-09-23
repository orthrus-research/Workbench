// Retained pinned GTCEu source; LGPL-3.0. See sources/native-items.lock.json and spec/native-items.md.
package research.orthrus.axiom.nativeconstruction;


/**
 * An unmodifiable {@link ItemVariantMap} instance with no elements.
 *
 * @see ItemVariantMap#empty()
 */
final class EmptyVariantMap implements ItemVariantMap<Object> {

    static final EmptyVariantMap INSTANCE = new EmptyVariantMap();

    @Override
    public boolean hasNonWildcardEntry() {
        return false;
    }

    @Override
    public boolean has(short meta) {
        return false;
    }

    
    @Override
    public Object get(short meta) {
        return null;
    }

    @Override
    public String toString() {
        return "EmptyEntry";
    }
}
