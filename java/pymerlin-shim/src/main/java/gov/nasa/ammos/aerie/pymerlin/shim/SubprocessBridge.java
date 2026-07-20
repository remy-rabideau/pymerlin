package gov.nasa.ammos.aerie.pymerlin.shim;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;

import java.util.Map;

/**
 * {@link PyBridge} implementation that wraps the existing {@link PythonProcess} /
 * {@link Protocol} newline-delimited JSON subprocess transport — unchanged from
 * the pre-Phase-2 code.
 *
 * This is the regression oracle for Phase 2: {@link GraalBridge} must produce
 * byte-identical simulation results against it (roadmap §5 exit criteria).
 * Selected by {@code -Dpymerlin.bridge=subprocess}.
 */
public final class SubprocessBridge implements PyBridge {

    private final PythonProcess process;
    private final Protocol protocol;

    public SubprocessBridge(String modelRef) throws Exception {
        this.process  = PythonProcess.start(modelRef);
        this.protocol = this.process.protocol();
    }

    @Override
    public JsonObject getActivityTypes() throws Exception {
        JsonObject resp = protocol.roundtrip(Protocol.obj("op", "get_activity_types"));
        return resp.getAsJsonObject("types");
    }

    @Override
    public JsonObject getResources() throws Exception {
        JsonObject resp = protocol.roundtrip(Protocol.obj("op", "get_resources"));
        return resp.getAsJsonObject("resources");
    }

    @Override
    public String getResourceValue(String name) throws Exception {
        JsonObject req = new JsonObject();
        req.addProperty("op", "get_resource_value");
        req.addProperty("name", name);
        JsonObject resp = protocol.roundtrip(req);
        return resp.has("value") ? resp.get("value").getAsString() : "";
    }

    @Override
    public JsonObject runActivity(String actId, String activityName, Map<String, JsonElement> args) throws Exception {
        JsonObject argsJson = new JsonObject();
        for (Map.Entry<String, JsonElement> e : args.entrySet()) {
            argsJson.add(e.getKey(), e.getValue());
        }
        JsonObject msg = new JsonObject();
        msg.addProperty("op", "run_activity");
        msg.addProperty("id", actId);
        msg.addProperty("name", activityName);
        msg.add("args", argsJson);
        return protocol.roundtrip(msg);
    }

    @Override
    public JsonObject resume(String actId) throws Exception {
        return protocol.roundtrip(Protocol.obj("op", "resume", "id", actId));
    }

    @Override
    public void close() {
        process.destroy();
    }
}
