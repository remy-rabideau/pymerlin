# pymerlin

<!-- start elevator-pitch -->
pymerlin is a Python mission modeling framework for the [PlanDev](https://github.com/NASA-AMMOS/aerie) discrete-event simulation ecosystem. It lets you write PlanDev mission models in Python and either simulate them locally or package them as an uploadable PlanDev mission model JAR.

To learn more about PlanDev, read the [PlanDev Docs](https://nasa-ammos.github.io/aerie-docs).
<!-- end elevator-pitch -->

## Prerequisites

- Python >= 3.10 — to author models and run `pymerlin package`.
- Java >= 21 — only for building the shim JAR from source (`./gradlew`); **not** needed to
  author a model or to run `pymerlin package`, which ships a prebuilt shim JAR.

At simulation time the model runs on the GraalPy interpreter that the PlanDev worker image
provides (see [Worker-image contract](#worker-image-contract)) — not on your local CPython.

## Installation

```shell
python -m venv venv
source ./venv/bin/activate
pip install -r requirements.txt
```

Or install from the package directly:

```shell
pip install pymerlin
```

## Packaging a model for PlanDev

To produce a PlanDev-uploadable mission model JAR from a Python model:

```shell
pymerlin package --model path/to/model.py:MissionClassName --out mission-model.jar
```

This copies the prebuilt shim JAR (`pymerlin-shim.jar`), bundles your Python model source
into it under `pymerlin_models/`, and stamps the model reference into the JAR manifest as
`Pymerlin-Model-Ref`. If the model file sits next to an `__init__.py`, the whole package
directory is bundled so intra-package imports keep working. The resulting JAR uploads to a
deployed PlanDev instance like any Java mission model.

Packaging also reads the model's own `import` statements and writes what it finds into the
JAR as `pymerlin_requirements.txt`, pinned to the versions installed alongside the model:

```shell
pymerlin package --model demo/model.py:Mission --out mission-model.jar
# [pymerlin] Preinstalled:    pymerlin (already in the worker image)
# [pymerlin] Requirements:    generated from the model's imports
# [pymerlin]                  toml==0.10.2
# [pymerlin] Bundled:         pymerlin_requirements.txt
```

Those imports *are* the dependency declaration: there is nothing to write by hand, and a
`requirements.txt` sitting next to your model is **not** read. `--no-requirements` is the
only opt-out, and it ships a model that gets whatever the worker image already has and
nothing else. See [Model dependencies](#model-dependencies) for what the worker does with
that file, and for what import scanning cannot see.

The JAR carries no Python: it holds the shim classes and the Java libraries they need
(`gson` for Java↔Python argument/description marshalling, `merlin-framework` and its Apache
Commons transitives), your model source, and that requirements file. It does **not** contain
the GraalPy runtime, the Python standard library, or any Python package — those come from
the worker image, or from the pip install the requirements file drives.

## Worker-image contract

A packaged model does not carry its own Python runtime or dependencies. Instead, the PlanDev
`merlin-worker` and `merlin-server` images ship an embedded GraalPy interpreter plus a
pre-built virtual environment ("`python-resources`"), and the shim runs the bundled model
against that. Both images are provisioned by the shared script
[`plandev/docker/graalpy/install.sh`](../plandev/docker/graalpy/install.sh); the layout it
produces (external-directory mode) is:

```
/opt/pymerlin/python-resources/
  venv/   <- GraalPy virtualenv: pymerlin + numpy + spiceypy, plus what a model declares
  src/    <- empty; required by GraalPyResources' external-directory convention
```

Your model source does **not** go in `src/`. At load time the shim extracts the bundled
`.py` from the uploaded JAR into a fresh temp directory (`/tmp/pymerlin-model-*`) and adds
that directory to `sys.path` directly, then deletes it when the simulation ends. `src/` is on
the Python path by `GraalPyResources.contextBuilder(root)` convention and must exist for the
context to build, but nothing is ever written to it. (Relocating the model source into `src/`
was considered and deferred — it only becomes necessary if filesystem access is sandboxed,
which would stop the shim from reading an arbitrary temp path.)

**Packages the image provides:** the pre-built venv ships **`pymerlin`, `numpy`, and
`spiceypy`**. Anything else a model imports is installed into that venv at model-load time,
from the requirements file in the model's own JAR — see
[Model dependencies](#model-dependencies) below. `pymerlin` is installed from a pinned git
ref (`PYMERLIN_GIT_URL`/`PYMERLIN_REF` in `install.sh`) — the image build
no longer needs a local pymerlin checkout alongside `plandev/`, so a model author working
from just `pip install pymerlin` and a standalone model file never needs this repo either.
`numpy`/`spiceypy` are installed with GraalPy's own patched `pip` against its wheel
repository — CPython wheels from PyPI are **not** binary-compatible with GraalPy and cannot
be used.

**Version compatibility.** The pymerlin ref a worker image is built against must ship a
`pymerlin-shim.jar` compiled against the *same* `graalPyVersion` as that image's own GraalPy
runtime (`GRAALPY_VERSION` in the Dockerfile / `graalPyVersion` in `gradle.properties`) —
mismatched, the shim compiles fine and fails confusingly at simulation time against an API
the worker doesn't provide (see `pymerlin-shim/build.gradle`'s `graalPyVersion` comment).
Decoupling the image build from a local checkout removes the accidental guarantee that these
two always moved together, so this needs to be checked explicitly now: bumping
`install.sh`'s `PYMERLIN_REF` means confirming the pymerlin-shim `build.gradle` at that ref
still matches `GRAALPY_VERSION`, and vice versa. `graalpy-preflight.yml`'s "Check GraalPy
versions agree" step automates half of this (a given pymerlin ref's shim version against
`GRAALPY_VERSION`); there is no equivalent automated check yet that `install.sh`'s *currently
pinned* `PYMERLIN_REF` specifically satisfies it.

### Model dependencies

The shim installs what a model JAR declares before it opens a GraalPy `Context`
(`RequirementsInstaller`, called from `ShimModelType.resolveModelRef`), using the **venv's
own pip** — never a system pip, because CPython wheels install cleanly under GraalPy and
then fail at import. Each installed requirements-set leaves a marker file next to the venv
and the run is guarded by a lock, so repeat loads and concurrent simulations install once.

Two consequences of *where* that runs are worth knowing before the first upload:

- **It happens once per container, not once per model.** `merlin-server` loads the model at
  upload time to register its activity types, so that image installs then; each
  `merlin-worker` installs the first time *it* simulates that model. Nothing is shared
  between them — there is no volume over `/opt/pymerlin` — so a dependency problem can
  surface at simulation time even though the upload succeeded, and a recreated container
  installs again from scratch.
- **The container needs outbound network at that moment**, to reach pypi.org and GraalPy's
  wheel repository. Without it the install fails, and the model fails to load carrying pip's
  own error rather than an unexplained `ImportError` later.

Pip runs with `${PYMERLIN_RESOURCES}/constraints.txt` (written by
[`install.sh`](../plandev/docker/graalpy/install.sh)) as `PIP_CONSTRAINT`, which is what
keeps a model from dragging the venv onto a version with no prebuilt GraalPy wheel.

**What import scanning cannot see.** Versions are pinned from *your* environment, which is
CPython: if GraalPy's wheel repository has no build of that exact version, the pin fails
where an unpinned requirement would have resolved. Packaging warns when a requirement ships
compiled extensions, and when it cannot resolve an import name to an installed distribution
— in that case it falls back to the import name itself, so a typo becomes a `pip install` of
that typo. Dynamic imports (`importlib.import_module("pandas")`) are invisible to a static
scan and simply never get installed; import the package normally somewhere in the model
instead. Scanning covers every file in a bundled package directory, including ones the
worker never runs, so a package's local plotting or driver script can pull its own imports
into the model's requirements.

**When an image rebuild is still the answer:** a package needing a toolchain the image
lacks, or one you want present before any model asks for it (as `numpy` and `spiceypy` are).
Add it to the install step in `plandev/docker/graalpy/install.sh`, pin it in
[`constraints.txt`](../plandev/docker/graalpy/constraints.txt), and rebuild both the
`merlin-worker` and `merlin-server` images.

## Architecture

A packaged pymerlin model runs **in-process** in the PlanDev worker JVM. The shim JAR
implements PlanDev's `MerlinPlugin` SPI; at load time it creates an embedded GraalPy
`Context` and imports the model's Python source into it. Activity-type registration,
resource description, and every activity body all execute by Java calling Python functions
directly and Python calling back into Java host objects — there is no subprocess, no
stdin/stdout protocol, and no JSON drive loop.

```
PlanDev merlin-worker (JVM)
  └── ShimModelType  (loaded from mission-model.jar)
        └── GraalBridge → embedded GraalPy Context
              ↕ direct host calls (org.graalvm.polyglot.Value)
            pymerlin model .py  (extracted from the JAR to a temp dir on sys.path)
```

- **Activity registration.** `ModelType.getDirectiveTypes()` /
  `getConfigurationType()` call `_describe_activity_types` / `_describe_config` on the model
  class directly and return the schema to PlanDev's activity palette — a model-class-only
  query that never instantiates the model.
- **Simulation.** `ModelType.instantiate()` builds the model's `_ModelState`, allocates a
  real PlanDev cell for each declared resource, and runs each activity body on the calling
  PlanDev task thread. `delay()`, `emit()`, `spawn()`, `call()`, and `wait_until()` route
  straight through a Java host object (`PyActions`) into the PlanDev engine — `wait_until`
  hands the Python predicate to Java as a `BooleanSupplier` wrapped in a PlanDev `Condition`,
  and `call()` genuinely blocks the parent until the child completes.

For the design rationale (why in-process GraalPy over py4j or a subprocess, the GIL/thread
model, abort semantics, native-extension support) and the phase-by-phase record, see
[`roadmap.md`](../roadmap.md). A focused description of the Java↔Python in-process interface is
in [`docs-src/shim-protocol.md`](docs-src/shim-protocol.md).

### Approachability over performance

The main tenet of pymerlin is approachability for rapid model prototyping. Running
in-process removes the old subprocess/serialization overhead, but a model author who needs
production simulation performance should still port the model to Java for a single,
fully instrumented JVM process.

## Building the shim JAR

If any changes are made to the Java shim code, rebuild and place the JAR where the Python
package expects it:

```shell
./scripts/build-shim.sh
```

Or manually:

```shell
cd java
./gradlew assemble
cp pymerlin-shim/build/libs/pymerlin-shim.jar ../pymerlin/_internal/jars/
```

The JAR lives inside the `pymerlin` Python source directory so it is included in the pip
distribution. `pymerlin package` copies whatever JAR is at that path — so re-copying after a
rebuild is required, or a packaged model ships stale shim classes.

The shim's polyglot/GraalPy dependencies are `compileOnly`: the worker's classloader supplies
them at runtime, so they are deliberately kept **out** of the shim JAR (bundling them would
pack hundreds of megabytes of Python runtime into every uploaded model). See
`java/pymerlin-shim/build.gradle` for the dependency rationale, including why `gson` is
still bundled.

## Known limitations and open work

Phases 1–4 of the GraalPy migration plus the 0.1.1 cell-evolution work closed most of the
functional gaps the earlier subprocess architecture had: `call()`, `wait_until` with real
conditions, linear (interpolated) resources, general cell evolution (user-defined
`evolution` functions, including clamped linear cells), model configuration, the
`MissionModelBase` helper for improved type checking, and temp-directory cleanup all work
now (see `roadmap.md` and `cell_evolution_roadmap.md`). What remains:

### Functional gaps

- **Only primitive activity/config parameter types.** Parameters typed as `int`, `float`,
  `str`, or `bool` map to the matching `ValueSchema`; lists, dicts, enums, `Duration`, or
  custom classes fall through to `ValueSchema.STRING` and appear in the PlanDev UI as
  unvalidated string fields.
- **Every published resource must be backed by exactly one cell.** The shim registers
  resources per-cell, so a resource whose getter can't be traced to a cell — an opaque
  `lambda: fn(cell.get())`, a bound method computing from several cells — cannot be
  published, and neither can a second resource on a cell that already backs one. Use
  `registrar.resource(name, cell.map(fn))`, which keeps the link to the cell; a value
  derived from several cells has to be computed into its own cell first.

  These resources *do* work under the local `simulate()` engine, so the same model file
  behaves differently in the two places. Model load now fails with the offending resource
  names rather than dropping them silently (which produced datasets that were quietly
  missing telemetry). Set `PYMERLIN_ALLOW_UNBACKED_RESOURCES=1` to downgrade that to a
  warning and load anyway, without those resources — intended for migrating an existing
  model, not as a permanent setting.

### Operational issues

- **No simulation timeout.** If a model's Python code hangs (e.g. an infinite loop with no
  `delay`), the worker task thread blocks indefinitely — there is no watchdog.
- **Python tracebacks are not yet surfaced to the PlanDev UI.** In-process, an uncaught
  model error arrives Java-side as a `PolyglotException` carrying the Python stack, so this
  is now fixable (unlike the old subprocess path where it was lost to stderr) — but the
  wiring to put it in the user-facing simulation-failure message is not done. Tracked as a
  Phase 6 item (`roadmap.md` §11.5).

### Open questions carried in the roadmap

- **Model-side `finally:` during task abort** may not run when a task is cancelled
  (`roadmap.md` §11.6) — needs re-verification against the real abort path, and if
  confirmed, documenting as a model-authoring constraint.
- **Concurrent same-tick emits to the same cell** were never exercised end-to-end
  (`roadmap.md` §6.5/§6.7); multi-threaded activity execution against a shared cell is not
  yet proven.

### Tests

The in-process JUnit suite (`DemoModelSimulationTest`, `SpanTimingTest`,
`CallSemanticsTest`) runs against a real GraalPy runtime + provisioned `python-resources`
venv — i.e. the built worker image, via the `dockerTestBundle` task (see
`java/pymerlin-shim/build.gradle`). On a stock JDK without that environment the tests
`assumeTrue`-skip rather than false-fail. Python-side cell-evolution tests live in
`tests/test_cell_evolution.py` and run under plain pytest.
