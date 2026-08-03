# Getting Started

Welcome to pymerlin modeling! In this tutorial you will build a simple model of an
on-board spacecraft solid state recorder (SSR). You'll learn the fundamentals: cells,
resources, activities, effects, and local simulation.

## Installing pymerlin

If you haven't already, go to the [quickstart](../../quickstart.md) guide to get set up
with pymerlin.

## Creating a Mission Model

Start by creating a `mission.py` file with the following contents:

```python
from pymerlin import MissionModel, MissionModelBase

@MissionModel
class Model(MissionModelBase):
    def __init__(self, registrar):
        self.data_model = DataModel(registrar)

class DataModel:
    def __init__(self, registrar):
        pass  # YOUR CODE HERE
```

`@MissionModel` marks this class as a mission model. `MissionModelBase` is an optional
base class that helps your IDE with type checking — it tells the checker about
`activity_types` and `ActivityType`, which `@MissionModel` attaches at runtime.

## Your First Resource

We will begin building our SSR model by creating a single resource, `recording_rate`, to
track the rate at which data is being written to the SSR over time. A **Resource** is any
measurable quantity whose behavior we want to track over the course of a plan.

In the `DataModel` class, declare the `recording_rate` resource by replacing `pass` with:

```python
self.recording_rate = registrar.cell(0)  # Megabits/s
```

### Cell types

pymerlin provides several cell types, distinguished by how their value evolves between
events:

- **`registrar.cell(v)`** — a discrete cell that holds a constant value between emits
  (step function). Works with any Python type (`int`, `float`, `str`, `bool`, etc.).
- **`registrar.linear(v, rate=r)`** — a linear cell whose value ramps as
  `value + rate × elapsed_seconds` between events. Supports optional `minimum`/`maximum`
  bounds for clamping.
- **`registrar.cell(v, evolution=fn)`** — an evolving cell with a custom evolution
  function `fn(value, elapsed_duration) → new_value` that the engine calls automatically
  as simulation time advances.
- **`pymerlin.clock.clock(registrar)`** — a *factory* holding one evolving cell that
  accumulates elapsed simulation time. Call `.start()` to get a stopwatch reading zero
  from that moment; `.reset()` re-zeroes it.

Our `recording_rate` is a plain discrete cell — it stays constant until we explicitly
change it.

:::{note}
`clock(registrar)` returns a `ClockMaker`, not a cell — it has no `.get()`, and you
cannot pass it to `registrar.resource()`. Call `.start()` from **inside an activity or
task**, never from `__init__`: cells are not allocated until simulation begins, so an
early `.start()` fails with `KeyError: None`.

```python
def __init__(self, registrar):
    self.timer = clock(registrar)      # in __init__: build the factory

@Model.ActivityType
def my_activity(mission):
    clk = mission.timer.start()        # in an activity: start a stopwatch
    delay("00:05:00")
    print(clk.get())                   # +00:05:00.0000.0 (a Duration)
```

Stopwatches are cheap: every `.start()` shares the same underlying cell and only stores
an offset, so no extra cells or events are created.

To expose elapsed time as a **registered resource** instead of a stopwatch, skip
`clock()` and declare the evolving cell yourself — this is exactly what `clock()` does
internally, and it gives you a `CellRef` you can register:

```python
from pymerlin.duration import ZERO, SECONDS

self.met = registrar.cell(ZERO, evolution=lambda x, d: x + d)
registrar.resource("mission_elapsed_time", self.met.map(lambda t: t.to_number_in(SECONDS)))
```

The `.map()` projects the `Duration` to a number so the resource plots as elapsed
seconds. Register the cell directly (`registrar.resource("met", self.met)`) only if you
actually want the raw `Duration` as the profile value.
:::

### Registering a resource

The last step is to **register** the cell as a named resource so it appears in simulation
results and (when packaged) in the PlanDev UI:

```python
registrar.resource("recording_rate", self.recording_rate)
```

You can pass a `CellRef` directly to `registrar.resource()`. Behind the scenes, this
creates a resource backed by that cell.

Your `DataModel` class should now look like this:

```python
class DataModel:
    def __init__(self, registrar):
        self.recording_rate = registrar.cell(0)
        registrar.resource("recording_rate", self.recording_rate)
```

And the full file:

```python
from pymerlin import MissionModel, MissionModelBase

@MissionModel
class Model(MissionModelBase):
    def __init__(self, registrar):
        self.data_model = DataModel(registrar)

class DataModel:
    def __init__(self, registrar):
        self.recording_rate = registrar.cell(0)
        registrar.resource("recording_rate", self.recording_rate)
```

## Your First Activity

Now let's build an activity called `collect_data` that changes our resource. Activities
are the building blocks of a plan — each one represents a unit of work the spacecraft
performs.

Activity types are implemented as regular Python functions, decorated with
`@Model.ActivityType`:

```python
@Model.ActivityType
def collect_data(model):
    pass
```

The first argument is always the model instance. Additional arguments become the
activity's **parameters** — values an operator can set in the PlanDev UI.

:::{note}
pymerlin interleaves the execution of your activities using threads, so each activity is a
regular (synchronous) function. When an activity calls `delay()`, `wait_until()`, or
`call()`, the framework pauses it and lets other activities run.
:::

### Adding parameters

Let's add `rate` and `duration` parameters with default values:

```python
@Model.ActivityType
def collect_data(model, rate=0.0, duration="01:00:00"):
    pass
```

Parameters are inferred from the function signature (excluding the first `model`
argument). Their types are inferred from annotations or default values. When uploaded to
PlanDev, these become editable fields in the activity palette.

### Parameter validation

You can validate parameters inside the activity body:

```python
@Model.ActivityType
def collect_data(model, rate=0.0, duration="01:00:00"):
    if rate >= 100.0:
        raise ValueError("Collection rate is beyond buffer limit of 100.0 Mbps")
    # ... effect model ...
```

### The effect model

Now let's fill in the body. We want the recording rate to increase by `rate` for the
duration of the activity:

```python
from pymerlin.model_actions import delay
from pymerlin import Duration

@Model.ActivityType
def collect_data(model, rate=0.0, duration="01:00:00"):
    duration = Duration.from_string(duration)
    model.data_model.recording_rate += rate
    delay(duration)
    model.data_model.recording_rate -= rate
```

Here's what happens:

1. `model.data_model.recording_rate += rate` — emits an effect that increases the cell's
   value by `rate`. This happens instantly at the activity's start time.
2. `delay(duration)` — advances simulation time by the specified duration. The activity is
   paused during this time, and other activities may run.
3. `model.data_model.recording_rate -= rate` — reverses the increase. The activity then
   ends.

The `+=` and `-=` operators on cells are syntactic sugar for
`cell.emit(lambda x: x + rate)` and `cell.emit(lambda x: x - rate)`.

Ok! Now we are all set to give this a spin.

