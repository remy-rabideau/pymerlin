package gov.nasa.ammos.aerie.pymerlin.shim;

/**
 * The Java host object handed to Python on the Phase 3 (roadmap §6) direct-call path.
 *
 * <p>A Python activity running in-process (via {@link GraalBridge}) calls these methods
 * <em>synchronously, on the Java {@code ThreadedTask} thread it is executing on</em>, in
 * place of the old queue-handoff-to-{@code driveToCompletion} protocol:
 * <ul>
 *   <li>{@code delay(micros)} → {@code ModelActions.delay(...)} parks this thread (with the
 *       Python frames still live on its stack) until the engine resumes it; Gate B proved
 *       GraalPy releases the context lock across this host call so other tasks can still run.</li>
 *   <li>{@code emit(resource, value)} → routes to the resource's topic.</li>
 *   <li>{@code spawnActivity(name, argsJson)} → a fresh child {@code ThreadedTask}
 *       ({@code InSpan.Fresh}), fire-and-forget.</li>
 *   <li>{@code callActivity(name, argsJson)} → a fresh child, blocking the caller until it
 *       completes ({@code InSpan.Fresh} + {@code call} semantics).</li>
 * </ul>
 *
 * <p>Arguments cross the boundary as a JSON string rather than a GraalPy {@code Value}, so
 * this interface (and everything it touches in {@link ShimModelType}) stays free of any
 * polyglot types — the same reason the rest of the shim speaks JSON.
 *
 * <p>A single instance is shared across every activity: its methods delegate to
 * {@code ModelActions.*}, which act on whichever {@code ThreadedTask} thread is currently
 * calling, so no per-activity state is needed here.
 */
public final class PyActions {

    private final ShimModelType shim;

    PyActions(ShimModelType shim) {
        this.shim = shim;
    }

    public void delay(long micros) {
        shim.directDelay(micros);
    }

    public void emit(String resource, String value) {
        shim.applyEmit(resource, value);
    }

    public void spawnActivity(String name, String argsJson) {
        shim.directSpawn(name, argsJson);
    }

    public void callActivity(String name, String argsJson) {
        shim.directCall(name, argsJson);
    }
}
