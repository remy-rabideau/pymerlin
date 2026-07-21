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
 * {@link PyBridge} implementation that calls into {@code _server.py} in-process via GraalPy,
 * passing {@link Value} objects instead of JSON where possible (roadmap §5.4).
 *
 * <p>{@code getActivityTypes}/{@code getResources}/{@code getResourceValue} call
 * {@code _describe_activity_types(model_class)} / {@code _ModelState.describe_resources()} /
 * {@code _ModelState.get_resource_value(name)} directly. {@link #runActivityDirect} calls
 * {@code run_activity_direct(model_state, actions, name, args)} (roadmap §6): the Python
 * activity function runs to completion on the calling {@code ThreadedTask} thread, with
 * delay/emit/spawn/call routed through the {@link PyActions} host object rather than by
 * returning yield responses for a Java-side drive loop to interpret — there is no drive
 * loop, no queue, no background Python thread.
 *
 * <p>Through Phase 2 there was also a request/response {@code runActivity}/{@code resume}
 * protocol here (mirroring a since-deleted subprocess bridge, kept only so both bridges
 * could be proven byte-identical — roadmap §5.5, §6.6). It's gone (roadmap §6.3): once the
 * direct-call path above was proven correct against it, it became unreachable dead weight —
 * nothing on the Python side implements the old runner protocol it drove either.
 */
public final class GraalBridge implements PyBridge {

    // ------------------------------------------------------------------
    // Static context cache (roadmap §11.3) — eliminates ~15s GraalPy
    // cold-start on every simulation after the first. The GraalPy Context,
    // model class, and Python function references are shared across all
    // GraalBridge instances for the same model ref. Each simulation still
    // gets a fresh _ModelState (clean cells).
    // ------------------------------------------------------------------
    private static final Object CACHE_LOCK = new Object();
    private static String  cachedModelRef;
    private static Context cachedCtx;
    private static Value   cachedModelClass;
    private static Value   cachedDescribeActivityTypes;
    private static Value   cachedRunActivityDirectFn;
    private static Value   cachedMakeModelState;
    private static Path    cachedCleanupDir;
    private static Thread  cachedShutdownHook;

    private final Context ctx;
    private final Value   modelClass;
    private final Value   describeActivityTypes;

    /**
     * The extracted model-source directory this bridge is responsible for deleting on
     * {@link #close}, or {@code null} when the model source is a user-provided path we
     * did not create (roadmap §5.3 — "somewhere to fix the temp-dirs-not-cleaned item").
     */
    private final Path    cleanupDir;

    /**
     * The model instance state. Built lazily: pure metadata queries (getActivityTypes,
     * used by the one-shot getDirectiveTypes() path) only need {@link #modelClass}, so a
     * metadata-only bridge never pays for instantiating the model or wiring up its cells.
     */
    private Value   modelState;
    private Value   runActivityDirectFn;
    private boolean closed = false;
    private final boolean ownsContext;  // true only if this instance created the cache

    public GraalBridge(String modelRef, String cacheKey) throws Exception {
        Path srcDir = resolveSrcDir(modelRef);

        synchronized (CACHE_LOCK) {
            if (cachedCtx != null && cacheKey.equals(cachedModelRef)) {
                // Reuse cached context — no cold-start.
                this.ctx = cachedCtx;
                this.modelClass = cachedModelClass;
                this.describeActivityTypes = cachedDescribeActivityTypes;
                this.runActivityDirectFn = cachedRunActivityDirectFn;
                this.cleanupDir = null;  // cached instance owns cleanup
                this.ownsContext = false;
                System.err.println("[PyMerlin][GraalBridge] reusing cached context for: " + modelRef);
            } else {
                // Invalidate any previous cache.
                evictCache();

                Path cleanupDir = findExtractionRoot(srcDir);
                Context ctx = PyContext.build();

                System.err.println("[PyMerlin][GraalBridge] context built, resources root: " + PyContext.resolveResourcesRoot());

                ctx.eval("python", "import sys");
                if (srcDir != null) {
                    ctx.eval("python", "sys.path.insert(0, '" + srcDir.toString().replace("'", "\\'") + "')");
                }

                ctx.eval("python", "from pymerlin._internal._server import "
                    + "_load_model_class, _describe_activity_types, _ModelState, run_activity_direct");

                Value loadModelClass = ctx.eval("python", "_load_model_class");
                Value modelClass = loadModelClass.execute(modelRef);

                // Populate cache.
                cachedModelRef = cacheKey;
                cachedCtx = ctx;
                cachedModelClass = modelClass;
                cachedDescribeActivityTypes = ctx.eval("python", "_describe_activity_types");
                cachedRunActivityDirectFn = ctx.eval("python", "run_activity_direct");
                cachedMakeModelState = ctx.eval("python", "_ModelState");
                cachedCleanupDir = cleanupDir;

                // Shutdown hook protects the cached context.
                cachedShutdownHook = new Thread(GraalBridge::evictCache, "pymerlin-graalbridge-cleanup");
                Runtime.getRuntime().addShutdownHook(cachedShutdownHook);

                this.ctx = ctx;
                this.modelClass = modelClass;
                this.describeActivityTypes = cachedDescribeActivityTypes;
                this.runActivityDirectFn = cachedRunActivityDirectFn;
                this.cleanupDir = cleanupDir;
                this.ownsContext = true;

                System.err.println("[PyMerlin][GraalBridge] model loaded (cold start): " + modelRef);
            }
        }
    }

    /** Lazily instantiate the model's {@code _ModelState} on first use (see field doc). */
    private Value modelState() {
        if (modelState == null) {
            synchronized (CACHE_LOCK) {
                modelState = cachedMakeModelState.execute(modelClass);
            }
        }
        return modelState;
    }

    /** Check if a context is cached for the given key (avoids redundant extraction). */
    static boolean isCached(String cacheKey) {
        synchronized (CACHE_LOCK) {
            return cachedCtx != null && cacheKey.equals(cachedModelRef);
        }
    }

    /** Tear down the cached context and clean up extracted source. */
    private static synchronized void evictCache() {
        if (cachedCtx != null) {
            try {
                cachedCtx.close(true);
            } catch (Exception e) {
                System.err.println("[PyMerlin][GraalBridge] error closing cached context: " + e.getMessage());
            }
            if (cachedCleanupDir != null) {
                deleteRecursively(cachedCleanupDir);
            }
            try {
                if (cachedShutdownHook != null) {
                    Runtime.getRuntime().removeShutdownHook(cachedShutdownHook);
                }
            } catch (IllegalStateException ignored) {
                // JVM shutdown already in progress.
            }
            cachedCtx = null;
            cachedModelRef = null;
            cachedModelClass = null;
            cachedDescribeActivityTypes = null;
            cachedRunActivityDirectFn = null;
            cachedMakeModelState = null;
            cachedCleanupDir = null;
            cachedShutdownHook = null;
        }
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
    public com.google.gson.JsonArray getCells() throws Exception {
        Value describeCells = modelState().getMember("describe_cells");
        Value result = describeCells.execute();
        // result is a Python list of dicts — convert to JsonArray
        com.google.gson.JsonArray arr = new com.google.gson.JsonArray();
        if (result != null && !result.isNull() && result.hasArrayElements()) {
            for (long i = 0; i < result.getArraySize(); i++) {
                arr.add(valueToJsonElement(result.getArrayElement(i)));
            }
        }
        return arr;
    }

    // ------------------------------------------------------------------
    // Phase 3 (roadmap §6) — direct-call execution
    // ------------------------------------------------------------------

    @Override
    public void runActivityDirect(String actId, String activityName,
                                  Map<String, JsonElement> args, PyActions actions) throws Exception {
        // Runs the Python activity function on THIS (Java ThreadedTask) thread. delay/emit/
        // spawn/call call straight back into `actions`; the call returns when the activity
        // function returns. No _ActivityRunner, no queues — the whole point of Phase 3.
        Value pyArgs = jsonArgsToPyDict(args);
        runActivityDirectFn.execute(modelState(), actions, activityName, pyArgs);
    }

    @Override
    public synchronized void close() {
        if (closed) return;
        closed = true;
        // The cached context is intentionally kept alive for reuse. Only the
        // per-simulation modelState is discarded. The cache is cleaned up on
        // JVM shutdown or when a different model ref invalidates it.
        modelState = null;
    }

    // ------------------------------------------------------------------
    // Internals
    // ------------------------------------------------------------------

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
