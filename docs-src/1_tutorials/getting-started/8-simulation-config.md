# Simulation Configuration

There is often a need for certain aspects of a model to be exposed to the planner —
things like SSR capacity, sampling intervals, or initial modes that should be tweakable
without editing model source code. pymerlin exposes these as **model configuration
parameters**.

## How it works

In pymerlin, configuration parameters are simply keyword arguments on your
`@MissionModel` class's `__init__` method, **after** the required `registrar` argument.
Each extra keyword argument becomes a configuration field with its default value:

```python
from pymerlin import MissionModel, MissionModelBase

@MissionModel
class Model(MissionModelBase):
    def __init__(self, registrar,
                 ssr_max_capacity=250.0,
                 integration_sample_interval=60,
                 starting_mag_mode="OFF"):
        self.data_model = DataModel(
            registrar,
            ssr_max_capacity=ssr_max_capacity,
            integration_sample_interval=integration_sample_interval,
            starting_mag_mode=starting_mag_mode,
        )
```

When this model is packaged and uploaded to PlanDev, the configuration parameters appear
in the Simulation panel under "Arguments". Planners can change them before simulating
without re-uploading the model.

## Wiring configuration into your model

Pass the configuration values through to wherever they're used. For our `DataModel`:

```python
class DataModel:
    def __init__(self, registrar,
                 ssr_max_capacity=250.0,
                 integration_sample_interval=60,
                 starting_mag_mode="OFF"):

        self.mag_data_mode = registrar.cell(starting_mag_mode)
        registrar.resource("mag_data_mode", self.mag_data_mode)

        self.mag_data_rate = self.mag_data_mode.map(
            lambda mode: MagDataCollectionMode[mode]
        )
        registrar.resource("mag_data_rate", self.mag_data_rate)

        initial_rate = MagDataCollectionMode[starting_mag_mode] / 1.0e3
        self.recording_rate = registrar.cell(initial_rate)
        registrar.resource("recording_rate", self.recording_rate)

        self.ssr_volume = registrar.linear(
            0.0, minimum=0.0, maximum=ssr_max_capacity
        )
        registrar.resource("ssr_volume", self.ssr_volume)
```

Key points:

- `starting_mag_mode` initializes the mag mode cell **and** sets the initial recording
  rate to match — no ordering surprises.
- `ssr_max_capacity` flows directly into the `registrar.linear()` bounds.
- `integration_sample_interval` can be passed to a daemon task if you're using the
  sampling approach.

## Local simulation with configuration

When calling `simulate()` locally, configuration values come from the `__init__` defaults.
To override them, pass keyword arguments through the `config` parameter:

```python
profiles, spans, events = simulate(
    Model,
    schedule,
    "01:00:00",
    config={"ssr_max_capacity": 100.0, "starting_mag_mode": "HIGH_RATE"},
)
```

## In PlanDev

Package and upload as usual:

```shell
pymerlin package --model mission.py:Model --out mission-model.jar
```

In the PlanDev UI, select "Simulation" in the left panel dropdown. Your configuration
parameters appear under "Arguments" with type-appropriate input fields (numbers get
number inputs, strings get text inputs, etc.).

![Simulation Config](assets/Simulation_Config.png)
