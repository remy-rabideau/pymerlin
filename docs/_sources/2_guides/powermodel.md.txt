# Modeling Power

:::{note}
This guide is under construction. The patterns below are drawn from the demo models
shipped with pymerlin.
:::

Power modeling typically involves tracking power draw, solar generation, and battery state
of charge.

## Basic pattern

Track total power draw as a discrete cell, solar generation as another, and derive the
battery from the net balance:

```python
self.power_w = registrar.cell(0.0)
self.solar_generation_w = registrar.cell(0.0)
self.battery_pct = registrar.linear(100.0, minimum=0.0, maximum=100.0)
```

Activities state what they draw and call a recompute helper:

```python
@Mission.ActivityType
def collect_data(mission):
    mission.power_w.emit(lambda x: x + 15.0)
    _recompute_power(mission)
    delay("00:05:00")
    mission.power_w.emit(lambda x: x - 15.0)
    _recompute_power(mission)

def _recompute_power(mission):
    net_w = mission.solar_generation_w.get() - mission.power_w.get()
    battery_capacity_wh = 100.0
    mission.battery_pct.set_rate((net_w / battery_capacity_wh) * (100.0 / 3600.0))
```

This pattern — activities state only their draw, and a single recompute function
re-derives all consequences — prevents the common bug where a 25 W downlink is
supposed to generate heat but the activity forgot to update the thermal resource.

## Thermal coupling

Waste heat from power draw can drive a thermal model via cell evolution. See
`demo/model.py` for the `_thermal_evolution` and `_recompute_heat_input` pattern, where
temperature evolves autonomously toward an equilibrium determined by current power draw.

## Power Equipment List (PEL)

For missions with many components, use a state-table approach: each component is a
discrete cell holding a state name, and a lookup table maps states to (CBE, MEV) power
draws. See the `PELModel` class in `demo/aerie_orbiter_model.py` for a full
implementation.