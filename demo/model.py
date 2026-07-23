from pymerlin import MissionModel
from pymerlin.model_actions import delay, wait_until, spawn


# Arbitrary demo battery capacity, Watt-hours. Only used to convert net Watts
# into a %/sec rate for battery_pct.
_BATTERY_CAPACITY_WH = 100.0


def _recompute_battery_rate(mission):
    """battery_pct's charge/discharge rate is DERIVED from net power balance
    (solar generation minus current draw), not an independently-set rate --
    mirrors stock Aerie's RealResource.minus(...) combinator (see
    merlin-framework's RealResource.java and examples/foo-missionmodel's
    `registrar.real("/batterySoC", this.source.minus(this.sink))`), which
    composes two resources' RealDynamics via component-wise {initial, rate}
    subtraction. Computed imperatively here, called after every change to either
    input, since pymerlin doesn't yet expose that resource-algebra layer to
    Python model authors -- same net numeric behavior either way."""
    net_w = mission.solar_generation_w.get() - mission.power_w.get()
    mission.battery_pct.set_rate((net_w / _BATTERY_CAPACITY_WH) * (100.0 / 3600.0))


@MissionModel
class Mission:
    def __init__(self, registrar, initial_battery_pct: float = 100.0, high_gain: bool = True):
        # Configuration (roadmap §7): constructor parameters after `registrar` become the
        # model's simulation-configuration schema, set per-plan in the Aerie UI. Downlink
        # transmits faster with the high-gain antenna; battery starts at the configured level.
        self.high_gain = high_gain

        self.power_w = registrar.cell(0.0)
        self.solar_generation_w = registrar.cell(0.0)
        self.data_volume_mb = registrar.linear(0.0)
        self.temperature_c = registrar.cell(20.0)
        self.battery_pct = registrar.linear(float(initial_battery_pct))
        self.mode = registrar.cell("IDLE")

        registrar.resource("/power_w", self.power_w)
        registrar.resource("/solar_generation_w", self.solar_generation_w)
        registrar.resource("/data_volume_mb", self.data_volume_mb)
        registrar.resource("/temperature_c", self.temperature_c)
        registrar.resource("/battery_pct", self.battery_pct)
        registrar.resource("/mode", self.mode)


@Mission.ActivityType
def collect_data(mission, data=1024):
    """Turns on the instrument, records data for 5 minutes, then powers off.
    The buffer fills continuously (linear RealDynamics) at data/duration MB/s over
    the collection window instead of jumping by `data` only when the activity ends."""
    mission.mode.emit("COLLECTING")
    mission.power_w.emit(lambda x: x + 15.0)
    _recompute_battery_rate(mission)
    mission.temperature_c.emit(lambda x: x + 5.0)

    duration_s = 5 * 60
    mission.data_volume_mb.set_rate(data / duration_s)

    delay("00:05:00")

    mission.data_volume_mb.set_rate(0.0)
    mission.power_w.emit(lambda x: x - 15.0)
    _recompute_battery_rate(mission)
    mission.temperature_c.emit(lambda x: x - 5.0)
    mission.mode.emit("IDLE")

    spawn(compress_data(mission))


@Mission.ActivityType
def compress_data(mission):
    """Compresses data after collection — spawned as a child activity. Drains the
    buffer linearly (RealDynamics) down to 60% of its pre-compression volume over
    the 2-minute window, instead of jumping only when the activity ends."""
    mission.mode.emit("COMPRESSING")
    mission.power_w.emit(lambda x: x + 5.0)
    _recompute_battery_rate(mission)

    duration_s = 2 * 60
    volume = mission.data_volume_mb.get()
    target = volume * 0.6
    mission.data_volume_mb.set_rate((target - volume) / duration_s)

    delay("00:02:00")

    mission.data_volume_mb.set_rate(0.0)
    mission.data_volume_mb.emit(target)
    mission.power_w.emit(lambda x: x - 5.0)
    _recompute_battery_rate(mission)
    mission.mode.emit("IDLE")


@Mission.ActivityType
def downlink(mission):
    """Waits for data to be available, then transmits — draining the buffer at a
    constant rate over the 10-minute span so data_volume_mb ramps down continuously
    (linear RealDynamics) instead of stepping to zero only when the activity ends."""
    wait_until(lambda: mission.data_volume_mb.get() > 0.0)

    mission.mode.emit("DOWNLINKING")
    mission.power_w.emit(lambda x: x + 25.0)
    _recompute_battery_rate(mission)

    # High-gain antenna drains the buffer in half the time (config-driven behavior).
    minutes = 5 if mission.high_gain else 10
    duration_s = minutes * 60
    volume = mission.data_volume_mb.get()
    mission.data_volume_mb.set_rate(-volume / duration_s)

    delay(f"00:{minutes:02d}:00")

    mission.data_volume_mb.set_rate(0.0)
    mission.data_volume_mb.emit(0.0)
    mission.power_w.emit(lambda x: x - 25.0)
    _recompute_battery_rate(mission)
    mission.mode.emit("IDLE")


@Mission.ActivityType
def extend_solar_panels(mission, generation_w=20.0):
    """Deploys the solar panels. battery_pct's rate is derived from net power
    balance (this generation minus whatever else is currently drawing power), not
    an independent hardcoded charge rate — see _recompute_battery_rate. Doesn't
    touch `mode`: that field tracks short-lived foreground activity state
    (COLLECTING, DOWNLINKING, ...), and panel deployment is a persistent
    background state that would otherwise get clobbered back to IDLE by the next
    unrelated activity."""
    mission.solar_generation_w.emit(generation_w)
    _recompute_battery_rate(mission)


@Mission.ActivityType
def retract_solar_panels(mission):
    """Retracts the solar panels, removing their contribution to net power
    balance. battery_pct's rate is recomputed accordingly — it'll now reflect
    whatever else happens to be drawing power at the time, not simply "stop"."""
    mission.solar_generation_w.emit(0.0)
    _recompute_battery_rate(mission)


@Mission.ActivityType
def safe_mode(mission):
    """Enters safe mode: powers down non-essential systems, waits, recovers."""
    mission.mode.emit("SAFE")
    mission.power_w.emit(3.0)
    _recompute_battery_rate(mission)

    delay("00:30:00")

    mission.power_w.emit(0.0)
    _recompute_battery_rate(mission)
    mission.mode.emit("IDLE")
