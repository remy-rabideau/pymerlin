"""
Aerie Orbiter mission model — single-file version.

All subsystems (geometry, power, data, telecom, radar) are inlined here so this file can be
packaged as a standalone pymerlin model with no package imports.

Three framework rules shape how state is declared below. Getting any of them wrong produces
a model that still runs under the standalone `simulate()` engine but is broken — or refuses
to load at all — once packaged and run inside a PlanDev worker:

1. EVERY REGISTERED RESOURCE NEEDS ITS OWN CELL. Java allocates one Aerie cell per pymerlin
   cell and registers resources against them, so a resource whose getter is an opaque lambda
   or bound method has nothing to attach to, and a second resource on an already-published
   cell loses to the first. Derived quantities — battery state of charge, total bus load,
   aggregate data volume — therefore each get a cell of their own, recomputed when an input
   changes, instead of being computed at read time.

2. DISCRETE CELLS HOLD str/float/int/bool. A discrete cell's value crosses to Java as
   str(value) and comes back parsed by type, so an Enum member round-trips into the string
   "EPS_State.OFF" and every attribute access on it fails. Component states are stored as
   plain state-name strings, with a module-level table mapping each name to its power draw.

3. CONTINUOUS QUANTITIES ARE EVOLVING (value, rate) CELLS — see `Ramp`. The engine
   integrates them as time advances, so no activity has to tick them forward, and
   dynamics="real" draws each profile segment as a sloped chord rather than a staircase.
   `registrar.linear()` models the same thing on the Java path, but it is inert under the
   standalone engine (a battery reads flat at its initial charge for the entire run), and
   this model is meant to be runnable both ways.

Model configuration is the constructor's parameter list — everything after `registrar` is
exposed by PlanDev as simulation configuration. Only scalars cross that boundary, so the
settings below are flat floats/ints/strings/bools rather than nested config objects.

Activities never mutate a subsystem's derived state directly. They state what changed (a
component turned on, the array deployed, the spacecraft entered eclipse) and call
`_recompute_power`, which re-derives production, load, and battery behaviour from the
current inputs. Keeping each consequence as a second number an activity has to remember to
update is how a radar that draws 543 W ends up charging the battery.
"""

import math

from pymerlin import MissionModel, MissionModelBase
from pymerlin.clock import clock
from pymerlin.duration import MICROSECONDS, SECONDS, Duration
from pymerlin.model_actions import delay

SECONDS_PER_HOUR = 3600.0
AU_TO_KM = 149597870.691


def _load_spice():
    """Import the SPICE bindings. DELIBERATELY NOT A MODULE-LEVEL IMPORT.

    `pymerlin.spice` imports spiceypy, which imports numpy, which loads a NATIVE extension
    module. Unless GraalPy is built with `python.IsolateNativeModules`, a native module can
    be loaded by only one Python context per process — and PlanDev creates several contexts
    against the same model: one to read the configuration schema, one to read the activity
    types, then one per simulation. Importing spiceypy at module scope therefore drags numpy
    into contexts that have no use for it, and the second one to load the model dies on
    numpy's .so with a SystemError — during UPLOAD, before any simulation runs.

    Deferring it here means only a model that was actually configured with kernels pays the
    import, in the one context that needs it. Note also that catching the failure instead is
    not a fix: SystemError is an ordinary Exception, so a try/except around the import
    swallows it and leaves every geometry resource silently frozen at its initial value.
    """
    from pymerlin import spice
    return spice


# =============================================================================
# Continuously-integrating quantities
# =============================================================================

# How often the engine re-samples a ramping cell, unless configuration says otherwise.
#
# This is the model's single most expensive setting, so it is worth understanding before
# tightening it. Every `Ramp` expires this often, which means one profile segment per
# interval per ramping resource for the whole plan, whether or not anything is happening:
# fourteen ramps at one-minute resolution is ~20k evolution calls and ~20k segments per
# simulated DAY. Five minutes cuts that fivefold.
#
# Accuracy barely enters into it. The evolution is linear in time, so the secant slope
# dynamics="real" computes is EXACT at any resolution; all the resolution bounds is how
# long a profile can keep ramping past a clamp before a fresh segment picks up the
# flattened rate. Coarsening it rounds the knee where a battery reaches full — it does not
# make the ramp itself wrong.
DEFAULT_RAMP_RESOLUTION = "00:05:00"


def _clamp(value, minimum, maximum):
    return max(minimum, min(maximum, value))


def _ramp_evolution(minimum, maximum):
    """Build the evolution function for a `Ramp` bounded by [minimum, maximum].

    The cell carries its own rate as the second half of its state, which is what lets the
    engine integrate it without an activity driving it: `fn(current_value, elapsed) ->
    new_value` is called automatically as simulation time advances. Clamping lives here
    rather than in the setters because integration is where a bound is actually crossed —
    a battery charging at 100% has to stop at 100, not keep climbing to 130.
    """
    def _evolve(state, elapsed):
        value, rate = state
        return _clamp(value + rate * elapsed.to_number_in(SECONDS), minimum, maximum), rate
    return _evolve


class Ramp:
    """A quantity that integrates its own rate, bounded to [minimum, maximum].

    Backed by a single evolving cell holding the tuple (value, rate) and published as one
    resource showing the value. The rate is model internals — it is the *reason* the
    resource moves, not telemetry in its own right — so it is deliberately not published.

    Writes preserve whichever half they are not changing: `set_rate` keeps the integrated
    value (so a rate change resumes from where the quantity actually is, rather than
    restarting), and `set_value`/`add` keep the rate (so a discrete jump does not stop the
    ramp).
    """

    def __init__(self, registrar, name, initial, resolution,
                 minimum=0.0, maximum=float("inf")):
        self.name = name
        self.minimum = minimum
        self.maximum = maximum
        # `resolution` is passed in rather than read from a module constant so every ramp in
        # the model shares one configured value — see DEFAULT_RAMP_RESOLUTION for why it is
        # worth configuring at all.
        self.cell = registrar.cell(
            (_clamp(float(initial), minimum, maximum), 0.0),
            evolution=_ramp_evolution(minimum, maximum),
            resolution=resolution,
            dynamics="real")
        # .map() rather than a bare lambda: a plain lambda is opaque, so the resource could
        # not be traced back to the cell backing it and would never be created on the Java
        # side. map() keeps that link, and the projection is what stops the whole (value,
        # rate) tuple from being published as the resource.
        registrar.resource(name, self.cell.map(lambda state: state[0]))

    @property
    def value(self) -> float:
        return self.cell.get()[0]

    @property
    def rate(self) -> float:
        return self.cell.get()[1]

    def set_rate(self, rate: float):
        value, _old_rate = self.cell.get()
        self.cell.emit((value, float(rate)))

    def set_value(self, value: float):
        _old_value, rate = self.cell.get()
        self.cell.emit((_clamp(float(value), self.minimum, self.maximum), rate))

    def add(self, delta: float):
        value, rate = self.cell.get()
        self.cell.emit((_clamp(value + float(delta), self.minimum, self.maximum), rate))


# =============================================================================
# Power equipment list
# =============================================================================
#
# Each component is a state machine whose current state fixes its power draw, as
# (CBE, MEV) watts -- current best estimate, and maximum expected value. The two are equal
# for every state here, but they are carried separately because the pair is what a real PEL
# tracks: the CBE bus load is what the spacecraft is expected to draw, the MEV load is what
# it must survive, and the two batteries below are sized against them independently.
#
# States are dict keys, not Enum members, because a discrete cell's value crosses to Java
# through str() -- an Enum member comes back as the string "EPS_State.OFF" and any
# attribute lookup on it fails. The cell holds the state NAME and the draw is looked up
# here, which behaves identically under both engines.

EPS_STATES            = {"OFF": (0.0, 0.0), "ON": (29.8, 29.8), "SAFE": (32.8, 32.8)}
CDH_STATES            = {"OFF": (0.0, 0.0), "ON": (30.0, 30.0)}
SSR_STATES            = {"OFF": (0.0, 0.0), "ON": (19.2, 19.2), "DOWNLINK": (21.4, 21.4)}
HEATER_STATES         = {"SURVIVAL": (114.4, 114.4), "DOWNLINK": (173.4, 173.4),
                         "RADAR_ON": (137.6, 137.6)}
ADCS_STATES           = {"OFF": (0.0, 0.0), "ON": (79.0, 79.0), "TURNING": (187.0, 187.0)}
HARNESS_LOSS_STATES   = {"OFF": (0.0, 0.0), "DOWNLINK": (38.4, 38.4), "RADAR_ON": (38.2, 38.2),
                         "RADAR_OFF": (16.2, 16.2), "TCM": (61.9, 61.9)}
PROP_STATES           = {"OFF": (0.0, 0.0), "DOWNLINK": (67.7, 67.7), "TCM": (249.6, 249.6)}
IDST_STATES           = {"OFF": (0.0, 0.0), "ON": (16.0, 16.0), "DOWNLINK": (22.0, 22.0)}
X_TWTA_STATES         = {"OFF": (0.0, 0.0), "ON": (173.4, 173.4)}
KA_TWTA_STATES        = {"OFF": (0.0, 0.0), "ON": (135.4, 135.4)}
RADAR_STATES          = {"OFF": (0.0, 0.0), "DOWNLINK": (198.2, 198.2), "ON": (543.2, 543.2)}
IMAGER_STATES         = {"OFF": (0.0, 0.0), "ON": (13.0, 13.0)}
RADAR_HEATER_STATES   = {"OFF": (0.0, 0.0), "CRUISE_SURVIVAL": (164.8, 164.8),
                         "SCIENCE_SURVIVAL": (9.5, 9.5)}
IMAGER_HEATER_STATES  = {"OFF": (0.0, 0.0), "CRUISE_SURVIVAL": (123.1, 123.1),
                         "IMAGER_ON": (92.4, 92.4), "DOWNLINK": (14.0, 14.0),
                         "SCIENCE_SURVIVAL": (105.2, 105.2)}

# component key -> (resource name, initial state, state table)
PEL_COMPONENTS = {
    "eps":            ("epsState",           "OFF",      EPS_STATES),
    "cdh":            ("cdhState",           "OFF",      CDH_STATES),
    "ssr":            ("ssrState",           "OFF",      SSR_STATES),
    "heaters":        ("heatersState",       "SURVIVAL", HEATER_STATES),
    "adcs":           ("adcsState",          "OFF",      ADCS_STATES),
    "harness_loss":   ("harnessLossState",   "OFF",      HARNESS_LOSS_STATES),
    "prop":           ("propState",          "OFF",      PROP_STATES),
    "idst":           ("idstState",          "OFF",      IDST_STATES),
    "x_twta":         ("xTwtaState",         "OFF",      X_TWTA_STATES),
    "ka_twta":        ("kaTwtaState",        "OFF",      KA_TWTA_STATES),
    "radar":          ("radarState",         "OFF",      RADAR_STATES),
    "imager":         ("imagerState",        "OFF",      IMAGER_STATES),
    "radar_heaters":  ("radarHeatersState",  "OFF",      RADAR_HEATER_STATES),
    "imager_heaters": ("imagerHeatersState", "OFF",      IMAGER_HEATER_STATES),
}


def _initial_pel_load():
    """The (CBE, MEV) bus load implied by every component's initial state.

    Computed from the tables rather than by reading the cells, because a cell has no value
    to read until after the model constructor returns — the framework assigns cell
    identities once construction is complete. Every derived cell below gets its initial
    value the same way, from plain numbers.
    """
    cbe = sum(table[initial][0] for _res, initial, table in PEL_COMPONENTS.values())
    mev = sum(table[initial][1] for _res, initial, table in PEL_COMPONENTS.values())
    return cbe, mev


class PELModel:
    """Component states plus the two bus-load totals derived from them."""

    def __init__(self, registrar):
        self.state_cells = {}
        for component, (resource_name, initial, _table) in PEL_COMPONENTS.items():
            cell = registrar.cell(initial)
            self.state_cells[component] = cell
            registrar.resource(resource_name, cell)

        initial_cbe, initial_mev = _initial_pel_load()
        # The totals are cells of their own rather than a sum computed at read time: a
        # resource has to be backed by exactly one cell, and a value summed across fourteen
        # of them cannot be traced to any single one.
        self.cbe_load = registrar.cell(initial_cbe)
        self.mev_load = registrar.cell(initial_mev)
        registrar.resource("spacecraft.cbeLoad", self.cbe_load)
        registrar.resource("spacecraft.mevLoad", self.mev_load)

    def set_state(self, component: str, state: str):
        if component not in PEL_COMPONENTS:
            raise ValueError(
                f"Unknown PEL component {component!r}. "
                f"Known components: {', '.join(sorted(PEL_COMPONENTS))}")
        _resource_name, _initial, table = PEL_COMPONENTS[component]
        if state not in table:
            raise ValueError(
                f"Unknown state {state!r} for PEL component {component!r}. "
                f"Valid states: {', '.join(table)}")
        self.state_cells[component].set(state)

    def get_state(self, component: str) -> str:
        return self.state_cells[component].get()

    def recompute_loads(self):
        cbe = 0.0
        mev = 0.0
        for component, (_resource_name, _initial, table) in PEL_COMPONENTS.items():
            component_cbe, component_mev = table[self.state_cells[component].get()]
            cbe += component_cbe
            mev += component_mev
        self.cbe_load.set(cbe)
        self.mev_load.set(mev)


# =============================================================================
# Solar array
# =============================================================================

SOLAR_INTENSITY_AT_EARTH = 1360.8   # W/m^2

ARRAY_DEPLOYMENT_STATES = ("UNDEPLOYED", "DEPLOYING", "DEPLOYED")

# Where the spacecraft is assumed to be before geometry has been computed once. Mars
# aphelion-ish, and the value the solar-distance cell starts at, so the array's initial
# power and the geometry model's initial range describe the same spacecraft.
INITIAL_SOLAR_DISTANCE_AU = 1.5


def _solar_power(distance_au, cell_area_m2, array_angle_deg, sun_fraction,
                 deployment_state, static_losses) -> float:
    """Power off the array, in watts. A stowed array produces nothing."""
    if deployment_state != "DEPLOYED" or distance_au <= 0.0:
        return 0.0
    return (SOLAR_INTENSITY_AT_EARTH / (distance_au * distance_au)
            * cell_area_m2
            * static_losses
            * math.cos(math.radians(array_angle_deg))
            * sun_fraction)


class GenericSolarArray:
    """Array pointing, deployment, and the power it produces.

    Solar distance and eclipse fraction are read through getters rather than copied in, so
    the array always sees the geometry model's current values instead of a stale snapshot.
    """

    def __init__(self, registrar, mech_area_m2, packing_factor, cell_efficiency,
                 conversion_efficiency, other_losses, initial_deployment_state,
                 initial_off_sun_angle_deg, solar_distance_au, sun_fraction):
        self.cell_area_m2 = mech_area_m2 * packing_factor
        self.static_losses = cell_efficiency * conversion_efficiency * other_losses
        self._solar_distance_au = solar_distance_au
        self._sun_fraction = sun_fraction

        # Held as an attribute so the batteries can be seeded from the same figure rather
        # than recomputing it from the same inputs and risking the two drifting apart.
        self.initial_power_production = _solar_power(
            INITIAL_SOLAR_DISTANCE_AU, self.cell_area_m2, initial_off_sun_angle_deg, 1.0,
            initial_deployment_state, self.static_losses)

        self.deployment_state = registrar.cell(initial_deployment_state)
        self.array_to_sun_angle = registrar.cell(float(initial_off_sun_angle_deg))
        self.power_production = registrar.cell(self.initial_power_production)

        registrar.resource("array.deploymentState", self.deployment_state)
        registrar.resource("spacecraft.arrayToSunAngle", self.array_to_sun_angle)
        registrar.resource("array.powerProduction", self.power_production)

    def set_deployment_state(self, state: str):
        if state not in ARRAY_DEPLOYMENT_STATES:
            raise ValueError(
                f"Unknown array deployment state {state!r}. "
                f"Valid states: {', '.join(ARRAY_DEPLOYMENT_STATES)}")
        self.deployment_state.set(state)

    def recompute(self):
        self.power_production.set(_solar_power(
            self._solar_distance_au(),
            self.cell_area_m2,
            self.array_to_sun_angle.get(),
            self._sun_fraction(),
            self.deployment_state.get(),
            self.static_losses))


# =============================================================================
# Battery
# =============================================================================

class BatteryModel:
    """One battery, charged by whatever the array produces beyond the bus load.

    Charge and state of charge are `Ramp`s: the engine integrates them from the current
    running between events, so nothing has to step them forward and both clamp at their
    physical limits instead of charging past full. Current, and the full/empty flags, are
    discrete — they change only when the power balance does.
    """

    def __init__(self, registrar, name, capacity_ah, bus_voltage, initial_soc_pct,
                 initial_production_w, initial_load_w, ramp_resolution):
        self.capacity_ah = capacity_ah
        self.bus_voltage = bus_voltage

        prefix = name + "Battery."
        initial_current = (initial_production_w - initial_load_w) / bus_voltage
        self.charge = Ramp(registrar, prefix + "batteryCharge",
                           capacity_ah * initial_soc_pct / 100.0, ramp_resolution,
                           minimum=0.0, maximum=capacity_ah)
        self.soc = Ramp(registrar, prefix + "batterySOC", initial_soc_pct, ramp_resolution,
                        minimum=0.0, maximum=100.0)
        self.current = registrar.cell(initial_current)
        self.full = registrar.cell(initial_soc_pct >= 100.0)
        self.empty = registrar.cell(initial_soc_pct <= 0.0)

        registrar.resource(prefix + "batteryCurrent", self.current)
        registrar.resource(prefix + "batteryFull", self.full)
        registrar.resource(prefix + "batteryEmpty", self.empty)

    def recompute(self, production_w: float, load_w: float):
        current_a = (production_w - load_w) / self.bus_voltage
        self.current.set(current_a)
        # Amp-hours accumulate per hour, and the profile is per second.
        self.charge.set_rate(current_a / SECONDS_PER_HOUR)
        self.soc.set_rate(current_a / self.capacity_ah * 100.0 / SECONDS_PER_HOUR)
        # Sampled rather than continuous: these two are read off the state of charge at the
        # moment the power balance changes, so between events they answer as of the last
        # change. `advance_state` refreshes them on every step for exactly that reason.
        soc = self.soc.value
        self.full.set(soc >= 100.0)
        self.empty.set(soc <= 0.0)


# =============================================================================
# Data
# =============================================================================

class OnboardBin:
    """One onboard storage bin: what it holds, and what has flowed through it.

    `volume` is what is stored and clamps at the bin's capacity; `received` counts
    everything the bin was OFFERED, clamped by nothing. The two diverge exactly when the bin
    overflows, and `received - removed - volume` is then the data that was dropped for want
    of space — which is worth seeing in a plan rather than hiding behind a single number.
    """

    def __init__(self, registrar, name, capacity_bits, ramp_resolution):
        self.name = name
        self.volume = Ramp(registrar, name + ".volume", 0.0, ramp_resolution,
                           maximum=capacity_bits)
        self.received = Ramp(registrar, name + ".receivedVolume", 0.0, ramp_resolution)
        self.removed = Ramp(registrar, name + ".removedVolume", 0.0, ramp_resolution)
        self.desired_receive_rate = registrar.cell(0.0)
        self.desired_remove_rate = registrar.cell(0.0)
        registrar.resource(name + ".desiredReceiveRate", self.desired_receive_rate)
        registrar.resource(name + ".desiredRemoveRate", self.desired_remove_rate)


class Data:
    """Onboard storage and its ground-side counterpart, in bits.

    Onboard capacity is split evenly across the bins rather than pooled. A shared pool would
    need each bin's ceiling to depend on how full its neighbours are at that instant, which
    is not something a bound fixed at declaration time can express; an equal share is a real
    allocation policy and it clamps correctly on its own.

    Downlink COPIES to the ground rather than moving: a bin's onboard volume is unchanged by
    a pass, and space is only recovered by `delete_data`. That is what makes
    `limit_to_sent_data` meaningful — the flight rule is to delete only what the ground has
    already confirmed.
    """

    def __init__(self, registrar, num_bins, onboard_capacity_bits, downlink_bit_rate,
                 ramp_resolution):
        self._downlink_bit_rate = downlink_bit_rate
        self.capacity_per_bin = onboard_capacity_bits / num_bins

        # Every bin adds four ramping resources, and each of those re-samples once per
        # `ramp_resolution` for the whole plan — bin count and resolution multiply.
        self.onboard_bins = [
            OnboardBin(registrar, f"scBin{i}", self.capacity_per_bin, ramp_resolution)
            for i in range(num_bins)]
        # The ground only ever accumulates; there is nothing to delete or overwrite there,
        # so a single received-volume ramp per bin says everything a plan needs.
        self.ground_bins = [
            Ramp(registrar, f"gndBin{i}.receivedVolume", 0.0, ramp_resolution)
            for i in range(num_bins)]

        self.onboard_volume = Ramp(registrar, "onboard.volume", 0.0, ramp_resolution,
                                   maximum=onboard_capacity_bits)
        self.ground_volume = Ramp(registrar, "ground.receivedVolume", 0.0, ramp_resolution)
        self.downlink_active = registrar.cell(False)
        self.volume_requested = registrar.cell(0.0)
        registrar.resource("downlinkActive", self.downlink_active)
        registrar.resource("volumeRequestedToDownlink", self.volume_requested)

    def downlink_rate(self) -> float:
        return self._downlink_bit_rate()

    def recompute_rates(self):
        """Push each bin's desired rates onto the ramps that integrate them."""
        net_total = 0.0
        for onboard_bin in self.onboard_bins:
            receive = onboard_bin.desired_receive_rate.get()
            remove = onboard_bin.desired_remove_rate.get()
            onboard_bin.volume.set_rate(receive - remove)
            onboard_bin.received.set_rate(receive)
            onboard_bin.removed.set_rate(remove)
            net_total += receive - remove
        self.onboard_volume.set_rate(net_total)


# =============================================================================
# Telecom
# =============================================================================

class TelecomModel:
    """The downlink itself. One bit rate, owned here and read by the data model."""

    def __init__(self, registrar, initial_bit_rate):
        self.downlink_bit_rate = registrar.cell(float(initial_bit_rate))
        registrar.resource("downlinkBitRate", self.downlink_bit_rate)


# =============================================================================
# Radar
# =============================================================================

# Collection mode -> instrument data rate, Mbps.
RADAR_DATA_MODES = {
    "OFF": 0.0,
    "LOW_RES": 0.1,
    "MED_RES": 1.0,
    "HI_RES": 4.0,
}


class RadarModel:
    def __init__(self, registrar):
        self.data_mode = registrar.cell("OFF")
        # Derived from the mode, but published from its own cell: a discrete cell's
        # projection is not applied on the packaged path, so a .map() of the mode would
        # publish the mode string under a resource named for a rate.
        self.data_rate = registrar.cell(0.0)
        registrar.resource("radarDataMode", self.data_mode)
        registrar.resource("radarDataRate", self.data_rate)

    def set_data_mode(self, mode: str) -> float:
        """Set the collection mode and return the resulting data rate in bits/second."""
        if mode not in RADAR_DATA_MODES:
            raise ValueError(
                f"Unknown radar data mode {mode!r}. "
                f"Valid modes: {', '.join(RADAR_DATA_MODES)}")
        rate_bps = RADAR_DATA_MODES[mode] * 1e6
        self.data_mode.set(mode)
        self.data_rate.set(rate_bps)
        return rate_bps


# =============================================================================
# Geometry
# =============================================================================

# Fraction of the Sun still visible in each eclipse state. The partial and annular figures
# are demo placeholders -- a real model computes them from the occultation geometry rather
# than assuming one number covers every partial eclipse.
ECLIPSE_SUN_FRACTION = {
    "NONE": 1.0,
    "ANNULAR": 0.7,
    "PARTIAL": 0.5,
    "FULL": 0.0,
}


class Body:
    def __init__(self, name, naif_id, frame, radius_km, mu,
                 calculate_altitude=False, calculate_earth_sc_angle=False,
                 calculate_beta_angle=False, calculate_orbit_parameters=False):
        self.name = name
        self.naif_id = naif_id
        self.frame = frame
        self.radius_km = radius_km
        self.mu = mu
        self.calculate_altitude = calculate_altitude
        self.calculate_earth_sc_angle = calculate_earth_sc_angle
        self.calculate_beta_angle = calculate_beta_angle
        self.calculate_orbit_parameters = calculate_orbit_parameters


DEFAULT_BODIES = {
    "SUN":   Body("SUN",   10,  "IAU_SUN",   695700.0, 1.32712440018e11),
    "EARTH": Body("EARTH", 399, "IAU_EARTH", 6378.14,  3.986004418e5),
    "MARS":  Body("MARS",  499, "IAU_MARS",  3396.19,  4.282837e4,
                  calculate_altitude=True, calculate_earth_sc_angle=True,
                  calculate_beta_angle=True, calculate_orbit_parameters=True),
}


class GeometryResources:
    """Every geometric quantity the rest of the model reads, one cell per resource."""

    def __init__(self, registrar, bodies, primary_body):
        self.bodies = bodies
        self.primary_body = primary_body

        self.spacecraft_body_range = {}
        self.spacecraft_body_speed = {}
        self.body_half_angle_size = {}
        self.sun_spacecraft_body_angle = {}
        self.sun_body_spacecraft_angle = {}
        self.spacecraft_eclipse_by_body = {}
        self.periapsis = {}
        self.apoapsis = {}
        self.spacecraft_altitude = {}
        self.beta_angle_by_body = {}
        self.earth_spacecraft_body_angle = {}
        self.orbit_inclination_by_body = {}
        self.orbit_period_by_body = {}

        for name, body in bodies.items():
            initial_range = (INITIAL_SOLAR_DISTANCE_AU * AU_TO_KM if name == "SUN"
                             else 1.0e5)
            self.spacecraft_body_range[name] = registrar.cell(initial_range)
            self.spacecraft_body_speed[name] = registrar.cell(0.0)
            self.body_half_angle_size[name] = registrar.cell(0.0)
            self.sun_spacecraft_body_angle[name] = registrar.cell(0.0)
            self.sun_body_spacecraft_angle[name] = registrar.cell(0.0)
            self.spacecraft_eclipse_by_body[name] = registrar.cell("NONE")
            self.periapsis[name] = registrar.cell(False)
            self.apoapsis[name] = registrar.cell(False)

            registrar.resource(f"SpacecraftBodyRange_{name}", self.spacecraft_body_range[name])
            registrar.resource(f"SpacecraftBodySpeed_{name}", self.spacecraft_body_speed[name])
            registrar.resource(f"BodyHalfAngleSize_{name}", self.body_half_angle_size[name])
            registrar.resource(f"SunSpacecraftBodyAngle_{name}", self.sun_spacecraft_body_angle[name])
            registrar.resource(f"SunBodySpacecraftAngle_{name}", self.sun_body_spacecraft_angle[name])
            registrar.resource(f"SpacecraftEclipseByBody_{name}", self.spacecraft_eclipse_by_body[name])
            registrar.resource(f"Periapsis_{name}", self.periapsis[name])
            registrar.resource(f"Apoapsis_{name}", self.apoapsis[name])

            if body.calculate_altitude:
                self.spacecraft_altitude[name] = registrar.cell(300.0)
                registrar.resource(f"SpacecraftAltitude_{name}", self.spacecraft_altitude[name])
            if body.calculate_beta_angle:
                self.beta_angle_by_body[name] = registrar.cell(0.0)
                registrar.resource(f"BetaAngle_{name}", self.beta_angle_by_body[name])
            if body.calculate_earth_sc_angle:
                self.earth_spacecraft_body_angle[name] = registrar.cell(0.0)
                registrar.resource(f"EarthSpacecraftAngle_{name}", self.earth_spacecraft_body_angle[name])
            if body.calculate_orbit_parameters:
                self.orbit_inclination_by_body[name] = registrar.cell(0.0)
                self.orbit_period_by_body[name] = registrar.cell(0.0)
                registrar.resource(f"orbitInclinationByBody_{name}", self.orbit_inclination_by_body[name])
                registrar.resource(f"orbitPeriodByBody_{name}", self.orbit_period_by_body[name])

        self.any_spacecraft_eclipse = registrar.cell("NONE")
        self.occultation = registrar.cell(0)
        self.fraction_of_sun_not_in_eclipse = registrar.cell(1.0)
        self.spacecraft_declination = registrar.cell(0.0)
        self.spacecraft_right_ascension = registrar.cell(0.0)
        # Solar distance in AU gets its own cell rather than being derived from the SUN
        # range at read time -- one resource per cell, and the power model wants AU.
        self.solar_distance_au = registrar.cell(INITIAL_SOLAR_DISTANCE_AU)

        registrar.resource("AnySpacecraftEclipse", self.any_spacecraft_eclipse)
        registrar.resource("Occultation", self.occultation)
        registrar.resource("FractionOfSunNotInEclipse", self.fraction_of_sun_not_in_eclipse)
        registrar.resource("SpacecraftDeclination", self.spacecraft_declination)
        registrar.resource("SpacecraftRightAscension", self.spacecraft_right_ascension)
        registrar.resource("SpacecraftBodyRange_SUN_AU", self.solar_distance_au)

    def set_sun_range_km(self, range_km: float):
        self.spacecraft_body_range["SUN"].set(range_km)
        self.solar_distance_au.set(range_km / AU_TO_KM)

    def worst_eclipse(self) -> str:
        """The eclipse state leaving the least Sun visible, across every body."""
        worst = "NONE"
        for cell in self.spacecraft_eclipse_by_body.values():
            state = cell.get()
            if ECLIPSE_SUN_FRACTION[state] < ECLIPSE_SUN_FRACTION[worst]:
                worst = state
        return worst


class GeometryCalculator:
    """Fills the geometry cells from SPICE.

    Without kernels there is nothing to compute, so `compute` returns False and the cells
    keep whatever the eclipse/occultation activities put there. That is what makes this
    model runnable with no kernel set: the power and data behaviour still exercises fully,
    driven by activities instead of ephemerides.
    """

    def __init__(self, resources, bodies, epoch_et=0.0,
                 spacecraft="MRO", frame="J2000", spice_kernel=None):
        self.res = resources
        self.bodies = bodies
        self.epoch_et = epoch_et
        self.spacecraft = spacecraft
        self.frame = frame
        self.spice = spice_kernel
        # Bound once, here, rather than imported per step: a kernel only exists because
        # `Mission.__init__` already imported the module, and re-entering the import on
        # every step would put it in the engine's hot loop for nothing. See `_load_spice`
        # for why this is not a module-level import.
        self._duration_to_et = _load_spice().duration_to_et if spice_kernel is not None else None

    def compute(self, elapsed) -> bool:
        if self.spice is None:
            return False
        et = self._duration_to_et(elapsed, self.epoch_et)
        for body in self.bodies.values():
            self._compute_body(body, et)
        return True

    def _compute_body(self, body, et):
        state = self.spice.state(body.name, self.spacecraft, self.frame, et)
        r = (state[0], state[1], state[2])   # body as seen from the spacecraft
        v = (state[3], state[4], state[5])
        r_norm = _norm(r)

        if body.name == "SUN":
            self.res.set_sun_range_km(float(r_norm))
        else:
            self.res.spacecraft_body_range[body.name].set(float(r_norm))
        self.res.spacecraft_body_speed[body.name].set(float(_norm(v)))
        if r_norm > 0:
            self.res.body_half_angle_size[body.name].set(
                math.degrees(math.asin(min(1.0, body.radius_km / r_norm))))

        if body.name != "SUN":
            sun_wrt_body = self.spice.position("SUN", body.name, self.frame, et)
            # r + (sun - body) is the Sun as seen from the spacecraft.
            self.res.sun_spacecraft_body_angle[body.name].set(
                math.degrees(_angle(_add(r, sun_wrt_body), r)))
            self.res.sun_body_spacecraft_angle[body.name].set(
                math.degrees(_angle(_scale(r, -1.0), sun_wrt_body)))

        if body.calculate_altitude:
            self.res.spacecraft_altitude[body.name].set(float(r_norm - body.radius_km))

        if body.calculate_beta_angle and body.name != "SUN":
            # Orbit normal. Negating both r and v to get the spacecraft relative to the
            # body leaves the cross product unchanged, so this is the same normal either way.
            normal = _normalize(_cross(r, v))
            sun_wrt_body = self.spice.position("SUN", body.name, self.frame, et)
            self.res.beta_angle_by_body[body.name].set(
                math.degrees(_angle(normal, _scale(sun_wrt_body, -1.0))) - 90.0)

        if body.calculate_earth_sc_angle:
            earth_wrt_sc = self.spice.position("EARTH", self.spacecraft, self.frame, et)
            self.res.earth_spacecraft_body_angle[body.name].set(
                math.degrees(_angle(earth_wrt_sc, r)))

        if body.calculate_orbit_parameters:
            inclination, period, eccentricity = _orbit_elements(r, v, body.mu)
            if eccentricity is not None and eccentricity < 1.0:
                self.res.orbit_inclination_by_body[body.name].set(float(math.degrees(inclination)))
                self.res.orbit_period_by_body[body.name].set(float(period))

        if body.name == self.res.primary_body and r_norm > 0:
            # Spacecraft as seen from the primary body, in the working frame.
            sc = _scale(r, -1.0)
            self.res.spacecraft_declination.set(math.degrees(math.asin(sc[2] / r_norm)))
            self.res.spacecraft_right_ascension.set(
                math.degrees(math.atan2(sc[1], sc[0])) % 360.0)


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _scale(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _norm(a):
    return math.sqrt(_dot(a, a))


def _normalize(a):
    n = _norm(a)
    return _scale(a, 1.0 / n) if n > 0 else a


def _angle(a, b):
    na, nb = _norm(a), _norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return math.acos(_clamp(_dot(a, b) / (na * nb), -1.0, 1.0))


def _orbit_elements(r, v, mu):
    """Inclination (rad), period (s), and eccentricity from a state vector."""
    r_norm = _norm(r)
    v2 = _dot(v, v)
    if r_norm == 0 or mu == 0:
        return 0.0, 0.0, None
    energy = v2 / 2.0 - mu / r_norm
    if energy == 0:
        return 0.0, 0.0, None
    a = -mu / (2.0 * energy)
    h = _cross(r, v)
    h_norm = _norm(h)
    inclination = math.acos(_clamp(h[2] / h_norm, -1.0, 1.0)) if h_norm > 0 else 0.0
    e_vec = _scale(_add(_scale(r, v2 - mu / r_norm), _scale(v, -_dot(r, v))), 1.0 / mu)
    eccentricity = _norm(e_vec)
    period = 2.0 * math.pi * math.sqrt(a ** 3 / mu) if a > 0 and eccentricity < 1.0 else 0.0
    return inclination, period, eccentricity


# =============================================================================
# Mission model
# =============================================================================

@MissionModel
class Mission(MissionModelBase):
    """The orbiter.

    Everything after `registrar` is simulation configuration, set per-plan in the PlanDev
    UI. Only scalars cross that boundary, which is why the array and battery settings are
    flat parameters here rather than nested configuration objects — a dataclass would
    arrive as a string.
    """

    def __init__(self, registrar,
                 battery_capacity_ah: float = 94.5,
                 bus_voltage: float = 28.0,
                 initial_soc_pct: float = 100.0,
                 array_mech_area_m2: float = 16.0,
                 array_packing_factor: float = 1.0,
                 cell_efficiency: float = 0.295,
                 conversion_efficiency: float = 0.9,
                 other_array_losses: float = 0.9,
                 array_initially_deployed: bool = True,
                 off_point_angle_deg: float = 70.0,
                 onboard_capacity_bits: float = 50e9,
                 initial_downlink_bit_rate: float = 1e6,
                 num_data_bins: int = 2,
                 ramp_resolution: str = DEFAULT_RAMP_RESOLUTION,
                 epoch_utc: str = "2026-04-05T12:00:00Z",
                 spacecraft: str = "MRO",
                 frame: str = "J2000",
                 kernel_paths: str = ""):
        # Elapsed plan time, so SPICE lookups are anchored to the plan's start rather than
        # to whenever an activity happened to begin. The cell evolves on its own; nothing
        # has to advance it.
        self._clock = clock(registrar)
        self.mission_clock = self._clock._system_clock

        # Parsed once and shared by every ramping resource, so the plan has a single dial
        # for how finely continuous profiles are recorded. See DEFAULT_RAMP_RESOLUTION.
        ramp_step = Duration.from_string(ramp_resolution)

        self.bodies = DEFAULT_BODIES
        self.geometry_res = GeometryResources(registrar, self.bodies, primary_body="MARS")

        # Kernels are a comma-separated list because configuration values are scalars. With
        # none configured the geometry cells are driven by the eclipse/occultation
        # activities instead, which is what makes this model runnable with no kernel set.
        paths = [path.strip() for path in kernel_paths.split(",") if path.strip()]
        spice_kernel = None
        epoch_et = 0.0
        if paths:
            # Imported here, not at module scope, so a context that only wants the model's
            # metadata never loads numpy's native extension — see `_load_spice`.
            spice = _load_spice()
            # A plan that configured kernels asked for SPICE geometry. Failing here beats
            # silently leaving every geometry resource frozen at its initial value and
            # reporting the resulting power numbers as if they meant something.
            if not spice.SPICE_AVAILABLE:
                raise ImportError(
                    "kernel_paths is configured but spiceypy is not installed. "
                    "Install it with: pip install pymerlin[spice], or clear kernel_paths "
                    "to run with activity-driven geometry.")
            spice_kernel = spice.SpiceKernel(registrar, kernel_paths=paths)
            spice_kernel.load_kernels()
            epoch_et = spice_kernel.utc_to_et(epoch_utc)
        self.geometry_calc = GeometryCalculator(
            self.geometry_res, self.bodies, epoch_et=epoch_et,
            spacecraft=spacecraft, frame=frame, spice_kernel=spice_kernel)

        self.pel = PELModel(registrar)
        initial_deployment = "DEPLOYED" if array_initially_deployed else "UNDEPLOYED"
        self.array = GenericSolarArray(
            registrar,
            mech_area_m2=array_mech_area_m2,
            packing_factor=array_packing_factor,
            cell_efficiency=cell_efficiency,
            conversion_efficiency=conversion_efficiency,
            other_losses=other_array_losses,
            initial_deployment_state=initial_deployment,
            initial_off_sun_angle_deg=off_point_angle_deg,
            solar_distance_au=self.geometry_res.solar_distance_au.get,
            sun_fraction=self.geometry_res.fraction_of_sun_not_in_eclipse.get)

        # Seeded from plain numbers rather than by reading the cells just declared: a cell
        # has no readable value until the model constructor has returned.
        initial_cbe_load, initial_mev_load = _initial_pel_load()
        initial_production = self.array.initial_power_production
        self.cbe_battery = BatteryModel(
            registrar, "cbe", battery_capacity_ah, bus_voltage, initial_soc_pct,
            initial_production, initial_cbe_load, ramp_step)
        self.mev_battery = BatteryModel(
            registrar, "mev", battery_capacity_ah, bus_voltage, initial_soc_pct,
            initial_production, initial_mev_load, ramp_step)

        self.telecom = TelecomModel(registrar, initial_downlink_bit_rate)
        self.data = Data(registrar, num_data_bins, onboard_capacity_bits,
                         downlink_bit_rate=self.telecom.downlink_bit_rate.get,
                         ramp_resolution=ramp_step)
        self.radar = RadarModel(registrar)


# =============================================================================
# Derived-state recomputation
# =============================================================================

def _recompute_power(mission):
    """Re-derive everything that follows from the current power balance.

    Array production, bus load, and battery behaviour are CONSEQUENCES of component states
    and geometry, not independent numbers an activity gets to set alongside them. Every
    activity that changes an input calls this, so an activity says only "the radar is on"
    and the 543 W, the resulting battery current, and the discharge rate all follow.
    """
    mission.array.recompute()
    mission.pel.recompute_loads()
    production = mission.array.power_production.get()
    mission.cbe_battery.recompute(production, mission.pel.cbe_load.get())
    mission.mev_battery.recompute(production, mission.pel.mev_load.get())


def _set_pel_states(mission, **states):
    """Set one or more PEL component states, then re-derive the power balance.

    Takes every component at once rather than one per call so a mode change lands as a
    single coherent update — and so the recompute cannot be forgotten.
    """
    for component, state in states.items():
        mission.pel.set_state(component, state)
    _recompute_power(mission)


def _apply_sun_fraction(mission, sun_fraction):
    mission.geometry_res.fraction_of_sun_not_in_eclipse.set(sun_fraction)
    _recompute_power(mission)


def _set_eclipse(mission, body, eclipse_type):
    """Put `body` into `eclipse_type` and apply the resulting illumination.

    The published fraction comes from the WORST eclipse across all bodies, not from this
    one: leaving Mars' shadow while still behind Phobos is not sunlight. Returns the
    fraction that ended up applying.
    """
    if eclipse_type not in ECLIPSE_SUN_FRACTION:
        raise ValueError(
            f"Unknown eclipse type {eclipse_type!r}. "
            f"Valid types: {', '.join(ECLIPSE_SUN_FRACTION)}")
    mission.geometry_res.spacecraft_eclipse_by_body[body].set(eclipse_type)
    worst = mission.geometry_res.worst_eclipse()
    mission.geometry_res.any_spacecraft_eclipse.set(worst)
    sun_fraction = ECLIPSE_SUN_FRACTION[worst]
    _apply_sun_fraction(mission, sun_fraction)
    return sun_fraction


def _advance(mission, total, step):
    """Delay for `total`, refreshing geometry and the power balance every `step`.

    Only the SPICE-derived geometry and the sampled battery flags need this cadence — the
    battery charge and data volumes integrate themselves between events, so nothing here
    accumulates step error into them.

    Durations are compared as numbers: two Durations cannot be compared directly.
    """
    total_us = total.to_number_in(MICROSECONDS)
    step_us = step.to_number_in(MICROSECONDS)
    if step_us <= 0:
        raise ValueError("step must be a positive duration")
    elapsed_us = 0.0
    while elapsed_us < total_us:
        this_step_us = int(min(step_us, total_us - elapsed_us))
        delay(Duration.of(this_step_us, MICROSECONDS))
        elapsed_us += this_step_us
        mission.geometry_calc.compute(mission.mission_clock.get())
        _recompute_power(mission)


def _playback(mission, volume_bits, window):
    """Downlink onboard data, draining bins in priority order.

    Exact rather than sampled: each bin's transfer is set up as a rate and then delayed for
    precisely as long as that bin's undownlinked data takes to move, so no fixed step size
    can leave a partial bit behind or overshoot what was actually onboard.

    `volume_bits <= 0` means "whatever fits in the window". The activity always occupies the
    full window even if the data runs out early — a scheduled pass holds the antenna
    regardless.
    """
    rate = mission.data.downlink_rate()
    window_us = window.to_number_in(MICROSECONDS)
    if rate <= 0.0:
        delay(Duration.of(int(window_us), MICROSECONDS))
        return 0.0

    remaining_us = window_us
    remaining_bits = volume_bits if volume_bits > 0 else float("inf")
    moved = 0.0

    mission.data.downlink_active.set(True)
    mission.data.volume_requested.set(max(0.0, volume_bits))

    for onboard_bin, ground_bin in zip(mission.data.onboard_bins, mission.data.ground_bins):
        if remaining_us <= 0 or remaining_bits <= 0:
            break
        # Bounded by what is still ON BOARD as well as by what the ground has yet to see:
        # a bin that overflowed received more than it could store, and the difference was
        # dropped rather than kept for a later pass.
        available = min(onboard_bin.received.value - ground_bin.value,
                        onboard_bin.volume.value)
        sendable = min(available, remaining_bits, rate * remaining_us / 1e6)
        transfer_us = int(sendable / rate * 1e6) if sendable > 0 else 0
        if transfer_us <= 0:
            continue
        ground_bin.set_rate(rate)
        mission.data.ground_volume.set_rate(rate)
        delay(Duration.of(transfer_us, MICROSECONDS))
        ground_bin.set_rate(0.0)
        mission.data.ground_volume.set_rate(0.0)

        sent = rate * transfer_us / 1e6
        moved += sent
        remaining_bits -= sent
        remaining_us -= transfer_us
        if volume_bits > 0:
            mission.data.volume_requested.set(max(0.0, remaining_bits))

    mission.data.downlink_active.set(False)
    mission.data.volume_requested.set(0.0)
    if remaining_us > 0:
        delay(Duration.of(int(remaining_us), MICROSECONDS))
    return moved


def _delete_data(mission, bin_index, volume_bits, limit_to_sent_data):
    """Free up to `volume_bits` from a bin. Returns the volume actually deleted."""
    onboard_bin = mission.data.onboard_bins[bin_index]
    ground_bin = mission.data.ground_bins[bin_index]
    onboard = onboard_bin.volume.value
    not_yet_downlinked = max(0.0, onboard_bin.received.value - ground_bin.value)
    deletable = max(0.0, onboard - not_yet_downlinked) if limit_to_sent_data else onboard
    deleted = max(0.0, min(volume_bits, deletable))
    onboard_bin.volume.add(-deleted)
    onboard_bin.removed.add(deleted)
    mission.data.onboard_volume.add(-deleted)
    return deleted


# =============================================================================
# Geometry activities
# =============================================================================

@Mission.ActivityType
def apoapsis(mission, body: str = "MARS"):
    """Mark apoapsis passage as a one-second pulse the plan can key off."""
    mission.geometry_res.apoapsis[body].set(True)
    delay(Duration.SECOND)
    mission.geometry_res.apoapsis[body].set(False)


@Mission.ActivityType
def periapsis(mission, body: str = "MARS"):
    """Mark periapsis passage as a one-second pulse the plan can key off."""
    mission.geometry_res.periapsis[body].set(True)
    delay(Duration.SECOND)
    mission.geometry_res.periapsis[body].set(False)


@Mission.ActivityType
def enter_occultation(mission, body: str = "MARS", station: str = "DSS-24"):
    """Start an occultation of the link to `station`. Counted, since several can overlap."""
    mission.geometry_res.occultation.add(1)


@Mission.ActivityType
def exit_occultation(mission, body: str = "MARS", station: str = "DSS-24"):
    mission.geometry_res.occultation.add(-1)


@Mission.ActivityType
def enter_eclipse(mission, body: str = "MARS", eclipse_type: str = "FULL",
                  duration: str = "00:30:00", segments: int = 10):
    """Enter an eclipse, ramping the visible fraction of the Sun over `duration`.

    Stepped rather than continuous because the fraction feeds the power balance, which is
    re-derived per step — the array output, both battery currents, and the discharge rates
    all follow each step down. `segments` trades profile smoothness against event count.
    """
    segments = max(1, int(segments))
    total_us = Duration.from_string(duration).to_number_in(MICROSECONDS)
    segment_us = int(total_us / segments)

    start_fraction = mission.geometry_res.fraction_of_sun_not_in_eclipse.get()
    # Record the eclipse up front so the state is right from the moment entry begins, then
    # walk the illumination down to the fraction that state implies.
    target_fraction = _set_eclipse(mission, body, eclipse_type)
    _apply_sun_fraction(mission, start_fraction)
    for segment in range(1, segments + 1):
        delay(Duration.of(segment_us, MICROSECONDS))
        _apply_sun_fraction(mission, start_fraction
                            + (target_fraction - start_fraction) * segment / segments)


@Mission.ActivityType
def exit_eclipse(mission, body: str = "MARS"):
    """Leave `body`'s shadow. Any other body still eclipsing keeps its own contribution."""
    _set_eclipse(mission, body, "NONE")
    delay(Duration.SECOND)


@Mission.ActivityType
def advance_state(mission, duration: str = "24:00:00", step: str = "00:05:00"):
    """Step SPICE geometry and the sampled battery flags across the plan.

    Schedule this alongside the rest of the plan when running against the Java-backed
    engine, where activities run concurrently. The standalone `simulate()` engine runs
    activities strictly in sequence, so a long-running daemon there would block everything
    scheduled after it — use `run_orbiter_demo`, which steps its own timeline instead.
    """
    _advance(mission, Duration.from_string(duration), Duration.from_string(step))


# =============================================================================
# Power activities
# =============================================================================

@Mission.ActivityType
def deploy_solar_array(mission, duration: str = "00:30:00"):
    """Deploy the array. It produces nothing until deployment completes."""
    mission.array.set_deployment_state("DEPLOYING")
    _recompute_power(mission)
    delay(Duration.from_string(duration))
    mission.array.set_deployment_state("DEPLOYED")
    _recompute_power(mission)


@Mission.ActivityType
def point_solar_array(mission, off_sun_angle_deg: float = 0.0):
    """Slew the array to `off_sun_angle_deg` off the Sun line.

    Power falls off with the cosine of this angle, so it is the cheapest way for a plan to
    trade array output against whatever else the attitude is being held for.
    """
    mission.array.array_to_sun_angle.set(float(off_sun_angle_deg))
    _recompute_power(mission)


# =============================================================================
# Data activities
# =============================================================================

@Mission.ActivityType
def change_data_generation_rate(mission, bin_index: int = 0, rate: float = 0.0):
    """Set a bin's net generation rate in bits/second. Negative drains the bin."""
    onboard_bin = mission.data.onboard_bins[bin_index]
    if rate >= 0:
        onboard_bin.desired_receive_rate.set(float(rate))
        onboard_bin.desired_remove_rate.set(0.0)
    else:
        onboard_bin.desired_receive_rate.set(0.0)
        onboard_bin.desired_remove_rate.set(-float(rate))
    mission.data.recompute_rates()


@Mission.ActivityType
def generate_data(mission, bin_index: int = 0, rate: float = 0.0,
                  volume: float = 0.0, duration: str = ""):
    """Fill a bin for a while, then stop.

    Give any two of rate (bits/s), volume (bits), and duration; the third follows. Zero (or
    empty, for duration) means "derive me". Adds to whatever the bin is already receiving,
    so a background collection rate keeps running underneath.
    """
    duration_s = Duration.from_string(duration).to_number_in(SECONDS) if duration else 0.0
    rate, _volume, duration_s = _derive_generation(float(rate), float(volume), duration_s)

    onboard_bin = mission.data.onboard_bins[bin_index]
    onboard_bin.desired_receive_rate.set(onboard_bin.desired_receive_rate.get() + rate)
    mission.data.recompute_rates()

    delay(Duration.of(int(duration_s * 1e6), MICROSECONDS))

    onboard_bin.desired_receive_rate.set(onboard_bin.desired_receive_rate.get() - rate)
    mission.data.recompute_rates()


def _derive_generation(rate, volume, duration_s):
    given = (rate > 0) + (volume > 0) + (duration_s > 0)
    if given < 2:
        raise ValueError(
            "Specify at least two of rate, volume, and duration; got "
            f"rate={rate}, volume={volume}, duration_s={duration_s}")
    if duration_s <= 0:
        duration_s = volume / rate
    elif volume <= 0:
        volume = rate * duration_s
    elif rate <= 0:
        rate = volume / duration_s
    return rate, volume, duration_s


@Mission.ActivityType
def delete_data(mission, bin_index: int = 0, volume: float = -1.0,
                limit_to_sent_data: bool = True):
    """Free space in a bin. A negative `volume` deletes everything eligible.

    With `limit_to_sent_data` set — the usual flight rule — only data the ground has already
    received can go, so a deletion can never destroy something that has not been downlinked.
    """
    requested = float("inf") if volume < 0 else float(volume)
    _delete_data(mission, bin_index, requested, limit_to_sent_data)


@Mission.ActivityType
def playback_data(mission, volume: float = -1.0, duration: str = "01:00:00"):
    """Downlink for a fixed window, optionally stopping once `volume` bits have gone."""
    _playback(mission, float(volume), Duration.from_string(duration))


@Mission.ActivityType
def reprioritize_data(mission, volume: float = 0.0, source_bin: int = 0,
                      destination_bin: int = 1):
    """Move data between onboard bins, up to the destination's remaining capacity."""
    source = mission.data.onboard_bins[source_bin]
    destination = mission.data.onboard_bins[destination_bin]
    receivable = destination.volume.maximum - destination.volume.value
    moved = max(0.0, min(float(volume), source.volume.value, receivable))
    source.volume.add(-moved)
    source.removed.add(moved)
    destination.volume.add(moved)
    destination.received.add(moved)


# =============================================================================
# Telecom activities
# =============================================================================

MAX_DOWNLINK_RATE_KBPS = 2000.0


@Mission.ActivityType
def downlink(mission, duration: str = "01:00:00", bit_rate_kbps: float = 1000.0):
    """A downlink pass: configure the link, play back, then delete what the ground has.

    Restores the radar and imager to whatever they were doing before the pass rather than
    to a fixed state, so a pass taken during science does not silently end the science.
    """
    if bit_rate_kbps > MAX_DOWNLINK_RATE_KBPS:
        raise ValueError(
            f"Downlink rate {bit_rate_kbps} kbps is beyond the buffer limit of "
            f"{MAX_DOWNLINK_RATE_KBPS} kbps")

    mission.telecom.downlink_bit_rate.set(float(bit_rate_kbps) * 1000.0)

    was_radar_on = mission.pel.get_state("radar") != "OFF"
    was_imager_on = mission.pel.get_state("imager") != "OFF"

    _set_pel_states(
        mission,
        x_twta="ON", ka_twta="ON", ssr="DOWNLINK", idst="DOWNLINK", prop="DOWNLINK",
        radar="DOWNLINK", radar_heaters="OFF", imager_heaters="DOWNLINK",
        heaters="DOWNLINK", harness_loss="DOWNLINK")

    _playback(mission, -1.0, Duration.from_string(duration))
    for bin_index in range(len(mission.data.onboard_bins)):
        _delete_data(mission, bin_index, float("inf"), limit_to_sent_data=True)

    if was_radar_on:
        _set_pel_states(mission, radar="ON", radar_heaters="OFF",
                        heaters="RADAR_ON", harness_loss="RADAR_ON")
    else:
        _set_pel_states(mission, radar="OFF", radar_heaters="SCIENCE_SURVIVAL",
                        heaters="SURVIVAL", harness_loss="RADAR_OFF")

    if was_imager_on:
        _set_pel_states(mission, imager="ON", imager_heaters="IMAGER_ON")
    else:
        _set_pel_states(mission, imager="OFF", imager_heaters="SCIENCE_SURVIVAL")

    _set_pel_states(mission, x_twta="OFF", ka_twta="OFF", ssr="ON", idst="ON", prop="OFF")


# =============================================================================
# Radar activities
# =============================================================================

@Mission.ActivityType
def radar_on(mission):
    mission.pel.set_state("radar", "ON")
    mission.pel.set_state("heaters", "RADAR_ON")
    mission.pel.set_state("harness_loss", "RADAR_ON")
    _recompute_power(mission)
    delay(Duration.SECOND)


@Mission.ActivityType
def radar_off(mission):
    """Power the radar down, and stop the data it was feeding into bin 0 with it."""
    _change_radar_data_mode(mission, "OFF")
    _set_pel_states(mission, radar="OFF", radar_heaters="SCIENCE_SURVIVAL",
                    heaters="SURVIVAL", harness_loss="RADAR_OFF")
    delay(Duration.SECOND)


@Mission.ActivityType
def change_radar_data_mode(mission, mode: str = "LOW_RES"):
    """Set the radar collection mode and feed its rate into data bin 0."""
    _change_radar_data_mode(mission, mode)


def _change_radar_data_mode(mission, mode):
    rate_bps = mission.radar.set_data_mode(mode)
    bin_zero = mission.data.onboard_bins[0]
    bin_zero.desired_receive_rate.set(rate_bps)
    bin_zero.desired_remove_rate.set(0.0)
    mission.data.recompute_rates()


# =============================================================================
# Demo scenario
# =============================================================================

@Mission.ActivityType
def run_orbiter_demo(mission, step: str = "00:05:00"):
    """A self-contained timeline: deploy, collect radar science, eclipse, downlink.

    Written as one activity rather than a schedule of several so it also runs under the
    standalone engine, which executes activities strictly in sequence. `step` only sets how
    often geometry and the sampled battery flags refresh — the battery and the data bins
    integrate continuously no matter what it is set to.
    """
    step_duration = Duration.from_string(step)

    mission.array.set_deployment_state("DEPLOYED")
    _set_pel_states(mission, eps="ON", cdh="ON", ssr="ON", idst="ON", adcs="ON",
                    harness_loss="RADAR_OFF", radar_heaters="SCIENCE_SURVIVAL",
                    imager_heaters="SCIENCE_SURVIVAL")
    _advance(mission, Duration.from_string("00:30:00"), step_duration)

    # Radar science: 4 Mbps into bin 0, and 543 W off the battery.
    _set_pel_states(mission, radar="ON", heaters="RADAR_ON", harness_loss="RADAR_ON")
    _change_radar_data_mode(mission, "HI_RES")
    _advance(mission, Duration.from_string("01:00:00"), step_duration)

    # Into Mars' shadow: array output goes to zero and both batteries start discharging.
    _set_eclipse(mission, "MARS", "FULL")
    _advance(mission, Duration.from_string("00:45:00"), step_duration)

    _set_eclipse(mission, "MARS", "NONE")
    _advance(mission, Duration.from_string("00:30:00"), step_duration)

    # Stop collecting and play the bins back at 1.5 Mbps.
    _change_radar_data_mode(mission, "OFF")
    mission.telecom.downlink_bit_rate.set(1500.0 * 1000.0)
    _set_pel_states(mission, x_twta="ON", ka_twta="ON", ssr="DOWNLINK",
                    radar="DOWNLINK", heaters="DOWNLINK", harness_loss="DOWNLINK")
    _playback(mission, -1.0, Duration.from_string("01:00:00"))

    for bin_index in range(len(mission.data.onboard_bins)):
        _delete_data(mission, bin_index, float("inf"), limit_to_sent_data=True)
    _set_pel_states(mission, x_twta="OFF", ka_twta="OFF", ssr="ON", radar="OFF",
                    heaters="SURVIVAL", harness_loss="RADAR_OFF")
    _advance(mission, Duration.from_string("00:30:00"), step_duration)
