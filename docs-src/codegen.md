# Java Codegen: Building Aerie-Compatible Mission Model JARs

This document explains how `pymerlin build-jar` works — how a Python mission model class is
introspected, converted into Java source files, compiled, and packaged into a JAR that Aerie
can load and simulate.

## Overview

Aerie's simulation engine (Merlin) is a Java process. Mission models must be uploaded as JARs
that implement a specific Java service-provider interface (SPI). PyMerlin bridges this gap by:

1. Introspecting a Python `@MissionModel` class at build time
2. Generating Java source files that satisfy the Merlin SPI
3. Compiling and packaging those sources into a fat JAR
4. At simulation time, launching the Python model as a subprocess and bridging calls via py4j

```
pymerlin build-jar               Aerie simulation
─────────────────                ────────────────────────────────
Python model class                GeneratedMerlinPlugin (SPI)
      │                                     │
      │  introspect                         │ getModelType()
      ▼                                     ▼
_codegen.py ──generates──► GeneratedModelType.instantiate()
      │                           │              │
      │  javac + jar              │ starts        │ registers
      ▼                           ▼              ▼
mission-model.jar        PyMerlinRuntime    builder.allocate(topic)
                                │           builder.resource(...)
                          py4j gateway      ActivityTypes.cellEmitters
                                │
                     python -m _runtime_server
                                │
                          PyMerlinBridge
                   ┌────────────┴────────────┐
             startActivity()          getResource()
             registerCellEmitter()
                   │
              TaskHandle (per activity)
           ┌───────┼──────────────┐
        step()  getStatus()  getSpawnedHandle()
                             getPendingEmitResource/Value()
```

---

## CLI Entry Point

```
pymerlin build-jar --model ./model.py:Mission --name my-model --version 1.0.0 --out my-model.jar
```

Defined in `pymerlin/_internal/_cli.py`. It:

1. Loads the Python model class via `importlib` (`_load_model_class`)
2. Calls `generate_mission_model_jar()` from `_codegen.py`

---

## Build-Time: `_codegen.py`

### `generate_mission_model_jar()`

Top-level orchestrator. Steps:

1. **`_extract_activity_types(model_class)`** — walks `model_class.activity_types` (populated by
   `@MissionModel` / `@ActivityType` decorators) and inspects each function's signature to collect
   parameter names, type annotations, and defaults.

2. **`_extract_resources(model_class)`** — instantiates the model class with a bare `Registrar`
   and captures every `registrar.resource(name, func)` call. This is purely for metadata (names);
   the actual resource functions run later at simulation time.

3. **Generate Java sources** into a temp directory:
   - `GeneratedMerlinPlugin.java` — SPI entry point
   - `GeneratedModelType.java` — `ModelType<Unit, Unit>` implementation
   - `ActivityTypes.java` — static registry of all activity mappers
   - `<ActivityName>Mapper.java` — one per activity type

4. **`_compile_java_sources()`** — runs `javac` against the generated sources, with a classpath
   built from `merlin-sdk`, `merlin-framework`, `merlin-driver`, `contrib`, `pymerlin-codegen`,
   and `py4j` JARs (see `_get_merlin_sdk_classpath()`).

5. **`_package_jar()`** — creates a fat JAR by:
   - Writing the SPI descriptor at
     `META-INF/services/gov.nasa.jpl.aerie.merlin.protocol.model.MerlinPlugin`
   - Exploding all dependency JARs into the classes directory
   - Bundling the Python model file into `/pymerlin_models/<filename>` inside the JAR
   - Running `jar cf` to produce the final artifact

### Generated Java Files

#### `GeneratedMerlinPlugin.java`

Implements the `MerlinPlugin` SPI. Aerie discovers this class via the `META-INF/services`
descriptor and calls `getModelType()` to obtain the model.

```java
public final class GeneratedMerlinPlugin implements MerlinPlugin {
    @Override
    public GeneratedModelType getModelType() { return new GeneratedModelType(); }
}
```

#### `GeneratedModelType.java`

Implements `ModelType<Unit, Unit>`. The key method is `instantiate()`, which runs once when
Aerie initializes a simulation:

- Sets the `pymerlin.model.ref` system property so the runtime subprocess knows which model to load
- Calls `ActivityTypes.registerTopics(builder)` — registers input/output topics for all activities
- Attempts `PyMerlinRuntime.getInstance(modelRef)` — starts the Python subprocess and blocks until
  ready. Wrapped in try-catch: on the `aerie_merlin` server (no Python installed), it fails
  gracefully so resource type extraction still succeeds.
- For each Python resource, allocates an Aerie cell backed by a `Topic<String>` field:
  - `builder.allocate(initialValue, CellType, event->event, topic)` — cell updates whenever
    an event is emitted to the topic; `CellType.apply` sets `state[0] = effect` (mutable `String[]` holder)
  - `builder.resource(name, ...)` — `getDynamics()` reads `querier.getState(cell)[0]`
  - Registers a `CellEmitter` lambda with the bridge and in `ActivityTypes.cellEmitters` —
    Python calls this to push new values; the lambda calls `ModelActions.emit(value, topic)`
    on the simulation task thread via the emit queue drain mechanism

#### `ActivityTypes.java`

Static `Map<String, ActivityMapper<Unit, ?, ?>>` built from one instance of each mapper.
Also provides:
- `registerTopics(Initializer)` — registers input/output topics for all activities
- `getTaskFactory(String name)` — looks up a mapper and calls `getTaskFactory()` via a typed
  private helper to avoid ambiguous overload compile errors
- `cellEmitters` — `ConcurrentHashMap<String, CellEmitter>` populated during `instantiate()`;
  used by `drainEmits()` in every mapper to call `ModelActions.emit` on the correct topic

#### `<ActivityName>Mapper.java`

One per activity. Implements `ActivityMapper<Unit, Map<String, SerializedValue>, Unit>`:

- **`getInputType()`** — `InputMapper`: passes `SerializedValue` arguments through as-is
  (parameter schema introspection is not yet wired up)
- **`getOutputType()`** — `OutputMapper`: returns `Unit` serialized as an empty struct
- **`getTaskFactory()`** — returns a `ModelActions.threaded(...)` task that:
  1. Emits the activity arguments onto the input topic
  2. Sets the thread's context classloader to the mission model URLClassLoader (needed for py4j
     interface resolution), then calls `bridge.startActivity(activityName, args)` to get a `TaskHandle`
  3. Calls `driveHandle(handle, ActivityTypes.cellEmitters)` — steps the handle to completion,
     draining pending cell emits and spawned children after each step
  4. Emits `Unit.UNIT` onto the output topic

- **`driveHandle(handle, emitters)`** — loop: call `drainEmits`, `spawnChildren`, check status,
  `ModelActions.delay` if needed, `handle.step()`, repeat until `"completed"`
- **`drainEmits(handle, emitters)`** — polls `handle.getPendingEmitResource()` / `getPendingEmitValue()`
  in a loop; calls the matching `CellEmitter.emit(value)` on the simulation task thread so
  `ModelActions.emit` has a valid context
- **`spawnChildren(handle)`** — polls `handle.getSpawnedHandle()`; looks up child mapper in
  `ActivityTypes.directiveTypes`; calls `getTaskFactory()` and passes to `ModelActions.spawnWithSpan()`
  so the child is recorded as a named simulated activity with `activity_type_name`

---

## Runtime: Java Side — `PyMerlinRuntime.java`

Located in `java/pymerlin-codegen/`. This is compiled into `pymerlin-codegen.jar`, which is
bundled into every generated mission model JAR.

**Singleton** — `getInstance(modelRef)` uses double-checked locking to ensure the Python
subprocess is started exactly once per JVM.

### Startup sequence

1. Picks a free TCP port (`findFreePort()`)
2. Creates a `PyMerlinBridgeHolder` (holds the bridge reference + a `CountDownLatch`)
3. Starts a py4j `GatewayServer` on that port with the holder as entry point
4. Spawns `python -m pymerlin._internal._runtime_server --model <ref> --gateway-port <port>`
5. Waits up to 30 seconds for the latch to count down (i.e., for Python to call
   `entry_point.register(bridge)`)
6. Retrieves the registered `PyMerlinBridge` proxy

### Model extraction from JAR

If `modelRef` starts with `/pymerlin_models/`, `extractModelFromJar()` reads the file out of the
JAR's classpath resources and writes it to a temp directory, then updates the ref to point to the
temp path. This is how the bundled Python file is made available at simulation time.

### `PyMerlinBridge` interface

```java
public interface PyMerlinBridge {
    TaskHandle startActivity(String activityName, Map<String, Object> args);
    Object getResource(String resourceName);
    void registerCellEmitter(String resourceName, CellEmitter emitter);
}
```

py4j implements this interface as a transparent proxy to the Python `PyMerlinBridge` object.

`startActivity` returns a `TaskHandle` py4j proxy rather than blocking; the caller drives the
handle to completion via the step loop. `registerCellEmitter` passes a Java lambda to Python;
Python stores it but does **not** call it directly — instead it queues `(resource_name, value)`
pairs on the `TaskHandle`'s emit queue so Java can call `ModelActions.emit` on the correct thread.

### `TaskHandle` interface

```java
public interface TaskHandle {
    void step();
    String getStatus();           // "running" | "delayed" | "completed" | "error"
    long getDelayMicros();
    SpawnInfo getSpawnedHandle(); // null if none pending
    String getPendingEmitResource(); // null if none pending
    String getPendingEmitValue();    // call immediately after getPendingEmitResource
}
```

### `CellEmitter` interface

```java
public interface CellEmitter {
    void emit(String value);
}
```

Passed to Python via `registerCellEmitter`. Stored in `ActivityTypes.cellEmitters`. Called by
`drainEmits()` on the simulation task thread to trigger `ModelActions.emit(value, topic)`.

---

## Runtime: Python Side — `_runtime_server.py`

Launched as a subprocess by `PyMerlinRuntime`. Steps:

1. Parses `--model` and `--gateway-port` from args
2. Loads the model class via `importlib` (`load_model_class`)
3. Connects to the Java `GatewayServer` via `JavaGateway(GatewayParameters(port=...))`
4. Constructs a `PyMerlinBridge` instance and calls `entry_point.register(bridge)`
5. Sleeps in a loop, keeping the process alive for the duration of the simulation

### `PyMerlinBridge` (Python)

```python
class Java:
    implements = ["gov.nasa.ammos.aerie.merlin.python.codegen.PyMerlinRuntime$PyMerlinBridge"]
```

py4j uses the `Java.implements` declaration to expose this Python object as an implementation of
the Java interface.

**`_ensure_model()`** — lazily constructs the model instance with a real `Registrar` on the first
call. Assigns cell IDs, patches `CellRef.emit` on every cell to enqueue `(resource_name, value)`
into `_current_emit_queue[0]` (a holder pointing to the active `TaskHandle`'s emit queue), and
builds `_cell_id_to_resource` reverse-map from the registrar's resource list.

**`registerCellEmitter(resource_name, emitter)`**:
- Stores the Java `CellEmitter` proxy in `self._cell_emitters` (not called directly from Python)

**`startActivity(activity_name, args)`**:
- Calls `_ensure_model()`, creates a `Queue()` as the emit queue
- Sets `_current_emit_queue[0]` to that queue so subsequent `CellRef.emit` calls enqueue there
- Creates and starts a `TaskHandle` wrapping the activity function
- Returns the `TaskHandle` py4j proxy to Java

**`getResource(resource_name)`**:
- Calls `_ensure_model()`, walks `self._registrar.resources`, returns `str(current_value)`

### `TaskHandle` (Python)

Wraps a Python activity running in a daemon thread. Communicates via two `Queue` objects:
- `_outbox`: Python → Java: `("yield", status)`, `("done",)`, or `("error", exc)`
- `_inbox`: Java → Python: `"resume"` or `"abort"`

**`step()`** — puts `"resume"` on inbox, then blocks on outbox for next yield/done/error.

**`getSpawnedHandle()`** — non-blocking drain of `_spawn_queue`; returns a `SpawnInfo` py4j
proxy (name of spawned activity) or `None`.

**`getPendingEmitResource()` / `getPendingEmitValue()`** — non-blocking drain of `_emit_queue`;
returns resource name and value strings (one item at a time) or `None`.

---

## Data Flow During Simulation

```
Aerie triggers activity "activity1"
        │
        ▼
Activity1Mapper.getTaskFactory() → ModelActions.threaded(...)
        │
        ├─ emit args onto inputTopic
        │
        ├─ bridge.startActivity("activity1", {})  [py4j call]
        │       │
        │       └─ Python starts activity thread → TaskHandle returned
        │
        ├─ driveHandle(handle, cellEmitters):
        │     loop:
        │       drainEmits → getPendingEmitResource/Value → CellEmitter.emit(value)
        │                    [ModelActions.emit(value, topic) on task thread]
        │       spawnChildren → getSpawnedHandle → ActivityTypes.getTaskFactory(childName)
        │                       → ModelActions.spawnWithSpan(childTaskFactory)
        │       if "delayed": ModelActions.delay(micros)
        │       handle.step()
        │     until "completed"
        │
        └─ emit Unit onto outputTopic

Python activity calls cell.emit("foo")
        │
        ▼
patched CellRef.emit → updates _cell_values dict
        │
        └─ _emit_queue.put(("/cell1", "foo"))
              [drained by drainEmits on Java task thread]
              [→ ModelActions.emit("foo", topic_cell1)]
              [→ Aerie records new profile segment for /cell1]

Python activity calls spawn(activity2(mission))
        │
        ▼
spawner → _spawn_queue.put(SpawnInfo("activity2", {}))
              [drained by spawnChildren on Java task thread]
              [→ ActivityTypes.getTaskFactory("activity2")]
              [→ ModelActions.spawnWithSpan(...)]
              [→ Aerie records activity2 as named child span]
```

---

## Known Limitations

- **Resource values are always serialized as strings.** The `ValueSchema` is hardcoded to
  `ValueSchema.STRING`. Proper type-aware serialization (int, double, struct, etc.) is not yet
  implemented.
- **Activity parameter schemas are empty.** `InputMapper.getParameters()` always returns
  `List.of()`. Aerie will not be able to validate or display parameter schemas for activities.
- **`deserializeValue()` in the activity mapper is a stub.** `SerializedValue` objects are passed
  to Python unconverted; Python code receives py4j proxy objects rather than native Python types.
- **Classpath resolution is hardcoded to `~/Desktop/plandev`.** `_get_merlin_sdk_classpath()` only
  looks in that fixed directory for Merlin JARs.
- **No end-to-end test.** There is no automated test that exercises the full pipeline from
  `build-jar` through upload to Aerie and simulation.
- **Single model instance per JVM.** `PyMerlinRuntime` is a singleton; running multiple
  simulations in the same JVM would share one Python subprocess.
