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
 *     src/    ← on the Python path by GraalPyResources convention; must exist
 * </pre>
 *
 * The root path is read from the {@code PYMERLIN_RESOURCES} environment
 * variable (set in the Dockerfile), falling back to the {@code pymerlin.resources}
 * system property, then to {@code /opt/pymerlin/python-resources}.
 *
 * <p>Phase 2 note: {@link GraalBridge} currently loads the model by adding its extracted
 * directory to {@code sys.path} directly (which works because {@link #build} sets
 * {@code allowAllAccess(true)}), rather than copying it into {@code ${root}/src} as
 * roadmap §5.3 ultimately calls for. That relocation is deferred — it is only strictly
 * needed once filesystem access is sandboxed — but the source is now cleaned up on
 * bridge close either way, which is the item §5.3 flagged.
 *
 * The {@link Context} is cached and reused across simulations by {@link GraalBridge}
 * (roadmap §11.3), eliminating ~15s cold-start on every simulation after the first.
 */
public final class PyContext {

    private PyContext() {}

    public static Context build() {
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
