---
hide-toc: true
---

# pymerlin

pymerlin is a Python mission-modeling framework for [PlanDev](https://github.com/NASA-AMMOS/plandev/tree/pymerlin/develop/) (formerly Aerie). It lets you write PlanDev mission models in Python and either simulate them locally or package them as an uploadable mission-model JAR.

## What you can do

- **Model.** Declare cells, resources, and activities with Python decorators and a simple `Registrar` API.
- **Simulate locally.** Run `pymerlin.simulate()` for instant feedback — no Java, no deployment.
- **Package & upload.** Run `pymerlin package` to produce a JAR that uploads to PlanDev like any Java mission model.
- **Evolve state.** Use cell evolution (`evolution=`), linear resources (`registrar.linear()`), and `dynamics="real"` for continuously-varying quantities.
- **Leverage SPICE.** Compute geometry, ephemerides, and derived resources from SPICE kernels.

## How it works

The same `@MissionModel` class runs under two completely different engines:

| | Local `simulate()` | Packaged upload |
|---|---|---|
| Engine | Pure-Python (`_framework.py`) | PlanDev's real `merlin-driver` |
| Runtime | CPython on your machine | GraalPy embedded in the PlanDev worker JVM |
| Purpose | Fast logic iteration, notebooks | Production simulation in PlanDev |

When packaged, the model runs **in-process** inside the worker JVM via an embedded GraalPy interpreter — no subprocess, no wire protocol, no serialization loop. See [Architecture](architecture.md) for details.

:::{note}
pymerlin is in active development. APIs may change between versions.
:::

Ready to get started? Check out the [Quickstart](./quickstart.md) guide or dive into the [Tutorials](./1_tutorials/index.md).

## Source code

You can access the source code at: [https://github.com/mattdailis/pymerlin](https://github.com/mattdailis/pymerlin).

## How to get help, contribute, or provide feedback

See our [feedback and contribution submission guidelines](contribute.md).

```{toctree}
:hidden:

quickstart
1_tutorials/index
2_guides/index
3_explanation/index
apidocs/index
common-errors
glossary
```

```{toctree}
:caption: Development
:hidden:

architecture
shim-protocol
developer
contribute
license
publishing
documentation
```