# Monte-Carlo analyses

:::{note}
This guide is under construction.
:::

It's sometimes useful to run many simulations with variations in inputs to gain insight
into the variance in system behavior that may arise from perturbations in its inputs.

Because `pymerlin.simulate()` is a regular Python function, you can call it in a loop
with randomized parameters:

```python
import random
from pymerlin import simulate, Schedule, Directive

results = []
for _ in range(100):
    rate = random.gauss(10.0, 2.0)
    schedule = Schedule.build(
        ("00:00:00", Directive("collect_data", {"rate": rate})),
    )
    profiles, spans, events = simulate(Mission, schedule, "01:00:00")
    results.append((rate, profiles))
```

This is purely local — no PlanDev deployment needed. For large-scale Monte Carlo, consider
parallelizing with `multiprocessing` or `concurrent.futures`.
