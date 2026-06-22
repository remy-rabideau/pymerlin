package gov.nasa.ammos.aerie.merlin.python.graal;

import java.io.IOException;
import java.io.InputStream;
import java.util.Properties;

public final class ResourceConfig {
    private static final Properties PROPS = new Properties();

    static {
        try (InputStream is = ResourceConfig.class.getResourceAsStream("/pymerlin.properties")) {
            if (is != null) PROPS.load(is);
        } catch (IOException e) {
            throw new RuntimeException("Failed to load pymerlin.properties", e);
        }
    }

    public static String get(String key) {
        String value = System.getProperty(key, PROPS.getProperty(key));
        if (value == null) throw new IllegalStateException("Missing config key: " + key);
        return value;
    }
}
