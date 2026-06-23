"""
PlanDev / Aerie integration: serve a Python model to a running PlanDev JVM.

Usage
-----
Register one or more models with a running PlanDev JVM (merlin-server,
merlin-worker, or scheduler-worker) that has the pymerlin-gateway agent loaded:

    from pymerlin._internal._serve import serve
    from mymodel import Mission
    serve([("my-model", "1.0.0", Mission)])

Or from the CLI:
    pymerlin serve --model ./model.py:Mission --name my-model --version 1.0.0

Build the uploadable bridge JAR:
    pymerlin build-plandev-jar --name my-model --version 1.0.0 --out my-model.jar
"""

import importlib.util
import os
import shutil
import sys
import threading
import time
import zipfile

from py4j.java_gateway import CallbackServerParameters, GatewayParameters, JavaGateway

from pymerlin._internal._model_type import ModelType

_BRIDGE_JAR_NAME = "pymerlin-bridge.jar"


class ModelProvider:
    """
    py4j callback object that implements the Java ModelProvider interface.
    The Python sidecar registers one of these per (name, version) pair so that
    the PyMerlinBridgePlugin in the host JVM can retrieve the ModelType.
    """

    def __init__(self, model_type: ModelType):
        self._model_type = model_type

    def getModelType(self):
        return self._model_type

    class Java:
        implements = ["gov.nasa.ammos.aerie.merlin.python.gateway.ModelTypeSupplier"]


def _get_local_ip(remote_host):
    import socket as _socket
    s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
    try:
        s.connect((remote_host, 80))
        return s.getsockname()[0]
    finally:
        s.close()


def serve(models, host="127.0.0.1", port=25333, callback_port=0):
    """
    Connect to the py4j GatewayServer already running inside a PlanDev JVM
    (started by the pymerlin-gateway agent) and register each model.

    Parameters
    ----------
    models : list of (name, version, ModelClass) tuples
    host   : hostname/IP of the PlanDev JVM (default localhost)
    port   : py4j gateway port (default 25333, matches GatewayAgent default)
    """
    max_attempts = 30
    delay = 2
    local_ip = _get_local_ip(host)
    gw = None
    for attempt in range(1, max_attempts + 1):
        try:
            gw = JavaGateway(
                gateway_parameters=GatewayParameters(
                    address=host,
                    port=port,
                    auto_convert=True,
                ),
                callback_server_parameters=CallbackServerParameters(
                    port=callback_port,
                    address=local_ip,
                ),
            )
            registry = gw.entry_point.getRegistry()
            break
        except Exception as e:
            try:
                gw.shutdown()
            except Exception:
                pass
            gw = None
            print(f"[pymerlin] Waiting for JVM at {host}:{port} (attempt {attempt}/{max_attempts}): {e}")
            if attempt == max_attempts:
                raise
            time.sleep(delay)

    for name, version, model_class in models:
        mt = ModelType(model_class)
        mt.set_gateway(gw)
        provider = ModelProvider(mt)
        registry.registerObject(name, version, provider)
        print(f"[pymerlin] Registered '{name}@{version}' with PlanDev JVM at {host}:{port}")

    print("[pymerlin] Serving — waiting for simulation callbacks (Ctrl-C to stop)")
    threading.Event().wait()


def build_plandev_jar(name, version, out):
    """
    Produce an uploadable Aerie mission-model JAR by copying the prebuilt
    pymerlin-bridge.jar and stamping pymerlin-model.properties into it.

    Parameters
    ----------
    name    : model name (must match what the sidecar registers)
    version : model version (must match what the sidecar registers)
    out     : output path for the stamped JAR file
    """
    src = _find_bridge_jar()
    shutil.copy(src, out)
    with zipfile.ZipFile(out, "a") as z:
        z.writestr(
            "pymerlin-model.properties",
            f"name={name}\nversion={version}\n",
        )
    print(f"[pymerlin] Wrote bridge JAR → {out}  (name={name}, version={version})")


def _find_bridge_jar():
    """
    Locate pymerlin-bridge.jar bundled alongside this package.
    Falls back to the Gradle build output for development use.
    """
    bundled = os.path.join(os.path.dirname(__file__), "jars", _BRIDGE_JAR_NAME)
    if os.path.exists(bundled):
        return bundled

    repo_build = os.path.join(
        os.path.dirname(__file__),
        "..", "..", "java", "pymerlin-bridge", "build", "libs", _BRIDGE_JAR_NAME,
    )
    repo_build = os.path.normpath(repo_build)
    if os.path.exists(repo_build):
        return repo_build

    raise FileNotFoundError(
        f"Cannot find {_BRIDGE_JAR_NAME}. "
        "Either install pymerlin from a release (which bundles the JAR), "
        "or build it first with: cd java && ./gradlew :pymerlin-bridge:jar"
    )


def _load_model_class(model_ref):
    """
    Load a model class from 'path/to/file.py:ClassName'.
    """
    if ":" not in model_ref:
        raise ValueError(f"model_ref must be 'path/to/file.py:ClassName', got: {model_ref!r}")
    file_path, class_name = model_ref.rsplit(":", 1)
    file_path = os.path.abspath(file_path)
    spec = importlib.util.spec_from_file_location("_pymerlin_user_model", file_path)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, os.path.dirname(file_path))
    spec.loader.exec_module(module)
    return getattr(module, class_name)


def main():
    """
    CLI entry point.

    Subcommands
    -----------
    serve
        pymerlin serve --model ./model.py:Mission --name my-model --version 1.0.0
                       [--host 127.0.0.1] [--port 25333]

    build-plandev-jar
        pymerlin build-plandev-jar --name my-model --version 1.0.0 --out my-model.jar
    """
    import argparse

    parser = argparse.ArgumentParser(prog="pymerlin")
    sub = parser.add_subparsers(dest="command", required=True)

    p_serve = sub.add_parser("serve", help="Register a Python model with a running PlanDev JVM")
    p_serve.add_argument("--model", required=True,
                         help="path/to/model.py:ClassName")
    p_serve.add_argument("--name", required=True, help="Model name")
    p_serve.add_argument("--version", required=True, help="Model version")
    p_serve.add_argument("--host", default="127.0.0.1", help="PlanDev JVM host (default: 127.0.0.1)")
    p_serve.add_argument("--port", type=int, default=25333, help="py4j gateway port (default: 25333)")
    p_serve.add_argument("--callback-port", type=int, default=0, help="py4j callback server port (default: OS-assigned)")

    p_build = sub.add_parser("build-plandev-jar",
                              help="Build an uploadable Aerie mission-model JAR")
    p_build.add_argument("--name", required=True, help="Model name")
    p_build.add_argument("--version", required=True, help="Model version")
    p_build.add_argument("--out", required=True, help="Output JAR path")

    args = parser.parse_args()

    if args.command == "serve":
        model_class = _load_model_class(args.model)
        serve([(args.name, args.version, model_class)], host=args.host, port=args.port, callback_port=args.callback_port)

    elif args.command == "build-plandev-jar":
        build_plandev_jar(args.name, args.version, args.out)


if __name__ == "__main__":
    main()
