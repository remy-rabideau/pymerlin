package gov.nasa.ammos.aerie.merlin.python.gateway;

import java.util.concurrent.ConcurrentHashMap;

/**
 * Singleton registry mapping (name, version) -> ModelProvider (Python sidecar).
 * Must be loaded by the shared parent classloader so that both this class
 * and the per-model bridge JAR see the same instance.
 */
public final class ModelRegistry {
    public static final ModelRegistry INSTANCE = new ModelRegistry();

    private final ConcurrentHashMap<String, ModelTypeSupplier> byKey = new ConcurrentHashMap<>();

    private ModelRegistry() {}

    /** Called by Python sidecar via py4j callback. */
    public void registerObject(String name, String version, ModelTypeSupplier provider) {
        byKey.put(key(name, version), provider);
        System.out.println("[pymerlin-gateway] Registered '" + name + "@" + version + "'");
    }

    /** Returns the ModelType as Object (cast to ModelType in the bridge JAR). */
    public Object lookupModelType(String name, String version) {
        String k = key(name, version);
        int maxAttempts = 10;
        for (int attempt = 1; attempt <= maxAttempts; attempt++) {
            ModelTypeSupplier p = byKey.get(k);
            if (p == null) {
                throw new IllegalStateException(
                    "No pymerlin sidecar registered for " + name + "@" + version +
                    ". Is 'pymerlin serve' running and connected?");
            }
            try {
                return p.getModelType();
            } catch (Exception e) {
                if (attempt == maxAttempts || !e.getMessage().contains("Object ID unknown")) throw e;
                System.out.println("[pymerlin-gateway] Stale proxy for '" + k +
                    "', waiting for sidecar to re-register (attempt " + attempt + "/" + maxAttempts + ")...");
                byKey.remove(k);
                waitForReregistration(k, 10_000);
            }
        }
        throw new IllegalStateException("Failed to obtain ModelType for " + k + " after " + maxAttempts + " attempts");
    }

    private void waitForReregistration(String k, long timeoutMs) {
        long deadline = System.currentTimeMillis() + timeoutMs;
        while (!byKey.containsKey(k) && System.currentTimeMillis() < deadline) {
            try { Thread.sleep(500); } catch (InterruptedException ex) { Thread.currentThread().interrupt(); return; }
        }
    }

    /** Exposed as py4j entry_point so Python can call gw.entry_point.getRegistry() */
    public ModelRegistry getRegistry() {
        return this;
    }

    private static String key(String name, String version) {
        return name + "@" + version;
    }
}
