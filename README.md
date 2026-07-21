# pymerlin

<!-- start elevator-pitch -->
pymerlin is a Python mission modeling framework for the [PlanDev](https://github.com/NASA-AMMOS/aerie) discrete-event simulation ecosystem. It lets you write PlanDev mission models in Python and either simulate them locally or package them as an uploadable PlanDev mission model JAR.

To learn more about PlanDev, read the [PlanDev Docs](https://nasa-ammos.github.io/aerie-docs).
<!-- end elevator-pitch -->

> **Branch note:** This fork is on `feature/pymerlin-shim-protocol`. The architecture has changed significantly from `main` — py4j has been replaced with a stdin/stdout JSON shim protocol. See [Architecture](#architecture) below.

## Prerequisites

- Python >= 3.10
- Java >= 21 (only needed for local simulation via the `main`-branch py4j path; not needed for `pymerlin package`)

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

This bundles the Python model files into the JAR alongside the prebuilt shim, and stamps the model reference into the JAR manifest. The resulting JAR can be uploaded to a deployed PlanDev instance.

**PlanDev worker requirement:** The PlanDev merlin-worker container must have `python3` and `pymerlin` installed. The PlanDev Dockerfiles on this branch already handle this — `python3`, `pip3`, and `pip3 install pymerlin` are included in both `merlin-server` and `merlin-worker` images.

## Architecture

> **The subprocess description below is superseded (2026-07-21).** As of Phase 3
> (roadmap.md §6.3/§6.6), the shim no longer spawns a Python subprocess or speaks the
> newline-delimited-JSON protocol described here — activities run in-process via GraalPy,
> with Java and Python calling each other directly. `python3`/`pip3 install pymerlin` is
> also no longer required in the worker/server images for this branch. Kept below for
> historical context on the architecture's evolution; `roadmap.md` (particularly §2, §5,
> §6) is the current source of truth. A full rewrite of this section is Phase 5 work
> (§8, "Document the worker-image contract").

### Shim protocol (superseded — subprocess/JSON, Phase 0–2)

The original py4j architecture (Python owns the process, launches Java as a subprocess) cannot produce a PlanDev-uploadable JAR because PlanDev requires Java to own the process and load mission models via its own classloader.

This branch replaces py4j with a **shim protocol**: a prebuilt JAR (`pymerlin-shim.jar`) implements the PlanDev `MerlinPlugin` SPI and at simulation time spawns `python3 -m pymerlin._server` as a subprocess, communicating over **newline-delimited JSON on stdin/stdout**.

```
PlanDev merlin-worker (JVM)
  └── ShimModelType (loaded from mission-model.jar)
        └── spawns: python3 -m pymerlin._server --model model.py:Mission
              ↕ newline-delimited JSON over stdin/stdout
```

**Pre-simulation (activity type registration):** When PlanDev uploads a JAR it calls `ModelType.getDirectiveTypes()`. The shim starts a one-shot Python subprocess, sends `{"op": "get_activity_types"}`, reads the response, then kills the subprocess. This populates the activity palette in the PlanDev UI.

**At simulation time:** `ModelType.instantiate()` starts a long-lived Python subprocess for the duration of the simulation. The shim queries resources, allocates PlanDev cells for each one, then drives activities via the protocol:

| Java → Python | Python → Java |
|---|---|
| `{"op": "run_activity", "id": "act-1", "name": "Foo", "args": {...}}` | `{"op": "delay", "duration_us": 3600000000, "emits": [...], "spawns": [...]}` |
| `{"op": "resume", "id": "act-1"}` | `{"op": "done", "emits": [...]}` |

Resource updates (`emits`) and child activity launches (`spawns`) are inlined into each yield response.

### Approachability over performance

The main tenet of pymerlin is approachability for rapid model prototyping. Users who need production simulation performance should port their model to Java, which eliminates the subprocess communication overhead and gives a single instrumented JVM process.

## Building the shim JAR

If any changes are made to the Java shim code, rebuild and place the JAR where the Python package expects it:

```shell
cd java
./gradlew assemble
cp pymerlin-shim/build/libs/pymerlin-shim.jar ../pymerlin/_internal/jars/
```

The JAR lives inside the `pymerlin` Python source directory so it is included in the pip distribution.

## Known limitations and open work

### Functional gaps

- **`call()` not implemented.** The `call()` action (parent waits for a child activity to complete before continuing) has no protocol op. It needs a `{"op": "call", ...}` message and a corresponding inline drive loop in `ShimModelType.driveToCompletion()`.

- **`wait_until` does not work correctly.** `_try_encode_condition()` in `_server.py` is a stub that always returns `None`, so all conditions fall back to `wait_until_opaque`. On the Java side `wait_until_opaque` resumes immediately without actually waiting on the condition. Activities that block on resource values will not behave correctly.

- **Cell evolution is ignored.** Cells declared with an `evolution` function (e.g. linear/polynomial resource dynamics) have their evolution silently discarded in `_ModelState.__init__`. Evolving resources always report their initial value mid-simulation.

- **All resources are typed as `discrete`.** Float resources that should be interpolated linearly by PlanDev will appear as step functions in simulation results.

- **Only primitive parameter types.** Activity parameters typed as lists, dicts, enums, `Duration`, or custom classes fall through to `ValueSchema.STRING`. PlanDev will show them as string fields with no validation.

- **No model configuration.** `getConfigurationType()` returns empty — there is no way to pass mission-level configuration parameters to the model at simulation time.

- **Concurrent activities share a single protocol pipe unsynchronized.** PlanDev runs activities in parallel Java threads, but all share one `pythonProcess`. Concurrent activity execution will corrupt the protocol. A serialization lock or multiplexed protocol is needed.

### Operational issues

- **Temp directories are not cleaned up.** `extractIfBundled` creates a temp directory on every simulation run and never deletes it.

- **Python version path scan is incomplete.** `PythonProcess.java` scans a hardcoded list of site-packages paths up to Python 3.11. Python 3.12 (the default on Ubuntu Jammy) is not in the list, so `PYTHONPATH` may be empty even when pymerlin is installed. Set `PYMERLIN_SITE` explicitly if needed.

- **No simulation timeout.** If the Python process hangs, the Java worker thread blocks indefinitely.

- **Python tracebacks are not surfaced to the PlanDev UI.** Errors appear only in worker stderr logs, not in the simulation failure message returned to the user.

### Missing tests

`tests/test_simulation.py` covers only the py4j path. There are no tests for `_server.py` or the end-to-end `pymerlin package` + upload flow. Needed:
- Activity execution with delays and emits
- Spawn (child activities)
- `call()` once implemented
- `wait_until` with a simple condition
- Resource value reporting through the protocol