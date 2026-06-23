from pymerlin import MissionModel
from pymerlin.model_actions import delay, wait_until, spawn


@MissionModel
class Mission:
    def __init__(self, registrar):
        self.cell1 = registrar.cell("init")

        registrar.resource("/cell1", self.cell1)
        registrar.topic("/cell1")

@Mission.ActivityType
def activity1(mission):
    mission.cell1.set("foo")
    result = mission.cell1.get()
    assert result == "foo", result
    delay("00:00:12")
    mission.cell1.set("bar")
    spawn(activity2(mission))

@Mission.ActivityType
def activity2(mission):
    wait_until(lambda: True)