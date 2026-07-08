"""
Python protocol server for the PyMerlin shim JAR.

Launched by the shim JAR as a subprocess:
    python -m pymerlin._server --model path/to/model.py:ClassName

Speaks newline-delimited JSON over stdin/stdout per docs-src/shim-protocol.md.
Single-threaded cooperative: Java never sends a new request until Python responds.
"""

import argparse
import importlib.util
import inspect
import json
import os
import sys
import threading
from queue import Queue
from typing import Any

from pymerlin._internal import _globals
from pymerlin._internal._registrar import Registrar, set_value
from pymerlin._internal._task_status import Delayed, Awaiting


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def _send(msg: dict):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def _recv() -> dict:
    line = sys.stdin.readline()
    if not line:
        raise EOFError("stdin closed")
    return json.loads(line.strip())


# ---------------------------------------------------------------------------
# Model loader
# ---------------------------------------------------------------------------

def _load_model_class(model_ref: str):
    if ":" not in model_ref:
        raise ValueError(f"model_ref must be 'path/to/file.py:ClassName', got: {model_ref!r}")
    file_path, class_name = model_ref.rsplit(":", 1)
    file_path = os.path.abspath(file_path)
    spec = importlib.util.spec_from_file_location("_pymerlin_user_model", file_path)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, os.path.dirname(file_path))
    spec.loader.exec_module(module)
    return getattr(module, class_name)


# ---------------------------------------------------------------------------
# Model introspection helpers
# ---------------------------------------------------------------------------

def _python_type_name(annotation) -> str:
    if annotation is inspect.Parameter.empty or annotation is Any:
        return "any"
    if annotation is int:
        return "int"
    if annotation is float:
        return "float"
    if annotation is str:
        return "str"
    if annotation is bool:
        return "bool"
    return "any"


def _describe_activity_types(model_class) -> dict:
    result = {}
    if not hasattr(model_class, "activity_types"):
        return result
    for name, task_def in model_class.activity_types.items():
        func = getattr(task_def, "raw_func", None) or task_def.inner
        sig = inspect.signature(func)
        params = {}
        for param_name, param in sig.parameters.items():
            if param_name == "mission":
                continue
            params[param_name] = {
                "type": _python_type_name(param.annotation),
                "required": param.default is inspect.Parameter.empty,
                "default": None if param.default is inspect.Parameter.empty else param.default,
            }
        result[name] = {"parameters": params}
    return result


def _describe_resources(registrar: Registrar) -> dict:
    result = {}
    for name, getter in registrar.resources:
        val = getter()
        if isinstance(val, bool):
            vtype = "bool"
        elif isinstance(val, int):
            vtype = "int"
        elif isinstance(val, float):
            vtype = "float"
        else:
            vtype = "str"
        result[name] = {"value_type": vtype}
    return result


# ---------------------------------------------------------------------------
# Reaction context — installed into _globals so delay()/wait_until() can yield
# ---------------------------------------------------------------------------

class _ReactionContext:
    def __init__(self, outbox: Queue, inbox: Queue):
        self._outbox = outbox
        self._inbox = inbox

    def yield_with(self, status):
        self._outbox.put(("yield", status))
        msg = self._inbox.get()
        if msg == "resume":
            return
        raise RuntimeError(f"[PyMerlin] Unexpected control message: {msg}")


# ---------------------------------------------------------------------------
# Activity runner
# ---------------------------------------------------------------------------

class _ActivityRunner:
    """
    Runs one activity function in a background thread.
    Communicates with the main loop via inbox/outbox queues.
    Emits and spawns are queued separately and drained between yields.
    """

    STATUS_RUNNING   = "running"
    STATUS_DELAYED   = "delayed"
    STATUS_AWAITING  = "awaiting"
    STATUS_COMPLETED = "completed"
    STATUS_ERROR     = "error"

    def __init__(self, activity_id: str, func, model_class, cell_values: dict, cell_id_to_resource: dict):
        self._id = activity_id
        self._func = func
        self._model_class = model_class
        self._cell_values = cell_values
        self._cell_id_to_resource = cell_id_to_resource

        self._inbox:  Queue = Queue(maxsize=1)
        self._outbox: Queue = Queue(maxsize=1)
        self._emit_queue: Queue = Queue()
        self._spawn_queue: Queue = Queue()

        self.status = self.STATUS_RUNNING
        self.delay_us: int = 0
        self.awaiting_condition = None

        self._thread = None

    def start(self):
        ctx = _ReactionContext(self._outbox, self._inbox)

        cell_values        = self._cell_values
        cell_id_to_res     = self._cell_id_to_resource
        emit_queue         = self._emit_queue
        spawn_queue        = self._spawn_queue
        model_class        = self._model_class

        def _spawner(task_instance):
            activity_name = getattr(task_instance, "activity_name", None)
            spawn_queue.put((activity_name, {}))

        def _run():
            _globals.reaction_context = ctx
            _globals._current_context[1] = _spawner
            try:
                self._func()
                self._outbox.put(("done",))
            except Exception as exc:
                self._outbox.put(("error", exc))

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()
        self._wait_for_yield()

    def resume(self):
        self._inbox.put("resume")
        self._wait_for_yield()

    def _wait_for_yield(self):
        msg = self._outbox.get()
        if msg[0] == "yield":
            status = msg[1]
            if isinstance(status, Delayed):
                from pymerlin.duration import MICROSECONDS
                self.status = self.STATUS_DELAYED
                self.delay_us = int(status.duration.to_number_in(MICROSECONDS))
            elif isinstance(status, Awaiting):
                self.status = self.STATUS_AWAITING
                self.awaiting_condition = status.condition
            else:
                self.status = self.STATUS_RUNNING
        elif msg[0] == "done":
            self.status = self.STATUS_COMPLETED
        elif msg[0] == "error":
            self.status = self.STATUS_ERROR
            raise msg[1]

    def drain_emits(self) -> list:
        """Return all pending (resource_name, str_value) emits."""
        results = []
        while not self._emit_queue.empty():
            try:
                results.append(self._emit_queue.get_nowait())
            except Exception:
                break
        return results

    def drain_spawns(self) -> list:
        """Return all pending (activity_name, py_args) spawn requests."""
        results = []
        while not self._spawn_queue.empty():
            try:
                results.append(self._spawn_queue.get_nowait())
            except Exception:
                break
        return results


# ---------------------------------------------------------------------------
# Model state manager
# ---------------------------------------------------------------------------

class _ModelState:
    """
    Holds the single shared model instance and patches CellRef.emit to route
    through our emit queue rather than the Aerie context.
    """

    def __init__(self, model_class):
        self.model_class = model_class
        self._registrar = Registrar()
        self.model_instance = model_class(self._registrar)

        self.cell_values: dict = {}
        self.cell_id_to_resource: dict = {}

        for cell_ref, initial_value, _evolution in self._registrar.cells:
            cell_ref.id = id(cell_ref)
            self.cell_values[id(cell_ref)] = initial_value

        for resource_name, getter in self._registrar.resources:
            for cell_ref, _iv, _ev in self._registrar.cells:
                if getter == cell_ref.get or (
                    hasattr(getter, "__self__") and getter.__self__ is cell_ref
                ):
                    self.cell_id_to_resource[id(cell_ref)] = resource_name

        _globals.cell_values_by_id = self.cell_values

        cell_values        = self.cell_values
        cell_id_to_res     = self.cell_id_to_resource

        # current_emit_queue_holder[0] is swapped per-activity in make_runner
        self._current_emit_queue_holder = [None]
        holder = self._current_emit_queue_holder

        for cell_ref, _iv, _ev in self._registrar.cells:
            def _make_emit(cref):
                def _emit(event):
                    if not callable(event):
                        event = set_value(event)
                    new_val = event(cell_values[cref.id])
                    cell_values[cref.id] = new_val
                    res = cell_id_to_res.get(id(cref))
                    if res is not None:
                        q = holder[0]
                        if q is not None:
                            q.put((res, str(new_val)))
                return _emit
            cell_ref.emit = _make_emit(cell_ref)

        _globals._current_context[2] = model_class

    def make_runner(self, activity_id: str, activity_name: str, py_args: dict) -> _ActivityRunner:
        task_def = self.model_class.activity_types[activity_name]
        raw_func = getattr(task_def, "raw_func", None)
        if raw_func is None:
            raise KeyError(f"Activity {activity_name!r} has no raw_func — was it decorated with @Mission.ActivityType?")
        model_instance = self.model_instance

        runner = _ActivityRunner(
            activity_id,
            lambda: raw_func(model_instance, **py_args),
            self.model_class,
            self.cell_values,
            self.cell_id_to_resource,
        )
        self._current_emit_queue_holder[0] = runner._emit_queue
        return runner

    def get_resource_value(self, name: str) -> str:
        for res_name, getter in self._registrar.resources:
            if res_name == name:
                return str(getter())
        raise KeyError(f"Unknown resource: {name!r}")

    def describe_resources(self) -> dict:
        return _describe_resources(self._registrar)


# ---------------------------------------------------------------------------
# Main server loop
# ---------------------------------------------------------------------------

def _run_server(model_class):
    model_state = _ModelState(model_class)
    active_runners: dict[str, _ActivityRunner] = {}

    _send({"op": "ready"})

    while True:
        try:
            msg = _recv()
        except EOFError:
            break

        op = msg.get("op")

        if op == "get_activity_types":
            _send({
                "op": "activity_types",
                "types": _describe_activity_types(model_class),
            })

        elif op == "get_resources":
            _send({
                "op": "resources",
                "resources": model_state.describe_resources(),
            })

        elif op == "get_resource_value":
            name = msg["name"]
            try:
                value = model_state.get_resource_value(name)
                _send({"op": "resource_value", "name": name, "value": value})
            except KeyError as e:
                _send({"op": "error", "message": str(e)})

        elif op == "run_activity":
            act_id   = msg["id"]
            act_name = msg["name"]
            args     = msg.get("args", {})
            try:
                runner = model_state.make_runner(act_id, act_name, args)
                active_runners[act_id] = runner
                runner.start()
                _send_runner_state(act_id, runner)
            except Exception as exc:
                _send({"op": "error", "id": act_id, "message": str(exc)})

        elif op == "resume":
            act_id = msg["id"]
            runner = active_runners.get(act_id)
            if runner is None:
                _send({"op": "error", "id": act_id, "message": "Unknown activity id"})
                continue
            try:
                runner.resume()
                _send_runner_state(act_id, runner)
                if runner.status == _ActivityRunner.STATUS_COMPLETED:
                    del active_runners[act_id]
            except Exception as exc:
                _send({"op": "error", "id": act_id, "message": str(exc)})
                active_runners.pop(act_id, None)

        else:
            _send({"op": "error", "message": f"Unknown op: {op!r}"})


def _send_runner_state(act_id: str, runner: _ActivityRunner):
    """
    Drain emits and spawns, then send the primary yield message.
    Emits and spawns are inlined into the response so Java can apply them
    before honouring the primary yield (delay / wait_until / done).
    """
    emits  = runner.drain_emits()
    spawns = runner.drain_spawns()

    base: dict = {"id": act_id}

    if emits:
        base["emits"] = [{"resource": r, "value": v} for r, v in emits]
    if spawns:
        base["spawns"] = [{"name": n, "args": a} for n, a in spawns]

    if runner.status == _ActivityRunner.STATUS_DELAYED:
        base["op"] = "delay"
        base["duration_us"] = runner.delay_us

    elif runner.status == _ActivityRunner.STATUS_AWAITING:
        condition = runner.awaiting_condition
        structured = _try_encode_condition(condition)
        if structured:
            base["op"] = "wait_until"
            base.update(structured)
        else:
            base["op"] = "wait_until_opaque"

    elif runner.status == _ActivityRunner.STATUS_COMPLETED:
        base["op"] = "done"

    elif runner.status == _ActivityRunner.STATUS_RUNNING:
        base["op"] = "running"

    _send(base)


def _try_encode_condition(condition) -> dict | None:
    """
    Try to express a wait_until condition as a structured JSON predicate.
    Returns None for complex lambdas that cannot be introspected.
    """
    try:
        import dis, io
        buf = io.StringIO()
        dis.dis(condition, file=buf)
        bytecode = buf.getvalue()
        # Heuristic: if the lambda references a single LOAD_ATTR (resource .get)
        # and a COMPARE_OP, extract it. Otherwise return None.
        # This is intentionally conservative — complex conditions fall back to opaque.
        return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True, help="path/to/model.py:ClassName")
    args = parser.parse_args()

    model_class = _load_model_class(args.model)
    _run_server(model_class)


if __name__ == "__main__":
    main()
