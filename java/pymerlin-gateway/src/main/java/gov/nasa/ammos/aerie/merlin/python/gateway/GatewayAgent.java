package gov.nasa.ammos.aerie.merlin.python.gateway;

import py4j.GatewayServer;
import py4j.GatewayServer.GatewayServerBuilder;

import java.lang.instrument.Instrumentation;
import java.net.InetAddress;

/**
 * Java agent that starts a standing py4j ClientServer inside the host JVM.
 * ClientServer mode uses a single bidirectional connection — Python connects
 * to Java, and Java calls Python back over the same socket, eliminating the
 * need for a reverse TCP connection from JVM to sidecar container.
 */
public final class GatewayAgent {
    private static final int DEFAULT_PORT = 25333;

    public static void premain(String agentArgs, Instrumentation inst) {
        int port = DEFAULT_PORT;
        int callbackPort = 0;
        if (agentArgs != null && !agentArgs.isBlank()) {
            String[] parts = agentArgs.trim().split(":");
            try {
                port = Integer.parseInt(parts[0]);
                if (parts.length > 1) callbackPort = Integer.parseInt(parts[1]);
            } catch (NumberFormatException e) {
                System.err.println("[pymerlin-gateway] Invalid args '" + agentArgs + "', using defaults");
            }
        }
        final int finalPort = port;
        final int finalCallbackPort = callbackPort;
        Thread t = new Thread(() -> {
            try {
                InetAddress allInterfaces = InetAddress.getByName("0.0.0.0");
                GatewayServer server = new GatewayServerBuilder()
                    .entryPoint(ModelRegistry.INSTANCE)
                    .javaPort(finalPort)
                    .javaAddress(allInterfaces)
                    .build();
                server.addListener(new py4j.GatewayServerListener() {
                    public void connectionStarted(py4j.Py4JServerConnection c) {
                        java.net.InetAddress remoteAddr = null;
                        try {
                            java.lang.reflect.Field socketF = c.getClass().getDeclaredField("socket");
                            socketF.setAccessible(true);
                            remoteAddr = ((java.net.Socket) socketF.get(c)).getInetAddress();
                        } catch (Exception e) { /* ignore */ }
                        final java.net.InetAddress finalRemoteAddr = remoteAddr;
                        new java.lang.Thread(() -> {
                            try {
                                java.lang.Thread.sleep(500);
                                py4j.Py4JPythonClient cb = server.getCallbackClient();
                                java.lang.reflect.Field addrF = cb.getClass().getDeclaredField("address");
                                java.lang.reflect.Field portF = cb.getClass().getDeclaredField("port");
                                addrF.setAccessible(true); portF.setAccessible(true);
                                if (finalRemoteAddr != null) {
                                    addrF.set(cb, finalRemoteAddr);
                                    if (finalCallbackPort > 0) portF.set(cb, finalCallbackPort);
                                }
                            } catch (Exception ex) { /* ignore */ }
                        }, "pymerlin-callback-fixer").start();
                    }
                    public void connectionStopped(py4j.Py4JServerConnection c) {}
                    public void serverError(Exception e) {}
                    public void serverPostShutdown() {}
                    public void serverPreShutdown() {}
                    public void serverStarted() {}
                    public void serverStopped() {}
                    public void connectionError(Exception e) {}
                });
                server.start();
                System.out.println("[pymerlin-gateway] GatewayServer started on 0.0.0.0:" + finalPort);
            } catch (Exception e) {
                System.err.println("[pymerlin-gateway] Failed to start GatewayServer: " + e.getMessage());
            }
        }, "pymerlin-gateway-thread");
        t.setDaemon(true);
        t.start();
    }
}
