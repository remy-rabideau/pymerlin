from pymerlin import simulate, Schedule, Directive
from demo.model import Mission


def main():
    schedule = Schedule.build(
        ("00:00:00", Directive("collect_data", 5012)),
        ("00:20:00", Directive("downlink", ...)),
        ("00:30:00", Directive("collect_data", 2048)),
        ("01:00:00", Directive("safe_mode", ...)),
    )
    duration = "02:00:00"
    profiles, spans, events = simulate(Mission, schedule, duration)
    print("=== Profiles ===")
    for name, segments in profiles.items():
        print(f"  {name}: {segments}")
    print("\n=== Spans ===")
    for span in spans:
        print(f"  {span}")
    print("\n=== Events ===")
    for event in events:
        print(f"  {event}")


if __name__ == "__main__":
    main()
