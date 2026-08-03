# Modeling Data Volume

:::{note}
This guide is under construction. The patterns below are drawn from the demo models
shipped with pymerlin.
:::

Data volume — how much data has been recorded, how much is buffered onboard, how much has
been downlinked — is one of the most common quantities to track in a mission model.

## Linear resource (simplest)

For a buffer that fills at a constant rate during collection and drains during downlink,
`registrar.linear()` is the simplest option:

```python
self.data_volume_mb = registrar.linear(0.0)
```

Activities set the rate:

```python
@Mission.ActivityType
def collect_data(mission, rate_mbps=10.0):
    mission.data_volume_mb.set_rate(rate_mbps)
    delay("00:05:00")
    mission.data_volume_mb.set_rate(0.0)
```

For bounded buffers (e.g. a finite SSR), use bounds:

```python
self.data_volume_mb = registrar.linear(0.0, minimum=0.0, maximum=4096.0)
```

## Evolving cell (custom integration)

For nonlinear or multi-component integration, use an evolving cell with a tuple
`(value, rate)`:

```python
def _integrate(state, elapsed):
    value, rate = state
    return value + rate * elapsed.to_number_in(SECONDS), rate

self.data = registrar.cell((0.0, 0.0), evolution=_integrate, dynamics="real")
```

See `demo/model.py` and `demo/aerie_orbiter_model.py` for full working examples of both
approaches.