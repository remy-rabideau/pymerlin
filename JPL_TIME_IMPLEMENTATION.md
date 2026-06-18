# JPL Time Format Implementation

## Overview

This document describes the implementation of JPL/Aerie time format support in pymerlin, completing the first step of the `feature/jpl-time` branch.

## What Was Implemented

### 1. ISO 8601 Time Format Support

Added methods to the `Duration` class for working with ISO 8601 timestamps:

- **`Duration.from_iso8601(iso_string, epoch_iso)`**: Parse an ISO 8601 timestamp and return a Duration relative to an epoch
- **`duration.to_iso8601(epoch_iso)`**: Convert a Duration to an ISO 8601 timestamp

**Supported formats:**
- `2024-01-01T12:30:45Z` (UTC with Z suffix)
- `2024-01-01T12:30:45.123456Z` (with microseconds)
- `2024-01-01T12:30:45+00:00` (with timezone offset)
- `2024-01-01T12:30:45.123456+00:00` (with microseconds and timezone)

### 2. DOY (Day of Year) Time Format Support

Added methods for NASA/JPL's Day of Year format:

- **`Duration.from_doy(doy_string, epoch_doy)`**: Parse a DOY timestamp and return a Duration relative to an epoch
- **`duration.to_doy(epoch_doy)`**: Convert a Duration to a DOY timestamp

**Format:**
- `YYYY-DDDTHH:MM:SS.ffffff`
- Example: `2024-095T12:30:45.123456` (95th day of 2024)

### 3. Helper Functions

Added internal parsing functions:

- **`_parse_iso8601(iso_string)`**: Parse ISO 8601 strings to Python datetime objects
- **`_parse_doy(doy_string)`**: Parse DOY strings to Python datetime objects

## Key Features

1. **Microsecond Precision**: All conversions maintain microsecond precision, matching pymerlin's internal Duration representation
2. **Timezone Support**: ISO 8601 parser handles timezone offsets and converts to UTC
3. **Negative Durations**: Supports times before the epoch (negative durations)
4. **Leap Year Support**: DOY format correctly handles leap years
5. **Roundtrip Conversions**: Converting to/from absolute timestamps is reversible

## Usage Examples

### Basic ISO 8601 Usage

```python
from pymerlin.duration import Duration, HOURS

# Define mission epoch
epoch = "2024-01-01T00:00:00Z"

# Parse an absolute timestamp
duration = Duration.from_iso8601("2024-01-01T12:00:00Z", epoch)
print(duration.to_number_in(HOURS))  # 12.0

# Convert back to ISO 8601
timestamp = duration.to_iso8601(epoch)
print(timestamp)  # "2024-01-01T12:00:00.000000Z"
```

### Basic DOY Usage

```python
from pymerlin.duration import Duration, HOURS

# Define mission epoch (Day 1 of 2024)
epoch = "2024-001T00:00:00"

# Parse a DOY timestamp (Day 2 of 2024)
duration = Duration.from_doy("2024-002T00:00:00", epoch)
print(duration.to_number_in(HOURS))  # 24.0

# Convert back to DOY
timestamp = duration.to_doy(epoch)
print(timestamp)  # "2024-002T00:00:00.000000"
```

### Mission Planning Example

```python
from pymerlin.duration import Duration, HOURS, MINUTES

# Define mission epoch
mission_start = "2024-096T12:00:00"  # April 5, 2024

# Define activities relative to mission start
activities = [
    (Duration.ZERO, "Launch"),
    (Duration.of(2, HOURS), "Orbit Insertion"),
    (Duration.of(6, HOURS) + Duration.of(30, MINUTES), "Deploy Solar Panels"),
]

# Convert to absolute timestamps
for duration, activity in activities:
    timestamp = duration.to_doy(mission_start)
    print(f"{timestamp} - {activity}")
```

## Integration with SPICE

The JPL time format support integrates seamlessly with pymerlin's SPICE module:

```python
from pymerlin.spice import SpiceKernel
from pymerlin.duration import Duration

# Define mission epoch in DOY format
mission_epoch_doy = "2024-096T12:00:00"

# Convert to SPICE ephemeris time
spice = SpiceKernel(registrar, kernel_paths=[...])
epoch_et = spice.utc_to_et("2024-04-05T12:00:00")

# Work with durations
t_plus_3h = Duration.of(3, HOURS)
absolute_time = t_plus_3h.to_doy(mission_epoch_doy)
```

## Testing

Comprehensive tests have been added in `tests/test_jpl_time.py` covering:

- Basic ISO 8601 parsing
- ISO 8601 with microseconds
- ISO 8601 with timezone offsets
- Basic DOY parsing
- DOY with microseconds
- Leap year handling
- Roundtrip conversions
- Negative durations (times before epoch)
- Cross-year boundaries
- Invalid format error handling

Run tests with:
```bash
python -m pytest tests/test_jpl_time.py -v
```

## Files Modified

- **`pymerlin/duration.py`**: Added `from_iso8601()`, `from_doy()`, `to_iso8601()`, `to_doy()` methods and helper functions

## Files Created

- **`tests/test_jpl_time.py`**: Comprehensive test suite for JPL time formats
- **`demo/jpl_time_example.py`**: Demonstration of JPL time format usage
- **`JPL_TIME_IMPLEMENTATION.md`**: This documentation file

## Next Steps

The remaining implementation steps for the `feature/jpl-time` branch:

2. **Extend SPICE integration** - Add DOY format support to `SpiceKernel.utc_to_et()`
3. **Add tests** - Extend SPICE tests to cover new time formats
4. **Update documentation** - Add JPL time format examples to SPICE guide
5. **Aerie integration** - Ensure compatibility with Aerie's time representation

## Compatibility

- **Python Version**: Requires Python 3.7+ (uses `datetime.fromisoformat`)
- **Dependencies**: Uses only Python standard library (`datetime`, `re`)
- **Backward Compatibility**: All existing Duration functionality remains unchanged

## Technical Notes

### Time Representation

- All timestamps are converted to UTC internally
- Duration calculations use microsecond precision (matching pymerlin's internal representation)
- Epoch-based approach allows working with absolute timestamps while maintaining Duration's relative time semantics

### Timezone Handling

- ISO 8601 parser converts all timestamps to UTC
- 'Z' suffix is treated as UTC (+00:00)
- Timezone offsets are properly handled and converted

### Leap Year Support

- DOY format correctly handles leap years (366 days)
- Day 60 in a leap year is February 29
- Day 366 in a leap year is December 31
