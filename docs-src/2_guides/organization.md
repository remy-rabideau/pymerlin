# How to organize your project

The tutorials build a whole mission model in one file. As your model grows, you will want
to split it across multiple files. This guide covers practical patterns and pitfalls.

## Single-file models

For small models, keep everything in one file. `pymerlin package` handles single-file
models natively — no `__init__.py` needed.

## Multi-file / package models

For larger models, organize subsystems into a Python package:

```
my_model/
  __init__.py         # required! makes this a package
  model.py            # @MissionModel class
  power.py            # power subsystem class
  data.py             # data subsystem class
  activities.py       # activity definitions
```

`pymerlin package` detects the `__init__.py` and bundles the entire package directory.

### Avoiding circular imports

The most common issue with multi-file models is a circular import between the model and
its activities. The recommended pattern:

- **Activities import the model**, not the other way around.
- The model file defines the `@MissionModel` class and subsystem classes.
- Activities are in a separate file that imports the model class and decorates functions
  with `@Model.ActivityType`.
- A separate `main.py` imports both the model and activities.

```python
# model.py
from pymerlin import MissionModel, MissionModelBase

@MissionModel
class Mission(MissionModelBase):
    def __init__(self, registrar):
        self.counter = registrar.cell(0)
        registrar.resource("counter", self.counter)

# activities.py
from .model import Mission
from pymerlin.model_actions import delay

@Mission.ActivityType
def increment(mission):
    mission.counter.emit(lambda x: x + 1)
    delay("00:01:00")

# main.py
from my_model.model import Mission
import my_model.activities  # registers activity types
from pymerlin import simulate, Schedule, Directive

profiles, spans, events = simulate(
    Mission,
    Schedule.build(("00:00:00", Directive("increment", {}))),
    "01:00:00"
)
```

## Subsystem decomposition

The demo model (`demo/model.py`) shows one effective pattern: subsystem state is declared
in a plain class that takes the `Registrar`, and the top-level `@MissionModel` class
instantiates each subsystem. Activities reference the subsystem through the model
instance. This keeps each subsystem's cells, resources, and helper functions together
without a circular-import risk.