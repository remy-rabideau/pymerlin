# How to use pymerlin in Jupyter

pymerlin's local `simulate()` engine runs entirely in Python, so it works naturally in
Jupyter notebooks. This makes notebooks ideal for rapid model iteration and visualization.

## Setup

Install pymerlin into your notebook environment (bokeh and numpy are included):

```shell
pip install "pymerlin @ git+https://github.com/remy-rabideau/pymerlin.git@v0.2.1#subdirectory=pymerlin"
```

## Running a simulation

```python
from pymerlin import MissionModel, MissionModelBase, simulate, Schedule, Directive

@MissionModel
class Model(MissionModelBase):
    def __init__(self, registrar):
        self.counter = registrar.cell(0)
        registrar.resource("counter", self.counter)

@Model.ActivityType
def increment(mission):
    from pymerlin.model_actions import delay
    mission.counter.emit(lambda x: x + 1)
    delay("00:01:00")

profiles, spans, events = simulate(
    Model,
    Schedule.build(("00:00:00", Directive("increment", {}))),
    "01:00:00"
)
```

## Plotting results

pymerlin ships Bokeh-based plotting helpers in `pymerlin._internal._plot`:

```python
from pymerlin._internal._plot import plot_profiles, plot_spans
from bokeh.io import output_notebook, show

output_notebook()
show(plot_profiles(profiles))
show(plot_spans(spans))
```

:::{note}
The plotting helpers require the `plotting` extra (`bokeh` and `numpy`). They are
imported lazily — if you don't need plots, you don't need these dependencies.
:::