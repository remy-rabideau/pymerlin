package gov.nasa.ammos.aerie.pymerlin.shim;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;
import com.google.gson.JsonPrimitive;
import org.graalvm.polyglot.Context;
import org.graalvm.polyglot.Value;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;

/**
 * {@link PyBridge} implementation that calls the same {@code _server.py} functions
 * in-process via GraalPy, passing {@link Value} objects instead of JSON (roadmap §5.4).
 *
 * {@code _send} / {@code _recv} are never called — the server loop in
 * {@code _run_server()} is not used. Instead this bridge directly calls:
 * <ul>
 *   <li>{@code _describe_activity_types(model_class)}</li>
 *   <li>{@code _ModelState.describe_resources()}</li>
 *   <li>{@code _ModelState.get_resource_value(name)}</li>
 *   <li>{@code _ModelState.make_runner(id, name, args)} + {@code runner.start()}</li>
 *   <li>{@code runner.resume()}</li>
 * </ul>
 *
 * The subprocess bridge's {@code _send_runner_state} is deliberately NOT reused here:
 * it writes to stdout via {@code _send}, which is meaningless in-process. Instead
 * {@link #captureRunnerState} reads the runner's state fields directly and assembles
 * the same {@link JsonObject} schema, so {@link ShimModelType}'s {@code driveToCompletion}
 * loop stays unchanged (roadmap §5 "keep every seam"). The consequence — the two bridges'
 * state-capture logic must be kept in sync by hand — is what the Phase 2 byte-identical
 * exit criterion (both bridges → identical results) guards against.
 *
 * Selected by {@code -Dpymerlin.bridge=graal} (the default, since the byte-identical exit
 * criterion has passed against a real GraalPy image — roadmap §5.5; see {@link PyBridge#create}).
 */
public final class GraalBridge implements PyBridge {

    private final Context ctx;
    private final Value   modelClass;
    private final Value   describeActivityTypes;

    /**
     * The extracted model-source directory this bridge is responsible for deleting on
     * {@link #close}, or {@code null} when the model source is a user-provided path we
     * did not create (roadmap §5.3 — "somewhere to fix the temp-dirs-not-cleaned item").
     */
    private final Path    cleanupDir;
    private final Thread  shutdownHook;

    /**
     * The model instance state. Built lazily: pure metadata queries (getActivityTypes,
     * used by the one-shot getDirectiveTypes() path) only need {@link #modelClass}, so a
     * metadata-only bridge never pays for instantiating the model or wiring up its cells.
     */
    private Value   modelState;
    private boolean closed = false;

    public GraalBridge(String modelRef) throws Exception {
        Path srcDir = resolveSrcDir(modelRef);
        this.cleanupDir = findExtractionRoot(srcDir);

        ctx = PyContext.build();

        System.err.println("[PyMerlin][GraalBridge] context built, resources root: " + PyContext.resolveResourcesRoot());

        ctx.eval("python", "import sys");
        if (srcDir != null) {
            ctx.eval("python", "sys.path.insert(0, '" + srcDir.toString().replace("'", "\\'") + "')");
        }

        ctx.eval("python", "from pymerlin._internal._server import "
            + "_load_model_class, _describe_activity_types, _ModelState");

        Value loadModelClass = ctx.eval("python", "_load_model_class");
        modelClass = loadModelClass.execute(modelRef);

        describeActivityTypes = ctx.eval("python", "_describe_activity_types");

        // Ensure the GraalPy Context and any extracted source dir are released even if
        // close() is never called on this bridge (the persistent instantiate() bridge has
        // no explicit teardown hook in ModelType). Mirrors PythonProcess's shutdown hook.
        this.shutdownHook = new Thread(this::hookClose, "pymerlin-graalbridge-cleanup");
        Runtime.getRuntime().addShutdownHook(this.shutdownHook);

        System.err.println("[PyMerlin][GraalBridge] model loaded: " + modelRef);
    }

    /** Lazily instantiate the model's {@code _ModelState} on first use (see field doc). */
    private Value modelState() {
        if (modelState == null) {
            Value makeModelState = ctx.eval("python", "_ModelState");
            modelState = makeModelState.execute(modelClass);
        }
        return modelState;
    }

    // ------------------------------------------------------------------
    // PyBridge implementation
    // ------------------------------------------------------------------

    @Override
    public JsonObject getActivityTypes() throws Exception {
        Value result = describeActivityTypes.execute(modelClass);
        return valueToJsonObject(result);
    }

    @Override
    public JsonObject getResources() throws Exception {
        Value describeResources = modelState().getMember("describe_resources");
        Value result = describeResources.execute();
        return valueToJsonObject(result);
    }

    @Override
    public String getResourceValue(String name) throws Exception {
        Value getVal = modelState().getMember("get_resource_value");
        Value result = getVal.execute(name);
        return result.asString();
    }

    @Override
    public JsonObject runActivity(String actId, String activityName, Map<String, JsonElement> args) throws Exception {
        Value makeRunner = modelState().getMember("make_runner");
        Value pyArgs = jsonArgsToPyDict(args);
        Value runner = makeRunner.execute(actId, activityName, pyArgs);
        Value startMethod = runner.getMember("start");
        startMethod.execute();
        return captureRunnerState(actId, runner);
    }

    @Override
    public JsonObject resume(String actId) throws Exception {
        Value runner = getActiveRunner(actId);
        Value resumeMethod = runner.getMember("resume");
        resumeMethod.execute();
        return captureRunnerState(actId, runner);
    }

    @Override
    public synchronized void close() {
        if (closed) return;
        closed = true;
        try {
            Runtime.getRuntime().removeShutdownHook(shutdownHook);
        } catch (IllegalStateException ignored) {
            // JVM shutdown already in progress — the hook will run doClose() itself.
        }
        doClose();
    }

    /** Invoked only from the shutdown hook, when close() was never called explicitly. */
    private synchronized void hookClose() {
        if (closed) return;
        closed = true;
        doClose();
    }

    private void doClose() {
        try {
            ctx.close(true);
        } catch (Exception e) {
            System.err.println("[PyMerlin][GraalBridge] error closing context: " + e.getMessage());
        }
        if (cleanupDir != null) {
            deleteRecursively(cleanupDir);
        }
    }

    // ------------------------------------------------------------------
    // Internals
    // ------------------------------------------------------------------

    /**
     * The Python side's {@code _send_runner_state} writes to stdout, which we don't
     * want. Instead we read the runner's state fields directly and assemble the same
     * JsonObject that the subprocess bridge would have returned.
     */
    private JsonObject captureRunnerState(String actId, Value runner) throws Exception {
        storeActiveRunner(actId, runner);

        Value status = runner.getMember("status");
        String statusStr = status.asString();

        JsonObject base = new JsonObject();
        base.addProperty("id", actId);

        Value emitsMethod = runner.getMember("drain_emits");
        Value emitsList   = emitsMethod.execute();
        if (emitsList.hasArrayElements()) {
            long size = emitsList.getArraySize();
            if (size > 0) {
                JsonArray emitsArr = new JsonArray();
                for (long i = 0; i < size; i++) {
                    Value pair = emitsList.getArrayElement(i);
                    JsonObject e = new JsonObject();
                    e.addProperty("resource", pair.getArrayElement(0).asString());
                    e.addProperty("value",    pair.getArrayElement(1).asString());
                    emitsArr.add(e);
                }
                base.add("emits", emitsArr);
            }
        }

        Value spawnsMethod = runner.getMember("drain_spawns");
        Value spawnsList   = spawnsMethod.execute();
        if (spawnsList.hasArrayElements()) {
            long size = spawnsList.getArraySize();
            if (size > 0) {
                JsonArray spawnsArr = new JsonArray();
                for (long i = 0; i < size; i++) {
                    Value pair = spawnsList.getArrayElement(i);
                    JsonObject s = new JsonObject();
                    s.addProperty("name", pair.getArrayElement(0).asString());
                    spawnsArr.add(s);
                }
                base.add("spawns", spawnsArr);
            }
        }

        switch (statusStr) {
            case "delayed" -> {
                base.addProperty("op", "delay");
                long delayUs = runner.getMember("delay_us").asLong();
                base.addProperty("duration_us", delayUs);
            }
            case "awaiting" -> {
                base.addProperty("op", "wait_until_opaque");
            }
            case "completed" -> {
                base.addProperty("op", "done");
                removeActiveRunner(actId);
            }
            case "running" -> {
                base.addProperty("op", "running");
            }
            case "error" -> {
                base.addProperty("op", "error");
                base.addProperty("message", "(GraalBridge: runner in error state)");
                removeActiveRunner(actId);
            }
            default -> {
                base.addProperty("op", "error");
                base.addProperty("message", "Unknown runner status: " + statusStr);
            }
        }
        return base;
    }

    private void storeActiveRunner(String actId, Value runner) {
        ctx.eval("python",
            "if '_graal_bridge_runners' not in dir(): _graal_bridge_runners = {}");
        Value runners = ctx.eval("python", "_graal_bridge_runners");
        runners.putHashEntry(actId, runner);
    }

    private Value getActiveRunner(String actId) {
        Value runners = ctx.eval("python", "_graal_bridge_runners");
        Value runner  = runners.getHashValue(actId);
        if (runner == null || runner.isNull()) {
            throw new RuntimeException("[PyMerlin][GraalBridge] No active runner for id: " + actId);
        }
        return runner;
    }

    private void removeActiveRunner(String actId) {
        Value runners = ctx.eval("python", "_graal_bridge_runners");
        runners.removeHashEntry(actId);
    }

    /**
     * Convert a Java {@code Map<String, JsonElement>} of serialized activity args
     * into a Python dict {@link Value} the model function can consume natively.
     */
    private Value jsonArgsToPyDict(Map<String, JsonElement> args) {
        Value dict = ctx.eval("python", "{}");
        for (Map.Entry<String, JsonElement> entry : args.entrySet()) {
            dict.putHashEntry(entry.getKey(), jsonElementToPyValue(entry.getValue()));
        }
        return dict;
    }

    private Object jsonElementToPyValue(JsonElement el) {
        if (el == null || el.isJsonNull()) return null;
        if (el.isJsonPrimitive()) {
            JsonPrimitive p = el.getAsJsonPrimitive();
            if (p.isBoolean()) return p.getAsBoolean();
            if (p.isNumber()) {
                double d = p.getAsDouble();
                if (d == Math.floor(d) && !Double.isInfinite(d) && Math.abs(d) < Long.MAX_VALUE) {
                    return (long) d;
                }
                return d;
            }
            return p.getAsString();
        }
        if (el.isJsonObject()) {
            Value dict = ctx.eval("python", "{}");
            for (Map.Entry<String, JsonElement> entry : el.getAsJsonObject().entrySet()) {
                dict.putHashEntry(entry.getKey(), jsonElementToPyValue(entry.getValue()));
            }
            return dict;
        }
        if (el.isJsonArray()) {
            JsonArray arr = el.getAsJsonArray();
            Value list = ctx.eval("python", "[]");
            Value append = list.getMember("append");
            for (JsonElement item : arr) {
                append.execute(jsonElementToPyValue(item));
            }
            return list;
        }
        return el.toString();
    }

    /**
     * Convert a Python dict/mapping {@link Value} to a {@link JsonObject}.
     * Used for {@code getActivityTypes()} and {@code getResources()} results.
     */
    private JsonObject valueToJsonObject(Value val) {
        JsonObject obj = new JsonObject();
        if (val == null || val.isNull()) return obj;
        if (val.hasHashEntries()) {
            Value keys = val.getHashKeysIterator();
            while (keys.hasIteratorNextElement()) {
                String key = keys.getIteratorNextElement().asString();
                Value v = val.getHashValue(key);
                obj.add(key, valueToJsonElement(v));
            }
        }
        return obj;
    }

    private JsonElement valueToJsonElement(Value val) {
        if (val == null || val.isNull()) return JsonNull.INSTANCE;
        if (val.isBoolean()) return new JsonPrimitive(val.asBoolean());
        if (val.isNumber()) {
            double d = val.asDouble();
            if (d == Math.floor(d) && !Double.isInfinite(d) && Math.abs(d) < Long.MAX_VALUE) {
                return new JsonPrimitive((long) d);
            }
            return new JsonPrimitive(d);
        }
        if (val.isString()) return new JsonPrimitive(val.asString());
        if (val.hasHashEntries()) return valueToJsonObject(val);
        if (val.hasArrayElements()) {
            JsonArray arr = new JsonArray();
            for (long i = 0; i < val.getArraySize(); i++) {
                arr.add(valueToJsonElement(val.getArrayElement(i)));
            }
            return arr;
        }
        return new JsonPrimitive(val.toString());
    }

    /**
     * Resolve the directory that should be on the Python path so
     * {@code _load_model_class(modelRef)} can find the model file.
     *
     * For bundled models the ref is an absolute path like
     * {@code /tmp/pymerlin-model-xxx/model.py:Mission} — the parent dir is
     * what needs to be on sys.path.
     */
    private static Path resolveSrcDir(String modelRef) {
        if (modelRef.contains(":")) {
            String filePart = modelRef.split(":", 2)[0];
            return Path.of(filePart).toAbsolutePath().getParent();
        }
        return Path.of(".").toAbsolutePath();
    }

    /**
     * If {@code srcDir} lives inside one of {@code ShimModelType.extractIfBundled}'s own
     * {@code pymerlin-model-*} temp extractions, return that extraction root so {@link #close}
     * can delete it. Returns {@code null} for user-provided source paths we did not create —
     * we must never delete those. Only ever returns a directory under the JVM temp dir whose
     * name starts with {@code pymerlin-model-}, so the deletion is tightly scoped.
     */
    private static Path findExtractionRoot(Path srcDir) {
        if (srcDir == null) return null;
        Path tmp = Path.of(System.getProperty("java.io.tmpdir")).toAbsolutePath().normalize();
        Path d = srcDir.toAbsolutePath().normalize();
        while (d != null && d.startsWith(tmp) && !d.equals(tmp)) {
            Path name = d.getFileName();
            if (name != null && name.toString().startsWith("pymerlin-model-")) return d;
            d = d.getParent();
        }
        return null;
    }

    private static void deleteRecursively(Path dir) {
        if (dir == null || !Files.exists(dir)) return;
        try (var paths = Files.walk(dir)) {
            paths.sorted(java.util.Comparator.reverseOrder()).forEach(p -> {
                try {
                    Files.deleteIfExists(p);
                } catch (java.io.IOException e) {
                    System.err.println("[PyMerlin][GraalBridge] could not delete " + p + ": " + e.getMessage());
                }
            });
        } catch (java.io.IOException e) {
            System.err.println("[PyMerlin][GraalBridge] could not clean up " + dir + ": " + e.getMessage());
        }
    }
}
