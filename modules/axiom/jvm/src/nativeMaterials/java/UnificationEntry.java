// Retained pinned GTCEu source; LGPL-3.0. See sources/native-items.lock.json and spec/native-items.md.
package research.orthrus.axiom.nativeconstruction;



import java.util.Objects;

public class UnificationEntry {

    public final OrePrefix orePrefix;
    
    public final FluidMaterial material;

    public UnificationEntry(OrePrefix orePrefix,  FluidMaterial material) {
        this.orePrefix = orePrefix;
        this.material = material;
    }

    public UnificationEntry(OrePrefix orePrefix) {
        this.orePrefix = orePrefix;
        this.material = null;
    }

    @Override
    public boolean equals(Object o) {
        if (this == o) return true;
        if (o == null || getClass() != o.getClass()) return false;

        UnificationEntry that = (UnificationEntry) o;

        if (orePrefix != that.orePrefix) return false;
        return Objects.equals(material, that.material);
    }

    @Override
    public int hashCode() {
        int result = orePrefix.hashCode();
        result = 31 * result + (material != null ? material.hashCode() : 0);
        return result;
    }

    @Override
    public String toString() {
        return orePrefix.name() + (material != null ? material.toCamelCaseString() : "");
    }
}
