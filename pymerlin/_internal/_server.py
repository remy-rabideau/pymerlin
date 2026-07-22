"""
In-process model runtime for the PyMerlin shim, called directly by GraalBridge (Java) via
GraalPy host calls — no subprocess, no protocol, no queues.

An activity runs on the calling Java `ThreadedTask` thread. `delay`/`call` call straight
back into a Java host object (`java_actions`); emits route through `CellRef.emit` →
`java_actions.emitCell` (Phase 4, roadmap §7); `wait_until` passes the Python predicate to
`java_actions.waitUntil` as a `BooleanSupplier` and the engine re-evaluates it when cell
dependencies change (no polling); `spawn` schedules a fresh child `ThreadedTask` via
`java_actions.spawnActivity`. `CellRef.get` calls `java_actions.ask(cell_index)` which goes
through `ModelActions.ask(cellId)`, registering read dependencies in QueryContext for
waitUntil (Phase 4, roadmap §7).
"""

import importlib.util
import inspect
import json
import os
import sys
from typing import Any

from pymerlin._internal import _globals
from pymerlin._internal._registrar import Registrar
from pymerlin._internal._task_status import Delayed, Awaiting, Calling


# ---------------------------------------------------------------------------
# Model loader
# ---------------------------------------------------------------------------

def _load_model_class(model_ref: str):
    if ":" not in model_ref:
        raise ValueError(f"model_ref must be 'path/to/file.py:ClassName', got: {model_ref!r}")
    file_path, class_name = model_ref.rsplit(":", 1)
    file_path = os.path.abspath(file_path)
    pkg_dir = os.path.dirname(file_path)
    pkg_init = os.path.join(pkg_dir, "__init__.py")
    module_stem = os.path.splitext(os.path.basename(file_path))[0]

    if os.path.exists(pkg_init):
        # Model is part of a package — load as a proper package so relative imports work.
        pkg_name = os.path.basename(pkg_dir)
        parent_dir = os.path.dirname(pkg_dir)
        if parent_dir not in sys.path:
            sys.path.insert(0, parent_dir)
        # Import the package first so the module can do relative imports.
        importlib.import_module(pkg_name)
        module = importlib.import_module(f"{pkg_name}.{module_stem}")
    else:
        # Standalone file — load directly.
        if pkg_dir not in sys.path:
            sys.path.insert(0, pkg_dir)
        spec = importlib.util.spec_from_file_location("_pymerlin_user_model", file_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

    return getattr(module, class_name)


# ---------------------------------------------------------------------------
# Model introspection helpers
# ---------------------------------------------------------------------------

def _python_type_name(annotation, default=inspect.Parameter.empty) -> str:
    if annotation is not inspect.Parameter.empty and annotation is not Any:
        if annotation is int:
            return "int"
        if annotation is float:
            return "float"
        if annotation is str:
            return "str"
        if annotation is bool:
            return "bool"
    # Fall back to inferring from the default value
    if default is not inspect.Parameter.empty and default is not None:
        if isinstance(default, bool):
            return "bool"
        if isinstance(default, int):
            return "int"
        if isinstance(default, float):
            return "float"
        if isinstance(default, str):
            return "str"
    return "str"


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
                "type": _python_type_name(param.annotation, param.default),
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


def _child_args(model_class, instance) -> dict:
    """
    Reconstruct the activity arguments for a spawned/called child from the TaskInstance
    that `spawn(...)`/`call(...)` was handed. `make_instance` stores `instance.args`
    (positional, with the mission/model as args[0]) and `instance.kwargs`; here we drop the
    mission and map any remaining positionals onto the child's declared parameter names,
    matching the 'mission'-skipping convention `_describe_activity_types` already uses.
    """
    name = getattr(instance, "activity_name", None)
    result = dict(getattr(instance, "kwargs", {}) or {})
    positional = getattr(instance, "args", ()) or ()
    if name is not None and name in getattr(model_class, "activity_types", {}):
        task_def = model_class.activity_types[name]
        func = getattr(task_def, "raw_func", None) or task_def.inner
        param_names = [p for p in inspect.signature(func).parameters if p != "mission"]
        # positional[1:] skips the mission/model instance passed as the first argument
        for param_name, value in zip(param_names, positional[1:]):
            result.setdefault(param_name, value)
    return result


def _child_args_json(model_class, instance) -> str:
    """Same as `_child_args`, serialized to JSON so it crosses to Java as a plain String
    (keeps the PyActions host interface free of GraalPy `Value` types)."""
    return json.dumps(_child_args(model_class, instance))


# ---------------------------------------------------------------------------
# Reaction context — installed into _globals so delay()/call()/wait_until() call
# straight back into Java (roadmap §6.2)
# ---------------------------------------------------------------------------

class _ReactionContext:
    """
    There is no queue and no separate Python thread: the activity runs on the calling Java
    ThreadedTask thread, and delay/call/wait_until call straight back into the Java host
    object (`java_actions`) synchronously. Gate B proved a host call that parks the
    ThreadedTask thread (with Python frames live on its stack) does not hold the context
    lock, so the engine's next task can still enter the Context; §6.6 confirmed this holds
    for Aerie's real virtual threads, not just Gate B's platform-thread spike.

    Stateless apart from `java_actions`, which is a single shared object whose methods
    delegate to `ModelActions.*` and therefore act on whichever ThreadedTask thread is
    currently executing. That is why it is safe for concurrently-running activities to
    share one instance via the `_globals.reaction_context` global (§6.4) — every instance
    of this class is functionally interchangeable, since none of them carry per-activity
    state; Java's own thread-local `ModelActions` context is what makes the dispatch
    correct per-thread, not anything held here.
    """

    def __init__(self, java_actions):
        self._java = java_actions

    def yield_with(self, status):
        if isinstance(status, Delayed):
            from pymerlin.duration import MICROSECONDS
            self._java.delay(int(status.duration.to_number_in(MICROSECONDS)))
        elif isinstance(status, Calling):
            child = status.child
            self._java.callActivity(
                getattr(child, "activity_name", None),
                _child_args_json(_globals._current_context[2], child),
            )
        elif isinstance(status, Awaiting):
            # Phase 4 (§7): real cell-read-driven waitUntil. The Python predicate is
            # passed to Java as a BooleanSupplier (GraalPy auto-wraps). Inside the
            # Condition, the engine evaluates the predicate on the engine thread;
            # CellRef.get() calls java_actions.ask() which goes through QueryContext
            # and registers read dependencies. The engine re-evaluates when those
            # topics change — no polling.
            self._java.waitUntil(status.condition)


# ---------------------------------------------------------------------------
# Model state manager
# ---------------------------------------------------------------------------

class _ModelState:
    """
    Holds the single shared model instance. Phase 4 (roadmap §7) wires each Python
    CellRef to a real Aerie cell via sequential cell indices: CellRef.get() calls
    java_actions.ask(cell_index) which goes through ModelActions.ask(cellId), and
    CellRef.emit() applies the event locally then calls java_actions.emitCell(cell_index,
    str(new_val)). The local _globals.cell_values_by_id dict is still populated as a
    typed mirror for event function application (lambda x: x + 15.0 needs a float, not
    a string) and as a fallback during model __init__ before java_actions is available.
    """

    def __init__(self, model_class):
        self.model_class = model_class
        self._registrar = Registrar()
        self.model_instance = model_class(self._registrar)

        self.cell_values: dict = {}
        self.cell_id_to_resource: dict = {}

        for i, (cell_ref, initial_value, _evolution) in enumerate(self._registrar.cells):
            cell_ref.id = id(cell_ref)
            cell_ref._cell_index = i
            cell_ref._value_type = type(initial_value)
            self.cell_values[id(cell_ref)] = initial_value

        for resource_name, getter in self._registrar.resources:
            for cell_ref, _iv, _ev in self._registrar.cells:
                if getter == cell_ref.get or (
                    hasattr(getter, "__self__") and getter.__self__ is cell_ref
                ):
                    self.cell_id_to_resource[id(cell_ref)] = resource_name

        _globals.cell_values_by_id = self.cell_values
        _globals._current_context[2] = model_class

    def describe_cells(self) -> list:
        """Return cell metadata for Java to allocate real Aerie cells (Phase 4, §7).
        Cell order matches registrar.cells — indices are the contract between
        CellRef._cell_index (Python) and cellsByIndex (Java)."""
        cells = []
        for cell_ref, initial_value, _evolution in self._registrar.cells:
            current = _globals.cell_values_by_id.get(cell_ref.id, initial_value)
            res_name = self.cell_id_to_resource.get(id(cell_ref))
            if getattr(cell_ref, "_is_linear", False):
                # Continuously-integrating cell (roadmap §7.2): Java backs it with a
                # RealDynamics resource that ramps by `rate` per second between events.
                cells.append({
                    "type": "linear",
                    "initial": str(float(current)),
                    "rate": str(float(getattr(cell_ref, "_initial_rate", 0.0))),
                    "resource": res_name,
                })
                continue
            if isinstance(current, bool):
                vtype = "bool"
            elif isinstance(current, int):
                vtype = "int"
            elif isinstance(current, float):
                vtype = "float"
            else:
                vtype = "str"
            cells.append({
                "initial": str(current),
                "resource": res_name,
                "type": vtype,
            })
        return cells

    def get_resource_value(self, name: str) -> str:
        for res_name, getter in self._registrar.resources:
            if res_name == name:
                return str(getter())
        raise KeyError(f"Unknown resource: {name!r}")

    def describe_resources(self) -> dict:
        return _describe_resources(self._registrar)


# ---------------------------------------------------------------------------
# Graal direct-call entry point (roadmap §6) — the only way an activity runs
# ---------------------------------------------------------------------------

def run_activity_direct(model_state: "_ModelState", java_actions, activity_name: str, py_args: dict):
    """
    Run one activity to completion on the *calling* thread (a Java ThreadedTask).
    delay/call/wait_until call straight into `java_actions`; emits route through
    CellRef.emit → java_actions.emitCell (Phase 4); spawn schedules a fresh child
    ThreadedTask via `java_actions`.

    Returns normally when the activity function returns (Java then closes the span). If the
    activity raises, the exception propagates out through GraalPy to Java as a PolyglotException.
    """
    _globals.java_actions = java_actions
    _globals.reaction_context = _ReactionContext(java_actions)

    model_class = model_state.model_class

    def _spawner(child_instance):
        java_actions.spawnActivity(
            getattr(child_instance, "activity_name", None),
            _child_args_json(model_class, child_instance),
        )

    _globals._current_context[1] = _spawner

    task_def = model_class.activity_types[activity_name]
    raw_func = getattr(task_def, "raw_func", None)
    if raw_func is None:
        raise KeyError(f"Activity {activity_name!r} has no raw_func — was it decorated with @Mission.ActivityType?")
    raw_func(model_state.model_instance, **(py_args or {}))
