package gov.nasa.ammos.aerie.merlin.python.codegen;

import py4j.GatewayServer;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.ServerSocket;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

/**
 * Singleton that manages a py4j gateway to a Python subprocess.
 * The Python subprocess runs the user's model.py and exposes a
 * PyMerlinBridge object that the generated Java code calls into
 * for activity execution during simulation.
 */
public class PyMerlinRuntime {

    private static volatile PyMerlinRuntime INSTANCE;
    private static final Object LOCK = new Object();

    private final GatewayServer gatewayServer;
    private final PyMerlinBridge bridge;
    private final Process pythonProcess;

    private PyMerlinRuntime(GatewayServer gatewayServer, PyMerlinBridge bridge, Process pythonProcess) {
        this.gatewayServer = gatewayServer;
        this.bridge = bridge;
        this.pythonProcess = pythonProcess;
    }

    public static PyMerlinRuntime getInstance(String modelRef) {
        if (INSTANCE == null) {
            synchronized (LOCK) {
                if (INSTANCE == null) {
                    INSTANCE = start(modelRef);
                }
            }
        }
        return INSTANCE;
    }

    public PyMerlinBridge getBridge() {
        return bridge;
    }

    public void shutdown() {
        if (pythonProcess != null && pythonProcess.isAlive()) {
            pythonProcess.destroy();
            try {
                pythonProcess.waitFor(5, TimeUnit.SECONDS);
            } catch (InterruptedException e) {
                pythonProcess.destroyForcibly();
            }
        }
        if (gatewayServer != null) {
            gatewayServer.shutdown();
        }
    }

    private static PyMerlinRuntime start(String modelRef) {
        try {
            // Extract model file from JAR if it's a bundled path
            String actualModelRef = modelRef;
            if (modelRef.startsWith("/pymerlin_models/")) {
                actualModelRef = extractModelFromJar(modelRef);
            }
            
            int gatewayPort = findFreePort();

            CountDownLatch ready = new CountDownLatch(1);
            PyMerlinBridgeHolder holder = new PyMerlinBridgeHolder(ready);

            GatewayServer gatewayServer = new GatewayServer.GatewayServerBuilder()
                    .javaPort(gatewayPort)
                    .entryPoint(holder)
                    .build();
            gatewayServer.start();

            System.out.println("[PyMerlinRuntime] Starting Python subprocess on gateway port " + gatewayPort);

            ProcessBuilder pb = new ProcessBuilder(
                    "python", "-m", "pymerlin._internal._runtime_server",
                    "--model", actualModelRef,
                    "--gateway-port", String.valueOf(gatewayPort)
            );
            pb.redirectErrorStream(true);
            pb.inheritIO();
            Process process = pb.start();

            // Wait for Python to connect and register bridge
            if (!ready.await(30, TimeUnit.SECONDS)) {
                process.destroy();
                throw new RuntimeException("Timed out waiting for Python runtime to connect");
            }

            PyMerlinBridge bridge = holder.getBridge();
            if (bridge == null) {
                process.destroy();
                throw new RuntimeException("Python runtime connected but did not register a bridge");
            }

            System.out.println("[PyMerlinRuntime] Python subprocess connected successfully");

            return new PyMerlinRuntime(gatewayServer, bridge, process);
        } catch (Exception e) {
            throw new RuntimeException("Failed to start Python runtime", e);
        }
    }

    private static String extractModelFromJar(String modelRef) throws Exception {
        // Parse modelRef: /pymerlin_models/model.py:ClassName
        String[] parts = modelRef.split(":");
        String jarPath = parts[0];
        String className = parts[1];
        
        // Extract to temp directory
        java.io.InputStream is = PyMerlinRuntime.class.getResourceAsStream(jarPath);
        if (is == null) {
            throw new RuntimeException("Model file not found in JAR: " + jarPath);
        }
        
        java.nio.file.Path tempDir = java.nio.file.Files.createTempDirectory("pymerlin_model_");
        java.nio.file.Path tempFile = tempDir.resolve(new java.io.File(jarPath).getName());
        java.nio.file.Files.copy(is, tempFile, java.nio.file.StandardCopyOption.REPLACE_EXISTING);
        is.close();
        
        System.out.println("[PyMerlinRuntime] Extracted model to: " + tempFile);
        return tempFile.toString() + ":" + className;
    }
    
    private static int findFreePort() throws Exception {
        try (ServerSocket s = new ServerSocket(0)) {
            return s.getLocalPort();
        }
    }

    public interface PyMerlinBridge {
        Object runActivity(String activityName, java.util.Map<String, Object> args);
    }

    public static class PyMerlinBridgeHolder {
        private volatile PyMerlinBridge bridge;
        private final CountDownLatch latch;

        public PyMerlinBridgeHolder(CountDownLatch latch) {
            this.latch = latch;
        }

        public void register(PyMerlinBridge bridge) {
            System.out.println("[PyMerlinRuntime] Bridge registered from Python");
            this.bridge = bridge;
            latch.countDown();
        }

        public PyMerlinBridge getBridge() {
            return bridge;
        }
    }
}
