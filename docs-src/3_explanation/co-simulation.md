# Co-Simulation

:::{note}
This page is under construction.
:::

Many missions come to pymerlin with existing tools and processes. It would be great to be
able to leverage some of these instead of re-implementing them in pymerlin.

There are challenges that arise when trying to get two simulators to play nicely
together. Since pymerlin's `simulate()` is a regular Python function, one pragmatic
approach is to call into external tools from within an activity's effect model — for
example, invoking a thermal solver or trajectory propagator and feeding results back into
cells.

## Further reading

- [System Design, Modeling, and Simulation in Ptolemy II](https://ptolemy.berkeley.edu/books/Systems/PtolemyII_DigitalV1_02.pdf)