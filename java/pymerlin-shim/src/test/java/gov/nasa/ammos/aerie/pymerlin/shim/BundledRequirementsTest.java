package gov.nasa.ammos.aerie.pymerlin.shim;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.net.URL;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.jar.Attributes;
import java.util.jar.JarEntry;
import java.util.jar.JarOutputStream;
import java.util.jar.Manifest;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Coverage for reading the requirements.txt that {@code pymerlin package} bundles into a
 * model JAR. Unlike the rest of this suite these tests need no GraalPy runtime and no venv:
 * a JAR is built on the fly and read back, which is the whole of what is under test.
 *
 * <p>The manifest attribute and the entry it names are written by pymerlin's Python side.
 * These tests pin the Java side's half of that contract — read the attribute, read the entry
 * it points at, out of that JAR specifically.
 */
public final class BundledRequirementsTest {

    private static final String REQUIREMENTS = "PyYAML==6.0.3\npython-dateutil==2.9.0\n";

    /** Build a model-shaped JAR: a manifest, plus whatever entries the test wants in it. */
    private static Path buildJar(Path dir, Manifest manifest, String entryName, String content)
            throws IOException {
        Path jar = dir.resolve("model.jar");
        try (JarOutputStream out = new JarOutputStream(Files.newOutputStream(jar), manifest)) {
            if (entryName != null) {
                out.putNextEntry(new JarEntry(entryName));
                out.write(content.getBytes(java.nio.charset.StandardCharsets.UTF_8));
                out.closeEntry();
            }
        }
        return jar;
    }

    private static Manifest manifestWith(String requirementsEntry) {
        Manifest manifest = new Manifest();
        Attributes main = manifest.getMainAttributes();
        main.put(Attributes.Name.MANIFEST_VERSION, "1.0");
        main.putValue("Pymerlin-Model-Ref", "pymerlin_models/model.py:Mission");
        if (requirementsEntry != null) {
            main.putValue("Pymerlin-Requirements", requirementsEntry);
        }
        return manifest;
    }

    private static String read(Path jar, Manifest manifest) throws IOException {
        return ShimModelType.readBundledRequirements(jar.toUri().toURL(), manifest);
    }

    @Test
    public void readsTheEntryTheManifestNames(@TempDir Path dir) throws Exception {
        Manifest manifest = manifestWith("pymerlin_requirements.txt");
        Path jar = buildJar(dir, manifest, "pymerlin_requirements.txt", REQUIREMENTS);

        assertEquals(REQUIREMENTS, read(jar, manifest));
    }

    @Test
    public void returnsNullWhenTheJarDeclaresNoRequirements(@TempDir Path dir) throws Exception {
        // A model needing nothing beyond the image is packaged without the attribute at all.
        Manifest manifest = manifestWith(null);
        Path jar = buildJar(dir, manifest, null, null);

        assertNull(read(jar, manifest));
    }

    @Test
    public void ignoresABlankAttribute(@TempDir Path dir) throws Exception {
        Manifest manifest = manifestWith("   ");
        Path jar = buildJar(dir, manifest, null, null);

        assertNull(read(jar, manifest));
    }

    @Test
    public void followsTheAttributeRatherThanAssumingTheEntryName(@TempDir Path dir) throws Exception {
        // The path lives in the manifest so both sides agree on one string. If this code
        // hardcoded "pymerlin_requirements.txt" instead, renaming it Python-side would
        // silently stop working.
        Manifest manifest = manifestWith("deps/elsewhere.txt");
        Path jar = buildJar(dir, manifest, "deps/elsewhere.txt", REQUIREMENTS);

        assertEquals(REQUIREMENTS, read(jar, manifest));
    }

    @Test
    public void failsLoudlyWhenTheDeclaredEntryIsMissing(@TempDir Path dir) throws Exception {
        // Only a broken or hand-edited JAR gets here. Reporting it at load time beats an
        // ImportError mid-simulation, which points nowhere near the cause.
        Manifest manifest = manifestWith("pymerlin_requirements.txt");
        Path jar = buildJar(dir, manifest, null, null);

        RuntimeException thrown = assertThrows(RuntimeException.class, () -> read(jar, manifest));

        assertTrue(thrown.getMessage().contains("pymerlin_requirements.txt"), thrown.getMessage());
        assertTrue(thrown.getMessage().contains("pymerlin package"), thrown.getMessage());
    }

    @Test
    public void readsFromTheJarItWasGivenNotTheClasspath(@TempDir Path dir) throws Exception {
        // Two JARs, each declaring its own requirements. Reading must be scoped to the URL
        // passed in: the shim runs under a classloader whose parents also have manifests and
        // resources, and a classpath-wide lookup could answer from the wrong one.
        Path first = dir.resolve("first");
        Path second = dir.resolve("second");
        Files.createDirectories(first);
        Files.createDirectories(second);

        Manifest manifest = manifestWith("pymerlin_requirements.txt");
        Path firstJar = buildJar(first, manifest, "pymerlin_requirements.txt", "toml==0.10.2\n");
        Path secondJar = buildJar(second, manifest, "pymerlin_requirements.txt", REQUIREMENTS);

        assertEquals("toml==0.10.2\n", read(firstJar, manifest));
        assertEquals(REQUIREMENTS, read(secondJar, manifest));
    }

    @Test
    public void readsARealJarProducedByPymerlinPackage(@TempDir Path dir) throws Exception {
        // End-to-end shape check against a JAR laid out the way `pymerlin package` writes
        // one: the generated file carries comment lines, and its exact bytes must survive.
        String generated = """
            # Generated by 'pymerlin package' from the imports in:
            #   model.py
            #
            # Versions are pinned to what was installed alongside the model when it was
            # packaged. Do not edit: 'pymerlin package' rewrites this file from the
            # model's imports every time. To change what the worker installs, change
            # what the model imports.
            PyYAML==6.0.3
            """;
        Manifest manifest = manifestWith("pymerlin_requirements.txt");
        Path jar = buildJar(dir, manifest, "pymerlin_requirements.txt", generated);

        assertEquals(generated, read(jar, manifest));
    }

    @Test
    public void manifestRoundTripsThroughARealJarUrl(@TempDir Path dir) throws Exception {
        // The production path does not get a Manifest handed to it — it parses one out of
        // the JAR through a jar: URL. Prove the attribute survives that round trip, since a
        // manifest written in memory is not evidence about one read off disk.
        Manifest written = manifestWith("pymerlin_requirements.txt");
        Path jar = buildJar(dir, written, "pymerlin_requirements.txt", REQUIREMENTS);

        URL manifestUrl = new URL("jar:" + jar.toUri().toURL().toExternalForm()
            + "!/META-INF/MANIFEST.MF");
        final Manifest parsed;
        try (var is = manifestUrl.openStream()) {
            parsed = new Manifest(is);
        }

        assertEquals("pymerlin_requirements.txt",
            parsed.getMainAttributes().getValue("Pymerlin-Requirements"));
        assertEquals(REQUIREMENTS, read(jar, parsed));
    }
}
