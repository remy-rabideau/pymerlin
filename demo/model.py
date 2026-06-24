from pymerlin import MissionModel
from pymerlin.model_actions import delay, call

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
BATTERY_CAPACITY_WH = 100.0      # watt-hours
DOWNLINK_RATE_MBPS  = 50.0       # megabits per second
SCIENCE_RATE_MBPS   = 10.0       # data production rate during science obs
STORAGE_CAPACITY_MB = 1000.0     # onboard storage cap


# ---------------------------------------------------------------------------
# Mission model
# ---------------------------------------------------------------------------
@MissionModel
class Mission:
    def __init__(self, registrar):
        # Power
        self.battery_soc   = registrar.cell(BATTERY_CAPACITY_WH)   # Wh remaining
        self.power_draw_w  = registrar.cell(0.0)                    # current draw (W)

        # Data
        self.data_volume_mb = registrar.cell(0.0)                   # MB stored onboard

        # Spacecraft mode: "safe" | "nominal" | "science" | "downlink"
        self.mode = registrar.cell("nominal")

        # Resources (visible in Aerie timeline)
        registrar.resource("/power/battery_soc_wh",  self.battery_soc)
        registrar.resource("/power/draw_w",          self.power_draw_w)
        registrar.resource("/data/volume_mb",        self.data_volume_mb)
        registrar.resource("/spacecraft/mode",       self.mode)


# ---------------------------------------------------------------------------
# Activities
# ---------------------------------------------------------------------------

@Mission.ActivityType
def science_observation(mission, duration_min: float = 30.0):
    """Collect science data for a given duration (minutes)."""
    mission.mode.set("science")
    mission.power_draw_w.set(25.0)

    steps = int(duration_min)
    for _ in range(steps):
        # Accumulate data and drain battery each simulated minute
        mission.data_volume_mb += SCIENCE_RATE_MBPS * 60 / 8  # MB per minute
        mission.battery_soc    -= mission.power_draw_w.get() * (1/60)  # Wh per minute

        # Cap storage
        if mission.data_volume_mb.get() >= STORAGE_CAPACITY_MB:
            mission.data_volume_mb.set(STORAGE_CAPACITY_MB)

        delay("00:01:00")

    mission.power_draw_w.set(5.0)
    mission.mode.set("nominal")


@Mission.ActivityType
def downlink(mission, duration_min: float = 20.0):
    """Transmit stored data to ground at DOWNLINK_RATE_MBPS."""
    mission.mode.set("downlink")
    mission.power_draw_w.set(30.0)

    steps = int(duration_min)
    for _ in range(steps):
        transmitted = DOWNLINK_RATE_MBPS * 60 / 8  # MB per minute
        current = mission.data_volume_mb.get()
        mission.data_volume_mb.set(max(0.0, current - transmitted))
        mission.battery_soc -= mission.power_draw_w.get() * (1/60)
        delay("00:01:00")

        if mission.data_volume_mb.get() <= 0.0:
            break

    mission.power_draw_w.set(5.0)
    mission.mode.set("nominal")


@Mission.ActivityType
def charge_battery(mission, duration_min: float = 60.0):
    """Charge battery via solar panels (no other activities during charge)."""
    mission.mode.set("safe")
    mission.power_draw_w.set(-20.0)   # negative = net charging

    steps = int(duration_min)
    for _ in range(steps):
        mission.battery_soc += 20.0 * (1/60)   # 20W charge rate
        if mission.battery_soc.get() >= BATTERY_CAPACITY_WH:
            mission.battery_soc.set(BATTERY_CAPACITY_WH)
            break
        delay("00:01:00")

    mission.power_draw_w.set(5.0)
    mission.mode.set("nominal")


@Mission.ActivityType
def contact_pass(mission, obs_min: float = 30.0, dl_min: float = 20.0):
    """Combined contact window: observe then downlink."""
    call(science_observation(mission, obs_min))
    call(downlink(mission, dl_min))