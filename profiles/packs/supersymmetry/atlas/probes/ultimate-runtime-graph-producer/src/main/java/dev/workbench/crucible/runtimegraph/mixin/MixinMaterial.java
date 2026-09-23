package dev.workbench.crucible.runtimegraph.mixin;

import dev.workbench.crucible.runtimegraph.provenance.ProgramProvenanceTrace;

import gregtech.api.unification.material.Material;
import gregtech.api.unification.material.info.MaterialFlag;
import gregtech.api.unification.material.info.MaterialIconSet;
import gregtech.api.unification.material.properties.IMaterialProperty;
import gregtech.api.unification.material.properties.PropertyKey;

import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/** Captures post-construction material mutations that static source cannot prove. */
@Mixin(value = Material.class, remap = false)
public abstract class MixinMaterial {
    @Inject(
        method = "addFlags([Lgregtech/api/unification/material/info/MaterialFlag;)V",
        at = @At("RETURN")
    )
    private void workbench$addFlags(MaterialFlag[] flags, CallbackInfo callback) {
        ProgramProvenanceTrace.recordMutation(
            "gt-material-add-flags",
            this,
            (Object[]) flags.clone()
        );
    }

    @Inject(
        method = "setProperty(Lgregtech/api/unification/material/properties/PropertyKey;Lgregtech/api/unification/material/properties/IMaterialProperty;)V",
        at = @At("RETURN")
    )
    private void workbench$setProperty(
        PropertyKey<?> key,
        IMaterialProperty property,
        CallbackInfo callback
    ) {
        ProgramProvenanceTrace.recordMutation(
            "gt-material-set-property",
            this,
            key,
            property
        );
    }

    @Inject(
        method = "setFormula(Ljava/lang/String;Z)Lgregtech/api/unification/material/Material;",
        at = @At("RETURN")
    )
    private void workbench$setFormula(
        String formula,
        boolean format,
        CallbackInfoReturnable<Material> callback
    ) {
        ProgramProvenanceTrace.recordMutation(
            "gt-material-set-formula",
            this,
            formula,
            Boolean.valueOf(format)
        );
    }

    @Inject(method = "setMaterialRGB(I)V", at = @At("RETURN"))
    private void workbench$setColor(int color, CallbackInfo callback) {
        ProgramProvenanceTrace.recordMutation(
            "gt-material-set-color",
            this,
            Integer.valueOf(color)
        );
    }

    @Inject(
        method = "setMaterialIconSet(Lgregtech/api/unification/material/info/MaterialIconSet;)V",
        at = @At("RETURN")
    )
    private void workbench$setIconSet(MaterialIconSet iconSet, CallbackInfo callback) {
        ProgramProvenanceTrace.recordMutation(
            "gt-material-set-icon-set",
            this,
            iconSet
        );
    }
}
