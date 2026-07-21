package gov.nasa.ammos.aerie.pymerlin.shim;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;

import java.util.Map;

/**
 * Transport-agnostic bridge between ShimModelType and the Python runtime.
 *
 * Two implementations exist (roadmap §5):
 *  - {@link GraalBridge} — calls the same {@code _server.py} functions in-process via
 *    GraalPy. Selected by {@code pymerlin.bridge=graal} (the default). Byte-identical
 *    parity against the subprocess oracle passed (roadmap §5.5) — verified via
 *    {@code BridgeParityTest#graalBridgeMatchesSubprocessOracle} against a real
 *    GraalPy-provisioned worker image, not just locally.
 *  - {@link SubprocessBridge} — wraps the existing {@link PythonProcess}/{@link Protocol}
 *    newline-delimited JSON subprocess. Selected by {@code pymerlin.bridge=subprocess}.
 *    Kept as the rollback switch and regression oracle (that is the whole point of §5's
 *    "keep every seam" framing) — retire it in Phase 3, not before.
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

    // ------------------------------------------------------------------
    // Phase 3 (roadmap §6) — direct-call execution
    // ------------------------------------------------------------------

    /**
     * Whether this bridge runs activities by calling the Python function directly on the
     * caller's thread with host callbacks ({@link #runActivityDirect}), instead of the
     * request/response {@link #runActivity}/{@link #resume} protocol that
     * {@code ShimModelType.driveToCompletion} drives.
     *
     * <p>{@code false} for {@link SubprocessBridge} (the JSON transport can only be
     * request/response); {@code true} for {@link GraalBridge}, where in-process host
     * callbacks make the queue-and-drive loop unnecessary.
     */
    default boolean isDirect() {
        return false;
    }

    /**
     * Run an activity to completion on the calling thread, driving delay/emit/spawn/call
     * through {@code actions} rather than by returning yield responses. Only meaningful when
     * {@link #isDirect()} is {@code true}; returns when the Python activity function returns.
     */
    default void runActivityDirect(String actId, String activityName,
                                   Map<String, JsonElement> args, PyActions actions) throws Exception {
        throw new UnsupportedOperationException(
            "this bridge does not support direct execution; use runActivity/resume");
    }

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
     * Defaults to {@code graal} — the byte-identical exit criterion (roadmap §5.5) has
     * passed against a real GraalPy image. {@code subprocess} remains available as an
     * explicit rollback switch (set {@code -Dpymerlin.bridge=subprocess}) if a
     * graal-specific issue surfaces that the parity test didn't catch — in particular,
     * the AERIE-1516 per-simulation teardown gap (roadmap §5.5) means a long-running
     * worker accumulates one un-closed GraalPy Context per simulation between restarts;
     * that's a real, separate, still-open concern this default flip does not resolve.
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
