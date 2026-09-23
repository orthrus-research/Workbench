package dev.workbench.crucible.runtimegraph.mixin;

import com.cleanroommc.groovyscript.registry.ReloadableRegistryManager;
import com.cleanroommc.groovyscript.sandbox.GroovyScriptSandbox;
import com.cleanroommc.groovyscript.sandbox.LoadStage;

import dev.workbench.crucible.runtimegraph.provenance.ProgramProvenanceTrace;

import groovy.lang.Script;

import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.Redirect;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;

/** Captures exact Groovy stage and script execution boundaries without timing data. */
@Mixin(value = GroovyScriptSandbox.class, remap = false)
public abstract class MixinGroovyScriptSandbox {
    @Inject(method = "run", at = @At("HEAD"))
    private void workbench$beginEpoch(LoadStage stage, CallbackInfo callback) {
        ProgramProvenanceTrace.beginEpoch(
            stage.getName(),
            stage.isReloadable(),
            ReloadableRegistryManager.isFirstLoad()
        );
    }

    @Inject(method = "run", at = @At("RETURN"))
    private void workbench$endEpoch(LoadStage stage, CallbackInfo callback) {
        ProgramProvenanceTrace.endEpoch(stage.getName());
    }

    @Redirect(
        method = "runScript",
        at = @At(value = "INVOKE", target = "Lgroovy/lang/Script;run()Ljava/lang/Object;")
    )
    private Object workbench$runScript(Script script) {
        ProgramProvenanceTrace.Execution execution = ProgramProvenanceTrace.beginExecution(
            "script-body",
            script.getClass().getName()
        );
        try {
            Object result = script.run();
            ProgramProvenanceTrace.endExecution(execution, null);
            return result;
        } catch (RuntimeException failure) {
            ProgramProvenanceTrace.endExecution(execution, failure);
            throw failure;
        } catch (Error failure) {
            ProgramProvenanceTrace.endExecution(execution, failure);
            throw failure;
        }
    }

    @Redirect(
        method = "runClass",
        at = @At(
            value = "INVOKE",
            target = "Ljava/lang/reflect/Method;invoke(Ljava/lang/Object;[Ljava/lang/Object;)Ljava/lang/Object;"
        )
    )
    private Object workbench$runClass(
        Method method,
        Object receiver,
        Object[] arguments
    ) throws IllegalAccessException, InvocationTargetException {
        ProgramProvenanceTrace.Execution execution = ProgramProvenanceTrace.beginExecution(
            "class-initializer",
            method.getDeclaringClass().getName()
        );
        try {
            Object result = method.invoke(receiver, arguments);
            ProgramProvenanceTrace.endExecution(execution, null);
            return result;
        } catch (IllegalAccessException failure) {
            ProgramProvenanceTrace.endExecution(execution, failure);
            throw failure;
        } catch (InvocationTargetException failure) {
            Throwable cause = failure.getCause() == null ? failure : failure.getCause();
            ProgramProvenanceTrace.endExecution(execution, cause);
            throw failure;
        } catch (RuntimeException failure) {
            ProgramProvenanceTrace.endExecution(execution, failure);
            throw failure;
        } catch (Error failure) {
            ProgramProvenanceTrace.endExecution(execution, failure);
            throw failure;
        }
    }
}
