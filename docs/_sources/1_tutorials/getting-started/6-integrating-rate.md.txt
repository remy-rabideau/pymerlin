# Integrating Data Rate

Now is where the fun really begins! Although having the data rate is useful, we are often
more concerned with the total data volume in the SSR — to make sure we don't overfill it
and have sufficient downlink opportunities. To compute total volume, we must integrate
`recording_rate`.

pymerlin offers several approaches, each with different trade-offs.

## Method 1 — Increase volume within the activity

The simplest approach is to compute the integral directly in the activity's effect model:

```python
self.ssr_volume_simple = registrar.cell(0.0)
registrar.resource("ssr_volume_simple", self.ssr_volume_simple)
```

Then in `collect_data`, after the `delay()`:

```python
model.data_model.ssr_volume_simple += rate * duration.to_number_in(Duration.SECONDS) / 1000.0
```

**Issues:**

- Volume only increases at the *end* of the activity — planners see a flat line followed
  by a jump, even though data is accumulating throughout.
- Doesn't transfer well to activities like `change_mag_mode` whose effects on rate persist
  beyond the activity's own duration.
- Rate and volume computations are separate and both live in the activity — their
  relationship should be modeled structurally.

For slightly higher fidelity, you can spread the accumulation across multiple steps:

```python
num_steps = 20
step_size = duration / num_steps
for i in range(num_steps):
    delay(step_size)
    model.data_model.ssr_volume_simple += rate * step_size.to_number_in(Duration.SECONDS) / 1000.0
```

This produces a staircase profile, but it's still a discrete resource.

## Method 2 — Sample-based volume update (daemon task)

A numerical approach: spawn a background task that samples `recording_rate` at a fixed
interval and accumulates volume.

```python
self.ssr_volume_sampled = registrar.cell(0.0)
registrar.resource("ssr_volume_sampled", self.ssr_volume_sampled)
```

```python
INTEGRATION_SAMPLE_INTERVAL = Duration.of(60, Duration.SECONDS)
```

Then spawn a daemon task from the model's `__init__`:

```python
from pymerlin.model_actions import spawn, delay

def integrate_sampled_ssr(data_model):
    while True:
        delay(INTEGRATION_SAMPLE_INTERVAL)
        current_rate = data_model.recording_rate.get()
        data_model.ssr_volume_sampled += (
            current_rate
            * INTEGRATION_SAMPLE_INTERVAL.to_number_in(Duration.SECONDS)
            / 1000.0
        )

# In Model.__init__:
spawn(integrate_sampled_ssr(self.data_model))
```

The infinite `while True` loop is intentional — PlanDev terminates the task when
simulation reaches the end of the plan.

**Issues:**

- Approximation — sampled points may not align with rate changes.
- Fixed interval means wasted work when the rate isn't changing.
- Still a discrete resource (staircase profile).

Despite these issues, daemon tasks are a powerful tool for modeling background behavior
(geometry updates, battery degradation, environmental effects, etc.).

## Method 3 — React to rate changes

An efficient and accurate approach: use `wait_until` to react whenever `recording_rate`
changes, then compute volume from the elapsed time and the previous rate.

```python
from pymerlin.model_actions import delay, spawn
from pymerlin.reactions import monitor_updates
from pymerlin.clock import clock

self.ssr_volume_reactive = registrar.cell(0.0)
registrar.resource("ssr_volume_reactive", self.ssr_volume_reactive)
self.timer = clock(registrar)
```

Then spawn a reactor:

```python
def monitor_recording_rate(data_model):
    previous_rate = 0.0
    for new_value in monitor_updates(lambda: data_model.recording_rate.get()):
        t = data_model.timer.get()
        if t > Duration.ZERO:
            data_model.ssr_volume_reactive += (
                previous_rate * t.to_number_in(Duration.SECONDS) / 1000.0
            )
        previous_rate = new_value
        data_model.timer.start()

# In Model.__init__:
spawn(monitor_recording_rate(self.data_model))
```

This fires only when the rate actually changes, so no wasted computation. The result is
still a discrete resource (step function), but it is exact at every change point.

:::{note}
`monitor_updates` is a helper in `pymerlin.reactions` that uses `wait_until` under the
hood. In the packaged PlanDev path, `wait_until` uses real dependency-tracked conditions —
no polling. In the local `simulate()` engine, `wait_until` polls 1 µs at a time.
:::

## Method 4 — Linear resource (recommended)

The simplest and most accurate approach in pymerlin: use a `registrar.linear()` cell.
This creates a resource whose value ramps as `value + rate × elapsed_seconds` between
events — no integration logic needed.

```python
self.ssr_volume_linear = registrar.linear(0.0, minimum=0.0, maximum=250.0)
registrar.resource("ssr_volume_linear", self.ssr_volume_linear)
```

Then in `collect_data`:

```python
@Model.ActivityType
def collect_data(model, rate=0.0, duration="01:00:00"):
    duration = Duration.from_string(duration)
    model.data_model.recording_rate += rate
    model.data_model.ssr_volume_linear.set_rate(rate / 1000.0)  # Mb/s -> Gb/s
    delay(duration)
    model.data_model.recording_rate -= rate
    model.data_model.ssr_volume_linear.set_rate(0.0)
```

**Advantages:**

- The PlanDev UI shows a *linear ramp* between events — true continuous profile.
- `minimum` and `maximum` bounds clamp the value automatically (SSR can't go below 0 or
  above 250 Gb).
- No daemon tasks, no sampling, no integration logic.
- Works correctly with activities like `change_mag_mode` whose rate effects persist beyond
  the activity.

This is the recommended approach for any quantity that integrates a piecewise-constant rate.
