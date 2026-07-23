from contextlib import contextmanager

from pymerlin._internal import _globals


class Registrar:
    def __init__(self):
        self.cells = []
        self.resources = []
        self.topics = []

    def cell(self, initial_value, evolution=None):
        ref = CellRef()
        self.cells.append((ref, initial_value, evolution))
        return ref

    def linear(self, initial_value, rate=0.0):
        """
        Declare a continuously-integrating (linear) cell (roadmap §7.2).

        The cell's value evolves as ``value + rate * elapsed_seconds`` — Java backs it
        with an Aerie ``RealDynamics`` resource that ramps between discrete events instead
        of snapshotting the last emitted value. ``set_rate(...)`` changes the slope (e.g.
        start/stop draining); ``emit(...)`` still applies a discrete jump to the value.
        Scoped to linear dynamics only, which is all Aerie's ``RealDynamics`` can represent.
        """
        ref = LinearCellRef(float(rate))
        self.cells.append((ref, float(initial_value), None))
        return ref

    def resource(self, name, f):
        """
        Declare a resource to track
        :param name: The name of the resource
        :param f: A function to calculate the resource, or a cell that contains the value of the resource
        """
        if not callable(f):
            cell = f
            f = cell.get
        self.resources.append((name, f))

    def topic(self, name):
        pass


class Gettable:
    def __init__(self, func):
        self.func = func

    def get(self):
        return self.func()

    def map(self, new_func):
        return Gettable(lambda: new_func(self.get()))

    def __add__(self, other):
        if _is_gettable(other):
            return Gettable(lambda: self.get() + other.get())
        else:
            return Gettable(lambda: self.get() + other)

    def __sub__(self, other):
        if _is_gettable(other):
            return Gettable(lambda: self.get() - other.get())
        else:
            return Gettable(lambda: self.get() - other)

    def __mul__(self, other):
        if _is_gettable(other):
            return Gettable(lambda: self.get() * other.get())
        else:
            return Gettable(lambda: self.get() * other)

    def __div__(self, other):
        if _is_gettable(other):
            return Gettable(lambda: self.get() / other.get())
        else:
            return Gettable(lambda: self.get() / other)

    def __pow__(self, other, modulo=None):
        if _is_gettable(other):
            return Gettable(lambda: self.get() ** other.get())
        else:
            return Gettable(lambda: self.get() ** other)

    def __mod__(self, other):
        if _is_gettable(other):
            return Gettable(lambda: self.get() % other.get())
        else:
            return Gettable(lambda: self.get() % other)

def _is_gettable(obj):
    return callable(getattr(obj, "get", None))

class CellRef(Gettable):
    """
    A reference to an allocated piece of simulation state.

    Under Java-backed execution (Phase 4, roadmap §7), get() calls through to
    ModelActions.ask(cellId) via java_actions.ask(cell_index), which registers a read
    dependency in QueryContext — enabling real waitUntil (the engine re-evaluates when
    the cell's topic changes). emit() applies the event function locally (for typed
    lambda support) then sends the string result to Java via java_actions.emitCell().

    Falls back to the _globals.cell_values_by_id dict when java_actions is None
    (standalone simulation via _framework.py, or model __init__ before activities run).
    """

    def __init__(self):
        super().__init__(self._get)
        self.id = None
        self.topic = None
        self._cell_index = None    # sequential int, set by _ModelState (Phase 4)
        self._value_type = str     # type of the cell value, set by _ModelState

    def emit(self, event):
        if not callable(event):
            event = set_value(event)
        current = self._get()  # reads from Java if available, minimizing stale-value races
        new_val = event(current)
        _globals.cell_values_by_id[self.id] = new_val
        ja = _globals.java_actions
        if ja is not None and self._cell_index is not None:
            ja.emitCell(self._cell_index, str(new_val))

    def set(self, new_value):
        self.emit(set_value(new_value))

    def add(self, addend):
        self.emit(lambda x: x + addend)

    def _get(self):
        ja = _globals.java_actions
        if ja is not None and self._cell_index is not None:
            val_str = ja.ask(self._cell_index)
            return self._convert_from_java(val_str)
        return _globals.cell_values_by_id[self.id]

    def _convert_from_java(self, val_str):
        """Convert a string value from Java back to the Python type."""
        if self._value_type is float:
            return float(val_str)
        elif self._value_type is int:
            return int(float(val_str))
        elif self._value_type is bool:
            return val_str.lower() in ("true", "1")
        return val_str

    def __iadd__(self, other):
        self.emit(lambda x: x + other)
        return self

    def __isub__(self, other):
        self.emit(lambda x: x - other)
        return self

    def __imul__(self, other):
        self.emit(lambda x: x * other)
        return self

    def __idiv__(self, other):
        self.emit(lambda x: x / other)
        return self

    def __imod__(self, other):
        self.emit(lambda x: x % other)
        return self

class LinearCellRef(CellRef):
    """
    A continuously-integrating cell (roadmap §7.2), declared via ``registrar.linear``.

    Behaves like a normal :class:`CellRef` for discrete reads/writes (``get``/``emit``),
    but additionally carries a *rate*: under Java-backed execution the cell's value ramps
    as ``value + rate * elapsed_seconds`` between events, wired to an Aerie
    ``RealDynamics`` resource. ``set_rate`` changes the slope as a discrete event; the
    continuously-integrating value itself is owned by the Java cell (its ``step`` hook),
    so ``get`` always reflects the ramped value at the current instant.
    """

    def __init__(self, initial_rate=0.0):
        super().__init__()
        self._is_linear = True
        self._initial_rate = float(initial_rate)
        self._value_type = float

    def set_rate(self, rate):
        """Set the cell's rate of change (units per second) as a discrete event."""
        rate = float(rate)
        ja = _globals.java_actions
        if ja is not None and self._cell_index is not None:
            ja.setRate(self._cell_index, rate)


def set_value(new_value):
    return lambda x: new_value

@contextmanager
def using(cell_ref, quantity):
    cell_ref += quantity
    yield
    cell_ref -= quantity

# class FunctionalEffect:
#     def __init__(self, f):
#         self.f = f
#
#     def apply(self, state):
#         return self.f(state)
#
#     class Java:
#         implements = ["java.util.function.Function"]


def add_number(addend):
    pass
