# Modeling Telecom

:::{note}
This guide is under construction.
:::

Telecommunications play a key role in any tele-operated system, and the availability and
bandwidth of the communication link limits the performance of the system.

## Key concepts

- **Link availability** depends on whether there is an occultation between the transmitter
  and receiver, whether antennas can be pointed at each other, and whether the link is
  already allocated to another purpose.
- **Link bandwidth** is determined by the properties of the transmitter and receiver, the
  distance between them, and any obstructions or media in the communication path.

## Modeling pattern

A common approach in pymerlin:

1. Use SPICE (via `pymerlin.spice`) to compute spacecraft-to-ground-station distances and
   occultation geometry.
2. Track antenna state as a discrete cell (`"HGA"` / `"LGA"` / `"OFF"`).
3. Derive the effective data rate from geometry + antenna state.
4. Drive a data-volume resource with the computed rate.

See the SPICE resources in `demo/aerie_orbiter_model.py` for a working example that
computes range and position from SPICE kernels.

## References

- [MathWorks — Link Budget](https://www.mathworks.com/discovery/link-budget.html)
- [Sklar — Digital Communications](http://www.sss-mag.com/pdf/an9804.pdf)