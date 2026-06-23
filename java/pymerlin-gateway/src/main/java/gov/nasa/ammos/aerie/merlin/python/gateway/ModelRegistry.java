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
        ModelTypeSupplier p = byKey.get(key(name, version));
        if (p == null) {
            throw new IllegalStateException(
                "No pymerlin sidecar registered for " + name + "@" + version +
                ". Is 'pymerlin serve' running and connected?");
        }
        return p.getModelType();
    }

    /** Exposed as py4j entry_point so Python can call gw.entry_point.getRegistry() */
    public ModelRegistry getRegistry() {
        return this;
    }

    private static String key(String name, String version) {
        return name + "@" + version;
    }
}
