package gov.nasa.ammos.aerie.pymerlin.shim;

import com.google.gson.Gson;
import com.google.gson.JsonObject;

import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.io.OutputStreamWriter;
import java.nio.charset.StandardCharsets;

/**
 * Newline-delimited JSON protocol helpers for talking to the Python server process.
 *
 * Wire format: each message is a single JSON object followed by '\n'.
 * Java sends to Python's stdin; Python responds on its stdout.
 */
public final class Protocol {
    private static final Gson GSON = new Gson();

    private final BufferedReader reader;
    private final BufferedWriter writer;

    public Protocol(InputStream pythonStdout, OutputStream pythonStdin) {
        this.reader = new BufferedReader(new InputStreamReader(pythonStdout, StandardCharsets.UTF_8));
        this.writer = new BufferedWriter(new OutputStreamWriter(pythonStdin, StandardCharsets.UTF_8));
    }

    /** Send a JSON object message to Python. */
    public void send(JsonObject msg) throws IOException {
        writer.write(GSON.toJson(msg));
        writer.write('\n');
        writer.flush();
    }

    /** Receive a JSON object message from Python. Blocks until one arrives. */
    public JsonObject recv() throws IOException {
        String line = reader.readLine();
        if (line == null) throw new IOException("Python process closed stdout unexpectedly");
        return GSON.fromJson(line, JsonObject.class);
    }

    /** Send a request and return the response. */
    public JsonObject roundtrip(JsonObject request) throws IOException {
        send(request);
        return recv();
    }

    public static JsonObject obj(String... keysAndValues) {
        if (keysAndValues.length % 2 != 0) throw new IllegalArgumentException("Must be pairs");
        JsonObject o = new JsonObject();
        for (int i = 0; i < keysAndValues.length; i += 2) {
            o.addProperty(keysAndValues[i], keysAndValues[i + 1]);
        }
        return o;
    }
}
