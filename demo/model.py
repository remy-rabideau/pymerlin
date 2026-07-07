from pymerlin import MissionModel
from pymerlin.model_actions import delay, wait_until, spawn


@MissionModel
class Mission:
    def __init__(self, registrar):
        self.counter = registrar.cell(0)
        self.temperature = registrar.cell(20.0)
        self.status = registrar.cell("idle")

        registrar.resource("/counter", self.counter)
        registrar.resource("/temperature", self.temperature)
        registrar.resource("/status", self.status)
        registrar.topic("/counter")
        registrar.topic("/temperature")

@Mission.ActivityType
def increment_counter(mission):
    current = mission.counter.get()
    mission.counter.emit(current + 1)
    delay("00:10:00")
    
    if mission.counter.get() < 5:
        spawn(increment_counter(mission))

@Mission.ActivityType
def temperature_cycle(mission):
    mission.status.emit("heating")
    
    for temp in [25.0, 30.0, 35.0, 40.0]:
        delay("00:15:00")
        mission.temperature.emit(temp)
    
    mission.status.emit("cooling")
    
    for temp in [35.0, 30.0, 25.0, 20.0]:
        delay("00:15:00")
        mission.temperature.emit(temp)
    
    mission.status.emit("idle")

@Mission.ActivityType
def monitor_and_alert(mission):
    wait_until(lambda: mission.temperature.get() > 30.0)
    mission.status.emit("alert: high temperature")
    delay("00:05:00")
    mission.status.emit("monitoring")