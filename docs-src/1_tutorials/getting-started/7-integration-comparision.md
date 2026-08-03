# Integral Method Comparison

Now that we have explored multiple approaches to integrating `recording_rate`, let's
compare them.

## Summary

| Method | Profile shape | Accuracy | Efficiency |
|---|---|---|---|
| **1 — within activity** | flat steps | only at activity endpoints | low overhead |
| **2 — daemon sampling** | staircase | approximate (depends on interval) | many wasted points |
| **3 — rate-change reaction** | flat steps | exact at change points | fires only on change |
| **4 — linear resource** | linear ramp | exact and continuous | built-in, no user code |

## Try it in PlanDev

Package your model and upload:

```shell
pymerlin package --model mission.py:Model --out mission-model.jar
```

See the [Build a JAR guide](../../2_guides/build-jar.md) for details.

Build a plan with a few `collect_data` and `change_mag_mode` activities, simulate, and
use PlanDev's
[Timeline Editing](https://ammos.nasa.gov/aerie-docs/planning/timeline-editing/) to put
all volume resources on one row for easy visual comparison.

## Key observations

- **Method 1** (`ssr_volume_simple`) jumps at activity endpoints but misses volume from
  rate changes outside of activities (e.g. `change_mag_mode`).
- **Method 2** (`ssr_volume_sampled`) looks smooth from a distance but shows a staircase
  when you zoom in. Accuracy depends on the sampling interval.
- **Method 3** (`ssr_volume_reactive`) computes the fewest points — it fires only when
  the rate actually changes and is exact at those points.
- **Method 4** (`ssr_volume_linear`) shows a true linear ramp in the PlanDev UI. With
  `minimum`/`maximum` bounds, it automatically clamps at the SSR capacity — no overflow
  logic needed.

**Recommendation:** use `registrar.linear()` (Method 4) for any quantity that integrates
a piecewise-constant rate. It's the least code, the most accurate, and produces the best
visual in the PlanDev timeline.
