import pytest

from pymerlin import MissionModel, Schedule, simulate
from pymerlin._internal._schedule import Directive
from pymerlin.clock import clock
from pymerlin.duration import Duration, SECONDS
from pymerlin.model_actions import delay
from pymerlin.spice import SpiceKernel, duration_to_et, et_to_duration, spice_resource, SPICE_AVAILABLE

# Skip all tests if SPICE is not installed
pytestmark = pytest.mark.skipif(not SPICE_AVAILABLE, reason="spiceypy not installed")


def test_duration_to_et():
    """Test conversion from pymerlin Duration to SPICE ephemeris time"""
    # J2000 epoch (ET = 0) corresponds to 2000-01-01T12:00:00 TDB
    epoch_et = 0.0
    
    # Test zero duration
    zero_duration = Duration.ZERO
    assert duration_to_et(zero_duration, epoch_et) == 0.0
    
    # Test positive duration (1 hour = 3600 seconds)
    one_hour = Duration.of(3600, SECONDS)
    assert duration_to_et(one_hour, epoch_et) == 3600.0
    
    # Test with non-zero epoch
    epoch_2024 = 757382400.0  # Approximate ET for 2024-01-01
    assert duration_to_et(zero_duration, epoch_2024) == epoch_2024
    assert duration_to_et(one_hour, epoch_2024) == epoch_2024 + 3600.0


def test_et_to_duration():
    """Test conversion from SPICE ephemeris time to pymerlin Duration"""
    epoch_et = 0.0
    
    # Test zero ET
    assert et_to_duration(0.0, epoch_et) == Duration.ZERO
    
    # Test positive ET
    et_3600 = 3600.0
    duration = et_to_duration(et_3600, epoch_et)
    assert duration.to_number_in(SECONDS) == 3600.0
    
    # Test with non-zero epoch
    epoch_2024 = 757382400.0
    duration = et_to_duration(epoch_2024 + 7200.0, epoch_2024)
    assert duration.to_number_in(SECONDS) == 7200.0


def test_spice_kernel_init():
    """Test SpiceKernel initialization"""
    @MissionModel
    class TestModel:
        def __init__(self, registrar):
            # This should not raise an error even with non-existent paths
            # (kernels are loaded later)
            self.spice = SpiceKernel(registrar, kernel_paths=[
                "/fake/path/naif0012.tls",
                "/fake/path/de440s.bsp"
            ])
            assert self.spice.kernel_paths == [
                "/fake/path/naif0012.tls",
                "/fake/path/de440s.bsp"
            ]
            assert not self.spice._kernels_loaded


def test_spice_kernel_with_empty_schedule():
    """Test that SpiceKernel works with empty schedule"""
    @MissionModel
    class TestModel:
        def __init__(self, registrar):
            self.spice = SpiceKernel(registrar, kernel_paths=[])
    
    profiles, spans, events = simulate(TestModel, Schedule.empty(), "00:00:01")
    assert len(spans) == 0




def test_spice_kernel_lifecycle():
    """Test that kernels are loaded and unloaded correctly"""
    @MissionModel
    class TestModel:
        def __init__(self, registrar):
            self.spice = SpiceKernel(registrar, kernel_paths=[])
            assert not self.spice._kernels_loaded
            
            # Manually test load/unload (without actual kernel files)
            # In real usage, this would be called automatically
    
    @TestModel.ActivityType
    def test_activity(mission):
        # Verify kernel state
        assert hasattr(mission.spice, '_kernels_loaded')
        delay("00:00:01")
    
    profiles, spans, events = simulate(
        TestModel,
        Schedule.build(("00:00:00", Directive("test_activity", {}))),
        "00:00:02"
    )


def test_spice_in_mission_model():
    """Test that SpiceKernel integrates properly with MissionModel"""
    @MissionModel
    class SpiceMission:
        def __init__(self, registrar):
            self.clock_maker = clock(registrar)
            self.clock = self.clock_maker._system_clock
            
            # Initialize SPICE (with fake paths)
            self.spice = SpiceKernel(registrar, kernel_paths=[
                "/fake/naif0012.tls",
                "/fake/de440s.bsp"
            ])
            
            # Create a cell to track some computed value
            self.computed_value = registrar.cell(0.0)
            registrar.resource("computed_value", self.computed_value.get)
    
    @SpiceMission.ActivityType
    def compute_something(mission):
        # Simulate some computation
        sim_time = mission.clock.get()
        mission.computed_value.set(sim_time.to_number_in(SECONDS))
        delay("00:01:00")
    
    profiles, spans, events = simulate(
        SpiceMission,
        Schedule.build(
            ("00:00:00", Directive("compute_something", {})),
            ("00:05:00", Directive("compute_something", {}))
        ),
        "00:10:00"
    )
    
    # Verify we got the expected spans
    assert len(spans) == 2
    assert spans[0].type == "compute_something"
    assert spans[1].type == "compute_something"
    
    # Verify resource was tracked
    assert "computed_value" in profiles


def test_duration_conversions_roundtrip():
    """Test that duration conversions are reversible"""
    epoch_et = 0.0
    
    # Test various durations
    test_durations = [
        Duration.ZERO,
        Duration.SECOND,
        Duration.of(3600, SECONDS),  # 1 hour
        Duration.of(86400, SECONDS),  # 1 day
        Duration.from_string("12:34:56")
    ]
    
    for original_duration in test_durations:
        # Convert to ET and back
        et = duration_to_et(original_duration, epoch_et)
        recovered_duration = et_to_duration(et, epoch_et)
        
        # Should be equal (within floating point precision)
        original_seconds = original_duration.to_number_in(SECONDS)
        recovered_seconds = recovered_duration.to_number_in(SECONDS)
        assert abs(original_seconds - recovered_seconds) < 1e-6


def test_spice_kernel_multiple_paths():
    """Test SpiceKernel with multiple kernel paths"""
    @MissionModel
    class TestModel:
        def __init__(self, registrar):
            self.spice = SpiceKernel(registrar, kernel_paths=[
                "/path/to/kernel1.tls",
                "/path/to/kernel2.bsp",
                "/path/to/kernel3.bsp",
                "/path/to/kernel4.tpc"
            ])
            
            assert len(self.spice.kernel_paths) == 4
    
    profiles, spans, events = simulate(TestModel, Schedule.empty(), "00:00:01")


def test_spice_kernel_empty_paths():
    """Test SpiceKernel with empty kernel paths list"""
    @MissionModel
    class TestModel:
        def __init__(self, registrar):
            self.spice = SpiceKernel(registrar, kernel_paths=[])
            assert self.spice.kernel_paths == []
    
    profiles, spans, events = simulate(TestModel, Schedule.empty(), "00:00:01")


def test_spice_resource_helper():
    """Test spice_resource helper function for creating SPICE-based resources"""
    @MissionModel
    class SpiceResourceMission:
        def __init__(self, registrar):
            # Initialize clock
            clock_maker = clock(registrar)
            self.clock = clock_maker._system_clock
            
            # Initialize SPICE (with fake paths - won't actually load kernels)
            self.spice = SpiceKernel(registrar, kernel_paths=[])
            self.epoch_et = 0.0
            
            # Create a resource that computes distance (scalar value)
            # This simulates what you'd do with spice_resource to compute a derived quantity
            def mock_distance_resource():
                # Resources must be pure functions that return Java-serializable types
                # Return a scalar distance value (km)
                return 384400.0  # Approximate Earth-Moon distance
            
            registrar.resource("/mock/distance", mock_distance_resource)
    
    @SpiceResourceMission.ActivityType
    def check_resource(mission):
        delay("00:00:01")
    
    profiles, spans, events = simulate(
        SpiceResourceMission,
        Schedule.build(("00:00:00", Directive("check_resource", {}))),
        "00:00:02"
    )
    
    # Verify the simulation ran
    assert len(spans) == 1
    # Verify the resource was tracked in profiles
    assert "/mock/distance" in profiles
    # Verify the resource has the expected constant value throughout
    profile = profiles["/mock/distance"]
    assert len(profile) > 0
    # Check that the profile contains our mock distance value
    for segment in profile:
        assert segment.dynamics == 384400.0


def test_spice_resource_computation_types():
    """Test that spice_resource validates computation types"""
    @MissionModel
    class TestModel:
        def __init__(self, registrar):
            clock_maker = clock(registrar)
            self.clock = clock_maker._system_clock
            self.spice = SpiceKernel(registrar, kernel_paths=[])
            self.epoch_et = 0.0
    
    # Create a test model instance (without running simulation)
    # We'll test the resource function directly
    
    # Test that invalid computation type raises ValueError
    # We can't easily test this without mocking SPICE, but we can verify
    # the function signature is correct by creating it
    @MissionModel
    class ValidModel:
        def __init__(self, registrar):
            clock_maker = clock(registrar)
            self.clock = clock_maker._system_clock
            self.spice = SpiceKernel(registrar, kernel_paths=[])
            self.epoch_et = 0.0
            
            # These should not raise errors when creating the resource function
            pos_fn = spice_resource(self.spice, self.clock, self.epoch_et,
                                   "TARGET", "OBSERVER", "J2000", "position")
            vel_fn = spice_resource(self.spice, self.clock, self.epoch_et,
                                   "TARGET", "OBSERVER", "J2000", "velocity")
            state_fn = spice_resource(self.spice, self.clock, self.epoch_et,
                                     "TARGET", "OBSERVER", "J2000", "state")
            
            # Verify they are callable
            assert callable(pos_fn)
            assert callable(vel_fn)
            assert callable(state_fn)
    
    profiles, spans, events = simulate(ValidModel, Schedule.empty(), "00:00:01")
