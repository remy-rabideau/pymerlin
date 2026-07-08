package gov.nasa.ammos.aerie.pymerlin.shim;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import gov.nasa.jpl.aerie.merlin.protocol.driver.CellId;
import gov.nasa.jpl.aerie.merlin.protocol.driver.Initializer;
import gov.nasa.jpl.aerie.merlin.protocol.driver.Topic;
import gov.nasa.jpl.aerie.merlin.protocol.model.DirectiveType;
import gov.nasa.jpl.aerie.merlin.protocol.model.InputType;
import gov.nasa.jpl.aerie.merlin.protocol.model.ModelType;
import gov.nasa.jpl.aerie.merlin.protocol.model.OutputType;
import gov.nasa.jpl.aerie.merlin.protocol.model.Resource;
import gov.nasa.jpl.aerie.merlin.protocol.model.TaskFactory;
import gov.nasa.jpl.aerie.merlin.protocol.types.Duration;
import gov.nasa.jpl.aerie.merlin.protocol.types.SerializedValue;
import gov.nasa.jpl.aerie.merlin.protocol.types.Unit;
import gov.nasa.jpl.aerie.merlin.protocol.types.ValueSchema;

import java.io.IOException;
import java.io.InputStream;
import java.net.URL;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.atomic.AtomicLong;
import java.util.jar.Manifest;

import gov.nasa.jpl.aerie.merlin.framework.Condition;
import static gov.nasa.jpl.aerie.merlin.framework.ModelActions.delay;
import static gov.nasa.jpl.aerie.merlin.framework.ModelActions.emit;
import static gov.nasa.jpl.aerie.merlin.framework.ModelActions.spawn;
import static gov.nasa.jpl.aerie.merlin.framework.ModelActions.threaded;
import static gov.nasa.jpl.aerie.merlin.framework.ModelActions.waitUntil;

/**
 * Generic Aerie ModelType implementation that delegates all model behaviour to a
 * Python subprocess via the shim JSON protocol.
 *
 * The model reference (e.g. "path/to/model.py:Mission") is read from the system
 * property {@code pymerlin.model.ref}, which is injected into the JAR manifest
 * by {@code pymerlin package} and set at startup.
 */
public final class ShimModelType implements ModelType<Unit, Unit> {

    // --- per-resource cell bookkeeping ---
    private record ResourceCell(
        Topic<String> topic,
        CellId<String[]> cellId
    ) {}

    private final Map<String, ResourceCell> resourceCells = new HashMap<>();
    private final Map<String, Topic<Map<String, SerializedValue>>> inputTopics  = new HashMap<>();
    private final Map<String, Topic<Unit>>                         outputTopics = new HashMap<>();

    // Activity names populated either by instantiate() or by a one-shot query in getDirectiveTypes().
    private volatile Set<String> activityNames = null;

    private final AtomicLong activityCounter = new AtomicLong(0);

    // Python process is shared across all activity executions for a given simulation.
    private volatile PythonProcess pythonProcess = null;

    // -----------------------------------------------------------------
    // ModelType interface
    // -----------------------------------------------------------------

    @Override
    public Map<String, ? extends DirectiveType<Unit, ?, ?>> getDirectiveTypes() {
        // Aerie may call this on a fresh instance (before instantiate()) to extract
        // activity type metadata for the DB. Start a one-shot Python process if needed.
        if (activityNames == null) {
            fetchActivityNames();
        }
        return buildDirectiveTypes();
    }

    private synchronized void fetchActivityNames() {
        if (activityNames != null) return; // double-checked
        String modelRef = resolveModelRef();
        try {
            PythonProcess proc = PythonProcess.start(modelRef);
            try {
                JsonObject resp = proc.protocol().roundtrip(Protocol.obj("op", "get_activity_types"));
                JsonObject types = resp.getAsJsonObject("types");
                Set<String> names = new java.util.LinkedHashSet<>();
                if (types != null) types.keySet().forEach(names::add);
                activityNames = names;
            } finally {
                proc.destroy();
            }
        } catch (Exception e) {
            throw new RuntimeException("[PyMerlin] Could not fetch activity types: " + e.getMessage(), e);
        }
    }

    @Override
    public InputType<Unit> getConfigurationType() {
        return new InputType<>() {
            @Override public List<InputType.Parameter> getParameters() { return List.of(); }
            @Override public List<String> getRequiredParameters()      { return List.of(); }
            @Override public Unit instantiate(Map<String, SerializedValue> arguments) { return Unit.UNIT; }
            @Override public Map<String, SerializedValue> getArguments(Unit value) { return Map.of(); }
            @Override public List<InputType.ValidationNotice> getValidationFailures(Unit value) { return List.of(); }
        };
    }

    @Override
    public Unit instantiate(Instant planStart, Unit configuration, Initializer builder) {
        String modelRef = resolveModelRef();
        try {
            pythonProcess = PythonProcess.start(modelRef);
        } catch (Exception e) {
            throw new RuntimeException("[PyMerlin] Failed to start Python server: " + e.getMessage(), e);
        }

        Protocol proto = pythonProcess.protocol();

        // Populate activityNames from the live process (avoids a second Python start).
        try {
            JsonObject typesResp = proto.roundtrip(Protocol.obj("op", "get_activity_types"));
            JsonObject types = typesResp.getAsJsonObject("types");
            Set<String> names = new java.util.LinkedHashSet<>();
            if (types != null) {
                for (String name : types.keySet()) {
                    names.add(name);
                    Topic<Map<String, SerializedValue>> inputTopic  = new Topic<>();
                    Topic<Unit>                         outputTopic = new Topic<>();
                    inputTopics.put(name, inputTopic);
                    outputTopics.put(name, outputTopic);
                    builder.topic("ActivityType.Input."  + name, inputTopic,  passthroughOutputType());
                    builder.topic("ActivityType.Output." + name, outputTopic, unitOutputType());
                }
            }
            activityNames = names;
        } catch (Exception e) {
            throw new RuntimeException("[PyMerlin] get_activity_types failed: " + e.getMessage(), e);
        }

        // Query resources — allocate a cell for each one
        try {
            JsonObject resResp = proto.roundtrip(Protocol.obj("op", "get_resources"));
            JsonObject resources = resResp.getAsJsonObject("resources");
            if (resources != null) {
                for (String resName : resources.keySet()) {
                    // Fetch initial value
                    JsonObject valReq = new JsonObject();
                    valReq.addProperty("op", "get_resource_value");
                    valReq.addProperty("name", resName);
                    JsonObject valResp = proto.roundtrip(valReq);
                    String initialValue = valResp.has("value") ? valResp.get("value").getAsString() : "";

                    Topic<String> topic = new Topic<>();
                    CellId<String[]> cellId = allocateStringCell(builder, initialValue, topic);
                    resourceCells.put(resName, new ResourceCell(topic, cellId));

                    final String capturedName = resName;
                    final CellId<String[]> capturedCell = cellId;
                    builder.resource(capturedName, new Resource<String>() {
                        @Override public String getType() { return "discrete"; }
                        @Override public OutputType<String> getOutputType() { return stringOutputType(); }
                        @Override public String getDynamics(gov.nasa.jpl.aerie.merlin.protocol.driver.Querier q) {
                            return q.getState(capturedCell)[0];
                        }
                    });
                }
            }
        } catch (Exception e) {
            throw new RuntimeException("[PyMerlin] get_resources failed: " + e.getMessage(), e);
        }

        return Unit.UNIT;
    }

    // -----------------------------------------------------------------
    // Model ref resolution
    // -----------------------------------------------------------------

    private static String resolveModelRef() {
        // 1. Check system property (set by tests or external tooling)
        String sysProp = System.getProperty("pymerlin.model.ref");
        if (sysProp != null && !sysProp.isBlank()) return sysProp;

        // 2. Read from the JAR that physically contains ShimModelType.
        //    We cannot use getClassLoader().getResource("META-INF/MANIFEST.MF")
        //    because the parent classloader's MANIFEST.MF would be found first.
        try {
            URL jarUrl = ShimModelType.class.getProtectionDomain().getCodeSource().getLocation();
            if (jarUrl != null) {
                // Build a jar: URL to access the manifest entry directly
                URL manifestUrl = new URL("jar:" + jarUrl.toExternalForm() + "!/META-INF/MANIFEST.MF");
                try (InputStream is = manifestUrl.openStream()) {
                    Manifest mf = new Manifest(is);
                    String ref = mf.getMainAttributes().getValue("Pymerlin-Model-Ref");
                    if (ref != null && !ref.isBlank()) {
                        return extractIfBundled(ref.trim());
                    }
                }
            }
        } catch (IOException e) {
            System.err.println("[PyMerlin] Could not read manifest: " + e.getMessage());
        }

        throw new RuntimeException("[PyMerlin] No Pymerlin-Model-Ref found in JAR manifest. " +
            "Did you build the JAR with 'pymerlin package'?");
    }

    /**
     * If the model ref points to a bundled resource (pymerlin_models/...), extract
     * the file to a temp directory and return a ref pointing at the extracted path.
     */
    private static String extractIfBundled(String ref) throws IOException {
        if (!ref.contains(":")) return ref;
        String[] parts = ref.split(":", 2);
        String resourcePath = parts[0];
        String className    = parts[1];

        URL resource = ShimModelType.class.getClassLoader().getResource(resourcePath);
        if (resource == null) {
            // Not bundled — treat as a filesystem path
            return ref;
        }

        // Extract to a temp file
        Path tmpDir = Files.createTempDirectory("pymerlin-model-");
        String fileName = Path.of(resourcePath).getFileName().toString();
        Path dest = tmpDir.resolve(fileName);
        try (InputStream is = resource.openStream()) {
            Files.copy(is, dest);
        }
        System.out.println("[PyMerlin] Extracted bundled model to: " + dest);
        return dest.toAbsolutePath() + ":" + className;
    }

    // -----------------------------------------------------------------
    // Directive types — built after instantiate() populates inputTopics
    // -----------------------------------------------------------------

    private Map<String, DirectiveType<Unit, Map<String, SerializedValue>, Unit>> buildDirectiveTypes() {
        Map<String, DirectiveType<Unit, Map<String, SerializedValue>, Unit>> result = new HashMap<>();
        Set<String> names = activityNames != null ? activityNames : Set.of();
        for (String name : names) {
            final String activityName = name;
            result.put(name, new DirectiveType<>() {
                @Override
                public InputType<Map<String, SerializedValue>> getInputType() {
                    return passthroughInputType();
                }

                @Override
                public OutputType<Unit> getOutputType() {
                    return unitOutputType();
                }

                @Override
                public TaskFactory<Unit> getTaskFactory(Unit model, Map<String, SerializedValue> args) {
                    return threaded(() -> runActivity(activityName, args));
                }
            });
        }
        return result;
    }

    // -----------------------------------------------------------------
    // Activity execution loop
    // -----------------------------------------------------------------

    private Unit runActivity(String activityName, Map<String, SerializedValue> args) {
        String actId = "act-" + activityCounter.incrementAndGet();
        Protocol proto = pythonProcess.protocol();

        // Serialize args to plain strings for Python
        JsonObject argsJson = new JsonObject();
        for (Map.Entry<String, SerializedValue> e : args.entrySet()) {
            argsJson.addProperty(e.getKey(), serializedValueToString(e.getValue()));
        }

        JsonObject startMsg = new JsonObject();
        startMsg.addProperty("op", "run_activity");
        startMsg.addProperty("id", actId);
        startMsg.addProperty("name", activityName);
        startMsg.add("args", argsJson);

        try {
            emit(args, inputTopics.get(activityName));
            JsonObject response = proto.roundtrip(startMsg);
            driveToCompletion(actId, response, proto);
            emit(Unit.UNIT, outputTopics.get(activityName));
        } catch (Exception e) {
            throw new RuntimeException("[PyMerlin] Activity " + activityName + " failed: " + e.getMessage(), e);
        }
        return Unit.UNIT;
    }

    /**
     * Process yield responses from Python, honouring delay/emit/spawn/wait_until,
     * until we receive "done" or "error".
     */
    private void driveToCompletion(String actId, JsonObject response, Protocol proto) throws Exception {
        while (true) {
            // Apply any emits first (before honouring the primary yield)
            if (response.has("emits")) {
                JsonArray emits = response.getAsJsonArray("emits");
                for (JsonElement el : emits) {
                    JsonObject e = el.getAsJsonObject();
                    String resource = e.get("resource").getAsString();
                    String value    = e.get("value").getAsString();
                    applyEmit(resource, value);
                }
            }

            // Schedule any spawns
            if (response.has("spawns")) {
                JsonArray spawns = response.getAsJsonArray("spawns");
                for (JsonElement el : spawns) {
                    JsonObject s = el.getAsJsonObject();
                    String childName = s.has("name") && !s.get("name").isJsonNull()
                        ? s.get("name").getAsString() : null;
                    if (childName != null && inputTopics.containsKey(childName)) {
                        final String cn = childName;
                        spawn(threaded(() -> runActivity(cn, Map.of())));
                    }
                }
            }

            String op = response.get("op").getAsString();

            switch (op) {
                case "done" -> { return; }

                case "error" -> {
                    String msg = response.has("message") ? response.get("message").getAsString() : "(no message)";
                    throw new RuntimeException("[PyMerlin] Python activity error: " + msg);
                }

                case "delay" -> {
                    long micros = response.get("duration_us").getAsLong();
                    delay(Duration.of(micros, Duration.MICROSECONDS));
                    JsonObject resume = Protocol.obj("op", "resume", "id", actId);
                    response = proto.roundtrip(resume);
                }

                case "wait_until" -> {
                    // Structured condition: immediately satisfied (Condition.TRUE) as a placeholder;
                    // real polling is handled by Aerie's cell change detection.
                    // For now we use the opaque path to let Python re-evaluate each tick.
                    waitUntil(Condition.TRUE);
                    JsonObject resume = Protocol.obj("op", "resume", "id", actId);
                    response = proto.roundtrip(resume);
                }

                case "wait_until_opaque" -> {
                    // Python re-evaluates the condition each time we resume
                    JsonObject resume = Protocol.obj("op", "resume", "id", actId);
                    response = proto.roundtrip(resume);
                    // If Python is still waiting it will send wait_until_opaque again;
                    // we loop and it gets handled next iteration.
                    // Insert a small delay to avoid busy-spinning in simulation time.
                    if ("wait_until_opaque".equals(response.get("op").getAsString())) {
                        delay(Duration.of(1, Duration.MICROSECONDS));
                    }
                }

                case "running" -> {
                    // Python yielded but isn't suspended — resume immediately
                    JsonObject resume = Protocol.obj("op", "resume", "id", actId);
                    response = proto.roundtrip(resume);
                }

                default -> throw new RuntimeException("[PyMerlin] Unknown op from Python: " + op);
            }
        }
    }

    private void applyEmit(String resourceName, String value) {
        ResourceCell rc = resourceCells.get(resourceName);
        if (rc != null) {
            emit(value, rc.topic());
        } else {
            System.err.println("[PyMerlin] emit for unknown resource: " + resourceName);
        }
    }

    private static boolean evaluateCondition(String current, String op, String threshold) {
        try {
            double cur = Double.parseDouble(current);
            double thr = Double.parseDouble(threshold);
            return switch (op) {
                case "gt"  -> cur >  thr;
                case "lt"  -> cur <  thr;
                case "gte" -> cur >= thr;
                case "lte" -> cur <= thr;
                case "eq"  -> cur == thr;
                case "neq" -> cur != thr;
                default    -> false;
            };
        } catch (NumberFormatException e) {
            // Fall back to string comparison
            return switch (op) {
                case "eq"  -> current.equals(threshold);
                case "neq" -> !current.equals(threshold);
                default    -> false;
            };
        }
    }

    // -----------------------------------------------------------------
    // Cell allocation
    // -----------------------------------------------------------------

    private static CellId<String[]> allocateStringCell(Initializer builder, String initial, Topic<String> topic) {
        return builder.allocate(
            new String[]{initial},
            new gov.nasa.jpl.aerie.merlin.protocol.model.CellType<String, String[]>() {
                @Override
                public gov.nasa.jpl.aerie.merlin.protocol.model.EffectTrait<String> getEffectType() {
                    return new gov.nasa.jpl.aerie.merlin.protocol.model.EffectTrait<>() {
                        @Override public String empty()                             { return null; }
                        @Override public String sequentially(String a, String b)   { return b != null ? b : a; }
                        @Override public String concurrently(String a, String b)   { return b != null ? b : a; }
                    };
                }
                @Override public void apply(String[] state, String effect) { if (effect != null) state[0] = effect; }
                @Override public String[] duplicate(String[] state)        { return new String[]{state[0]}; }
            },
            s -> s,
            topic
        );
    }

    // -----------------------------------------------------------------
    // Type stubs
    // -----------------------------------------------------------------

    private static String serializedValueToString(SerializedValue sv) {
        // Best-effort: extract the primitive value as a string
        return sv.match(new SerializedValue.Visitor<>() {
            @Override public String onNull()              { return "null"; }
            @Override public String onBoolean(boolean v)  { return Boolean.toString(v); }
            @Override public String onNumeric(java.math.BigDecimal v) { return v.toPlainString(); }
            @Override public String onString(String v)    { return v; }
            @Override public String onMap(Map<String, SerializedValue> m) { return m.toString(); }
            @Override public String onList(List<SerializedValue> l)       { return l.toString(); }
        });
    }

    private static InputType<Map<String, SerializedValue>> passthroughInputType() {
        return new InputType<>() {
            @Override public List<InputType.Parameter> getParameters()     { return List.of(); }
            @Override public List<String> getRequiredParameters()          { return List.of(); }
            @Override public Map<String, SerializedValue> instantiate(Map<String, SerializedValue> args) { return args; }
            @Override public Map<String, SerializedValue> getArguments(Map<String, SerializedValue> v)   { return v; }
            @Override public List<InputType.ValidationNotice> getValidationFailures(Map<String, SerializedValue> v) { return List.of(); }
        };
    }

    private static OutputType<Map<String, SerializedValue>> passthroughOutputType() {
        return new OutputType<>() {
            @Override public ValueSchema getSchema()                              { return ValueSchema.ofStruct(Map.of()); }
            @Override public SerializedValue serialize(Map<String, SerializedValue> v) { return SerializedValue.of(v); }
        };
    }

    private static OutputType<Unit> unitOutputType() {
        return new OutputType<>() {
            @Override public ValueSchema getSchema()           { return ValueSchema.ofStruct(Map.of()); }
            @Override public SerializedValue serialize(Unit v) { return SerializedValue.of(Map.of()); }
        };
    }

    private static OutputType<String> stringOutputType() {
        return new OutputType<>() {
            @Override public ValueSchema getSchema()             { return ValueSchema.STRING; }
            @Override public SerializedValue serialize(String v) { return SerializedValue.of(v); }
        };
    }
}
