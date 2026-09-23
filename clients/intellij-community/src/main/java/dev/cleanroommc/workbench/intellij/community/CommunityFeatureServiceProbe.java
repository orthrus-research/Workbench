package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonObject;

/** Standalone package probe loaded from the mandatory Community plugin jar. */
public final class CommunityFeatureServiceProbe {
    private CommunityFeatureServiceProbe() {
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 7) {
            throw new IllegalArgumentException(
                    "Community feature-service probe requires seven exact arguments"
            );
        }
        FeatureServiceClient.Result result = FeatureServiceClient.readResult(
                args[0],
                new FeatureServiceClient.Connection(args[1], args[2]),
                new FeatureServiceClient.Job(args[3], args[4], args[5], args[6])
        );
        JsonObject value = new JsonObject();
        value.addProperty("format", "workbench-community-feature-service-stage5-probe-v1");
        value.addProperty("schema_version", 1);
        value.addProperty("registryId", result.registryId());
        value.addProperty("serviceInstanceId", result.serviceInstanceId());
        value.addProperty("jobId", result.jobId());
        value.addProperty("serviceResultId", result.serviceResultId());
        value.addProperty("ownerRequestId", result.ownerRequestId());
        if (result.operationPlanId() == null) {
            value.add("operationPlanId", null);
        } else {
            value.addProperty("operationPlanId", result.operationPlanId());
        }
        value.addProperty("ownerResultId", result.ownerResultId());
        value.addProperty("ownerResultCanonicalSha256", result.ownerResultCanonicalSha256());
        value.addProperty("ownerResultCanonicalSize", result.ownerResultCanonicalSize());
        value.add("ownerResult", result.ownerResult());
        System.out.println(value);
    }
}
