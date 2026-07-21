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
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.ExecutionException;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assertions.fail;

/**
 * Phase 6 test #6 (roadmap.md §9), pulled forward and run early against the current
 * Phase 1 path (this repo predates the Java-side PyBridge split entirely —
 * {@code ShimModelType} talks directly to Python via {@code Protocol}/{@code PythonProcess}),
 * because it needs a real assertion, not a screenshot of a Gantt chart, to settle
 * spawned-child span timing.
 *
 * <p><b>Investigation outcome (see PR/commit description for the full trace):</b>
 * {@code compress_data.start} lands exactly on {@code collect_data.start + 5min} — that part
 * of the roadmap's stated invariant holds. But {@code collect_data}'s own span does <i>not</i>
 * end there; it stays open until {@code compress_data} (its {@code spawnWithSpan}/{@code
 * InSpan.Fresh} child) finishes, i.e. {@code collect_data.duration() == 7min}, not 5min. This
 * was traced to {@code EngineScheduler.spawn()} in plandev's {@code SimulationEngine} (shared
 * across all Aerie mission models, not pymerlin-specific): spawning a child — {@code Fresh}
 * span or not — always increments the spawning task's own span contributor count, so the
 * spawning task's span cannot close before every {@code InSpan.Fresh} descendant it ever
 * spawned has also finished.
 *
 * <p>This is not a pymerlin bug: {@code merlin-driver}'s own {@code AnchorSimulationTest
 * #decomposingActivitiesAndAnchors} independently encodes and asserts the identical behavior
 * (a decomposing activity's duration stretching to cover a later {@code InSpan.Fresh} spawn) as
 * expected. So the roadmap's stated invariant — "collect_data's span should end at exactly the
 * same simulated timestamp compress_data's span begins" — does not hold as a general Aerie
 * invariant; it was a mistaken assumption. This test asserts the real, confirmed behavior
 * instead, so it still serves as a regression guard, and flags the roadmap text as needing a
 * correction rather than a code fix.
 */
public final class SpanTimingTest {

    @Test
    public void compressDataSpawnedFromCollectDataHasCorrectSpanTiming() throws ExecutionException, InterruptedException {
        // Pin explicitly rather than ride PyBridge.create's ambient default (now `graal`,
        // roadmap §5.5) — this test is specifically about the Phase 1/SubprocessBridge path
        // per the class doc above, and must keep working on a plain JDK with no GraalPy
        // runtime, regardless of which bridge production defaults to.
        System.setProperty("pymerlin.bridge", "subprocess");

        final Instant startTime = Instant.parse("2026-01-01T00:00:00Z");
        final Timestamp start = new Timestamp(startTime);
        final Timestamp end = new Timestamp(startTime.plusSeconds(600)); // 10 min: covers 5+2 min plus margin

        final Map<ActivityDirectiveId, ActivityDirective> schedule = new HashMap<>();
        schedule.put(
            new ActivityDirectiveId(0),
            new ActivityDirective(Duration.ZERO, "collect_data", Map.of(), null, true));

        final Plan plan = new Plan("plan", start, end, schedule, Map.of());

        final MissionModel<Unit> missionModel =
            SimulationUtility.instantiateMissionModel(new ShimModelType(), startTime, Unit.UNIT);

        final SimulationResults results;
        try (var simUtil = new SimulationUtility()) {
            results = simUtil.simulate(missionModel, plan).get();
        }

        ActivityInstance collectData = null;
        ActivityInstance compressData = null;
        for (final ActivityInstance activity : results.simulatedActivities.values()) {
            if (activity.type().equals("collect_data")) collectData = activity;
            if (activity.type().equals("compress_data")) compressData = activity;
        }

        if (collectData == null) {
            fail("collect_data did not appear in simulatedActivities — did it fail to complete? "
                 + "simulatedActivities=" + results.simulatedActivities
                 + " unfinishedActivities=" + results.unfinishedActivities);
        }
        if (compressData == null) {
            fail("compress_data (spawned child) did not appear in simulatedActivities — was it "
                 + "never spawned, or did it not complete within the 10-minute simulation window? "
                 + "simulatedActivities=" + results.simulatedActivities
                 + " unfinishedActivities=" + results.unfinishedActivities);
        }

        final Instant collectDataStart = collectData.start();
        final Instant collectDataEnd = collectDataStart.plus(collectData.duration().in(Duration.MICROSECONDS), java.time.temporal.ChronoUnit.MICROS);
        final Instant compressDataStart = compressData.start();

        System.out.println("[SpanTimingTest] collect_data.start   = " + collectDataStart);
        System.out.println("[SpanTimingTest] collect_data.duration = " + collectData.duration());
        System.out.println("[SpanTimingTest] collect_data.end      = " + collectDataEnd);
        System.out.println("[SpanTimingTest] compress_data.start   = " + compressDataStart);
        System.out.println("[SpanTimingTest] compress_data.duration = " + compressData.duration());

        // The invariant that actually holds: the child starts exactly when the parent's own
        // 5-minute delay resumes and it spawns the child — no spurious cross-round-trip delay.
        assertEquals(
            collectDataStart.plus(5, java.time.temporal.ChronoUnit.MINUTES), compressDataStart,
            "compress_data.start must equal collect_data.start + 5min to microsecond precision, no tolerance");

        assertTrue(compressData.parentId() != null, "compress_data should be recorded as a child span of collect_data");

        // The invariant that does NOT hold, confirmed to be intentional Aerie engine behavior
        // (see class-level doc): collect_data's own span is stretched to cover its InSpan.Fresh
        // child's full duration, so it closes when compress_data does (7min in), not when
        // collect_data's own code returns (5min in).
        assertEquals(
            Duration.of(7, Duration.MINUTES), collectData.duration(),
            "collect_data's span is stretched to cover its spawned compress_data child (5min own + 2min child) "
            + "— by design in plandev's SimulationEngine.spawn(), not a pymerlin bug; see AnchorSimulationTest"
            + "#decomposingActivitiesAndAnchors for the equivalent platform-level assertion");
    }
}
