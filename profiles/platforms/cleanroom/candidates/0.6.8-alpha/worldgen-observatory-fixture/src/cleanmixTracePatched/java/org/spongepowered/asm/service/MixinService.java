/*
 * This file is part of Mixin, licensed under the MIT License (MIT).
 *
 * Copyright (c) SpongePowered <https://www.spongepowered.org>
 * Copyright (c) contributors
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in
 * all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
 * THE SOFTWARE.
 */
package org.spongepowered.asm.service;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.Iterator;
import java.util.List;
import java.util.ServiceConfigurationError;
import java.util.ServiceLoader;
import java.util.Set;

import org.spongepowered.asm.logging.ILogger;
import org.spongepowered.asm.logging.LoggerAdapterConsole;

import com.google.common.base.Joiner;
import com.google.common.collect.ObjectArrays;

import dev.workbench.crucible.cleanmixtrace.CleanMixDiscoveryTraceRuntime;
import dev.workbench.crucible.cleanmixtrace.CleanMixServiceComponentRuntime;

/**
 * Exact CleanMix 0.7.0 MixinService with fail-open observation calls around
 * its existing defining-loader discovery path. Provider order and provider
 * method call counts are unchanged.
 */
public final class MixinService {

    static class LogBuffer {

        public static class LogEntry {

            public String message;
            public Object[] params;
            public Throwable t;

            public LogEntry(String message, Object[] params, Throwable t) {
                this.message = message;
                this.params = params;
                this.t = t;
            }

        }

        private final List<LogEntry> buffer = new ArrayList<LogEntry>();

        private ILogger logger;

        synchronized void debug(String message, Object... params) {
            if (this.logger != null) {
                this.logger.debug(message, params);
                return;
            }
            this.buffer.add(new LogEntry(message, params, null));
        }

        synchronized void debug(String message, Throwable t) {
            if (this.logger != null) {
                this.logger.debug(message, t);
                return;
            }
            this.buffer.add(new LogEntry(message, new Object[0], t));
        }

        synchronized void flush(ILogger logger) {
            for (LogEntry buffered : this.buffer) {
                if (buffered.t != null) {
                    logger.debug(buffered.message, ObjectArrays.concat(buffered.params, buffered.t));
                } else {
                    logger.debug(buffered.message, buffered.params);
                }
            }
            this.buffer.clear();
            this.logger = logger;
        }

    }

    private static LogBuffer logBuffer = new LogBuffer();

    private static MixinService instance;

    private ServiceLoader<IMixinServiceBootstrap> bootstrapServiceLoader;

    private final Set<String> bootedServices = new HashSet<String>();

    private ServiceLoader<IMixinService> serviceLoader;

    private IMixinService service = null;

    private IGlobalPropertyService propertyService;

    private MixinService() {
        this.runBootServices();
    }

    private void runBootServices() {
        String serviceCls = System.getProperty("mixin.bootstrapService");
        MixinService.workbench$stageStart(
                "bootstrap", this.getClass().getClassLoader(), "mixin.bootstrapService", serviceCls);
        boolean completed = false;

        try {
            if (serviceCls != null) {
                long attempt = MixinService.workbench$attemptStart("bootstrap", "system_property");
                String operation = "provider_construction";
                try {
                    IMixinServiceBootstrap bootService = (IMixinServiceBootstrap) Class.forName(serviceCls).getConstructor().newInstance();
                    MixinService.workbench$providerConstructed(attempt, "bootstrap", bootService);
                    operation = "bootstrap";
                    bootService.bootstrap();
                    MixinService.workbench$bootstrapReturned(attempt, bootService);
                    operation = "service_class_name";
                    String bootedServiceClassName = bootService.getServiceClassName();
                    MixinService.workbench$bootstrapServiceClassName(
                            attempt, bootService, bootedServiceClassName);
                    this.bootedServices.add(bootedServiceClassName);
                    MixinService.workbench$attemptCompleted(attempt, "bootstrap", "completed");
                    completed = true;
                    return;
                } catch (ReflectiveOperationException e) {
                    MixinService.workbench$attemptFailed(attempt, "bootstrap", operation, e);
                    throw new RuntimeException(e);
                } catch (RuntimeException | Error failure) {
                    MixinService.workbench$attemptFailed(attempt, "bootstrap", operation, failure);
                    throw failure;
                }
            }

            this.bootstrapServiceLoader = ServiceLoader.<IMixinServiceBootstrap>load(
                    IMixinServiceBootstrap.class, this.getClass().getClassLoader());
            Iterator<IMixinServiceBootstrap> iter = this.bootstrapServiceLoader.iterator();
            while (iter.hasNext()) {
                long attempt = MixinService.workbench$attemptStart("bootstrap", "service_loader");
                String operation = "provider_construction";
                try {
                    IMixinServiceBootstrap bootService = iter.next();
                    MixinService.workbench$providerConstructed(attempt, "bootstrap", bootService);
                    operation = "bootstrap";
                    bootService.bootstrap();
                    MixinService.workbench$bootstrapReturned(attempt, bootService);
                    operation = "service_class_name";
                    String bootedServiceClassName = bootService.getServiceClassName();
                    MixinService.workbench$bootstrapServiceClassName(
                            attempt, bootService, bootedServiceClassName);
                    this.bootedServices.add(bootedServiceClassName);
                    MixinService.workbench$attemptCompleted(attempt, "bootstrap", "completed");
                } catch (ServiceInitialisationException ex) {
                    MixinService.workbench$attemptFailed(attempt, "bootstrap", operation, ex);
                    MixinService.logBuffer.debug("Mixin bootstrap service {} is not available: {}", ex.getStackTrace()[0].getClassName(),
                            ex.getMessage());
                } catch (Throwable th) {
                    MixinService.workbench$attemptFailed(attempt, "bootstrap", operation, th);
                    MixinService.logBuffer.debug("Catching {}:{} initialising service", th.getClass().getName(), th.getMessage(), th);
                }
            }
            completed = true;
        } catch (RuntimeException | Error failure) {
            MixinService.workbench$stageFailure("bootstrap", "discovery", failure);
            throw failure;
        } finally {
            MixinService.workbench$stageEnd("bootstrap", completed ? "completed" : "failed");
        }
    }

    private static MixinService getInstance() {
        if (MixinService.instance == null) {
            MixinService.instance = new MixinService();
        }

        return MixinService.instance;
    }

    public static void boot() {
        MixinService.getInstance();
    }

    public static IMixinService getService() {
        return MixinService.getInstance().getServiceInstance();
    }

    private synchronized IMixinService getServiceInstance() {
        if (this.service == null) {
            try {
                this.service = this.initService();
                ILogger serviceLogger = this.service.getLogger("CleanMix");
                MixinService.workbench$loggerObserved(serviceLogger);
                MixinService.logBuffer.flush(serviceLogger);
            } catch (Error err) {
                ILogger defaultLogger = MixinService.<ILogger>getDefaultLogger();
                MixinService.logBuffer.flush(defaultLogger);
                defaultLogger.error(err.getMessage(), err);
                throw err;
            }
        }
        return this.service;
    }

    private IMixinService initService() {
        String serviceCls = System.getProperty("mixin.service");
        MixinService.workbench$stageStart(
                "service", this.getClass().getClassLoader(), "mixin.service", serviceCls);
        boolean selected = false;

        try {
            if (serviceCls != null) {
                long attempt = MixinService.workbench$attemptStart("service", "system_property");
                String operation = "provider_construction";
                try {
                    IMixinService service = (IMixinService) Class.forName(serviceCls).getConstructor().newInstance();
                    MixinService.workbench$providerConstructed(attempt, "service", service);
                    operation = "is_valid";
                    boolean valid = service.isValid();
                    MixinService.workbench$validityReturned(attempt, service, valid);
                    if (!valid) {
                        throw new RuntimeException("invalid service " + serviceCls + " configured via system property");
                    }

                    MixinService.workbench$serviceSelected(attempt, service);
                    MixinService.workbench$attemptCompleted(attempt, "service", "selected");
                    selected = true;
                    return service;
                } catch (ReflectiveOperationException e) {
                    MixinService.workbench$attemptFailed(attempt, "service", operation, e);
                    throw new RuntimeException(e);
                } catch (RuntimeException | Error failure) {
                    MixinService.workbench$attemptFailed(attempt, "service", operation, failure);
                    throw failure;
                }
            }

            this.serviceLoader = ServiceLoader.<IMixinService>load(
                    IMixinService.class, this.getClass().getClassLoader());
            Iterator<IMixinService> iter = this.serviceLoader.iterator();
            List<String> badServices = new ArrayList<String>();
            int brokenServiceCount = 0;
            while (iter.hasNext()) {
                long attempt = MixinService.workbench$attemptStart("service", "service_loader");
                String operation = "provider_construction";
                try {
                    IMixinService service = iter.next();
                    MixinService.workbench$providerConstructed(attempt, "service", service);
                    if (this.bootedServices.contains(service.getClass().getName())) {
                        operation = "service_name";
                        String bootedServiceName = service.getName();
                        MixinService.workbench$serviceNameReturned(attempt, service, bootedServiceName);
                        MixinService.logBuffer.debug("MixinService [{}] was successfully booted in {}", bootedServiceName,
                                this.getClass().getClassLoader());
                    }
                    operation = "is_valid";
                    boolean valid = service.isValid();
                    MixinService.workbench$validityReturned(attempt, service, valid);
                    if (valid) {
                        MixinService.workbench$serviceSelected(attempt, service);
                        MixinService.workbench$attemptCompleted(attempt, "service", "selected");
                        selected = true;
                        return service;
                    }
                    operation = "service_name";
                    String invalidServiceName = service.getName();
                    MixinService.workbench$serviceNameReturned(attempt, service, invalidServiceName);
                    MixinService.logBuffer.debug("MixinService [{}] is not valid", invalidServiceName);
                    String badServiceName = service.getName();
                    MixinService.workbench$serviceNameReturned(attempt, service, badServiceName);
                    badServices.add(String.format("INVALID[%s]", badServiceName));
                    MixinService.workbench$attemptCompleted(attempt, "service", "invalid");
                } catch (ServiceConfigurationError sce) {
                    MixinService.workbench$attemptFailed(attempt, "service", operation, sce);
                    sce.printStackTrace();
                    brokenServiceCount++;
                } catch (Throwable th) {
                    MixinService.workbench$attemptFailed(attempt, "service", operation, th);
                    String faultingClassName = th.getStackTrace()[0].getClassName();
                    MixinService.logBuffer.debug("MixinService [{}] failed initialisation: {}", faultingClassName, th.getMessage());
                    int pos = faultingClassName.lastIndexOf('.');
                    badServices.add(String.format("ERROR[%s]", pos < 0 ? faultingClassName : faultingClassName.substring(pos + 1)));
                    th.printStackTrace();
                }
            }

            String brokenServiceNote = brokenServiceCount == 0 ? "" : " and " + brokenServiceCount + " other invalid services.";
            throw new ServiceNotAvailableError("No mixin host service is available. Services: " + Joiner.on(", ").join(badServices) + brokenServiceNote);
        } catch (RuntimeException | Error failure) {
            MixinService.workbench$stageFailure("service", "discovery", failure);
            throw failure;
        } finally {
            MixinService.workbench$stageEnd("service", selected ? "selected" : "failed");
        }
    }

    public static IGlobalPropertyService getGlobalPropertyService() {
        return MixinService.getInstance().getGlobalPropertyServiceInstance();
    }

    private IGlobalPropertyService getGlobalPropertyServiceInstance() {
        if (this.propertyService == null) {
            this.propertyService = this.initPropertyService();
        }
        return this.propertyService;
    }

    private IGlobalPropertyService initPropertyService() {
        ServiceLoader<IGlobalPropertyService> serviceLoader = ServiceLoader.<IGlobalPropertyService>load(IGlobalPropertyService.class,
                this.getClass().getClassLoader());

        Iterator<IGlobalPropertyService> iter = serviceLoader.iterator();
        while (iter.hasNext()) {
            try {
                IGlobalPropertyService service = iter.next();
                return service;
            } catch (ServiceConfigurationError serviceError) {
//                serviceError.printStackTrace();
            } catch (Throwable th) {
//                th.printStackTrace();
            }
        }
        throw new ServiceNotAvailableError("No mixin global property service is available");
    }

    @SuppressWarnings("unchecked")
    private static <T> T getDefaultLogger() {
        return (T)new LoggerAdapterConsole("CleanMix").setDebugStream(System.err);
    }

    private static void workbench$stageStart(
            String stage, ClassLoader loader, String propertyName, String propertyValue) {
        try {
            CleanMixDiscoveryTraceRuntime.stageStart(stage, loader, propertyName, propertyValue);
        } catch (Throwable ignored) { }
    }

    private static long workbench$attemptStart(String stage, String mechanism) {
        try {
            return CleanMixDiscoveryTraceRuntime.attemptStart(stage, mechanism);
        } catch (Throwable ignored) {
            return -1L;
        }
    }

    private static void workbench$providerConstructed(long attempt, String stage, Object provider) {
        try {
            CleanMixDiscoveryTraceRuntime.providerConstructed(attempt, stage, provider);
        } catch (Throwable ignored) { }
    }

    private static void workbench$bootstrapReturned(long attempt, Object provider) {
        try {
            CleanMixDiscoveryTraceRuntime.bootstrapReturned(attempt, provider);
        } catch (Throwable ignored) { }
    }

    private static void workbench$bootstrapServiceClassName(
            long attempt, Object provider, String serviceClassName) {
        try {
            CleanMixDiscoveryTraceRuntime.bootstrapServiceClassName(
                    attempt, provider, serviceClassName);
        } catch (Throwable ignored) { }
    }

    private static void workbench$validityReturned(long attempt, Object provider, boolean valid) {
        try {
            CleanMixDiscoveryTraceRuntime.validityReturned(attempt, provider, valid);
        } catch (Throwable ignored) { }
    }

    private static void workbench$serviceNameReturned(long attempt, Object provider, String name) {
        try {
            CleanMixDiscoveryTraceRuntime.serviceNameReturned(attempt, provider, name);
        } catch (Throwable ignored) { }
    }

    private static void workbench$attemptCompleted(long attempt, String stage, String outcome) {
        try {
            CleanMixDiscoveryTraceRuntime.attemptCompleted(attempt, stage, outcome);
        } catch (Throwable ignored) { }
    }

    private static void workbench$attemptFailed(
            long attempt, String stage, String operation, Throwable failure) {
        try {
            CleanMixDiscoveryTraceRuntime.attemptFailed(attempt, stage, operation, failure);
        } catch (Throwable ignored) { }
    }

    private static void workbench$serviceSelected(long attempt, Object provider) {
        try {
            CleanMixDiscoveryTraceRuntime.serviceSelected(attempt, provider);
        } catch (Throwable ignored) { }
        try {
            CleanMixServiceComponentRuntime.serviceSelected(provider);
        } catch (Throwable ignored) { }
    }

    private static void workbench$loggerObserved(Object logger) {
        try {
            CleanMixServiceComponentRuntime.loggerObserved(logger);
        } catch (Throwable ignored) { }
    }

    private static void workbench$stageFailure(String stage, String operation, Throwable failure) {
        try {
            CleanMixDiscoveryTraceRuntime.stageFailure(stage, operation, failure);
        } catch (Throwable ignored) { }
    }

    private static void workbench$stageEnd(String stage, String outcome) {
        try {
            CleanMixDiscoveryTraceRuntime.stageEnd(stage, outcome);
        } catch (Throwable ignored) { }
    }
}
