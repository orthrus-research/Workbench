// Source-qualified GTCEu members, LGPL-3.0; see sources/material-blocks.lock.json and spec/material-blocks.md.
package research.orthrus.axiom.nativeconstruction;


import net.minecraft.block.properties.PropertyHelper;

import com.google.common.base.Optional;
import com.google.common.collect.ImmutableList;

import java.util.Arrays;
import java.util.Collection;

public class PropertyMaterial extends PropertyHelper<FluidMaterial> {

    private final ImmutableList<FluidMaterial> allowedValues;

    protected PropertyMaterial(String name, Collection<? extends FluidMaterial> allowedValues) {
        super(name, FluidMaterial.class);
        this.allowedValues = ImmutableList.copyOf(allowedValues);
    }

    public static PropertyMaterial create(String name, Collection<? extends FluidMaterial> allowedValues) {
        return new PropertyMaterial(name, allowedValues);
    }

    public static PropertyMaterial create(String name, FluidMaterial[] allowedValues) {
        return new PropertyMaterial(name, Arrays.asList(allowedValues));
    }

    
    @Override
    public ImmutableList<FluidMaterial> func_177700_c() {
        return allowedValues;
    }

    
    @Override
    public Optional<FluidMaterial> func_185929_b( String value) {
        int index = value.indexOf("__");
        String materialName = index < 0 ? value : value.substring(0, index) + ':' + value.substring(index + 2);
        FluidMaterial material = (FluidMaterial) FluidEnvironment.current().runtime().materials().getMaterial(materialName);
        if (material != null && this.allowedValues.contains(material)) {
            return Optional.of(material);
        }
        return Optional.of(PrefixDependencies.material("NULL"));
    }

    
    @Override
    public String func_177702_a( FluidMaterial material) {
        // Use double underscore to prevent ${modid}_${material_name} being ambiguous with ${material_name} when parsing
        return material.getModid() + "__" + material.getName();
    }

    @Override
    public boolean equals(Object obj) {
        if (this == obj) {
            return true;
        } else if (obj instanceof PropertyMaterial) {
            PropertyMaterial propertyMaterial = (PropertyMaterial) obj;
            return this.allowedValues.equals(propertyMaterial.allowedValues);
        } else {
            return false;
        }
    }

    @Override
    public int hashCode() {
        int i = super.hashCode();
        i = 31 * i + this.allowedValues.hashCode();
        return i;
    }
}
