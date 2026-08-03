# Modeling Concurrent Events

:::{note}
This page is under construction.
:::

Predecessors to PlanDev handled concurrent events during simulation by running them
serially — sorted by some deterministic-but-largely-arbitrary attribute, such as
alphabetical order.

PlanDev's Merlin engine avoids this by running concurrent activities on separate task
threads. Each activity runs synchronously on its own thread; when it calls `delay()`,
`wait_until()`, or `call()`, the engine parks that thread and may resume another. The
effect of concurrent same-tick events on a cell is resolved by the cell's effect trait
(e.g. last-writer-wins for discrete cells, additive for linear cells).

In pymerlin's local `simulate()` engine, activities are also run on separate threads
(daemon `threading.Thread`), but they are processed one at a time from a priority queue —
true concurrent execution is a PlanDev-engine property.

In the packaged path (GraalPy in-process), activities run on Java virtual threads
(`ThreadedTask`). GraalPy releases its interpreter lock across Python→Java host calls, so
multiple activities can genuinely run concurrently. See the
[architecture](../architecture.md) page for details on the threading model.