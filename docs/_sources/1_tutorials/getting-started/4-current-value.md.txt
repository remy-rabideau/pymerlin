# Using Current Value in an Effect Model

Now that we have our magnetometer resources, we need to build an activity that changes
the `mag_data_mode` (since `mag_data_rate` is a derived resource, it updates
automatically) and adjusts the overall SSR `recording_rate` to reflect the magnetometer's
data rate change.

This activity, `change_mag_mode`, takes one parameter `mode` defaulting to `"LOW_RATE"`.
The tricky part is computing the net change to `recording_rate`: we need the current rate
*before* we change the mode, then the new rate *after*, and adjust by the difference.

We get the current value of a derived cell with `.get()`:

```python
@Model.ActivityType
def change_mag_mode(model, mode="LOW_RATE"):
    current_rate = model.data_model.mag_data_rate.get()
    new_rate = MagDataCollectionMode[mode]
    # kbps -> Mbps conversion
    model.data_model.recording_rate += (new_rate - current_rate) / 1.0e3
    model.data_model.mag_data_mode.set(mode)
```

Key points:

- `model.data_model.mag_data_rate.get()` reads the *current* derived value before we
  change the mode.
- `cell.set(value)` replaces the cell's value directly (equivalent to
  `cell.emit(lambda _: value)`).
- The `+=` on `recording_rate` increases it by the net difference — if the new rate is
  lower, the difference is negative, which correctly decreases the overall rate.

You may notice the magic number `1.0e3` for the kbps→Mbps conversion. In a production
model you would want to use named constants or a unit-aware approach to avoid such
hard-coded conversions.
