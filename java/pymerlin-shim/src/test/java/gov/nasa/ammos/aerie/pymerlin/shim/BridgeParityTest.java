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
 * Phase 2 exit-criterion harness (roadmap §5): {@code demo/model.py} must produce
 * byte-identical simulation results under the {@link SubprocessBridge} (the oracle) and
 * the {@link GraalBridge}.
 *
 * <p>Two tests:
 * <ol>
 *   <li>{@link #subprocessOracleRunsThroughTheSeam()} — always runs (needs only CPython +
 *       the repo dev venv). Proves the Phase 2 {@code PyBridge} refactor did not regress the
 *       working path: the demo simulates end-to-end through the seam and yields the expected
 *       activities/resources.</li>
 *   <li>{@link #graalBridgeMatchesSubprocessOracle()} — the actual exit criterion. It is
 *       skipped unless {@code -Dpymerlin.test.graal=true} is set, because it needs a GraalPy
 *       language runtime and a provisioned {@code python-resources} venv, i.e. the built
 *       worker image. Run it there:
 *       <pre>./gradlew :pymerlin-shim:test -Dpymerlin.test.graal=true \
 *          -Dpymerlin.resources=/opt/pymerlin/python-resources</pre>
 *       When it passes, Phase 2's exit criterion is met and {@link PyBridge#create}'s default
 *       can flip to {@code graal}.</li>
 * </ol>
 */
public final class BridgeParityTest {

    private static final Instant START = Instant.parse("2026-01-01T00:00:00Z");

    private static Map<ActivityDirectiveId, ActivityDirective> demoSchedule() {
        final Map<ActivityDirectiveId, ActivityDirective> schedule = new HashMap<>();
        // collect_data exercises emit + delay + spawn(compress_data); a single directive
        // keeps the run fully deterministic.
        schedule.put(new ActivityDirectiveId(0),
            new ActivityDirective(Duration.ZERO, "collect_data", Map.of(), null, true));
        return schedule;
    }

    private static SimulationResults simulateWith(String bridge) {
        final String previous = System.getProperty("pymerlin.bridge");
        System.setProperty("pymerlin.bridge", bridge);
        try {
            final Timestamp start = new Timestamp(START);
            final Timestamp end = new Timestamp(START.plusSeconds(600));
            final Plan plan = new Plan("plan", start, end, demoSchedule(), Map.of());
            final MissionModel<Unit> model =
                SimulationUtility.instantiateMissionModel(new ShimModelType(), START, Unit.UNIT);
            try (var simUtil = new SimulationUtility()) {
                return simUtil.simulate(model, plan).get();
            } catch (Exception e) {
                throw new RuntimeException("simulation under bridge=" + bridge + " failed", e);
            }
        } finally {
            if (previous == null) System.clearProperty("pymerlin.bridge");
            else System.setProperty("pymerlin.bridge", previous);
        }
    }

    /**
     * A stable, order-independent textual projection of the parts of a SimulationResults
     * that a model's behaviour determines: discrete + real resource profiles and the
     * simulated-activity spans. Two runs that agree on this agree on the observable result.
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
    public void subprocessOracleRunsThroughTheSeam() {
        final SimulationResults results = simulateWith("subprocess");

        final List<String> types = new ArrayList<>();
        results.simulatedActivities.values().forEach(a -> types.add(a.type()));
        assertFalse(results.simulatedActivities.isEmpty(),
            "subprocess bridge produced no simulated activities — the PyBridge seam regressed the oracle");
        assertEquals(true, types.contains("collect_data"), "collect_data should have simulated");
        assertEquals(true, types.contains("compress_data"), "spawned compress_data should have simulated");

        System.out.println("[BridgeParityTest] subprocess oracle canonical result:\n" + canonicalize(results));
    }

    @Test
    public void graalBridgeMatchesSubprocessOracle() {
        assumeTrue(Boolean.getBoolean("pymerlin.test.graal"),
            "graal-vs-subprocess parity runs only with -Dpymerlin.test.graal=true (a GraalPy worker image "
            + "with a provisioned python-resources venv); skipped otherwise so a stock-JDK build does not "
            + "false-fail on the not-yet-validated path");

        final String subprocess = canonicalize(simulateWith("subprocess"));
        final String graal      = canonicalize(simulateWith("graal"));

        assertEquals(subprocess, graal,
            "GraalBridge and SubprocessBridge must produce byte-identical results (roadmap §5 exit criterion)");
    }
}
