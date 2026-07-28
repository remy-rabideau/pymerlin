"""
Tests for cell evolution (cell-evolution roadmap, step 8).

These tests run against the standalone Python simulation framework (_framework.py).
Java-side tests require a running Aerie worker and are out of scope here.
"""

from pymerlin import MissionModel
from pymerlin import simulate
from pymerlin._internal._registrar import Registrar
from pymerlin._internal._schedule import Directive, Schedule
from pymerlin._internal._server import _ModelState, _parse_value
from pymerlin.clock import clock
from pymerlin.duration import ZERO, Duration, SECONDS
from pymerlin.model_actions import delay

# ---------------------------------------------------------------------------
# Evolution functions
# ---------------------------------------------------------------------------

def constant_slope(rate):
    """Evolution: value increases linearly at `rate` per second."""
    def evolution(value, d):
        return value + rate * d.to_number_in(SECONDS)
    return evolution


def linear_evolution(x, d):
    """Evolution for (value, rate) tuples: value integrates by rate * dt."""
    value, rate = x
    return value + rate * d.to_number_in(SECONDS), rate


# ---------------------------------------------------------------------------
# Test models
# ---------------------------------------------------------------------------

@MissionModel
class SimpleEvolvingModel:
    def __init__(self, registrar: Registrar):
        self.counter = registrar.cell(0.0, evolution=constant_slope(1))
        registrar.resource("counter", self.counter.get)


@MissionModel
class TupleEvolvingModel:
    def __init__(self, registrar: Registrar):
        self.state = registrar.cell((0.0, 2.0), evolution=linear_evolution)
        registrar.resource("state", self.state.get)


@MissionModel
class MixedModel:
    """Model with both evolving and non-evolving cells."""
    def __init__(self, registrar: Registrar):
        self.evolving = registrar.cell(0.0, evolution=constant_slope(10))
        self.discrete = registrar.cell(0)
        self.clk = clock(registrar)
        registrar.resource("evolving", self.evolving.get)
        registrar.resource("discrete", self.discrete.get)


# ---------------------------------------------------------------------------
# Tests — basic evolution
# ---------------------------------------------------------------------------

def test_evolution_value_at_known_time():
    """An evolving cell with rate=1/s should equal 60 after 60 seconds."""

    @SimpleEvolvingModel.ActivityType
    def check_value(mission: SimpleEvolvingModel):
        assert mission.counter.get() == 0.0
        delay(Duration.of(60, SECONDS))
        assert mission.counter.get() == 60.0

    simulate(
        SimpleEvolvingModel,
        Schedule.build(("00:00:00", Directive("check_value", {}))),
        "00:05:00",
    )


def test_evolution_profile_segments():
    """Evolution should produce correct profile segment values."""
    profiles, _spans, _events = simulate(
        SimpleEvolvingModel,
        Schedule.empty(),
        "00:01:00",
    )
    # One segment covering the full minute; dynamics is the value at segment start (0.0)
    assert "counter" in profiles
    segs = profiles["counter"]
    assert len(segs) == 1
    assert segs[0].dynamics == 0.0
    assert segs[0].extent == Duration.of(60, SECONDS)


def test_evolution_tuple_state():
    """Tuple-based evolution: (value, rate) should integrate correctly."""

    @TupleEvolvingModel.ActivityType
    def check_tuple(mission: TupleEvolvingModel):
        v0, r0 = mission.state.get()
        assert v0 == 0.0
        assert r0 == 2.0
        delay(Duration.of(30, SECONDS))
        v1, r1 = mission.state.get()
        assert v1 == 60.0  # 2.0 * 30s
        assert r1 == 2.0

    simulate(
        TupleEvolvingModel,
        Schedule.build(("00:00:00", Directive("check_tuple", {}))),
        "00:05:00",
    )


# ---------------------------------------------------------------------------
# Tests — evolution + mid-sim emit
# ---------------------------------------------------------------------------

def test_evolution_after_emit():
    """After an explicit emit resets the cell, evolution should continue from the new value."""

    @SimpleEvolvingModel.ActivityType
    def reset_and_check(mission: SimpleEvolvingModel):
        delay(Duration.of(10, SECONDS))
        assert mission.counter.get() == 10.0
        mission.counter.set(100.0)
        assert mission.counter.get() == 100.0
        delay(Duration.of(5, SECONDS))
        assert mission.counter.get() == 105.0  # 100 + 1*5

    simulate(
        SimpleEvolvingModel,
        Schedule.build(("00:00:00", Directive("reset_and_check", {}))),
        "00:05:00",
    )


def test_evolution_profile_after_emit():
    """Profile segments should reflect evolution and emits at correct times."""

    @SimpleEvolvingModel.ActivityType
    def emit_midway(mission: SimpleEvolvingModel):
        delay(Duration.of(30, SECONDS))
        mission.counter.set(1000.0)
        delay(Duration.of(30, SECONDS))

    profiles, _spans, _events = simulate(
        SimpleEvolvingModel,
        Schedule.build(("00:00:00", Directive("emit_midway", {}))),
        "00:01:00",
    )
    segs = profiles["counter"]
    # Segment 1: 0s–30s, dynamics is the value at t=0 (0.0)
    assert segs[0].dynamics == 0.0
    assert segs[0].extent == Duration.of(30, SECONDS)
    # Segment 2: 30s–60s, dynamics is the evolved value at t=30s (30.0).
    # The emit(1000.0) happens mid-tick after the snapshot; the framework
    # captures the pre-emit evolved value for the segment start.
    assert segs[1].dynamics == 30.0
    assert segs[1].extent == Duration.of(30, SECONDS)


# ---------------------------------------------------------------------------
# Tests — mixed evolving and non-evolving cells
# ---------------------------------------------------------------------------

def test_mixed_model_evolution():
    """Non-evolving cells should be unaffected by evolution stepping."""

    @MixedModel.ActivityType
    def check_mixed(mission: MixedModel):
        assert mission.evolving.get() == 0.0
        assert mission.discrete.get() == 0
        delay(Duration.of(10, SECONDS))
        assert mission.evolving.get() == 100.0  # 10 * 10s
        assert mission.discrete.get() == 0  # unchanged
        mission.discrete.set(42)
        delay(Duration.of(5, SECONDS))
        assert mission.evolving.get() == 150.0  # 10 * 15s
        assert mission.discrete.get() == 42  # still 42

    simulate(
        MixedModel,
        Schedule.build(("00:00:00", Directive("check_mixed", {}))),
        "00:05:00",
    )


# ---------------------------------------------------------------------------
# Tests — clock.py evolution
# ---------------------------------------------------------------------------

def test_clock_evolution():
    """Verify that clock.py's evolution function works correctly."""

    @MixedModel.ActivityType
    def check_clock(mission: MixedModel):
        c = mission.clk.start()
        assert c.get() == Duration.ZERO
        delay(Duration.of(9, SECONDS))
        assert c.get() == Duration.of(9, SECONDS)
        c.reset()
        assert c.get() == Duration.ZERO
        delay(Duration.of(5, SECONDS))
        assert c.get() == Duration.of(5, SECONDS)

    simulate(
        MixedModel,
        Schedule.build(("00:00:00", Directive("check_clock", {}))),
        "00:05:00",
    )


# ---------------------------------------------------------------------------
# Tests — Java-backed initialization contract
#
# These cover the _server.py side of what the Java shim calls at instantiate() time.
# They are plain Python (no GraalPy host needed) but they pin the exact contract whose
# violation produced, in a real simulation:
#     ValueError: too many values to unpack (expected 2)  in _energy_evolution
# ---------------------------------------------------------------------------

def test_parse_value_requires_typed_reference():
    """_parse_value dispatches on `reference`'s runtime TYPE, not on the string.

    Passing the value string as its own reference (which the Java shim briefly did when
    allocating evolving cells) makes every isinstance branch fall through to the str
    fallback, silently stringifying the cell.
    """
    # Correct: a real tuple reference round-trips to a tuple.
    assert _parse_value("(1.5, 2.0)", (0.0, 0.0)) == (1.5, 2.0)
    assert _parse_value("3.5", 0.0) == 3.5
    assert _parse_value("7", 0) == 7

    # The regression: string-as-its-own-reference yields a str, NOT a tuple.
    assert _parse_value("(0.0, 0.0)", "(0.0, 0.0)") == "(0.0, 0.0)"


def test_get_initial_values_preserves_python_types():
    """get_initial_values() must hand Java live Python objects, not str(value).

    describe_cells()'s `initial` is str(value) and cannot be converted back to a tuple
    or Duration without knowing the target type, so evolving cells are allocated from
    these objects instead. If this regresses to strings, a tuple-valued evolving cell
    fails on its first step() with "too many values to unpack".
    """

    @MissionModel
    class TypedModel:
        def __init__(self, registrar: Registrar):
            self.scalar = registrar.cell(0.0, evolution=constant_slope(1))
            self.pair = registrar.cell((0.0, 2.0), evolution=linear_evolution)
            self.clk = clock(registrar)

    state = _ModelState(TypedModel, {})
    initials = state.get_initial_values()
    cells = state.describe_cells()
    evolutions = state.get_evolution_functions()

    # One entry per cell, in registrar.cells order -- the index is the Python/Java contract.
    assert len(initials) == len(cells) == len(evolutions)

    by_type = {type(v) for v in initials}
    assert tuple in by_type, f"tuple initial was not preserved: {initials}"
    assert float in by_type, f"float initial was not preserved: {initials}"
    assert Duration in by_type, f"Duration initial was not preserved: {initials}"

    # Nothing may arrive as the repr of itself.
    assert "(0.0, 2.0)" not in initials


def test_evolution_functions_accept_their_initial_values():
    """Each evolution function must survive being called with its own initial value.

    This is exactly the first thing the engine does on a time advance, and it is where
    the stringified-tuple bug surfaced -- so drive it directly rather than trusting that
    the types merely look right.
    """

    @MissionModel
    class TypedModel:
        def __init__(self, registrar: Registrar):
            self.scalar = registrar.cell(0.0, evolution=constant_slope(1))
            self.pair = registrar.cell((0.0, 2.0), evolution=linear_evolution)
            self.clk = clock(registrar)

    state = _ModelState(TypedModel, {})
    initials = state.get_initial_values()

    # get_evolution_functions() wraps each user fn to take (value, elapsed_micros),
    # which is the signature Java's CellType.step() actually calls.
    for initial, wrapped in zip(initials, state.get_evolution_functions()):
        if wrapped is None:
            continue
        result = wrapped(initial, 1_000_000)  # advance one second
        assert type(result) is type(initial), (
            f"evolution changed value type: {type(initial)} -> {type(result)}")

    # And concretely, one second of the (value, rate) pair integrates by its rate.
    pair_initial = next(v for v in initials if isinstance(v, tuple))
    pair_fn = next(
        w for v, w in zip(initials, state.get_evolution_functions())
        if isinstance(v, tuple) and w is not None)
    assert pair_fn(pair_initial, 1_000_000) == (2.0, 2.0)


def test_clock_initial_value_is_a_duration():
    """clock()'s cell starts at Duration.ZERO; its evolution adds elapsed time.

    Duration is the case a str round-trip cannot rescue at all -- there is no parse
    branch for it in _parse_value -- so it depends entirely on get_initial_values()
    handing over the real object.
    """

    @MissionModel
    class ClockOnly:
        def __init__(self, registrar: Registrar):
            self.clk = clock(registrar)

    state = _ModelState(ClockOnly, {})
    (initial,) = state.get_initial_values()
    (wrapped,) = state.get_evolution_functions()

    assert initial == ZERO
    assert isinstance(initial, Duration)
    assert wrapped(initial, 5_000_000) == Duration.of(5, SECONDS)


# ---------------------------------------------------------------------------
# Tests — derived resources over evolving cells
#
# A resource declared as cell.map(fn) must still be traceable to its backing cell.
# The Java path registers resources PER CELL, so a resource whose cell cannot be
# identified is never created at all -- it vanishes from the simulation silently
# rather than failing loudly, which is how /temperature_c disappeared once it
# became a tuple-valued cell exposed through a projection.
# ---------------------------------------------------------------------------

def test_mapped_resource_is_associated_with_its_cell():
    """cell.map(...) resources must resolve to a cell in describe_cells()."""

    @MissionModel
    class MappedModel:
        def __init__(self, registrar: Registrar):
            self.pair = registrar.cell((5.0, 0.0), evolution=linear_evolution)
            registrar.resource("/first", self.pair.map(lambda s: s[0]))

    state = _ModelState(MappedModel, {})
    described = {c.get("resource") for c in state.describe_cells()}
    assert "/first" in described, (
        "mapped resource lost its backing cell; Java would never register it")


def test_mapped_resource_types_from_projected_value():
    """The resource's declared type comes from what it PUBLISHES, not the raw cell.

    A cell holding a tuple whose projection yields a float must be declared "float";
    typing it off the tuple would declare a string resource in Aerie.
    """

    @MissionModel
    class MappedModel:
        def __init__(self, registrar: Registrar):
            self.pair = registrar.cell((5.0, 0.0), evolution=linear_evolution)
            registrar.resource("/first", self.pair.map(lambda s: s[0]))

    state = _ModelState(MappedModel, {})
    (cell,) = [c for c in state.describe_cells() if c.get("resource") == "/first"]
    assert cell["type"] == "float", f'expected float, got {cell["type"]}'
    assert cell.get("evolving") is True


def test_resource_projection_extracts_published_value():
    """get_resource_projections() returns raw-value -> published-string functions."""

    @MissionModel
    class MappedModel:
        def __init__(self, registrar: Registrar):
            self.plain = registrar.cell(1.0)
            self.pair = registrar.cell((5.0, 2.0), evolution=linear_evolution)
            registrar.resource("/plain", self.plain)
            registrar.resource("/first", self.pair.map(lambda s: s[0]))

    state = _ModelState(MappedModel, {})
    cells = state.describe_cells()
    projections = state.get_resource_projections()
    assert len(projections) == len(cells)

    by_resource = {
        c.get("resource"): p for c, p in zip(cells, projections)}

    # A bare cell resource publishes its value as-is -- no projection needed.
    assert by_resource["/plain"] is None
    # A mapped one projects the tuple down to its first element.
    assert by_resource["/first"]((7.5, 2.0)) == "7.5"


def test_unmapped_cell_resource_still_works():
    """Registering a bare cell (no .map) must keep working -- the common case."""

    @MissionModel
    class PlainModel:
        def __init__(self, registrar: Registrar):
            self.temp = registrar.cell(0.0, evolution=constant_slope(1))
            registrar.resource("/temp", self.temp)

    state = _ModelState(PlainModel, {})
    (cell,) = [c for c in state.describe_cells() if c.get("resource") == "/temp"]
    assert cell["type"] == "float"
    assert cell.get("evolving") is True


# ---------------------------------------------------------------------------
# Tests — value types across the Java boundary
#
# The standalone framework keeps live Python objects, so it cannot catch types that
# break only when marshalled. These simulate the Java path with a fake java_actions
# to pin the contract that a Duration-valued evolving cell (pymerlin.clock) is read
# and written as an OBJECT, never via str().
#
# Regression: clock.start() does `-self._system_clock.get()`, which raised
#   TypeError: bad operand type for unary-: 'str'
# once the value came back as "+00:00:00.0000.0" instead of a Duration.
# ---------------------------------------------------------------------------

class _FakeJavaActions:
    """Minimal stand-in for the Java host object, storing live Python objects."""

    def __init__(self, values):
        self._values = list(values)
        self.ask_calls = 0
        self.ask_object_calls = 0

    def ask(self, index):
        self.ask_calls += 1
        return str(self._values[index])

    def askObject(self, index):
        self.ask_object_calls += 1
        return self._values[index]

    def emitCell(self, index, value_str):
        self._values[index] = value_str

    def emitCellObject(self, index, value):
        self._values[index] = value


def _with_fake_java(values):
    from pymerlin._internal import _globals
    fake = _FakeJavaActions(values)
    _globals.java_actions = fake
    return fake


def _clear_fake_java():
    from pymerlin._internal import _globals
    _globals.java_actions = None


def test_evolving_duration_cell_reads_as_object():
    """A Duration-valued evolving cell must not come back as a string.

    str(Duration) is "+00:00:00.0000.0", and Duration.from_string cannot parse it, so
    the string path is unrecoverable -- clock.start()'s unary minus fails on it.
    """
    from pymerlin.duration import ZERO, Duration, SECONDS

    registrar = Registrar()
    cell = registrar.cell(ZERO, evolution=lambda x, d: x + d)
    cell._cell_index = 0
    cell._value_type = Duration

    fake = _with_fake_java([Duration.of(9, SECONDS)])
    try:
        value = cell.get()
        assert isinstance(value, Duration), f"got {type(value).__name__}: {value!r}"
        assert value == Duration.of(9, SECONDS)
        # Unary minus is exactly what clock.start() does; a str would raise TypeError.
        assert -value == Duration.of(-9, SECONDS)
        assert fake.ask_object_calls == 1
    finally:
        _clear_fake_java()


def test_evolving_duration_cell_writes_as_object():
    """Writing a Duration-valued evolving cell keeps it a Duration, not str()."""
    from pymerlin.duration import ZERO, Duration, SECONDS

    registrar = Registrar()
    cell = registrar.cell(ZERO, evolution=lambda x, d: x + d)
    cell._cell_index = 0
    cell._value_type = Duration

    fake = _with_fake_java([ZERO])
    try:
        cell.emit(Duration.of(30, SECONDS))
        stored = fake._values[0]
        assert isinstance(stored, Duration), f"stored as {type(stored).__name__}: {stored!r}"
        assert stored == Duration.of(30, SECONDS)
    finally:
        _clear_fake_java()


def test_non_evolving_cell_still_uses_string_path():
    """Only evolving cells take the object path; discrete cells are unchanged."""
    registrar = Registrar()
    cell = registrar.cell(1.5)
    cell._cell_index = 0
    cell._value_type = float

    fake = _with_fake_java([2.5])
    try:
        assert cell.get() == 2.5
        assert fake.ask_calls == 1
        assert fake.ask_object_calls == 0
    finally:
        _clear_fake_java()


# ---------------------------------------------------------------------------
# Tests — bounded linear cells (Aerie ClampedIntegrator equivalent)
#
# A linear cell integrates value + rate*t with no bounds by default. For a
# physically bounded quantity that is wrong: a battery at 100% with the solar
# panels generating a surplus keeps climbing to 130%, 200%, ...
# ---------------------------------------------------------------------------

def test_linear_cell_bounds_are_described():
    """minimum/maximum reach Java through describe_cells()."""

    @MissionModel
    class BoundedModel:
        def __init__(self, registrar: Registrar):
            self.battery = registrar.linear(100.0, minimum=0.0, maximum=100.0)
            registrar.resource("/battery", self.battery)

    state = _ModelState(BoundedModel, {})
    (cell,) = [c for c in state.describe_cells() if c.get("resource") == "/battery"]
    assert cell["type"] == "linear"
    assert cell["minimum"] == "0.0"
    assert cell["maximum"] == "100.0"


def test_linear_cell_without_bounds_stays_unbounded():
    """Bounds are opt-in: an unbounded cell must not gain them implicitly.

    Cumulative counters (data volume, total energy) are legitimately unbounded, and
    silently clamping them would be a worse bug than the one bounds fix.
    """

    @MissionModel
    class UnboundedModel:
        def __init__(self, registrar: Registrar):
            self.buffer = registrar.linear(0.0)
            registrar.resource("/buffer", self.buffer)

    state = _ModelState(UnboundedModel, {})
    (cell,) = [c for c in state.describe_cells() if c.get("resource") == "/buffer"]
    assert "minimum" not in cell
    assert "maximum" not in cell


def test_linear_cell_accepts_one_sided_bounds():
    """minimum and maximum are independent -- a floor without a ceiling is valid."""

    @MissionModel
    class FloorModel:
        def __init__(self, registrar: Registrar):
            self.tank = registrar.linear(50.0, minimum=0.0)
            registrar.resource("/tank", self.tank)

    state = _ModelState(FloorModel, {})
    (cell,) = [c for c in state.describe_cells() if c.get("resource") == "/tank"]
    assert cell["minimum"] == "0.0"
    assert "maximum" not in cell
