# Planning and Scheduling

:::{note}
This page is under construction.
:::

Planning and scheduling is the process of deciding *what* activities to execute, *when*
to execute them, and verifying that the resulting plan satisfies all constraints.

## pymerlin's role

pymerlin is a **modeling and simulation** framework, not a planner or scheduler. Its job
is to evaluate a given plan (a set of directives) against a mission model and produce
profiles and spans — the simulation results that planners use to make decisions.

PlanDev provides scheduling capabilities on top of Merlin's simulation engine. When you
upload a pymerlin model to PlanDev, the scheduler can use it to automatically place
activities based on constraints and goals.

## Local simulation

For local development, you build schedules manually:

```python
from pymerlin import simulate, Schedule, Directive

schedule = Schedule.build(
    ("00:00:00", Directive("activity_a", {})),
    ("01:00:00", Directive("activity_b", {"param": 42})),
)
profiles, spans, events = simulate(Mission, schedule, "04:00:00")
```

This lets you iterate on model logic without a deployed PlanDev instance.