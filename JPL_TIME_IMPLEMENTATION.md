# JPL Time Format Implementation

## Overview

This document describes the implementation of JPL/Aerie time format support in pymerlin for the `feature/jpl-time` branch. Steps 1-3 are complete: Duration class extensions, SPICE integration, and comprehensive testing.

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

Added internal parsing functions in `duration.py`:

- **`_parse_iso8601(iso_string)`**: Parse ISO 8601 strings to Python datetime objects
- **`_parse_doy(doy_string)`**: Parse DOY strings to Python datetime objects

### 4. SPICE Integration

Extended `SpiceKernel` class to accept JPL time formats:

- **`SpiceKernel.utc_to_et(utc_string)`**: Now accepts ISO 8601, DOY, and SPICE native formats
  - Automatically detects format and converts appropriately
  - ISO 8601 → SPICE calendar format: `"YYYY MON DD HH:MM:SS.ffffff"`
  - DOY → SPICE DOY format: `"YYYY-DDD::HH:MM:SS.ffffff"`
  
Added convenience functions in `spice.py`:

- **`iso8601_to_et(spice_kernel, iso_string)`**: Direct ISO 8601 to ephemeris time conversion
- **`doy_to_et(spice_kernel, doy_string)`**: Direct DOY to ephemeris time conversion

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

The JPL time format support integrates seamlessly with pymerlin's SPICE module. `SpiceKernel.utc_to_et()` now automatically accepts all time formats:

```python
from pymerlin.spice import SpiceKernel, iso8601_to_et, doy_to_et
from pymerlin.duration import Duration, HOURS

spice = SpiceKernel(registrar, kernel_paths=[...])
spice.load_kernels()

# All three formats work automatically
epoch_et_iso = spice.utc_to_et("2024-04-05T12:00:00Z")      # ISO 8601
epoch_et_doy = spice.utc_to_et("2024-096T12:00:00")         # DOY format
epoch_et_native = spice.utc_to_et("2024 APR 05 12:00:00")  # SPICE native

# Or use convenience functions
epoch_et = iso8601_to_et(spice, "2024-04-05T12:00:00Z")
epoch_et = doy_to_et(spice, "2024-096T12:00:00")

# Work with durations
t_plus_3h = Duration.of(3, HOURS)
absolute_time_doy = t_plus_3h.to_doy("2024-096T12:00:00")
absolute_time_iso = t_plus_3h.to_iso8601("2024-04-05T12:00:00Z")
```

## Testing

### Duration Tests (`tests/test_jpl_time.py`)

Comprehensive tests for Duration class JPL time format support:

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

### SPICE Integration Tests (`tests/test_spice.py`)

Extended SPICE tests to cover JPL time formats:

- `test_spice_iso8601_format()` - ISO 8601 format acceptance in `utc_to_et()`
- `test_spice_doy_format()` - DOY format acceptance in `utc_to_et()`
- `test_iso8601_to_et_helper()` - ISO 8601 convenience function
- `test_doy_to_et_helper()` - DOY convenience function

Run tests with:
```bash
python -m pytest tests/test_jpl_time.py -v
python -m pytest tests/test_spice.py -v
```

## Files Modified

- **`pymerlin/duration.py`**: 
  - Added `from_iso8601()`, `from_doy()`, `to_iso8601()`, `to_doy()` static/instance methods
  - Added `_parse_iso8601()` and `_parse_doy()` helper functions
  - Added imports: `datetime`, `timedelta`, `timezone`, `re`

- **`pymerlin/spice.py`**: 
  - Extended `SpiceKernel.utc_to_et()` to auto-detect and convert ISO 8601 and DOY formats
  - Added `_is_iso8601_format()` and `_is_doy_format()` helper methods
  - Added `iso8601_to_et()` and `doy_to_et()` convenience functions
  - Updated module docstring with JPL time format examples
  - Added imports: `re`, `_parse_iso8601`, `_parse_doy` from duration module

- **`tests/test_spice.py`**:
  - Added 4 new tests for JPL time format support in SPICE integration
  - Updated imports to include `iso8601_to_et` and `doy_to_et`

## Files Created

- **`tests/test_jpl_time.py`**: Comprehensive test suite (20+ tests) for JPL time formats
- **`JPL_TIME_IMPLEMENTATION.md`**: This documentation file

## Implementation Status

### ✅ Completed

1. **Duration class extensions** - Added ISO 8601 and DOY format parsers and converters
2. **SPICE integration** - Extended `utc_to_et()` to support ISO 8601 and DOY formats
3. **Comprehensive testing** - Added 20+ tests for Duration and 4 tests for SPICE integration

### 🔄 Remaining Steps

4. **Update documentation** - Add JPL time format examples to SPICE guide (`docs-src/2_guides/spice.md`)
5. **Aerie validation** - Verify compatibility with Aerie's time representation (if applicable)

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

### SPICE Format Conversion

When converting JPL time formats to SPICE:

- **ISO 8601** → SPICE calendar format: `"2024 JAN 01 12:00:00.000000"`
  - Uses month abbreviations (JAN, FEB, etc.)
  - Uppercase format required by SPICE
  
- **DOY** → SPICE DOY format: `"2024-001::12:00:00.000000"`
  - Uses `::` delimiter to indicate DOY format
  - Maintains 3-digit day-of-year with zero padding
  
- Format detection uses regex patterns:
  - DOY: `^\d{4}-\d{3}T` (3 digits after dash)
  - ISO 8601: `^\d{4}-\d{2}-\d{2}T` (2 digits for month and day)
