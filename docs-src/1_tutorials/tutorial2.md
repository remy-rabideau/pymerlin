# Going further

:::{note}
This page is under construction.
:::

After completing the getting-started tutorial, explore these topics to build more capable
models:

- **Cell evolution** — declare evolving cells with `registrar.cell(v, evolution=fn)` so
  quantities change autonomously (e.g. thermal decay, battery integration). See
  `demo/model.py` for examples.
- **Linear resources** — use `registrar.linear(v, rate=r, minimum=..., maximum=...)` for
  quantities that ramp linearly between events (e.g. data volume, battery charge).
- **`dynamics="real"`** — mark an evolving cell as real to get sloped profile segments in
  the PlanDev UI instead of flat steps.
- **`wait_until`** — block an activity until a condition on cell values becomes true. In
  the packaged path, this uses real dependency tracking.
- **`call()` and `spawn()`** — decompose complex activities into child activities.
  `call()` blocks until the child completes; `spawn()` runs it in parallel.
- **Model configuration** — expose configuration parameters by adding keyword arguments
  to your `@MissionModel` class's `__init__` (after `registrar`).
- **SPICE integration** — use `pymerlin.spice` for geometry, ephemerides, and
  SPICE-derived resources. See `demo/aerie_orbiter_model.py`.

See the [User Guides](../2_guides/index.md) for targeted how-to articles on each topic.