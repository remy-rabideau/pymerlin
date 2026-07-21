package gov.nasa.ammos.aerie.pymerlin.shim;

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
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.atomic.AtomicLong;
import java.util.jar.Manifest;

import static gov.nasa.jpl.aerie.merlin.framework.ModelActions.delay;
import static gov.nasa.jpl.aerie.merlin.framework.ModelActions.emit;
import static gov.nasa.jpl.aerie.merlin.framework.ModelActions.spawnWithSpan;
import static gov.nasa.jpl.aerie.merlin.framework.ModelActions.callWithSpan;
import static gov.nasa.jpl.aerie.merlin.framework.ModelActions.threaded;

/**
 * Generic Aerie ModelType implementation that delegates all model behaviour to Python,
 * running in-process via GraalPy ({@link GraalBridge}). Each activity runs on its own
 * Java {@code ThreadedTask} thread; delay/emit/spawn/call are driven by direct host
 * callbacks from Python into {@link PyActions} (roadmap §6) — there is no subprocess,
 * no wire protocol, and no drive loop interpreting yield responses.
 *
 * The model reference (e.g. "path/to/model.py:Mission") is read from the system
 * property {@code pymerlin.model.ref}, which is injected into the JAR manifest
 * by {@code pymerlin package} and set at startup.
 */
public final class ShimModelType implements ModelType<Unit, Unit> {

    // --- per-resource cell bookkeeping ---
    private record ResourceCell(
        Topic<String> topic,
        CellId<String[]> cellId,
        String valueType   // "float", "int", "bool", or "str"
    ) {}

    private final Map<String, ResourceCell> resourceCells = new HashMap<>();
    private final Map<String, Topic<Map<String, SerializedValue>>> inputTopics  = new HashMap<>();
    private final Map<String, Topic<Unit>>                         outputTopics = new HashMap<>();

    // --- per-parameter metadata ---
    private record ParamInfo(
        String name,
        ValueSchema schema,
        boolean required,
        SerializedValue defaultValue
    ) {}

    // Activity names populated either by instantiate() or by a one-shot query in getDirectiveTypes().
    private volatile Set<String> activityNames = null;

    // Per-activity ordered parameter metadata, populated alongside activityNames.
    private final Map<String, List<ParamInfo>> activityParams = new HashMap<>();

    private final AtomicLong activityCounter = new AtomicLong(0);

    // Bridge is shared across all activity executions for a given simulation.
    private volatile PyBridge bridge = null;

    // Host callback object handed to Python for every activity execution (roadmap §6).
    // Stateless (delegates to ModelActions on the calling ThreadedTask thread), so one
    // shared instance serves every activity.
    private final PyActions pyActions = new PyActions(this);

    // -----------------------------------------------------------------
    // ModelType interface
    // -----------------------------------------------------------------

    @Override
    public Map<String, ? extends DirectiveType<Unit, ?, ?>> getDirectiveTypes() {
        // Aerie may call this on a fresh instance (before instantiate()) to extract
        // activity type metadata for the DB. Start a one-shot bridge if needed.
        if (activityNames == null) {
            fetchActivityNames();
        }
        return buildDirectiveTypes();
    }

    private synchronized void fetchActivityNames() {
        if (activityNames != null) return; // double-checked
        String modelRef = resolveModelRef();
        try (PyBridge oneShot = PyBridge.create(modelRef)) {
            JsonObject types = oneShot.getActivityTypes();
            Set<String> names = new java.util.LinkedHashSet<>();
            if (types != null) {
                for (String name : types.keySet()) {
                    names.add(name);
                    activityParams.put(name, parseParams(types.getAsJsonObject(name)));
                }
            }
            activityNames = names;
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
            bridge = PyBridge.create(modelRef);
        } catch (Exception e) {
            throw new RuntimeException("[PyMerlin] Failed to start bridge: " + e.getMessage(), e);
        }

        // Populate activityNames from the live bridge (avoids a second startup).
        try {
            JsonObject types = bridge.getActivityTypes();
            Set<String> names = new java.util.LinkedHashSet<>();
            if (types != null) {
                for (String name : types.keySet()) {
                    names.add(name);
                    activityParams.put(name, parseParams(types.getAsJsonObject(name)));
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
            JsonObject resources = bridge.getResources();
            if (resources != null) {
                for (String resName : resources.keySet()) {
                    String initialValue = bridge.getResourceValue(resName);

                    JsonObject resMeta = resources.getAsJsonObject(resName);
                    String vtype = (resMeta != null && resMeta.has("value_type"))
                        ? resMeta.get("value_type").getAsString() : "str";

                    Topic<String> topic = new Topic<>();
                    CellId<String[]> cellId = allocateStringCell(builder, initialValue, topic);
                    resourceCells.put(resName, new ResourceCell(topic, cellId, vtype));

                    final String capturedName = resName;
                    final CellId<String[]> capturedCell = cellId;
                    final String capturedVtype = vtype;
                    builder.resource(capturedName, new Resource<String>() {
                        @Override public String getType() { return "discrete"; }
                        @Override public OutputType<String> getOutputType() { return typedOutputType(capturedVtype); }
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
     * the file (or entire package directory) to a temp directory and return a ref
     * pointing at the extracted path.
     *
     * Single-file:  pymerlin_models/model.py:ClassName
     * Package dir:  pymerlin_models/mypkg/model.py:ClassName
     */
    private static String extractIfBundled(String ref) throws IOException {
        if (!ref.contains(":")) return ref;
        String[] parts = ref.split(":", 2);
        String resourcePath = parts[0];   // e.g. pymerlin_models/mypkg/model.py
        String className    = parts[1];

        // Check whether the resource is actually bundled in our JAR.
        URL resource = ShimModelType.class.getClassLoader().getResource(resourcePath);
        if (resource == null) {
            // Not bundled — treat as a filesystem path
            return ref;
        }

        String simId = java.util.UUID.randomUUID().toString().substring(0, 8);
        Path tmpDir = Files.createTempDirectory("pymerlin-model-" + simId + "-");

        // Determine if this is a package (resourcePath has >2 segments, i.e. pymerlin_models/<pkg>/<file>)
        Path rp = Path.of(resourcePath);
        boolean isPackage = rp.getNameCount() >= 3; // pymerlin_models / pkg / file.py

        if (isPackage) {
            // Extract every resource under pymerlin_models/<pkg>/ from the JAR.
            String pkgPrefix = rp.getParent().toString().replace('\\', '/') + "/";
            String pkgName   = rp.getParent().getFileName().toString();
            Path pkgDest     = tmpDir.resolve(pkgName);
            Files.createDirectories(pkgDest);

            // Walk the JAR entries via the jar: URL protocol.
            URL jarUrl = ShimModelType.class.getProtectionDomain().getCodeSource().getLocation();
            java.net.JarURLConnection conn = (java.net.JarURLConnection) new URL("jar:" + jarUrl.toExternalForm() + "!/").openConnection();
            try (java.util.jar.JarFile jar = conn.getJarFile()) {
                java.util.Enumeration<java.util.jar.JarEntry> entries = jar.entries();
                while (entries.hasMoreElements()) {
                    java.util.jar.JarEntry entry = entries.nextElement();
                    String name = entry.getName();
                    if (!name.startsWith(pkgPrefix) || entry.isDirectory()) continue;
                    String relative = name.substring(pkgPrefix.length()); // e.g. "model.py" or "sub/foo.py"
                    Path dest = pkgDest.resolve(relative);
                    Files.createDirectories(dest.getParent());
                    try (InputStream is = jar.getInputStream(entry)) {
                        Files.copy(is, dest, java.nio.file.StandardCopyOption.REPLACE_EXISTING);
                    }
                }
            }
            Path modelFile = pkgDest.resolve(rp.getFileName().toString());
            System.out.println("[PyMerlin] Extracted bundled package to: " + pkgDest);
            return modelFile.toAbsolutePath() + ":" + className;
        } else {
            // Single file: extract just that one resource.
            String fileName = rp.getFileName().toString();
            Path dest = tmpDir.resolve(fileName);
            try (InputStream is = resource.openStream()) {
                Files.copy(is, dest);
            }
            System.out.println("[PyMerlin] Extracted bundled model to: " + dest);
            return dest.toAbsolutePath() + ":" + className;
        }
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
                    return activityInputType(activityName);
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

        // Serialize args to native JSON types so both bridges receive the correct types
        Map<String, com.google.gson.JsonElement> argsJson = new java.util.LinkedHashMap<>();
        for (Map.Entry<String, SerializedValue> e : args.entrySet()) {
            argsJson.put(e.getKey(), serializedValueToJson(e.getValue()));
        }

        try {
            emit(args, inputTopics.get(activityName));
            // Runs the Python function directly on this ThreadedTask thread (roadmap §6).
            // delay/emit/spawn/call happen via pyActions callbacks; returns when done.
            bridge.runActivityDirect(actId, activityName, argsJson, pyActions);
            emit(Unit.UNIT, outputTopics.get(activityName));
        } catch (Exception e) {
            throw new RuntimeException("[PyMerlin] Activity " + activityName + " failed: " + e.getMessage(), e);
        }
        return Unit.UNIT;
    }

    // -----------------------------------------------------------------
    // Direct-call callbacks (Phase 3, §6) — invoked from Python via PyActions,
    // synchronously, on the ThreadedTask thread currently running the activity.
    // -----------------------------------------------------------------

    void directDelay(long micros) {
        delay(Duration.of(micros, Duration.MICROSECONDS));
    }

    void directSpawn(String name, String argsJson) {
        Map<String, SerializedValue> args = parseChildArgs(argsJson);
        spawnWithSpan(threaded(() -> runActivity(name, args)));
    }

    void directCall(String name, String argsJson) {
        Map<String, SerializedValue> args = parseChildArgs(argsJson);
        callWithSpan(threaded(() -> runActivity(name, args)));
    }

    /** Parse the JSON args string PyActions hands over (from Python's _child_args_json). */
    private static Map<String, SerializedValue> parseChildArgs(String argsJson) {
        Map<String, SerializedValue> result = new LinkedHashMap<>();
        if (argsJson == null || argsJson.isBlank()) return result;
        JsonObject obj = com.google.gson.JsonParser.parseString(argsJson).getAsJsonObject();
        for (String key : obj.keySet()) {
            result.put(key, jsonToSerializedValue(obj.get(key)));
        }
        return result;
    }

    void applyEmit(String resourceName, String value) {
        ResourceCell rc = resourceCells.get(resourceName);
        if (rc != null) {
            emit(value, rc.topic());
        } else {
            System.err.println("[PyMerlin] emit for unknown resource: " + resourceName);
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

    private static SerializedValue coerceToSchema(SerializedValue val, ValueSchema schema) {
        String raw = serializedValueToString(val);
        if (schema == ValueSchema.REAL) {
            try { return SerializedValue.of(Double.parseDouble(raw)); }
            catch (NumberFormatException e) { return val; }
        } else if (schema == ValueSchema.INT) {
            try { return SerializedValue.of(Long.parseLong(raw)); }
            catch (NumberFormatException e) {
                try { return SerializedValue.of((long) Double.parseDouble(raw)); }
                catch (NumberFormatException e2) { return val; }
            }
        } else if (schema == ValueSchema.BOOLEAN) {
            return SerializedValue.of(Boolean.parseBoolean(raw));
        }
        return val; // STRING and others pass through unchanged
    }

    private static com.google.gson.JsonElement serializedValueToJson(SerializedValue sv) {
        return sv.match(new SerializedValue.Visitor<com.google.gson.JsonElement>() {
            @Override public com.google.gson.JsonElement onNull()             { return com.google.gson.JsonNull.INSTANCE; }
            @Override public com.google.gson.JsonElement onBoolean(boolean v) { return new com.google.gson.JsonPrimitive(v); }
            @Override public com.google.gson.JsonElement onNumeric(java.math.BigDecimal v) { return new com.google.gson.JsonPrimitive(v); }
            @Override public com.google.gson.JsonElement onString(String v)   { return new com.google.gson.JsonPrimitive(v); }
            @Override public com.google.gson.JsonElement onMap(Map<String, SerializedValue> m) {
                com.google.gson.JsonObject obj = new com.google.gson.JsonObject();
                for (var entry : m.entrySet()) obj.add(entry.getKey(), serializedValueToJson(entry.getValue()));
                return obj;
            }
            @Override public com.google.gson.JsonElement onList(List<SerializedValue> l) {
                com.google.gson.JsonArray arr = new com.google.gson.JsonArray();
                for (var item : l) arr.add(serializedValueToJson(item));
                return arr;
            }
        });
    }

    private InputType<Map<String, SerializedValue>> activityInputType(String activityName) {
        return new InputType<>() {
            private List<ParamInfo> params() {
                return activityParams.getOrDefault(activityName, List.of());
            }

            @Override
            public List<InputType.Parameter> getParameters() {
                List<InputType.Parameter> result = new ArrayList<>();
                for (ParamInfo p : params()) result.add(new InputType.Parameter(p.name(), p.schema()));
                return result;
            }

            @Override
            public List<String> getRequiredParameters() {
                List<String> result = new ArrayList<>();
                for (ParamInfo p : params()) if (p.required()) result.add(p.name());
                return result;
            }

            @Override
            public Map<String, SerializedValue> instantiate(Map<String, SerializedValue> args) {
                Map<String, SerializedValue> merged = new LinkedHashMap<>();
                for (ParamInfo p : params()) {
                    SerializedValue val = args.containsKey(p.name()) ? args.get(p.name())
                                       : p.defaultValue();
                    if (val != null) merged.put(p.name(), coerceToSchema(val, p.schema()));
                }
                // pass through any extra keys the caller provided
                for (Map.Entry<String, SerializedValue> e : args.entrySet()) {
                    merged.putIfAbsent(e.getKey(), e.getValue());
                }
                return merged;
            }

            @Override
            public Map<String, SerializedValue> getArguments(Map<String, SerializedValue> v) { return v; }

            @Override
            public List<InputType.ValidationNotice> getValidationFailures(Map<String, SerializedValue> v) { return List.of(); }
        };
    }

    private static List<ParamInfo> parseParams(JsonObject activityJson) {
        List<ParamInfo> result = new ArrayList<>();
        if (activityJson == null) return result;
        JsonObject parameters = activityJson.getAsJsonObject("parameters");
        if (parameters == null) return result;
        for (String paramName : parameters.keySet()) {
            JsonObject meta = parameters.getAsJsonObject(paramName);
            String typeStr  = meta.has("type")     ? meta.get("type").getAsString()     : "any";
            boolean required = meta.has("required") && meta.get("required").getAsBoolean();
            SerializedValue defaultVal = null;
            if (!required && meta.has("default") && !meta.get("default").isJsonNull()) {
                defaultVal = jsonToSerializedValue(meta.get("default"));
            }
            result.add(new ParamInfo(paramName, pythonTypeToSchema(typeStr), required, defaultVal));
        }
        return result;
    }

    private static ValueSchema pythonTypeToSchema(String pyType) {
        return switch (pyType) {
            case "int"   -> ValueSchema.INT;
            case "float" -> ValueSchema.REAL;
            case "bool"  -> ValueSchema.BOOLEAN;
            default      -> ValueSchema.STRING;
        };
    }

    private static SerializedValue jsonToSerializedValue(JsonElement el) {
        if (el.isJsonNull())              return SerializedValue.of("null");
        if (el.isJsonPrimitive()) {
            var prim = el.getAsJsonPrimitive();
            if (prim.isBoolean()) return SerializedValue.of(prim.getAsBoolean());
            if (prim.isNumber()) {
                double d = prim.getAsDouble();
                if (d == Math.floor(d) && !Double.isInfinite(d)) return SerializedValue.of((long) d);
                return SerializedValue.of(d);
            }
            return SerializedValue.of(prim.getAsString());
        }
        return SerializedValue.of(el.toString());
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

    private static OutputType<String> typedOutputType(String vtype) {
        return switch (vtype) {
            case "float" -> new OutputType<>() {
                @Override public ValueSchema getSchema() { return ValueSchema.REAL; }
                @Override public SerializedValue serialize(String v) {
                    try { return SerializedValue.of(Double.parseDouble(v)); }
                    catch (NumberFormatException e) { return SerializedValue.of(0.0); }
                }
            };
            case "int" -> new OutputType<>() {
                @Override public ValueSchema getSchema() { return ValueSchema.INT; }
                @Override public SerializedValue serialize(String v) {
                    try { return SerializedValue.of(Long.parseLong(v)); }
                    catch (NumberFormatException e) {
                        try { return SerializedValue.of((long) Double.parseDouble(v)); }
                        catch (NumberFormatException e2) { return SerializedValue.of(0L); }
                    }
                }
            };
            case "bool" -> new OutputType<>() {
                @Override public ValueSchema getSchema() { return ValueSchema.BOOLEAN; }
                @Override public SerializedValue serialize(String v) {
                    return SerializedValue.of(Boolean.parseBoolean(v));
                }
            };
            default -> stringOutputType();
        };
    }
}
