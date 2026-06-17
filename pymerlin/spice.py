"""
SPICE integration for pymerlin

This module provides utilities for using NASA's SPICE toolkit within pymerlin simulations.
SPICE (Spacecraft Planet Instrument C-matrix Events) is used for computing geometric information
about spacecraft, planets, and other solar system bodies.

To use this module, install pymerlin with the spice extra:
    pip install pymerlin[spice]

Example usage:
    from pymerlin import MissionModel
    from pymerlin.spice import SpiceKernel, spice_resource
    from pymerlin.clock import clock
    
    @MissionModel
    class MyMission:
        def __init__(self, registrar):
            # Initialize clock
            clock_maker = clock(registrar)
            self.clock = clock_maker._system_clock
            
            # Initialize SPICE
            self.spice = SpiceKernel(registrar, kernel_paths=[
                "/path/to/naif0012.tls",  # Leap seconds kernel
                "/path/to/de440.bsp",      # Planetary ephemeris
                "/path/to/spacecraft.bsp"  # Spacecraft ephemeris
            ])
            self.spice.load_kernels()
            self.epoch_et = self.spice.utc_to_et("2024-01-01T00:00:00")
            
            # Create a resource that computes spacecraft distance at simulation time
            registrar.resource("/spacecraft/distance", 
                spice_resource(self.spice, self.clock, self.epoch_et,
                              "SPACECRAFT", "EARTH", "J2000"))
"""

try:
    import spiceypy as spice
    SPICE_AVAILABLE = True
except ImportError:
    SPICE_AVAILABLE = False
    spice = None

from typing import List, Callable, Tuple
from .duration import Duration, SECONDS


class SpiceKernel:
    """
    Manages SPICE kernel loading and provides utilities for SPICE computations.
    
    This class handles kernel lifecycle within a pymerlin simulation, ensuring
    kernels are loaded at simulation start and unloaded at simulation end.
    """
    
    def __init__(self, registrar, kernel_paths: List[str]):
        """
        Initialize SPICE kernel manager.
        
        Args:
            registrar: The mission model registrar
            kernel_paths: List of paths to SPICE kernel files to load
        """
        if not SPICE_AVAILABLE:
            raise ImportError(
                "spiceypy is not installed. Install it with: pip install pymerlin[spice]"
            )
        
        self.kernel_paths = kernel_paths
        self._kernels_loaded = False
    
    def load_kernels(self):
        """Load all configured SPICE kernels."""
        if self._kernels_loaded:
            return
        
        for kernel_path in self.kernel_paths:
            spice.furnsh(kernel_path)
        
        self._kernels_loaded = True
    
    def unload_kernels(self):
        """Unload all SPICE kernels."""
        if not self._kernels_loaded:
            return
        
        spice.kclear()
        self._kernels_loaded = False
    
    def position(self, target: str, observer: str, frame: str, et: float) -> Tuple[float, float, float]:
        """
        Compute position of target relative to observer.
        
        Args:
            target: Name of target body
            observer: Name of observing body
            frame: Reference frame (e.g., "J2000")
            et: Ephemeris time (seconds past J2000)
        
        Returns:
            Tuple of (x, y, z) position in km
        """
        if not self._kernels_loaded:
            self.load_kernels()
        
        state, _ = spice.spkez(
            spice.bodn2c(target),
            et,
            frame,
            "NONE",
            spice.bodn2c(observer)
        )
        return state[0], state[1], state[2]
    
    def velocity(self, target: str, observer: str, frame: str, et: float) -> Tuple[float, float, float]:
        """
        Compute velocity of target relative to observer.
        
        Args:
            target: Name of target body
            observer: Name of observing body
            frame: Reference frame (e.g., "J2000")
            et: Ephemeris time (seconds past J2000)
        
        Returns:
            Tuple of (vx, vy, vz) velocity in km/s
        """
        if not self._kernels_loaded:
            self.load_kernels()
        
        state, _ = spice.spkez(
            spice.bodn2c(target),
            et,
            frame,
            "NONE",
            spice.bodn2c(observer)
        )
        return state[3], state[4], state[5]
    
    def state(self, target: str, observer: str, frame: str, et: float) -> Tuple[float, float, float, float, float, float]:
        """
        Compute full state (position and velocity) of target relative to observer.
        
        Args:
            target: Name of target body
            observer: Name of observing body
            frame: Reference frame (e.g., "J2000")
            et: Ephemeris time (seconds past J2000)
        
        Returns:
            Tuple of (x, y, z, vx, vy, vz)
        """
        if not self._kernels_loaded:
            self.load_kernels()
        
        state, _ = spice.spkez(
            spice.bodn2c(target),
            et,
            frame,
            "NONE",
            spice.bodn2c(observer)
        )
        return tuple(state)
    
    def utc_to_et(self, utc_string: str) -> float:
        """
        Convert UTC time string to ephemeris time.
        
        Args:
            utc_string: UTC time string (e.g., "2024-01-01T00:00:00")
        
        Returns:
            Ephemeris time in seconds past J2000
        """
        if not self._kernels_loaded:
            self.load_kernels()
        
        return spice.str2et(utc_string)
    
    def et_to_utc(self, et: float, format: str = "ISOC", precision: int = 3) -> str:
        """
        Convert ephemeris time to UTC string.
        
        Args:
            et: Ephemeris time in seconds past J2000
            format: Output format ("ISOC", "ISOD", "C", "D", "J")
            precision: Number of decimal places for seconds
        
        Returns:
            UTC time string
        """
        if not self._kernels_loaded:
            self.load_kernels()
        
        return spice.et2utc(et, format, precision)


def spice_resource(spice_kernel: SpiceKernel, 
                   clock_cell,
                   epoch_et: float,
                   target: str, 
                   observer: str, 
                   frame: str, 
                   computation_fn: Callable = None) -> Callable:
    """
    Create a resource function that computes SPICE-derived scalar values at simulation time.
    
    Note: Resources must return Java-serializable types (numbers, strings, booleans).
    Raw SPICE tuples (position, velocity, state) cannot be directly returned as resources.
    Use this helper to compute derived scalar quantities like distance, speed, etc.
    
    Args:
        spice_kernel: SpiceKernel instance
        clock_cell: Clock cell that provides current simulation time
        epoch_et: Ephemeris time at simulation start (seconds past J2000)
        target: Name of target body
        observer: Name of observing body
        frame: Reference frame (e.g., "J2000")
        computation_fn: Optional function that takes (position_tuple) and returns a scalar.
                       If None, returns distance magnitude by default.
    
    Returns:
        A function that can be registered as a resource
    
    Example:
        # Compute distance
        registrar.resource("/spacecraft/distance", 
            spice_resource(self.spice, self.clock, self.epoch_et,
                          "SPACECRAFT", "EARTH", "J2000"))
        
        # Compute custom quantity
        def compute_altitude(pos):
            distance = (pos[0]**2 + pos[1]**2 + pos[2]**2)**0.5
            return distance - 6371.0  # Subtract Earth radius
        
        registrar.resource("/spacecraft/altitude",
            spice_resource(self.spice, self.clock, self.epoch_et,
                          "SPACECRAFT", "EARTH", "J2000", compute_altitude))
    """
    
    if computation_fn is None:
        # Default: compute distance magnitude
        def default_distance(pos):
            return (pos[0]**2 + pos[1]**2 + pos[2]**2)**0.5
        computation_fn = default_distance
    
    def resource_fn():
        # Get current simulation time
        sim_time = clock_cell.get()
        
        # Convert to ephemeris time
        et = duration_to_et(sim_time, epoch_et)
        
        # Compute position
        position = spice_kernel.position(target, observer, frame, et)
        
        # Apply computation function to get scalar result
        return float(computation_fn(position))
    
    return resource_fn


def duration_to_et(duration: Duration, epoch_et: float = 0.0) -> float:
    """
    Convert a pymerlin Duration to SPICE ephemeris time.
    
    Args:
        duration: Duration since simulation start
        epoch_et: Ephemeris time at simulation start (seconds past J2000)
    
    Returns:
        Ephemeris time in seconds past J2000
    """
    return epoch_et + duration.to_number_in(SECONDS)


def et_to_duration(et: float, epoch_et: float = 0.0) -> Duration:
    """
    Convert SPICE ephemeris time to a pymerlin Duration.
    
    Args:
        et: Ephemeris time in seconds past J2000
        epoch_et: Ephemeris time at simulation start (seconds past J2000)
    
    Returns:
        Duration since simulation start
    """
    seconds = et - epoch_et
    return Duration.of(seconds, SECONDS)


__all__ = [
    "SpiceKernel",
    "spice_resource",
    "duration_to_et",
    "et_to_duration",
    "SPICE_AVAILABLE"
]
