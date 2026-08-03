package gov.nasa.ammos.aerie.pymerlin.shim;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.nio.channels.FileChannel;
import java.nio.channels.FileLock;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.List;

/**
 * Installs the Python packages a model JAR declares into the worker's GraalPy venv, at
 * model-load time, so adding an import to a model does not require rebuilding the image.
 *
 * <p>Runs before any GraalPy Context is created. That ordering is not incidental: pip
 * mutates the venv's {@code site-packages}, and a Context opened beforehand has already
 * resolved {@code sys.path} and cached module state, so it would not see the new packages.
 * {@link ShimModelType#resolveModelRef} calls this, and every entry point goes through
 * there before its {@code PyBridge.create()}.
 *
 * <p>Everything here targets the venv's own pip. A system pip must never be used: GraalPy
 * is not binary-compatible with CPython, and CPython wheels from pypi.org will install
 * cleanly and then fail at import.
 */
final class RequirementsInstaller {

    private RequirementsInstaller() {}

    /** Guards the venv against concurrent pip runs; pip is not safe to run against itself. */
    private static final String LOCK_FILE = ".pip-lock";

    /** Prefix of the per-content marker files that record what has already been installed. */
    private static final String MARKER_PREFIX = ".installed-";

    /** How long to wait for another worker thread's pip run before giving up. */
    private static final long LOCK_TIMEOUT_MILLIS = 10 * 60 * 1000L;

    /**
     * Ensure everything in {@code requirements} is installed in the venv at
     * {@code resourcesRoot}.
     *
     * <p>Returns quietly when the model declares nothing, or when this exact set has been
     * installed before. Throws when pip fails, so the reason reaches the Aerie UI as the
     * model-load error rather than surfacing later as an unexplained ImportError.
     */
    static void install(String requirements, Path resourcesRoot) {
        if (requirements == null || requirements.isBlank()) return;

        Path venvPip = resourcesRoot.resolve("venv/bin/pip");
        if (!Files.isExecutable(venvPip)) {
            throw new RuntimeException("[PyMerlin] The model declares Python packages, but no"
                + " GraalPy venv pip was found at " + venvPip + ". Set PYMERLIN_RESOURCES to"
                + " the python-resources directory the image provisioned, or repackage the"
                + " model with --no-requirements if it does not actually need them.");
        }

        Path marker = resourcesRoot.resolve(MARKER_PREFIX + sha256(requirements));
        if (Files.exists(marker)) return;

        long startedAt = System.currentTimeMillis();
        System.err.println("[PyMerlin] Installing model-declared Python packages into " + resourcesRoot);

        // The lock covers the marker check as well as pip itself. Two simulations of the
        // same model starting together would otherwise both miss the marker, and the second
        // would run a redundant pip against a venv the first is still writing.
        try (FileChannel channel = FileChannel.open(resourcesRoot.resolve(LOCK_FILE),
                 StandardOpenOption.CREATE, StandardOpenOption.WRITE);
             FileLock lock = acquire(channel, resourcesRoot)) {

            if (Files.exists(marker)) {
                System.err.println("[PyMerlin] Already installed by a concurrent model load");
                return;
            }

            Path requirementsFile = Files.createTempFile("pymerlin-requirements-", ".txt");
            try {
                Files.writeString(requirementsFile, requirements, StandardCharsets.UTF_8);
                runPip(venvPip, requirementsFile, resourcesRoot);
            } finally {
                Files.deleteIfExists(requirementsFile);
            }

            // Written only after pip succeeds: a marker for a half-finished install would
            // permanently skip the step that would have repaired it.
            Files.writeString(marker, requirements, StandardCharsets.UTF_8);
            System.err.println("[PyMerlin] Python dependency installation complete ("
                + (System.currentTimeMillis() - startedAt) / 1000.0 + "s)");

        } catch (IOException e) {
            throw new RuntimeException("[PyMerlin] Could not install the model's Python packages: "
                + e.getMessage(), e);
        }
    }

    /**
     * Take the venv-wide pip lock, waiting for whoever holds it.
     *
     * <p>Waiting rather than failing is deliberate: the common case for contention is two
     * simulations of the SAME model starting together, where the loser's work is already
     * being done by the winner and it only has to wait for the marker to appear. The timeout
     * exists so that a stale lock — a worker killed mid-install — surfaces as an error with
     * a name attached instead of a simulation that hangs forever.
     *
     * <p>{@link java.nio.channels.OverlappingFileLockException} counts as "held", not as an
     * error. A {@link FileLock} is owned by the whole JVM rather than by a thread, so a
     * second holder inside this same JVM gets that exception where a separate process would
     * simply get null — and inside one JVM is exactly where the contention lives: concurrent
     * simulations run as threads on one worker, each loading its model through its own
     * {@code URLClassLoader}, so they cannot see each other through any static either.
     */
    private static FileLock acquire(FileChannel channel, Path resourcesRoot) throws IOException {
        long deadline = System.currentTimeMillis() + LOCK_TIMEOUT_MILLIS;
        boolean reported = false;
        while (true) {
            FileLock lock;
            try {
                lock = channel.tryLock();
            } catch (java.nio.channels.OverlappingFileLockException e) {
                lock = null;   // another thread in this JVM holds it; wait like anyone else
            }
            if (lock != null) return lock;

            if (!reported) {
                System.err.println("[PyMerlin] Waiting for another model load to finish"
                    + " installing Python packages");
                reported = true;
            }
            if (System.currentTimeMillis() > deadline) {
                throw new IOException("timed out after " + (LOCK_TIMEOUT_MILLIS / 1000)
                    + "s waiting for " + resourcesRoot.resolve(LOCK_FILE)
                    + "; delete that file if no install is actually running");
            }
            try {
                Thread.sleep(250);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw new IOException("interrupted while waiting for the pip lock", e);
            }
        }
    }

    /**
     * Run the venv's pip, streaming its output into the worker log as it arrives.
     *
     * <p>Streaming rather than collecting matters for the case this feature makes possible:
     * a package with no GraalPy wheel compiles from source, which can take many minutes, and
     * a silent worker is indistinguishable from a hung one. The output is also retained so a
     * failure can carry it, since by then it has already scrolled past in the log.
     */
    private static void runPip(Path venvPip, Path requirementsFile, Path resourcesRoot)
            throws IOException {
        List<String> command = new ArrayList<>(List.of(
            venvPip.toString(), "install", "--no-cache-dir", "--disable-pip-version-check",
            "-r", requirementsFile.toString()));

        ProcessBuilder pb = new ProcessBuilder(command);
        pb.redirectErrorStream(true);

        // The same pins the image build used. Without them a model asking for numpy
        // unpinned resolves past the one version GraalVM publishes a wheel for and falls
        // back to a ~15-minute from-source compile that looks like a hang.
        Path constraints = resourcesRoot.resolve("constraints.txt");
        if (Files.isReadable(constraints)) {
            pb.environment().put("PIP_CONSTRAINT", constraints.toString());
        } else {
            System.err.println("[PyMerlin] WARNING: no constraints at " + constraints
                + " -- a model requiring numpy or spiceypy may trigger a long source build."
                + " Rebuild the image so install.sh persists them.");
        }

        Process process = pb.start();
        List<String> output = new ArrayList<>();
        try (BufferedReader reader = new BufferedReader(
                new InputStreamReader(process.getInputStream(), StandardCharsets.UTF_8))) {
            String line;
            while ((line = reader.readLine()) != null) {
                output.add(line);
                System.err.println("[PyMerlin][pip] " + line);
            }
        }

        final int exit;
        try {
            exit = process.waitFor();
        } catch (InterruptedException e) {
            process.destroy();
            Thread.currentThread().interrupt();
            throw new IOException("interrupted while waiting for pip", e);
        }

        if (exit != 0) {
            throw new RuntimeException("[PyMerlin] pip failed (exit " + exit + ") installing the"
                + " model's Python packages. Not every PyPI package works under GraalPy:"
                + " pure-Python ones generally do, C-extension ones need a wheel from"
                + " GraalVM's repository or a source build that succeeds here.\n"
                + String.join("\n", output));
        }
    }

    private static String sha256(String content) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            return HexFormat.of().formatHex(digest.digest(content.getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 is required of every JVM", e);
        }
    }
}
