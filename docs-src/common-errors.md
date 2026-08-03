# Common Errors

This section describes errors you might encounter and how to fix them.

## ImportError — circular import

```
ImportError: cannot import name '...' from partially initialized module 'mission' (most likely due to a circular import)
```

This error means you have two Python files that import each other. This can occur if you
have activities defined in one file and the model in another, and the two reference each
other.

The recommended practice is _not_ to import activities from the model file — activities
should reference the model, not the other way around. The activity files will need to be
imported from your main file prior to running simulation. Convenient as it may seem, we do
_not_ recommend using your model file as your main file — make a separate `main.py` and
import both the model and the activities.

## Resource not published (unbacked resource)

```
ValueError: The following resources have no identifiable backing cell: ['/my_resource']
```

Every published resource must be backed by exactly one cell. This error occurs when a
resource's getter cannot be traced back to a specific `CellRef`. Common causes:

- **Bare lambda instead of `.map()`:** `registrar.resource("x", lambda: cell.get()[0])`
  is opaque — use `registrar.resource("x", cell.map(lambda s: s[0]))` instead, which
  preserves the link to the backing cell.
- **Multiple resources on one cell:** each cell can back at most one resource. Derived
  quantities should be computed into their own cells.
- **Bound method computing from several cells:** a value derived from multiple cells needs
  its own dedicated cell.

These resources _do_ work under the local `simulate()` engine, so the same model file
behaves differently in the two places. Set `PYMERLIN_ALLOW_UNBACKED_RESOURCES=1` to
downgrade the error to a warning (intended for migration, not permanent use).

## `dynamics="real"` has no effect locally

`dynamics="real"` only takes effect when a model runs in-process inside a PlanDev worker
via GraalPy. The local `simulate()` engine always produces flat-value `ProfileSegment`
objects. This is intentional — use the JUnit suite or a real deployment to check profile
fidelity.

## Model fails to load after packaging

If your model imports sibling modules with relative imports but the directory has no
`__init__.py`, `pymerlin package` silently falls into the single-file branch and the model
fails at load time with an import error. The fix is to add an `__init__.py` to the
package directory, repackage, and re-upload.

## `wait_until` hangs under local `simulate()`

The local simulation engine polls `wait_until` conditions by advancing 1 µs at a time.
If a condition depends on an event that will never happen (e.g. waiting for data from an
activity that hasn't been scheduled), the simulation hangs. This is a known limitation of
the standalone engine. In the packaged PlanDev path, `wait_until` uses real dependency
tracking and only wakes when a cell the predicate reads actually changes.

## SPICE import error under GraalPy

```
SystemError: ... native module ... cannot be loaded in multiple Python contexts
```

`pymerlin.spice` imports `spiceypy`, which imports `numpy`, which loads a native extension
module. Unless GraalPy is built with `python.IsolateNativeModules`, a native module can
only be loaded by one Python context per process. Import SPICE lazily (inside a function,
not at module scope) so only the simulation context pays the cost. See the
`_load_spice()` pattern in `demo/aerie_orbiter_model.py`.

## Stack traces

In the packaged path, an uncaught model error arrives Java-side as a `PolyglotException`
carrying the Python traceback. Surfacing that traceback all the way to the PlanDev UI is
tracked as remaining work.
