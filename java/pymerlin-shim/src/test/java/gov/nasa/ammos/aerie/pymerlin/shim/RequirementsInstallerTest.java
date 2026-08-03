package gov.nasa.ammos.aerie.pymerlin.shim;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.PosixFilePermissions;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

/**
 * Coverage for installing a model's declared Python packages into the venv.
 *
 * <p>pip is replaced by a shell script that records how it was called, so everything around
 * the install — marker files, the lock, constraints, output streaming, failure handling — is
 * exercised without a GraalPy venv or a network. What pip itself does with a requirements
 * file is pip's business and is not re-tested here; that belongs to the on-image integration
 * test.
 */
public final class RequirementsInstallerTest {

    private static final String REQUIREMENTS = "PyYAML==6.0.3\ntoml==0.10.2\n";

    /**
     * Lay out a resources root with a stub pip in place of the venv's.
     *
     * @param script the stub's body; it can inspect "$@" and write to {@code $PYMERLIN_TEST_LOG}
     */
    private static Path resourcesRootWithPip(Path dir, String script) throws IOException {
        Path pip = dir.resolve("venv/bin/pip");
        Files.createDirectories(pip.getParent());
        Files.writeString(pip, "#!/bin/sh\n" + script + "\n");
        Files.setPosixFilePermissions(pip, PosixFilePermissions.fromString("rwxr-xr-x"));
        return dir;
    }

    /** A stub pip that appends one line per invocation to {@code calls.log} and succeeds. */
    private static Path recordingPip(Path dir) throws IOException {
        return resourcesRootWithPip(dir, """
            echo "invoked $*" >> "$0.calls.log"
            echo "PIP_CONSTRAINT=${PIP_CONSTRAINT:-unset}" >> "$0.calls.log"
            for arg in "$@"; do
              case "$arg" in
                *.txt) cat "$arg" >> "$0.calls.log" ;;
              esac
            done
            echo "Requirement already satisfied: PyYAML"
            """);
    }

    private static List<String> pipCalls(Path resourcesRoot) throws IOException {
        Path log = resourcesRoot.resolve("venv/bin/pip.calls.log");
        return Files.exists(log) ? Files.readAllLines(log) : List.of();
    }

    private static long invocations(Path resourcesRoot) throws IOException {
        return pipCalls(resourcesRoot).stream().filter(l -> l.startsWith("invoked ")).count();
    }

    private static void assumePosix() {
        assumeTrue(!System.getProperty("os.name").toLowerCase().contains("win"),
            "stub pip is a shell script; the worker image is Linux anyway");
    }

    @Test
    public void runsPipWithTheDeclaredRequirements(@TempDir Path dir) throws Exception {
        assumePosix();
        Path root = recordingPip(dir);

        RequirementsInstaller.install(REQUIREMENTS, root);

        List<String> calls = pipCalls(root);
        String invocation = calls.stream().filter(l -> l.startsWith("invoked ")).findFirst().orElseThrow();
        assertTrue(invocation.contains("install"), invocation);
        assertTrue(invocation.contains("--no-cache-dir"), invocation);
        assertTrue(invocation.contains("-r "), invocation);
        // The requirements reached pip as written, not paraphrased.
        assertTrue(calls.contains("PyYAML==6.0.3"), calls.toString());
        assertTrue(calls.contains("toml==0.10.2"), calls.toString());
    }

    @Test
    public void declaringNothingRunsNoPip(@TempDir Path dir) throws Exception {
        assumePosix();
        Path root = recordingPip(dir);

        RequirementsInstaller.install(null, root);
        RequirementsInstaller.install("", root);
        RequirementsInstaller.install("   \n", root);

        assertEquals(0, invocations(root));
    }

    @Test
    public void skipsTheSecondTimeTheSameSetIsRequested(@TempDir Path dir) throws Exception {
        assumePosix();
        Path root = recordingPip(dir);

        RequirementsInstaller.install(REQUIREMENTS, root);
        RequirementsInstaller.install(REQUIREMENTS, root);
        RequirementsInstaller.install(REQUIREMENTS, root);

        assertEquals(1, invocations(root), "a repeat simulation of one model should not re-run pip");
    }

    @Test
    public void aDifferentSetInstallsAgain(@TempDir Path dir) throws Exception {
        assumePosix();
        Path root = recordingPip(dir);

        RequirementsInstaller.install(REQUIREMENTS, root);
        RequirementsInstaller.install("toml==0.10.2\n", root);

        assertEquals(2, invocations(root), "a second model's packages must still be installed");
    }

    @Test
    public void theMarkerSurvivesARestart(@TempDir Path dir) throws Exception {
        assumePosix();
        Path root = recordingPip(dir);

        RequirementsInstaller.install(REQUIREMENTS, root);

        // The marker is on disk, next to the venv it describes, precisely so that it is not
        // lost when the classloader (or the whole worker) goes away. Nothing in-memory is
        // consulted here, so a fresh call stands in for a fresh process.
        try (var markers = Files.list(root)) {
            assertTrue(markers.anyMatch(p -> p.getFileName().toString().startsWith(".installed-")));
        }
        RequirementsInstaller.install(REQUIREMENTS, root);
        assertEquals(1, invocations(root));
    }

    @Test
    public void appliesTheImagesConstraints(@TempDir Path dir) throws Exception {
        assumePosix();
        Path root = recordingPip(dir);
        Path constraints = root.resolve("constraints.txt");
        Files.writeString(constraints, "numpy==2.2.4\n");

        RequirementsInstaller.install(REQUIREMENTS, root);

        assertTrue(pipCalls(root).contains("PIP_CONSTRAINT=" + constraints),
            "without the image's pins, a model asking for numpy triggers a long source build");
    }

    @Test
    public void proceedsWithAWarningWhenConstraintsAreMissing(@TempDir Path dir) throws Exception {
        assumePosix();
        Path root = recordingPip(dir);   // no constraints.txt written

        RequirementsInstaller.install(REQUIREMENTS, root);

        assertTrue(pipCalls(root).contains("PIP_CONSTRAINT=unset"));
        assertEquals(1, invocations(root), "missing constraints should not block the install");
    }

    @Test
    public void pipFailureCarriesPipsOutput(@TempDir Path dir) throws Exception {
        assumePosix();
        Path root = resourcesRootWithPip(dir, """
            echo "ERROR: Could not find a version that satisfies the requirement nope"
            exit 1
            """);

        RuntimeException thrown = assertThrows(RuntimeException.class,
            () -> RequirementsInstaller.install(REQUIREMENTS, root));

        assertTrue(thrown.getMessage().contains("Could not find a version"), thrown.getMessage());
        assertTrue(thrown.getMessage().contains("GraalPy"), thrown.getMessage());
    }

    @Test
    public void aFailedInstallIsRetriedRatherThanMarkedDone(@TempDir Path dir) throws Exception {
        assumePosix();
        // Fails once, then succeeds — as a transient network error would.
        Path root = resourcesRootWithPip(dir, """
            if [ -f "$0.failed-once" ]; then
              echo "invoked $*" >> "$0.calls.log"
              exit 0
            fi
            touch "$0.failed-once"
            echo "ERROR: network unreachable"
            exit 1
            """);

        assertThrows(RuntimeException.class, () -> RequirementsInstaller.install(REQUIREMENTS, root));

        try (var markers = Files.list(root)) {
            assertFalse(markers.anyMatch(p -> p.getFileName().toString().startsWith(".installed-")),
                "a marker for a half-finished install would permanently skip the repair");
        }

        RequirementsInstaller.install(REQUIREMENTS, root);
        assertEquals(1, invocations(root));
    }

    @Test
    public void missingVenvPipIsAClearError(@TempDir Path dir) throws Exception {
        RuntimeException thrown = assertThrows(RuntimeException.class,
            () -> RequirementsInstaller.install(REQUIREMENTS, dir));

        assertTrue(thrown.getMessage().contains("venv/bin/pip"), thrown.getMessage());
        assertTrue(thrown.getMessage().contains("PYMERLIN_RESOURCES"), thrown.getMessage());
    }

    @Test
    public void concurrentLoadsInOneJvmInstallOnce(@TempDir Path dir) throws Exception {
        assumePosix();
        // Two simulations starting together on one worker. They are threads in a single JVM,
        // which is the case a FileLock alone does NOT serialize: the second holder gets an
        // OverlappingFileLockException rather than a null lock. Each also loads its model
        // through its own URLClassLoader, so no static can coordinate them either.
        Path root = resourcesRootWithPip(dir, """
            echo "invoked $*" >> "$0.calls.log"
            sleep 1
            """);

        int threads = 4;
        CountDownLatch startTogether = new CountDownLatch(1);
        CountDownLatch finished = new CountDownLatch(threads);
        AtomicReference<Throwable> failure = new AtomicReference<>();

        for (int i = 0; i < threads; i++) {
            new Thread(() -> {
                try {
                    startTogether.await();
                    RequirementsInstaller.install(REQUIREMENTS, root);
                } catch (Throwable t) {
                    failure.compareAndSet(null, t);
                } finally {
                    finished.countDown();
                }
            }).start();
        }

        startTogether.countDown();
        assertTrue(finished.await(60, TimeUnit.SECONDS), "installs did not finish; lock deadlock?");

        if (failure.get() != null) {
            throw new AssertionError("a concurrent load failed: " + failure.get(), failure.get());
        }
        assertEquals(1, invocations(root), "pip is not safe to run concurrently against one venv");
    }
}
