# Scheduling

:::{note}
This guide is under construction.
:::

PlanDev includes a scheduling subsystem that can automatically place activities into a
plan based on constraints and goals. pymerlin models are compatible with PlanDev's
scheduler when the model runs on a GraalVM-based worker.

## Local scheduling with `simulate()`

For local development, you build schedules manually using `Schedule.build()`:

```python
from pymerlin import simulate, Schedule, Directive

schedule = Schedule.build(
    ("00:00:00", Directive("extend_solar_panels", {})),
    ("00:05:00", Directive("collect_data", {"data": 1024})),
    ("00:30:00", Directive("downlink", {})),
)
profiles, spans, events = simulate(Mission, schedule, "02:00:00")
```

Each entry is a tuple of `(start_time, Directive(activity_type_name, args_dict))`.
Start times are `HH:MM:SS` strings or `Duration` objects.
