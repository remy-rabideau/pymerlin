# Changelog

## 0.2.1 (2026-08-03)

### Added
- **A model's Python dependencies travel with its JAR.** `pymerlin package` reads the model's
  own `import` statements, pins each third-party package to the version installed alongside
  the model, and bundles the result into the JAR as `pymerlin_requirements.txt`. At model-load
  time the shim's `RequirementsInstaller` pip-installs anything missing into that container's
  GraalPy venv, using the venv's own pip with `${PYMERLIN_RESOURCES}/constraints.txt` as
  `PIP_CONSTRAINT`; a marker file per requirements-set and a lock keep repeat loads and
  concurrent simulations from installing twice. Adding an import to a model is now the whole
  workflow — no image rebuild for a package the image happens to lack, and no dependency file
  to keep in sync with the code. The imports are the only declaration read: a `requirements.txt`
  next to the model is ignored, and `--no-requirements` is the opt-out. Note that installs are
  per container — `merlin-server` at upload, each `merlin-worker` at its first simulation of
  that model — and need outbound network at that moment.

### Changed
- **`spiceypy`, `bokeh`, and `numpy` are now core dependencies.** They were previously
  declared as optional extras, but the extras block was commented out in `pyproject.toml`,
  so `pip install pymerlin[spice]` installed nothing and `pymerlin.spice` failed to import.
  Installing pymerlin now brings all three, matching what the docs already told users to
  expect. Local `simulate()` still uses none of them.

### Documentation
- Rewrote the docs-src tree against v0.2.0: dual-engine architecture, the in-process shim
  interface, packaging, cell/resource types, and the getting-started tutorial.
- Fixed the `clock()` usage in the tutorial's reactive-integration example — `clock()`
  returns a `ClockMaker`, and `.start()` must run inside the task, not in `__init__`.
- Corrected the pymerlin and PlanDev repository links.

## 0.2.0 (2026-08-02)

### Added
- **`dynamics='real'` for evolving cells.** `registrar.cell(..., dynamics='real')` publishes
  a `RealDynamics` resource instead of a discrete one, so each profile segment carries a
  value *and* a slope and renders as a sloped chord rather than a flat step — a nonlinear
  curve no longer draws as a staircase. The slope is the secant over one `resolution`
  interval, taken by evaluating the evolution function that far ahead, so segment endpoints
  stay exact and error within a segment is bounded by curvature. Requires both `evolution`
  and `resolution`, validated at registration. Sampling is unchanged: a nonlinear function
  still needs one segment per `resolution`. The pure-Python `simulate()` engine always
  produces flat-value segments regardless of this setting — it checks model logic, not
  profile fidelity.

### Fixed
- **Resources that cannot reach PlanDev now fail loudly instead of vanishing.** The shim
  registers resources per-cell, so a resource whose getter couldn't be traced to a cell (an
  opaque `lambda`, a method computing from several cells), or a second resource registered
  against a cell that already backed one, was silently never created — the model uploaded
  and simulated fine and produced a dataset quietly missing telemetry. Model load now raises
  with the offending resource names and how to fix each one. Set
  `PYMERLIN_ALLOW_UNBACKED_RESOURCES=1` to downgrade to a warning while migrating a model.
  The pure-Python `simulate()` engine is unaffected and still publishes these resources.
- **Numeric resource values no longer fail to parse.** The shim read polyglot values with
  `toString()`, a debug rendering, so a Python `"5.0"` arrived as `"'5.0'"` and blew up in
  `BigDecimal`; it now reads with `asString()`. A value that still won't parse raises naming
  the offending resource and value, instead of surfacing as a parse error identifying neither.

### Documentation
- Corrected the `python-resources/src/` description in `README.md` and
  `plandev/docker/graalpy/install.sh`: model sources are extracted to a temp directory added
  to `sys.path`, not into `src/`, which exists only to satisfy `GraalPyResources`' convention.

---

## 0.1.1 (2026-07-29)

### Added
- **Cell evolution.** `registrar.cell(initial, evolution=fn)` now wires the user-defined
  evolution function through to Java's `CellType.step()`, so cell values evolve
  automatically as simulation time advances — no daemon activity needed. Includes support
  for clamped linear cells with configurable bounds.
- **Evolving-cell resource projections.** Evolving cells produce correct resource profiles
  (discrete snapshots of the stepped value) in the PlanDev UI.
- **`MissionModelBase` helper class.** Mission model classes can now inherit from
  `MissionModelBase` for improved type checking and IDE support, alongside the existing
  `@MissionModel` decorator.
- **CLI `--version` flag.** `pymerlin --version` prints the installed version.
- **`build-shim.sh` script.** One-command rebuild of the shim JAR
  (`./scripts/build-shim.sh`).

### Changed
- `pymerlin-shim` now compiles against published PlanDev artifacts from GitHub Packages
  instead of requiring a local `plandev/` checkout.

### Fixed
- Linear resource clamp fix: prevent extrapolation past bounds and ensure correct segment
  scheduling.

---

## 0.1.0 (2026-07-23)

### Added
- **In-process GraalPy execution (Phases 1–4).** A packaged model now runs in-process
  inside the PlanDev worker's JVM via an embedded GraalPy interpreter — no subprocess, no
  stdin/stdout JSON protocol. Java and Python call each other directly across the polyglot
  boundary.
- **`call()` support.** `call(child(...))` genuinely blocks the parent activity until the
  child completes.
- **Real `wait_until`.** `wait_until(predicate)` passes the Python predicate to Java as a
  `BooleanSupplier` wrapped in a PlanDev `Condition` — dependency-tracked blocking, not
  polling.
- **Linear (interpolated) resources.** `registrar.linear(initial, rate)` declares a
  continuously-integrating resource backed by Aerie's `RealDynamics`, with
  `.set_rate(r)` for discrete rate changes.
- **Model configuration.** A model's `__init__` parameters (after `self` and `registrar`)
  are automatically exposed as simulation configuration in the PlanDev UI.
- **Worker-image contract documentation.** The README now documents the
  `python-resources/` layout, the fixed venv package set (`pymerlin` + `numpy` +
  `spiceypy`), and how to add a missing dependency (image rebuild, not JAR change).
- **Decoupled builds.** The worker/server image build no longer requires a local pymerlin
  checkout — `install.sh` pulls pymerlin from a pinned git tag. `pymerlin-shim` no longer
  requires a local `plandev/` checkout to compile.

### Changed
- Architecture docs (`architecture.md`, `shim-protocol.md`) rewritten to describe the
  in-process GraalPy design instead of the superseded py4j / subprocess protocols.
- All "Aerie" references renamed to "PlanDev" across docs.

### Removed
- Subprocess bridge (`Protocol.java`, `PythonProcess.java`, `SubprocessBridge.java`).
- Python-side subprocess machinery (`_ActivityRunner`, `_send`/`_recv`, `_run_server`,
  `pymerlin/_server/` CLI package).
- CPython subprocess blocks from both Dockerfiles.

---

## 0.0.9 and earlier

See the git history for changes prior to the GraalPy migration.
