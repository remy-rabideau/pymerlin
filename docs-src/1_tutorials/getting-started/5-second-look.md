# Second Look

With our second activity and resources built, let's test the model again. You can either
run it locally with `simulate()` or package and upload to PlanDev.

## Local test

```python
from pymerlin import simulate, Schedule, Directive

profiles, spans, events = simulate(
    Model,
    Schedule.build(
        ("00:00:00", Directive("change_mag_mode", {"mode": "HIGH_RATE"})),
        ("00:05:00", Directive("collect_data", {"rate": 10.0, "duration": "00:10:00"})),
        ("00:20:00", Directive("change_mag_mode", {"mode": "LOW_RATE"})),
    ),
    "01:00:00"
)
```

You should see `recording_rate` start at 0, jump to 5 Mbps when the mag mode changes to
HIGH_RATE, increase by another 10 to 15 Mbps during `collect_data`, then drop back to 5
after the activity ends, and finally fall to 0.5 Mbps when the mode switches to LOW_RATE.

## In PlanDev

Package and upload (`pymerlin package --model mission.py:Model --out mission-model.jar`),
create a 1-day plan, drag in your activities, and simulate.

Use PlanDev's
[Timeline Editing](https://ammos.nasa.gov/aerie-docs/planning/timeline-editing/) to put
`mag_data_mode` and `mag_data_rate` on the same row so you can see how mode changes drive
rate changes.

![Tutorial Plan 2](assets/Tutorial_Plan_2.png)
