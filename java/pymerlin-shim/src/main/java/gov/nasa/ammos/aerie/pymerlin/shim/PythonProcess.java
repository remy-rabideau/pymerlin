package gov.nasa.ammos.aerie.pymerlin.shim;

import com.google.gson.JsonObject;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.util.List;
import java.util.concurrent.TimeUnit;

/**
 * Manages the lifecycle of the Python server subprocess and owns the Protocol instance.
 *
 * Launched once per simulation (when ShimModelType.instantiate() is called).
 * Shut down when the JVM exits via a shutdown hook.
 */
public final class PythonProcess {
    private final Process process;
    private final Protocol protocol;

    private PythonProcess(Process process, Protocol protocol) {
        this.process = process;
        this.protocol = protocol;
        Runtime.getRuntime().addShutdownHook(new Thread(this::shutdown));
    }

    public static PythonProcess start(String modelRef) throws IOException, InterruptedException {
        String python = System.getProperty("pymerlin.python", "python3");
        ProcessBuilder pb = new ProcessBuilder(List.of(
            python, "-m", "pymerlin._server", "--model", modelRef
        ));
        pb.redirectErrorStream(false); // keep stderr separate so we can log it
        // Ensure pymerlin is on the Python path even when launched from a minimal JVM environment.
        // Check PYMERLIN_SITE env var first, then fall back to well-known locations.
        String pymerlinSite = System.getenv("PYMERLIN_SITE");
        if (pymerlinSite == null || pymerlinSite.isEmpty()) {
            for (String candidate : new String[]{
                "/usr/local/lib/python3.10/dist-packages",
                "/usr/local/lib/python3.11/dist-packages",
                "/usr/local/lib/python3.12/dist-packages",
                "/usr/lib/python3/dist-packages",
                "/usr/local/lib/python3.10/site-packages",
                "/usr/local/lib/python3.11/site-packages",
            }) {
                if (new java.io.File(candidate + "/pymerlin").exists()) {
                    pymerlinSite = candidate;
                    break;
                }
            }
        }
        String existingPythonPath = pb.environment().getOrDefault("PYTHONPATH", "");
        String newPythonPath = pymerlinSite != null && !pymerlinSite.isEmpty()
            ? (existingPythonPath.isEmpty() ? pymerlinSite : pymerlinSite + ":" + existingPythonPath)
            : existingPythonPath;
        pb.environment().put("PYTHONPATH", newPythonPath);
        System.err.println("[PyMerlin] Launching: " + python + " -m pymerlin._server --model " + modelRef);
        System.err.println("[PyMerlin] PYTHONPATH=" + newPythonPath);
        Process proc = pb.start();

        // Collect stderr in a background thread so it's available on failure
        StringBuilder stderrBuf = new StringBuilder();
        Thread stderrForwarder = new Thread(() -> {
            try (var reader = new BufferedReader(new InputStreamReader(proc.getErrorStream()))) {
                String line;
                while ((line = reader.readLine()) != null) {
                    System.err.println("[PyMerlin] " + line);
                    synchronized (stderrBuf) { stderrBuf.append(line).append('\n'); }
                }
            } catch (IOException ignored) {}
        }, "pymerlin-stderr-forwarder");
        stderrForwarder.setDaemon(true);
        stderrForwarder.start();

        Protocol protocol = new Protocol(proc.getInputStream(), proc.getOutputStream());

        // Wait for ready signal
        JsonObject ready;
        try {
            ready = protocol.recv();
        } catch (IOException e) {
            // Python crashed before writing 'ready' — collect stderr for diagnosis
            proc.waitFor(2, TimeUnit.SECONDS);
            stderrForwarder.join(2000);
            String stderr = stderrBuf.toString().trim();
            throw new IOException("Python process closed stdout unexpectedly. stderr: [" + stderr + "]", e);
        }
        if (!"ready".equals(ready.get("op").getAsString())) {
            proc.destroyForcibly();
            throw new RuntimeException("[PyMerlin] Expected 'ready', got: " + ready);
        }
        System.out.println("[PyMerlin] Python server ready for model: " + modelRef);

        return new PythonProcess(proc, protocol);
    }

    public Protocol protocol() {
        return protocol;
    }

    public boolean isAlive() {
        return process.isAlive();
    }

    public void destroy() {
        if (process.isAlive()) {
            process.destroyForcibly();
        }
    }

    public void shutdown() {
        if (process.isAlive()) {
            process.destroy();
        }
    }
}
