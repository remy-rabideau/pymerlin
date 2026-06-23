package gov.nasa.ammos.aerie.merlin.python.bridge;

import gov.nasa.ammos.aerie.merlin.python.gateway.ModelRegistry;
import gov.nasa.jpl.aerie.merlin.protocol.model.MerlinPlugin;
import gov.nasa.jpl.aerie.merlin.protocol.model.ModelType;

import java.io.IOException;
import java.io.InputStream;
import java.util.Properties;

/**
 * MerlinPlugin implementation loaded by Aerie's MissionModelLoader.
 *
 * This class is intentionally a thin stub: it reads the (name, version) tuple
 * from pymerlin-model.properties (stamped in by `pymerlin build-plandev-jar`),
 * then delegates to ModelRegistry.INSTANCE — which is shared via bootstrap
 * classpath (-Xbootclasspath/a) so all classloaders see the same singleton.
 */
public final class PyMerlinBridgePlugin implements MerlinPlugin {

    @Override
    public ModelType<?, ?> getModelType() {
        Properties props = loadProperties();
        String name = props.getProperty("name");
        String version = props.getProperty("version");
        if (name == null || version == null) {
            throw new IllegalStateException(
                "pymerlin-model.properties must contain 'name' and 'version' entries. " +
                "Did you use 'pymerlin build-plandev-jar' to produce this JAR?");
        }
        return (ModelType<?, ?>) ModelRegistry.INSTANCE.lookupModelType(name, version);
    }

    private static Properties loadProperties() {
        try (InputStream in = PyMerlinBridgePlugin.class
                .getClassLoader()
                .getResourceAsStream("pymerlin-model.properties")) {
            if (in == null) {
                throw new IllegalStateException(
                    "pymerlin-model.properties not found in JAR. " +
                    "Did you use 'pymerlin build-plandev-jar' to produce this JAR?");
            }
            Properties p = new Properties();
            p.load(in);
            return p;
        } catch (IOException e) {
            throw new RuntimeException("Failed to read pymerlin-model.properties", e);
        }
    }
}
