# Discrete Event Simulation

## Requirements

- Allow a mission model to express time-dependent state in a way that can be tracked and
  managed by the host system.
- Evaluate conditions whenever their dependencies change.
- Evaluate resources whenever their dependencies change.
- Allow for tasks to operate transactionally.

## Quality attributes

- **Avoid wasted work** — avoid computing the current state of a cell unless it is queried.
- **Minimize needless allocations** — cells should be mutable and not require duplication
  on every step.

## The theory

The Merlin concept of a **cell** implements a more generic simulation concept called a
**state variable**. Traditionally, every state variable in a simulation is given an initial
value, and it keeps that value until an *event* is emitted that changes it. The value of a
state variable at time `t` can be computed by some function `f : Value × History → Value`,
where `Value` is the initial value and `History` is the list of events that have occurred
before time `t`.

This function `f` should have the following property: for all partitions of `History` into
a `prefix` and a `suffix`, `f(f(initial, prefix), suffix) = f(initial, prefix + suffix)`.
As time moves forward, we can "forget" both the initial value and the prefix once we've
applied `f` to it.

Two additional enhancements extend this formulation:

### Continuous evolution

Borrowed from [hybrid systems](https://en.wikipedia.org/wiki/Hybrid_system). The value of
a state variable can change even in the absence of any events. This requires a separation
between the *dynamics* of a state variable and its *value*. The dynamics describes the
future evolution of the variable, while the value describes its current state.

In pymerlin, continuous evolution is expressed through:

- **Cell evolution functions** — `registrar.cell(v, evolution=fn)` attaches a function
  `fn(current_value, elapsed_duration) → new_value` that the engine calls automatically as
  time advances.
- **Linear cells** — `registrar.linear(v, rate=r)` provides a built-in linear evolution
  (`value + rate × elapsed_seconds`) with optional clamping bounds.
- **`dynamics="real"`** — marks an evolving cell's resource as having sloped profile
  segments (via secant-slope approximation) rather than flat discrete segments.

### Concurrent events

Concurrent events exist to model two phenomena:

- Two or more independent *tasks* are scheduled to act at the same time, and we'd like to
  avoid arbitrarily serializing their actions.
- A single task *spawns* another to run in parallel, and we'd like to avoid arbitrary
  choices regarding the order the parent and child perform their actions.

In order to enable tasks to have
*[read-your-writes](https://en.wikipedia.org/wiki/Consistency_model#Read-your-writes_consistency)*
semantics, Merlin introduces the notion of a task *step* with certain transactional
guarantees.

## pymerlin's two engines

pymerlin implements two simulation engines for the same `@MissionModel` class:

- **Local `simulate()`** — a pure-Python engine (`_framework.py`) that runs activities on
  daemon threads with a two-`Queue` handshake. Suitable for logic checking and rapid
  iteration, but `wait_until` polls 1 µs at a time, `call()` is not fully implemented,
  and `dynamics="real"` is ignored.
- **Packaged (PlanDev)** — the model runs in-process inside the PlanDev worker JVM via
  GraalPy. Activities run on Java virtual threads (`ThreadedTask`), `wait_until` uses real
  dependency-tracked conditions, `call()` genuinely blocks, and `dynamics="real"` produces
  sloped profile chords. See [Architecture](../architecture.md) for details.

## Further reading

- [Aerie — The Merlin Interface](https://nasa-ammos.github.io/aerie-docs/mission-modeling/advanced-the-merlin-interface/)
- [Hybrid systems (Wikipedia)](https://en.wikipedia.org/wiki/Hybrid_system)
- [Conflict-free replicated data types (Wikipedia)](https://en.wikipedia.org/wiki/Conflict-free_replicated_data_type)