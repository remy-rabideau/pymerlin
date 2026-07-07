"""
Python runtime server for PyMerlin code generation approach.

This module is launched as a subprocess by the generated Java mission model JAR.
It connects to the Java GatewayServer via py4j and registers a bridge object
that Java can call to execute Python activity functions.
"""

import argparse
import importlib.util
import os
import sys
import threading
from queue import Queue
from py4j.java_gateway import JavaGateway, GatewayParameters, CallbackServerParameters
from pymerlin._internal._registrar import Registrar, CellRef
from pymerlin._internal import _globals
from pymerlin._internal._task_status import Delayed, Completed, Awaiting


class PyMerlinBridge:
    """
    Bridge object that Java calls into for activity execution.
    This is registered with the Java GatewayServer entry point.
    """
    
    def __init__(self, model_class, gateway):
        self.model_class = model_class
        self.gateway = gateway
        self.model_instance = None
        self._cell_emitters = {}  # resource_name -> Java CellEmitter proxy
        
    def registerCellEmitter(self, resource_name, emitter):
        """Called by Java to register a CellEmitter for a named resource."""
        print(f"[PyMerlin] registerCellEmitter: {resource_name}", flush=True)
        self._cell_emitters[resource_name] = emitter

    def _ensure_model(self):
        """Initialize the model instance and capture its registrar if not done yet."""
        if self.model_instance is None:
            self._registrar = Registrar()
            self.model_instance = self.model_class(self._registrar)

            # Assign IDs and seed initial values for all cells so CellRef.get() works
            # without a simulation context. We maintain a simple dict-based state.
            self._cell_values = {}
            for cell_ref, initial_value, _evolution in self._registrar.cells:
                cell_id = id(cell_ref)
                cell_ref.id = cell_id
                self._cell_values[cell_id] = initial_value

            # Build reverse map: cell_id -> resource_name for emitter lookup
            self._cell_id_to_resource = {}
            for resource_name, getter in self._registrar.resources:
                for cell_ref, _iv, _ev in self._registrar.cells:
                    if getter == cell_ref.get or (hasattr(getter, '__self__') and getter.__self__ is cell_ref):
                        self._cell_id_to_resource[id(cell_ref)] = resource_name
                        print(f"[PyMerlin] cell_id_to_resource: cell {id(cell_ref)} -> {resource_name}", flush=True)
            print(f"[PyMerlin] cell_emitters at _ensure_model: {list(self._cell_emitters.keys())}", flush=True)

            # Patch _globals so CellRef._get() resolves against our local dict
            _globals.cell_values_by_id = self._cell_values

            # Patch each CellRef.emit to update cell_values and enqueue emit for Java task thread
            cell_values = self._cell_values
            cell_id_to_resource = self._cell_id_to_resource
            # _current_emit_queue holds the Queue for the currently-running TaskHandle
            self._current_emit_queue = [None]  # mutable single-element list
            current_emit_queue_holder = self._current_emit_queue
            for cell_ref, _iv, _ev in self._registrar.cells:
                def _make_emit(cref):
                    def _emit(event):
                        if not callable(event):
                            from pymerlin._internal._registrar import set_value
                            event = set_value(event)
                        new_val = event(cell_values[cref.id])
                        cell_values[cref.id] = new_val
                        resource_name = cell_id_to_resource.get(id(cref))
                        if resource_name is not None:
                            q = current_emit_queue_holder[0]
                            if q is not None:
                                q.put((resource_name, str(new_val)))
                    return _emit
                cell_ref.emit = _make_emit(cell_ref)

            # Install a minimal context stub so _current_context[0].get(id) works
            class _ContextStub:
                def get(self_, cell_id):
                    return cell_id
                def emit(self_, effect_id, topic):
                    pass
            _globals._current_context[0] = _ContextStub()

            # Set model type so get_topics() in _spawn_helpers can find activity topics
            _globals._current_context[2] = self.model_class

            # Set spawner: _current_context[1](child) is called by spawn()
            # child is a TaskInstance; we need to find its activity name and start it
            def _spawner(task_instance):
                # Spawn child activity in a daemon thread with a no-op reaction context
                # so delay()/wait_until() don't block (fire-and-forget semantics)
                class _NoOpReactionContext:
                    def yield_with(self_, status):
                        pass  # ignore delays/waits in spawned tasks
                def _run():
                    _globals.reaction_context = _NoOpReactionContext()
                    try:
                        task_instance.run()
                    except Exception as e:
                        print(f"[PyMerlin] Spawned task error: {e}")
                threading.Thread(target=_run, daemon=True).start()
            _globals._current_context[1] = _spawner

    def startActivity(self, activity_name, args):
        """
        Start a Python activity in a background thread and return a TaskHandle.
        Java calls step() on the handle each simulation step.
        """
        print(f"[PyMerlinBridge] Starting activity: {activity_name}")

        if not hasattr(self.model_class, 'activity_types'):
            raise ValueError(f"Model class has no activity_types: {self.model_class}")
        if activity_name not in self.model_class.activity_types:
            raise ValueError(f"Unknown activity: {activity_name}")

        task_def = self.model_class.activity_types[activity_name]
        self._ensure_model()

        # Convert Java Map to Python dict
        py_args = {}
        if args is not None:
            for key in args.keySet():
                py_args[key] = args.get(key)

        raw_func = getattr(task_def, 'raw_func', None) or task_def.inner
        model_instance = self.model_instance
        emit_queue = Queue()
        # Point the shared emit queue holder at this handle's queue before starting
        self._current_emit_queue[0] = emit_queue
        handle = TaskHandle(lambda: raw_func(model_instance, **py_args), self.gateway,
                            model_class=self.model_class, emit_queue=emit_queue)
        handle.start()
        return handle

    def getResource(self, resource_name):
        """
        Query the current value of a named resource from the Python model.
        
        Args:
            resource_name: Name of the resource as registered via registrar.resource()
        
        Returns:
            The current resource value (serialized as a string for Aerie)
        """
        self._ensure_model()
        
        for name, func in self._registrar.resources:
            if name == resource_name:
                value = func()
                return str(value)
        
        raise ValueError(f"Unknown resource: {resource_name!r}")

    class Java:
        implements = ["gov.nasa.ammos.aerie.merlin.python.codegen.PyMerlinRuntime$PyMerlinBridge"]


class _ReactionContext:
    """Installed as _globals.reaction_context so delay()/wait_until() can yield."""
    def __init__(self, outbox, inbox):
        self._outbox = outbox
        self._inbox = inbox

    def yield_with(self, status):
        self._outbox.put(("yield", status))
        msg = self._inbox.get()
        if msg == "resume":
            return
        elif msg == "abort":
            raise Exception("[PyMerlin] Task aborted")
        else:
            raise Exception(f"[PyMerlin] Unexpected message: {msg}")


class SpawnInfo:
    """Carries activity name + serialized args for a spawned child activity."""
    def __init__(self, activity_name, py_args):
        self._activity_name = activity_name
        self._py_args = py_args

    def getName(self):
        return self._activity_name

    def getArgCount(self):
        return len(self._py_args)

    class Java:
        implements = ["gov.nasa.ammos.aerie.merlin.python.codegen.PyMerlinRuntime$SpawnInfo"]


class TaskHandle:
    """
    Wraps a Python activity running in a background thread.
    Java calls step() to advance the activity; the activity suspends via yield_with().
    Java calls getStatus() to learn how long to wait before the next step.
    Java calls getSpawnedHandle() to drain child TaskHandles registered via spawn().
    """

    STATUS_RUNNING = "running"
    STATUS_DELAYED = "delayed"
    STATUS_COMPLETED = "completed"
    STATUS_ERROR = "error"

    def __init__(self, func, gateway, model_class=None, spawn_queue=None, emit_queue=None):
        self._func = func  # zero-arg callable
        self._gateway = gateway
        self._bridge_model_class = model_class
        self._inbox = Queue(maxsize=1)   # Java → Python: "resume" / "abort"
        self._outbox = Queue(maxsize=1)  # Python → Java: ("yield", status) / ("done",) / ("error", exc)
        self._status = self.STATUS_RUNNING
        self._delay_micros = 0
        self._thread = None
        # Queue of child TaskHandles created by spawn() calls inside this activity
        self._spawn_queue = spawn_queue if spawn_queue is not None else Queue()
        # Queue of (resource_name, value) emits to be applied on Java task thread
        self._emit_queue = emit_queue if emit_queue is not None else Queue()
        self._current_emit = None  # holds the current (resource_name, value) between method calls

    def start(self):
        ctx = _ReactionContext(self._outbox, self._inbox)
        spawn_queue = self._spawn_queue
        gateway = self._gateway

        model_class = self._bridge_model_class

        def _make_spawner():
            def _spawner(task_instance):
                # Find the activity name and args; queue them for Java to call startActivity()
                activity_name = None
                py_args = {}
                if hasattr(task_instance, 'raw_func'):
                    for name, td in model_class.activity_types.items():
                        if getattr(td, 'raw_func', None) is task_instance.raw_func:
                            activity_name = name
                            break
                    # raw_args is (mission_instance, ...) — skip the model arg
                    ra = task_instance.raw_args[1:] if task_instance.raw_args else ()
                    py_args = dict(zip([], ra))  # activities rarely have positional non-model args
                    py_args.update(task_instance.raw_kwargs)
                print(f"[PyMerlin] Queuing spawn: {activity_name}", flush=True)
                spawn_queue.put(SpawnInfo(activity_name, py_args))
            return _spawner

        def _run():
            _globals.reaction_context = ctx
            _globals._current_context[1] = _make_spawner()
            try:
                self._func()
                self._outbox.put(("done",))
            except Exception as e:
                self._outbox.put(("error", e))
        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()
        self._wait_for_yield()

    def _wait_for_yield(self):
        msg = self._outbox.get()
        if msg[0] == "yield":
            status = msg[1]
            if isinstance(status, Delayed):
                from pymerlin.duration import MICROSECONDS
                self._status = self.STATUS_DELAYED
                self._delay_micros = int(status.duration.to_number_in(MICROSECONDS))
            else:
                self._status = self.STATUS_RUNNING
        elif msg[0] == "done":
            self._status = self.STATUS_COMPLETED
        elif msg[0] == "error":
            self._status = self.STATUS_ERROR
            raise msg[1]

    def step(self):
        """Resume the Python thread and wait for its next yield."""
        self._inbox.put("resume")
        self._wait_for_yield()

    def getStatus(self):
        return self._status

    def getDelayMicros(self):
        return self._delay_micros

    def getSpawnedHandle(self):
        """Return the next SpawnInfo (activity name + args), or None if none pending."""
        try:
            info = self._spawn_queue.get_nowait()
            print(f"[PyMerlin] getSpawnedHandle() returning spawn: {info.getName()}", flush=True)
            return info
        except Exception:
            return None

    def getPendingEmitResource(self):
        """Return the resource name of the next pending emit, or None if none."""
        try:
            self._current_emit = self._emit_queue.get_nowait()
            return self._current_emit[0]
        except Exception:
            self._current_emit = None
            return None

    def getPendingEmitValue(self):
        """Return the value of the current pending emit (call after getPendingEmitResource)."""
        return self._current_emit[1] if self._current_emit else None

    class Java:
        implements = ["gov.nasa.ammos.aerie.merlin.python.codegen.PyMerlinRuntime$TaskHandle"]


def load_model_class(model_ref):
    """Load a model class from 'path/to/file.py:ClassName'."""
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True, help="Model reference (path/to/model.py:ClassName)")
    parser.add_argument("--gateway-port", type=int, required=True, help="Java gateway port")
    parser.add_argument("--callback-port", type=int, required=True, help="Port for Python callback server")
    args = parser.parse_args()
    
    print(f"[PyMerlinServer] Loading model: {args.model}")
    model_class = load_model_class(args.model)
    
    print(f"[PyMerlinServer] Connecting to Java gateway on port {args.gateway_port}")
    gateway = JavaGateway(
        gateway_parameters=GatewayParameters(port=args.gateway_port, auto_convert=True),
        callback_server_parameters=CallbackServerParameters(port=args.callback_port),
    )
    
    # Create bridge and register with Java
    bridge = PyMerlinBridge(model_class, gateway)
    
    print("[PyMerlinServer] Registering bridge with Java")
    entry_point = gateway.entry_point
    entry_point.register(bridge)
    
    print("[PyMerlinServer] Ready to execute activities")
    
    # Keep the process alive
    try:
        import time
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("[PyMerlinServer] Shutting down")
        gateway.shutdown()


if __name__ == "__main__":
    main()
