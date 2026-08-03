"""
Tutorial example: a solid-state recorder (SSR) model demonstrating cells, resources,
derived resources, activities, and local simulation.

Run directly: python tutorial.py
"""

from pymerlin import MissionModel, MissionModelBase, simulate, Schedule, Directive, Duration
from pymerlin.model_actions import delay, spawn


@MissionModel
class Model(MissionModelBase):
    def __init__(self, registrar):
        self.counter = registrar.cell(0)
        self.data_model = DataModel(registrar)
        registrar.resource("counter", self.counter)


@Model.ActivityType
def increment_counter(model):
    model.counter.emit(lambda x: x + 1)


class DataModel:
    def __init__(self, registrar):
        self.recording_rate = registrar.cell(0)
        registrar.resource("recording_rate", self.recording_rate)

        self.ssr_volume_simple = registrar.cell(0.0)
        registrar.resource("ssr_volume_simple", self.ssr_volume_simple)

        self.mag_data_mode = registrar.cell("OFF")
        registrar.resource("mag_data_mode", self.mag_data_mode)

        self.mag_data_rate = self.mag_data_mode.map(lambda mode: MagDataCollectionMode[mode])
        registrar.resource("mag_data_rate", self.mag_data_rate)

        self.total_data_rate = self.mag_data_rate + self.recording_rate
        registrar.resource("total_data_rate", self.total_data_rate)


@Model.ActivityType
def collect_data(model, rate=0.0, duration="01:00:00"):
    duration = Duration.from_string(duration)
    model.data_model.recording_rate += rate
    delay(duration)
    model.data_model.ssr_volume_simple += rate * duration.to_number_in(Duration.SECONDS) / 1000.0
    model.data_model.recording_rate -= rate


@Model.ActivityType
def change_mag_mode(model, mode="LOW_RATE"):
    current_rate = model.data_model.mag_data_rate.get()
    new_rate = MagDataCollectionMode[mode]
    model.data_model.recording_rate += (new_rate - current_rate) / 1.0e3
    model.data_model.mag_data_mode.set(mode)


MagDataCollectionMode = {
    "OFF": 0.0,       # kbps
    "LOW_RATE": 500.0, # kbps
    "HIGH_RATE": 5000.0 # kbps
}


def main():
    profiles, spans, events = simulate(
        Model,
        Schedule.build(
            ("00:00:01", Directive("collect_data", {"rate": 20.0, "duration": "00:10:00"})),
            ("00:05:00", Directive("change_mag_mode", {"mode": "HIGH_RATE"})),
            ("00:07:00", Directive("change_mag_mode", {"mode": "LOW_RATE"})),
            ("00:09:00", Directive("change_mag_mode", {"mode": "OFF"})),
        ),
        "01:00:00"
    )

    print("spans")
    for span in spans:
        print("  ", span)

    for profile, segments in sorted(profiles.items()):
        print(profile)
        elapsed_time = Duration.ZERO
        for segment in segments:
            print("  ", elapsed_time, ":", segment.dynamics)
            elapsed_time += segment.extent


if __name__ == '__main__':
    main()
