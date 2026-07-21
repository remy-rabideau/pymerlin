package gov.nasa.ammos.aerie.pymerlin.shim;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;

import java.util.Map;

/**
 * In-process bridge between {@link ShimModelType} and the Python runtime, backed by
 * {@link GraalBridge}.
 *
 * <p>Through Phase 2 this interface also had a {@code SubprocessBridge} implementation
 * wrapping a newline-delimited-JSON subprocess ({@code PythonProcess}/{@code Protocol}),
 * selected via {@code -Dpymerlin.bridge=subprocess}. That was the Phase 2/3 regression
 * oracle: {@code BridgeParityTest} proved the in-process direct-call path byte-identical
 * against it (roadmap §5.5), including on the real GraalPy image (§6.6) — including
 * {@code call()} semantics ({@code CallSemanticsTest}). Once proven, it was deleted
 * (roadmap §6.3/§6.6): {@code SubprocessBridge.java}, {@code PythonProcess.java},
 * {@code Protocol.java}, and the request/response {@code runActivity}/{@code resume}
 * methods this interface used to have. There is currently no runtime bridge-selection
 * switch — {@link #create} always builds a {@link GraalBridge}.
 *
 * <p>This interface still exists as a seam (not collapsed into {@code ShimModelType}
 * talking to {@link GraalBridge} directly) mainly so a future bridge — e.g. a
 * subprocess fallback reintroduced for a model dependency GraalPy genuinely can't support
 * — has somewhere to plug in without touching {@code ShimModelType}.
 */
public interface PyBridge extends AutoCloseable {

    /**
     * Query the model's activity types without instantiating it.
     * Returns the {@code "types"} JsonObject from the {@code get_activity_types} response.
     */
    JsonObject getActivityTypes() throws Exception;

    /**
     * Query the model's resources after instantiation.
     * Returns the {@code "resources"} JsonObject from the {@code get_resources} response.
     */
    JsonObject getResources() throws Exception;

    /**
     * Query the current value of a single named resource.
     * Returns the string representation of the resource's current value.
     */
    String getResourceValue(String name) throws Exception;

    /**
     * Run an activity to completion on the calling thread (a Java {@code ThreadedTask}),
     * driving delay/emit/spawn/call through {@code actions} (roadmap §6). Returns when the
     * Python activity function returns; propagates as an exception if it raises.
     */
    void runActivityDirect(String actId, String activityName,
                           Map<String, JsonElement> args, PyActions actions) throws Exception;

    /**
     * Release all resources held by this bridge (the GraalPy Context, any extracted model
     * source). Called when the simulation ends.
     */
    @Override
    void close();

    // ------------------------------------------------------------------
    // Factory
    // ------------------------------------------------------------------

    /**
     * Instantiate the bridge for the given model reference. Always {@link GraalBridge} —
     * see the class doc for why this indirection still exists.
     *
     * @param modelRef the model reference string (e.g. {@code /tmp/pymerlin-model-xxx/model.py:Mission})
     */
    static PyBridge create(String modelRef) throws Exception {
        return new GraalBridge(modelRef);
    }
}
