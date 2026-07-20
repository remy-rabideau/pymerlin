package gov.nasa.ammos.aerie.pymerlin.shim;

import org.graalvm.polyglot.Context;
import org.graalvm.python.embedding.GraalPyResources;

import java.nio.file.Path;

/**
 * Builds a GraalPy {@link Context} pointed at the external-directory layout
 * that the worker image provisions at build time (roadmap §5.2):
 *
 * <pre>
 *   ${PYMERLIN_RESOURCES}/
 *     venv/   ← pymerlin + numpy + spiceypy, pip-installed by install.sh
 *     src/    ← per-simulation model source, populated by GraalBridge
 * </pre>
 *
 * The root path is read from the {@code PYMERLIN_RESOURCES} environment
 * variable (set in the Dockerfile), falling back to the {@code pymerlin.resources}
 * system property, then to {@code /opt/pymerlin/python-resources}.
 *
 * One {@link Context} is created per simulation ({@code instantiate()} call).
 * Reuse across simulations is a future optimisation (roadmap §11.3).
 */
public final class PyContext {

    private PyContext() {}

    public static Context build(Path modelSrcDir) {
        Path resourcesRoot = resolveResourcesRoot();

        return GraalPyResources
            .contextBuilder(resourcesRoot)
            .allowAllAccess(true)
            .allowCreateThread(true)   // _ActivityRunner still uses Python threads in Phase 2
            .build();
    }

    static Path resolveResourcesRoot() {
        String env = System.getenv("PYMERLIN_RESOURCES");
        if (env != null && !env.isBlank()) return Path.of(env);

        String prop = System.getProperty("pymerlin.resources");
        if (prop != null && !prop.isBlank()) return Path.of(prop);

        return Path.of("/opt/pymerlin/python-resources");
    }
}
