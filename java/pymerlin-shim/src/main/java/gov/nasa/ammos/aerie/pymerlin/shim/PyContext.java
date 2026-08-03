package gov.nasa.ammos.aerie.pymerlin.shim;

import gov.nasa.jpl.aerie.graalpy.SharedPythonEngine;
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
 * <p>One {@link Context} is still created (and closed) per simulation ({@code
 * instantiate()} call) — that per-simulation isolation is intentional and stays,
 * since Python module globals and model state must never leak between unrelated
 * simulations. C extension modules are the one thing that does not isolate for free:
 * a shared library is loaded per OS process, so {@code python.IsolateNativeModules}
 * (set in {@link #build}) is what extends the same isolation to numpy and anything
 * built on it. What's shared (roadmap §11.3) is the underlying {@link
 * org.graalvm.polyglot.Engine}, via {@link SharedPythonEngine}: a JVM-wide singleton
 * that holds only the compiled-code cache and language configuration, no
 * simulation-specific state, so every {@code Context} built here amortizes the
 * interpreter/stdlib/pymerlin compile cost across every simulation this worker or
 * server process ever runs — instead of paying it fresh each time. A shared {@code
 * Context} (rather than a shared {@code Engine}) was considered and rejected: it
 * would carry {@code sys.modules} and model-level globals across simulations of
 * different plans/models, which nothing has validated as safe. {@link
 * SharedPythonEngine} deliberately lives in its own plandev module rather than here,
 * because a class bundled inside the uploaded model JAR gets reloaded — and its
 * statics reset — by the fresh {@code URLClassLoader} {@code MissionModelLoader}
 * creates for every simulation; only a class the worker's own parent classloader
 * supplies can actually stay shared.
 */
public final class PyContext {

    private PyContext() {}

    public static Context build() {
        Path resourcesRoot = resolveResourcesRoot();

        return GraalPyResources
            .contextBuilder(resourcesRoot)
            .engine(SharedPythonEngine.get())
            // Let every context load its own copy of any C extension module it imports.
            //
            // A C extension (numpy's _multiarray_umath.so, and therefore scipy, pandas, and
            // spiceypy through it) is a shared library, and dlopen maps it once per OS
            // PROCESS, not once per context. Its C globals -- type objects, caches, error
            // state -- were written for CPython, where one process means one interpreter.
            // GraalPy's default is to let the first context that imports such a module own
            // it outright and to fail any later context with
            //   SystemError: Option python.IsolateNativeModules is set to 'false' and a
            //   second GraalPy context attempted to load a native module ...
            // rather than let two interpreters silently corrupt one set of C globals.
            //
            // That default is unusable here, because this class hands out a context per
            // simulation (see below) plus one per metadata query. The SECOND of those to
            // touch numpy dies, and which one that is depends on the order contexts happen
            // to be created -- so it presents as a model that uploads fine one day and
            // fails extraction the next.
            //
            // Isolating instead gives each context a private copy of the library and its
            // globals. The cost is real: a copy of the .so is loaded and held per context,
            // so a numpy-importing model pays that memory for every concurrent simulation.
            // That is the price of the per-simulation isolation this class already commits
            // to; the alternative is not "cheaper", it is "one simulation per process".
            //
            // Set here, on the context, for two reasons. It is a context option, not an
            // engine option, so it cannot go on the shared Engine. And GraalPy's own help
            // text warns that ALL contexts in the process must set it for the feature to
            // work cooperatively -- so it belongs in the single place contexts are built,
            // never at a call site. allowExperimentalOptions is required, not incidental:
            // the option is EXPERIMENTAL in GraalPy 25.0.x, and setting an experimental
            // option without it throws instead of being ignored.
            .allowExperimentalOptions(true)
            .option("python.IsolateNativeModules", "true")
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
