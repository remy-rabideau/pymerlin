package gov.nasa.ammos.aerie.pymerlin.shim;

import gov.nasa.jpl.aerie.merlin.driver.MissionModel;
import gov.nasa.jpl.aerie.merlin.driver.SimulationResults;
import gov.nasa.jpl.aerie.merlin.protocol.types.Duration;
import gov.nasa.jpl.aerie.merlin.protocol.types.Unit;
import gov.nasa.jpl.aerie.orchestration.simulation.SimulationUtility;
import gov.nasa.jpl.aerie.types.ActivityDirective;
import gov.nasa.jpl.aerie.types.ActivityDirectiveId;
import gov.nasa.jpl.aerie.types.ActivityInstance;
import gov.nasa.jpl.aerie.types.Plan;
import gov.nasa.jpl.aerie.types.Timestamp;
import org.junit.jupiter.api.Test;

import java.time.Instant;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

/**
 * End-to-end regression check: {@code demo/model.py} simulates correctly through
 * {@code ShimModelType} on the (only, since roadmap §6.3/§6.6) direct-call GraalPy path.
 *
 * <p>Formerly {@code BridgeParityTest}, which compared this same demo-model run under both
 * {@code SubprocessBridge} and {@code GraalBridge} — that comparison was the Phase 2 exit
 * criterion (roadmap §5.5) and it passed, confirmed byte-identical against the real image
 * (roadmap §6.6) before {@code SubprocessBridge} was deleted. There is only one bridge now,
 * so there is nothing left to compare against; this is a plain correctness/regression test.
 *
 * <p>Requires a real GraalPy runtime + provisioned {@code python-resources} venv;
 * {@code assumeTrue}-skips without {@code -Dpymerlin.test.graal=true} so a stock JDK build
 * doesn't false-fail:
 * <pre>./gradlew :pymerlin-shim:test -Dpymerlin.test.graal=true \
 *    -Dpymerlin.resources=/opt/pymerlin/python-resources</pre>
 */
public final class DemoModelSimulationTest {

    private static final Instant START = Instant.parse("2026-01-01T00:00:00Z");

    private static Map<ActivityDirectiveId, ActivityDirective> demoSchedule() {
        final Map<ActivityDirectiveId, ActivityDirective> schedule = new HashMap<>();
        // collect_data exercises emit + delay + spawn(compress_data); a single directive
        // keeps the run fully deterministic.
        schedule.put(new ActivityDirectiveId(0),
            new ActivityDirective(Duration.ZERO, "collect_data", Map.of(), null, true));
        return schedule;
    }

    /**
     * A stable, order-independent textual projection of the parts of a SimulationResults
     * that a model's behaviour determines: discrete + real resource profiles and the
     * simulated-activity spans.
     */
    private static String canonicalize(SimulationResults r) {
        final List<String> lines = new ArrayList<>();

        r.discreteProfiles.forEach((name, profile) ->
            lines.add("discrete " + name + " = " + profile.segments()));
        r.realProfiles.forEach((name, profile) ->
            lines.add("real " + name + " = " + profile.segments()));

        final List<String> acts = new ArrayList<>();
        for (final ActivityInstance a : r.simulatedActivities.values()) {
            acts.add(a.type() + " start=" + a.start() + " dur=" + a.duration()
                     + " parent=" + a.parentId());
        }
        acts.sort(String::compareTo);
        lines.addAll(acts);

        lines.sort(String::compareTo);
        return String.join("\n", lines);
    }

    @Test
    public void demoModelSimulatesCorrectly() throws Exception {
        assumeTrue(Boolean.getBoolean("pymerlin.test.graal"),
            "requires a real GraalPy runtime + provisioned python-resources venv; "
            + "skipped without -Dpymerlin.test.graal=true so a stock JDK does not false-fail");

        final Timestamp start = new Timestamp(START);
        final Timestamp end = new Timestamp(START.plusSeconds(600));
        final Plan plan = new Plan("plan", start, end, demoSchedule(), Map.of());
        final MissionModel<Unit> model =
            SimulationUtility.instantiateMissionModel(new ShimModelType(), START, Unit.UNIT);

        final SimulationResults results;
        try (var simUtil = new SimulationUtility()) {
            results = simUtil.simulate(model, plan).get();
        }

        final List<String> types = new ArrayList<>();
        results.simulatedActivities.values().forEach(a -> types.add(a.type()));
        assertFalse(results.simulatedActivities.isEmpty(),
            "no simulated activities — did the direct-call path regress?");
        assertEquals(true, types.contains("collect_data"), "collect_data should have simulated");
        assertEquals(true, types.contains("compress_data"), "spawned compress_data should have simulated");

        System.out.println("[DemoModelSimulationTest] canonical result:\n" + canonicalize(results));
    }
}
