import pytest
from pymerlin.duration import Duration, SECONDS, HOURS, MINUTES


def test_from_iso8601_basic():
    """Test basic ISO 8601 parsing"""
    epoch = "2024-01-01T00:00:00Z"
    
    # Test same time (zero duration)
    duration = Duration.from_iso8601("2024-01-01T00:00:00Z", epoch)
    assert duration == Duration.ZERO
    
    # Test 12 hours later
    duration = Duration.from_iso8601("2024-01-01T12:00:00Z", epoch)
    assert duration.to_number_in(HOURS) == 12.0
    
    # Test 1 day later
    duration = Duration.from_iso8601("2024-01-02T00:00:00Z", epoch)
    assert duration.to_number_in(HOURS) == 24.0


def test_from_iso8601_with_microseconds():
    """Test ISO 8601 parsing with microsecond precision"""
    epoch = "2024-01-01T00:00:00.000000Z"
    
    # Test with microseconds
    duration = Duration.from_iso8601("2024-01-01T00:00:01.500000Z", epoch)
    assert duration.to_number_in(SECONDS) == 1.5
    
    # Test with partial microseconds
    duration = Duration.from_iso8601("2024-01-01T00:00:00.123456Z", epoch)
    assert abs(duration.to_number_in(SECONDS) - 0.123456) < 1e-6


def test_from_iso8601_with_timezone():
    """Test ISO 8601 parsing with timezone offsets"""
    epoch = "2024-01-01T00:00:00+00:00"
    
    # Test with explicit UTC
    duration = Duration.from_iso8601("2024-01-01T12:00:00+00:00", epoch)
    assert duration.to_number_in(HOURS) == 12.0
    
    # Test with different timezone (should convert to UTC)
    duration = Duration.from_iso8601("2024-01-01T13:00:00+01:00", epoch)
    assert duration.to_number_in(HOURS) == 12.0


def test_from_doy_basic():
    """Test basic DOY format parsing"""
    epoch = "2024-001T00:00:00"
    
    # Test same time (zero duration)
    duration = Duration.from_doy("2024-001T00:00:00", epoch)
    assert duration == Duration.ZERO
    
    # Test 1 day later
    duration = Duration.from_doy("2024-002T00:00:00", epoch)
    assert duration.to_number_in(HOURS) == 24.0
    
    # Test 12 hours later
    duration = Duration.from_doy("2024-001T12:00:00", epoch)
    assert duration.to_number_in(HOURS) == 12.0


def test_from_doy_with_microseconds():
    """Test DOY format parsing with microsecond precision"""
    epoch = "2024-001T00:00:00.000000"
    
    # Test with microseconds
    duration = Duration.from_doy("2024-001T00:00:01.500000", epoch)
    assert duration.to_number_in(SECONDS) == 1.5
    
    # Test with partial microseconds
    duration = Duration.from_doy("2024-001T00:00:00.123456", epoch)
    assert abs(duration.to_number_in(SECONDS) - 0.123456) < 1e-6


def test_from_doy_leap_year():
    """Test DOY format with leap year"""
    epoch = "2024-001T00:00:00"
    
    # Day 60 in a leap year is Feb 29
    duration = Duration.from_doy("2024-060T00:00:00", epoch)
    assert duration.to_number_in(HOURS) == 59 * 24.0
    
    # Day 366 in a leap year is Dec 31
    duration = Duration.from_doy("2024-366T00:00:00", epoch)
    assert duration.to_number_in(HOURS) == 365 * 24.0


def test_to_iso8601_basic():
    """Test conversion from Duration to ISO 8601"""
    epoch = "2024-01-01T00:00:00Z"
    
    # Test zero duration
    duration = Duration.ZERO
    result = duration.to_iso8601(epoch)
    assert result == "2024-01-01T00:00:00.000000Z"
    
    # Test 12 hours
    duration = Duration.of(12, HOURS)
    result = duration.to_iso8601(epoch)
    assert result == "2024-01-01T12:00:00.000000Z"
    
    # Test 1 day
    duration = Duration.of(24, HOURS)
    result = duration.to_iso8601(epoch)
    assert result == "2024-01-02T00:00:00.000000Z"


def test_to_iso8601_with_microseconds():
    """Test conversion to ISO 8601 with microsecond precision"""
    epoch = "2024-01-01T00:00:00Z"
    
    # Test with fractional seconds
    duration = Duration.from_string("00:00:01.500000")
    result = duration.to_iso8601(epoch)
    assert result == "2024-01-01T00:00:01.500000Z"


def test_to_doy_basic():
    """Test conversion from Duration to DOY format"""
    epoch = "2024-001T00:00:00"
    
    # Test zero duration
    duration = Duration.ZERO
    result = duration.to_doy(epoch)
    assert result == "2024-001T00:00:00.000000"
    
    # Test 12 hours
    duration = Duration.of(12, HOURS)
    result = duration.to_doy(epoch)
    assert result == "2024-001T12:00:00.000000"
    
    # Test 1 day
    duration = Duration.of(24, HOURS)
    result = duration.to_doy(epoch)
    assert result == "2024-002T00:00:00.000000"


def test_to_doy_with_microseconds():
    """Test conversion to DOY format with microsecond precision"""
    epoch = "2024-001T00:00:00"
    
    # Test with fractional seconds
    duration = Duration.from_string("00:00:01.500000")
    result = duration.to_doy(epoch)
    assert result == "2024-001T00:00:01.500000"


def test_roundtrip_iso8601():
    """Test that ISO 8601 conversions are reversible"""
    epoch = "2024-01-01T00:00:00Z"
    test_times = [
        "2024-01-01T00:00:00Z",
        "2024-01-01T12:30:45Z",
        "2024-01-15T08:15:30.123456Z",
        "2024-12-31T23:59:59.999999Z",
    ]
    
    for time_str in test_times:
        # Convert to Duration and back
        duration = Duration.from_iso8601(time_str, epoch)
        result = duration.to_iso8601(epoch)
        
        # Parse both and compare
        duration_original = Duration.from_iso8601(time_str, epoch)
        duration_result = Duration.from_iso8601(result, epoch)
        assert duration_original == duration_result


def test_roundtrip_doy():
    """Test that DOY conversions are reversible"""
    epoch = "2024-001T00:00:00"
    test_times = [
        "2024-001T00:00:00",
        "2024-001T12:30:45",
        "2024-095T08:15:30.123456",
        "2024-366T23:59:59.999999",
    ]
    
    for time_str in test_times:
        # Convert to Duration and back
        duration = Duration.from_doy(time_str, epoch)
        result = duration.to_doy(epoch)
        
        # Parse both and compare
        duration_original = Duration.from_doy(time_str, epoch)
        duration_result = Duration.from_doy(result, epoch)
        assert duration_original == duration_result


def test_negative_duration_iso8601():
    """Test ISO 8601 with times before epoch"""
    epoch = "2024-01-02T00:00:00Z"
    
    # Test time before epoch
    duration = Duration.from_iso8601("2024-01-01T00:00:00Z", epoch)
    assert duration.to_number_in(HOURS) == -24.0
    
    # Convert back
    result = duration.to_iso8601(epoch)
    assert result == "2024-01-01T00:00:00.000000Z"


def test_negative_duration_doy():
    """Test DOY with times before epoch"""
    epoch = "2024-002T00:00:00"
    
    # Test time before epoch
    duration = Duration.from_doy("2024-001T00:00:00", epoch)
    assert duration.to_number_in(HOURS) == -24.0
    
    # Convert back
    result = duration.to_doy(epoch)
    assert result == "2024-001T00:00:00.000000"


def test_cross_year_boundary():
    """Test conversions across year boundaries"""
    epoch = "2023-365T12:00:00"
    
    # 24 hours later should be Jan 1, 2024
    duration = Duration.of(24, HOURS)
    result = duration.to_doy(epoch)
    assert result.startswith("2024-001T12:00:00")


def test_invalid_iso8601_format():
    """Test that invalid ISO 8601 formats raise errors"""
    epoch = "2024-01-01T00:00:00Z"
    
    with pytest.raises(ValueError):
        Duration.from_iso8601("not-a-date", epoch)
    
    with pytest.raises(ValueError):
        Duration.from_iso8601("2024/01/01 12:00:00", epoch)


def test_invalid_doy_format():
    """Test that invalid DOY formats raise errors"""
    epoch = "2024-001T00:00:00"
    
    with pytest.raises(ValueError):
        Duration.from_doy("not-a-date", epoch)
    
    with pytest.raises(ValueError):
        Duration.from_doy("2024-1T00:00:00", epoch)


def test_iso8601_formats_supported():
    """Test all supported ISO 8601 format variations"""
    epoch = "2024-01-01T00:00:00Z"
    
    # UTC with Z suffix (12:30:45 = 12.5125 hours)
    d1 = Duration.from_iso8601("2024-01-01T12:30:45Z", epoch)
    assert abs(d1.to_number_in(HOURS) - 12.5125) < 1e-6
    
    # With microseconds (12:30:45.123456)
    d2 = Duration.from_iso8601("2024-01-01T12:30:45.123456Z", epoch)
    assert abs(d2.to_number_in(SECONDS) - 45045.123456) < 1e-6
    
    # With timezone offset (12:30:45 = 12.5125 hours)
    d3 = Duration.from_iso8601("2024-01-01T12:30:45+00:00", epoch)
    assert abs(d3.to_number_in(HOURS) - 12.5125) < 1e-6
    
    # With microseconds and timezone
    d4 = Duration.from_iso8601("2024-01-01T12:30:45.123456+00:00", epoch)
    assert abs(d4.to_number_in(SECONDS) - 45045.123456) < 1e-6


def test_doy_format_variations():
    """Test DOY format with various precision levels"""
    epoch = "2024-001T00:00:00"
    
    # Without microseconds (12:30:45 = 12.5125 hours)
    d1 = Duration.from_doy("2024-095T12:30:45", epoch)
    hours = (94 * 24) + 12.5125
    assert abs(d1.to_number_in(HOURS) - hours) < 1e-6
    
    # With microseconds
    d2 = Duration.from_doy("2024-095T12:30:45.123456", epoch)
    seconds = (94 * 24 * 3600) + (12 * 3600) + (30 * 60) + 45.123456
    assert abs(d2.to_number_in(SECONDS) - seconds) < 1e-6


def test_mission_planning_scenario():
    """Test realistic mission planning scenario"""
    mission_start = "2024-096T12:00:00"
    
    # Define mission events
    events = [
        (Duration.ZERO, "Launch"),
        (Duration.of(2, HOURS), "Orbit Insertion"),
        (Duration.of(6, HOURS) + Duration.of(30, MINUTES), "Deploy Panels"),
        (Duration.of(24, HOURS), "First Science Pass"),
    ]
    
    # Verify all events can be converted to DOY
    for duration, name in events:
        timestamp = duration.to_doy(mission_start)
        # Verify we can parse it back
        recovered = Duration.from_doy(timestamp, mission_start)
        assert recovered == duration


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
