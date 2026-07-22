from pymerlin import MissionModel
from pymerlin.model_actions import delay, wait_until, spawn


@MissionModel
class Mission:
    def __init__(self, registrar):
        self.power_w = registrar.cell(0.0)
        self.data_volume_mb = registrar.linear(0.0)
        self.temperature_c = registrar.cell(20.0)
        self.mode = registrar.cell("IDLE")

        registrar.resource("/power_w", self.power_w)
        registrar.resource("/data_volume_mb", self.data_volume_mb)
        registrar.resource("/temperature_c", self.temperature_c)
        registrar.resource("/mode", self.mode)


@Mission.ActivityType
def collect_data(mission, data=1024):
    """Turns on the instrument, records data for 5 minutes, then powers off.
    The buffer fills continuously (linear RealDynamics) at data/duration MB/s over
    the collection window instead of jumping by `data` only when the activity ends."""
    mission.mode.emit("COLLECTING")
    mission.power_w.emit(lambda x: x + 15.0)
    mission.temperature_c.emit(lambda x: x + 5.0)

    duration_s = 5 * 60
    mission.data_volume_mb.set_rate(data / duration_s)

    delay("00:05:00")

    mission.data_volume_mb.set_rate(0.0)
    mission.power_w.emit(lambda x: x - 15.0)
    mission.temperature_c.emit(lambda x: x - 5.0)
    mission.mode.emit("IDLE")

    spawn(compress_data(mission))


@Mission.ActivityType
def compress_data(mission):
    """Compresses data after collection — spawned as a child activity."""
    mission.mode.emit("COMPRESSING")
    mission.power_w.emit(lambda x: x + 5.0)

    delay("00:02:00")

    mission.data_volume_mb.emit(lambda x: x * 0.6)
    mission.power_w.emit(lambda x: x - 5.0)
    mission.mode.emit("IDLE")


@Mission.ActivityType
def downlink(mission):
    """Waits for data to be available, then transmits — draining the buffer at a
    constant rate over the 10-minute span so data_volume_mb ramps down continuously
    (linear RealDynamics) instead of stepping to zero only when the activity ends."""
    wait_until(lambda: mission.data_volume_mb.get() > 0.0)

    mission.mode.emit("DOWNLINKING")
    mission.power_w.emit(lambda x: x + 25.0)

    duration_s = 10 * 60
    volume = mission.data_volume_mb.get()
    mission.data_volume_mb.set_rate(-volume / duration_s)

    delay("00:10:00")

    mission.data_volume_mb.set_rate(0.0)
    mission.data_volume_mb.emit(0.0)
    mission.power_w.emit(lambda x: x - 25.0)
    mission.mode.emit("IDLE")


@Mission.ActivityType
def safe_mode(mission):
    """Enters safe mode: powers down non-essential systems, waits, recovers."""
    mission.mode.emit("SAFE")
    mission.power_w.emit(3.0)

    delay("00:30:00")

    mission.power_w.emit(0.0)
    mission.mode.emit("IDLE")