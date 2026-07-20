package gov.nasa.ammos.aerie.pymerlin.shim;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;

import java.util.Map;

/**
 * Transport-agnostic bridge between ShimModelType and the Python runtime.
 *
 * Two implementations exist (roadmap §5):
 *  - {@link SubprocessBridge} — wraps the existing {@link PythonProcess}/{@link Protocol}
 *    newline-delimited JSON subprocess. Selected by {@code pymerlin.bridge=subprocess}.
 *  - {@link GraalBridge} — calls the same {@code _server.py} functions in-process via
 *    GraalPy. Selected by {@code pymerlin.bridge=graal} (default).
 *
 * Select at runtime via {@code -Dpymerlin.bridge=graal|subprocess}.
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
     * Start running an activity. Returns the first yield response (delay/done/error/running/…)
     * as a JsonObject with the same schema the subprocess bridge has always used.
     *
     * @param actId        unique string ID for this activity execution
     * @param activityName name of the activity type
     * @param args         serialized arguments — same map that was passed to
     *                     {@link ShimModelType#runActivity}
     */
    JsonObject runActivity(String actId, String activityName, Map<String, JsonElement> args) throws Exception;

    /**
     * Resume a previously yielded activity. Returns the next yield response.
     *
     * @param actId the same ID passed to {@link #runActivity}
     */
    JsonObject resume(String actId) throws Exception;

    /**
     * Release all resources held by this bridge (subprocess, GraalPy Context, etc.).
     * Called when the simulation ends.
     */
    @Override
    void close();

    // ------------------------------------------------------------------
    // Factory
    // ------------------------------------------------------------------

    /**
     * Instantiate the bridge selected by {@code -Dpymerlin.bridge=graal|subprocess}.
     * Defaults to {@code graal} if the property is absent.
     *
     * @param modelRef the model reference string (e.g. {@code /tmp/pymerlin-model-xxx/model.py:Mission})
     */
    static PyBridge create(String modelRef) throws Exception {
        String choice = System.getProperty("pymerlin.bridge", "graal");
        return switch (choice) {
            case "subprocess" -> new SubprocessBridge(modelRef);
            case "graal"      -> new GraalBridge(modelRef);
            default -> throw new RuntimeException(
                "[PyMerlin] Unknown pymerlin.bridge value: " + choice + " (expected graal or subprocess)");
        };
    }
}
