# Build a PlanDev-compatible JAR file

`pymerlin package` turns a Python model into a mission-model JAR you can upload to a deployed
PlanDev instance. The model then runs **in-process** inside the PlanDev worker via an embedded
GraalPy interpreter — no subprocess, no separate Python install on the worker. See
[Architecture](../architecture.md) for how that works.

## Package the model

```shell
pymerlin package --model path/to/model.py:MissionClassName --out mission-model.jar
```

This copies the prebuilt shim JAR (`pymerlin-shim.jar`, shipped inside the `pymerlin` pip
package), bundles your model source into it under `pymerlin_models/`, and stamps the model
reference into the JAR manifest as `Pymerlin-Model-Ref`. If your model file sits next to an
`__init__.py`, the whole package directory is bundled so intra-package imports keep working.

You don't need Java installed to run `pymerlin package` — it ships a prebuilt shim.

## Upload it

Follow the PlanDev docs to
[upload the mission model](https://nasa-ammos.github.io/aerie-docs/planning/upload-mission-model/),
give it a name and version, then
[create a plan and simulate](https://nasa-ammos.github.io/aerie-docs/planning/create-plan-and-simulate/).

## What the worker must provide

The JAR carries only the shim, its `gson` dependency, and your model source — **not** the
Python runtime or any Python packages. The GraalPy runtime and stdlib come from the PlanDev
worker image, which also ships a pre-built virtual environment containing `pymerlin`, `numpy`,
and `spiceypy`.

Any **additional** packages the model imports are handled automatically: `pymerlin package`
detects third-party imports in your model source, pins them to the versions installed in your
local environment, and bundles the result as a `requirements.txt` inside the JAR. When the
model JAR is **uploaded** to PlanDev, the worker pip-installs any missing packages from that
file into the GraalPy venv — so by the time a simulation runs, every dependency is already
present. This means:

- You don't need an image rebuild just because your model imports a new pure-Python package.
- Versions are pinned to what you had when you ran `pymerlin package`.
- If the package requires native compilation and the worker image lacks the toolchain, the
  install will fail at upload time (not silently at simulation time).

Use `--no-requirements` to skip auto-detection if you know the worker image already has
everything you need.

## Testing without uploading

You don't need a JAR (or a deployed PlanDev) to exercise a model during development: pymerlin's
pure-Python [`simulate()`](../1_tutorials/getting-started/2-model-test-drive.md) runs the same
`@MissionModel` class locally. Package and upload when you want the model in a real PlanDev plan;
use `simulate()` for fast iteration.
