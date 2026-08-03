# Quickstart

## Prerequisites

- **Python >= 3.10** — to author models and run local simulations.
- **Java >= 21** — only needed if you are building the Java shim JAR from source (`./gradlew`). **Not** required to write models, run `simulate()`, or run `pymerlin package`.

You do **not** need Java or GraalPy on your development machine. pymerlin's `simulate()` is
a pure-Python engine. Java and GraalPy only enter the picture when a packaged model runs
inside a deployed PlanDev worker — see [Architecture](architecture.md) — and that runtime is
supplied by the worker image, not your laptop.

## Installation

Create and activate a [Python virtual environment](https://docs.python.org/3/library/venv.html) for your project:

```shell
python3 -m venv venv
source venv/bin/activate    # macOS / Linux
# venv\Scripts\activate     # Windows
```

Install `pymerlin` from the GitHub repo (replace `v0.2.0` with the desired tag):

```shell
pip install "pymerlin @ git+https://github.com/remy-rabideau/pymerlin.git@v0.2.0#subdirectory=pymerlin"
```

This installs pymerlin and all its dependencies (`spiceypy`, `bokeh`, `numpy`).

## Verify installation

```shell
python3 -c "import pymerlin; pymerlin.checkout()"
```

If you see `pymerlin checkout successful: All systems GO 🚀`, you're ready to go.

## Next steps

- **[Tutorial](1_tutorials/getting-started/index.md)** — build a solid-state recorder model from scratch.
- **[Packaging guide](2_guides/build-jar.md)** — turn your model into a PlanDev-uploadable JAR.
- **[Architecture](architecture.md)** — understand the dual-engine design.