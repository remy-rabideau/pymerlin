# Glossary

% Entries should be sorted alphabetically, cross-reference other entries, and end in periods.

:::{glossary}
Activity Type
  A named action or behavior of the system. In pymerlin, defined by decorating a function
  with `@Model.ActivityType`. Parameters come from the function's signature (minus the
  first argument, the model instance).

Cell
  A container for a piece of simulation state. To guarantee correctness of simulation
  results, all mutable state must be tracked in cells. Declared via `registrar.cell()` or
  `registrar.linear()`.

Cell Evolution
  An optional function `fn(current_value, elapsed_duration) -> new_value` attached to a
  cell via `registrar.cell(v, evolution=fn)`. The engine calls it automatically as
  simulation time advances, so quantities can change without an activity driving them
  (e.g. thermal decay, battery integration).

Daemon Task
  A task that is spawned during mission model initialization, meaning it starts execution
  at the beginning of simulation rather than as a result of a Directive.

Directive
  A request to instantiate a certain Activity Type with certain arguments at a certain time.

Effect
  An action that changes the value of a cell. Effects are defined as functions from an old
  value to a new value — e.g. `cell.emit(lambda x: x + 1)`.

Effect Model
  The body of the function describing an activity's behavior during simulation.

GraalPy
  The GraalVM Python implementation that runs pymerlin models in-process inside the PlanDev
  worker JVM. Not used for local `simulate()` (which runs on CPython).

Linear Cell
  A cell declared via `registrar.linear()` whose value ramps as
  `value + rate × elapsed_seconds` between events. Backed by Aerie's `RealDynamics`
  resource type. Supports optional `minimum`/`maximum` bounds.

Merlin
  The modeling and simulation component of PlanDev (Aerie).

Mission Model
  A `@MissionModel`-decorated Python class that declares cells, resources, and activity
  types via a `Registrar`. The same class runs under both the local `simulate()` engine
  and the packaged PlanDev path.

Model Configuration
  Parameters exposed to planners in the PlanDev UI. Defined by the `__init__` signature of
  a `@MissionModel` class — every parameter after `registrar` becomes a configuration field.

PlanDev
  A suite of planning and scheduling, modeling and simulation, constraint checking and
  sequencing tools (fork of NASA AMMOS Aerie).

Profile
  A piecewise-defined function from time to a value, recording how a resource changes over
  the course of a simulation.

Profile Segment
  A single piece of a Profile with a start and end time. Profile segments within one
  profile must be contiguous and non-overlapping. May be discrete (flat value) or real
  (value + slope).

Registrar
  An object provided to the mission model at initialization time. The model uses it to
  declare cells (`registrar.cell()`, `registrar.linear()`) and register resources
  (`registrar.resource()`).

Resolution
  The maximum time the engine may let pass before re-sampling an evolving cell. Only
  affects how the resource profile is recorded — reads are always exact.

Resource
  A named, measurable quantity whose behavior is tracked over the course of a plan. Published
  via `registrar.resource(name, cell_or_getter)` and backed by exactly one cell.

Span
  A component of simulation results representing a start time, a duration, an optional
  parent, and some metadata. Each activity execution produces one span.
:::

## Other terminology:
There are some general terms that PlanDev uses very specifically, that may not be worth a glossary entry, but do deserve
some attention.

### Parameters vs Arguments
Take as an example the `add1` function below:
```python
def add1(x):
    return x + 1
```
and this invocation of the add function:
```python
add1(5)
```
In this example, `x` is a parameter of the `add1` function, while `5` is an argument to the invocation of the `add1` 
function.

### Register vs Registrar
A `Register` is an analogy to a [processor register](https://en.wikipedia.org/wiki/Processor_register), which can store
a single value at a time, and supports the `get` operation to read that value, and the `set` operation to overwrite the
entire value.

A `Registrar` means a "record-keeper" - you can "register" (verb) something with a registrar. In `pymerlin`, the
registrar is an object provided to the mission model at initialization time, which the model can use to register
its cells and resources.

% You can use {term}`MyST` to create glossaries.)