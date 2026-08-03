package gov.nasa.ammos.aerie.pymerlin.shim;

import org.graalvm.polyglot.Context;
import org.graalvm.polyglot.PolyglotException;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.graalvm.python.embedding.GraalPyResources;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.PosixFilePermissions;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

/**
 * Drives {@link RequirementsInstaller} against the image's REAL GraalPy pip, which is the
 * one thing the unit suite cannot cover: its stub proves the orchestration around pip, and
 * says nothing about GraalPy's patched pip, its wheel repository, or whether a package
 * actually installs and imports.
 *
 * <p>Requires a provisioned {@code python-resources} venv, so it skips unless
 * {@code -Dpymerlin.test.graal=true}, like the rest of the graal-only tests here. Run it
 * inside the worker image via the {@code dockerTestBundle} task — see build.gradle.
 *
 * <p><b>This test installs into the venv it is pointed at.</b> {@code toml} is chosen for
 * being tiny, pure-Python, and absent from the base image; each test uninstalls it again.
 * Run it in a throwaway container rather than one you care about.
 *
 * <p>Markers and the lock go in a per-test temp root whose {@code venv/bin/pip} is a thin
 * wrapper around the real one. That keeps each test independent and leaves no bookkeeping
 * behind in the image, while every package operation is still done by the real pip.
 */
public final class RequirementsInstallIntegrationTest {

    /** Absent from the base image (pymerlin, numpy, spiceypy), pure-Python, ~16 kB. */
    private static final String PACKAGE = "toml";
    private static final String REQUIREMENTS = "toml==0.10.2\n";

    private static Path realResources() {
        return PyContext.resolveResourcesRoot();
    }

    private static Path realPip() {
        return realResources().resolve("venv/bin/pip");
    }

    /**
     * A resources root that delegates to the real venv's pip but keeps its own markers.
     *
     * <p>The installer only ever executes {@code <root>/venv/bin/pip}, so a wrapper there is
     * indistinguishable from the real thing to the code under test, while letting a test
     * swap in a failing pip to prove that a skip really did skip.
     */
    private static Path delegatingRoot(Path dir) throws IOException {
        return rootWithPip(dir, "exec " + realPip() + " \"$@\"");
    }

    private static Path rootWithPip(Path dir, String script) throws IOException {
        Path pip = dir.resolve("venv/bin/pip");
        Files.createDirectories(pip.getParent());
        Files.writeString(pip, "#!/bin/sh\n" + script + "\n");
        Files.setPosixFilePermissions(pip, PosixFilePermissions.fromString("rwxr-xr-x"));
        return dir;
    }

    private static void assumeVenv() {
        assumeTrue(Boolean.getBoolean("pymerlin.test.graal"),
            "requires a provisioned python-resources venv; skipped without -Dpymerlin.test.graal=true");
        assumeTrue(Files.isExecutable(realPip()),
            "no venv pip at " + realPip() + "; run this inside the worker image");
    }

    /** Whether a fresh GraalPy Context can import the package — the only proof that counts. */
    private static boolean importable(String module) {
        try (Context context = GraalPyResources.contextBuilder(realResources())
                 .allowAllAccess(true)
                 .build()) {
            context.eval("python", "import " + module);
            return true;
        } catch (PolyglotException e) {
            return false;
        }
    }

    @AfterEach
    public void removeTheInstalledPackage() throws Exception {
        if (!Files.isExecutable(realPip())) return;
        new ProcessBuilder(realPip().toString(), "uninstall", "-y", "-q", PACKAGE)
            .redirectErrorStream(true)
            .start()
            .waitFor();
    }

    @Test
    public void installsAPackageTheImageDoesNotHaveAndItThenImports(@TempDir Path dir) throws Exception {
        assumeVenv();
        assumeTrue(!importable(PACKAGE),
            PACKAGE + " is already installed, so this proves nothing; uninstall it first");

        RequirementsInstaller.install(REQUIREMENTS, delegatingRoot(dir));

        // A Context built AFTER the install sees the package. This is what makes the
        // no-Engine-invalidation reasoning in Step 4 of the roadmap true rather than assumed:
        // pip runs before any Context exists, so a fresh one picks up site-packages as it is.
        assertTrue(importable(PACKAGE),
            PACKAGE + " installed but a new Context cannot import it");
    }

    @Test
    public void repeatingTheSameRequirementsDoesNotRunPipAgain(@TempDir Path dir) throws Exception {
        assumeVenv();
        Path root = delegatingRoot(dir);

        RequirementsInstaller.install(REQUIREMENTS, root);

        // Replace pip with one that fails if executed. If the second call still succeeds,
        // it genuinely skipped rather than merely re-running a fast no-op pip.
        rootWithPip(root, "echo 'pip should not have been invoked' >&2; exit 1");

        assertDoesNotThrow(() -> RequirementsInstaller.install(REQUIREMENTS, root),
            "a repeat simulation of the same model must skip pip entirely");
    }

    @Test
    public void aModelDeclaringNothingNeverTouchesPip(@TempDir Path dir) throws Exception {
        assumeVenv();
        Path root = rootWithPip(dir, "echo 'pip should not have been invoked' >&2; exit 1");

        assertDoesNotThrow(() -> RequirementsInstaller.install(null, root));
        assertDoesNotThrow(() -> RequirementsInstaller.install("", root));
    }

    @Test
    public void aPackageThatCannotBeInstalledFailsWithPipsOwnWords(@TempDir Path dir) throws Exception {
        assumeVenv();

        RuntimeException thrown = assertThrows(RuntimeException.class, () ->
            RequirementsInstaller.install(
                "pymerlin-no-such-package-exists-anywhere==9.9.9\n", delegatingRoot(dir)));

        // Whatever pip says is what the Aerie UI shows; the wrapper only adds the GraalPy note.
        assertTrue(thrown.getMessage().contains("pip failed"), thrown.getMessage());
        assertTrue(thrown.getMessage().contains("pymerlin-no-such-package-exists-anywhere"),
            thrown.getMessage());
    }

    @Test
    public void constraintsReachRealPip(@TempDir Path dir) throws Exception {
        assumeVenv();
        Path root = delegatingRoot(dir);
        // numpy is pinned in the image's constraints; asking for a different version must be
        // refused by the resolver rather than quietly starting a multi-minute source build.
        Files.writeString(root.resolve("constraints.txt"), "toml==0.10.2\n");

        RuntimeException thrown = assertThrows(RuntimeException.class, () ->
            RequirementsInstaller.install("toml==0.10.1\n", root));

        assertTrue(thrown.getMessage().contains("pip failed"), thrown.getMessage());
    }
}
