package gov.nasa.ammos.aerie.merlin.python.gateway;

/**
 * Implemented by the Python sidecar via py4j callback.
 * Returns Object (not ModelType) to avoid bootstrap classpath issues
 * with merlin-sdk classes that are not on the bootstrap classpath.
 */
public interface ModelTypeSupplier {
    Object getModelType();
}
