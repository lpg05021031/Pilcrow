package auditkit.spring;

import auditkit.AuditConfig;
import auditkit.EffectRecorder;
import auditkit.Faults;
import auditkit.Outcome;
import auditkit.ResultWriter;
import auditkit.Scenario;
import auditkit.Snapshot;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.io.File;
import java.util.ArrayList;
import java.util.Collection;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.DynamicTest;
import org.junit.jupiter.api.TestFactory;
import org.junit.jupiter.api.condition.EnabledIfSystemProperty;
import org.junit.jupiter.api.function.Executable;

/**
 * Makes each step of a successful command fail in turn, and looks at what is left behind.
 *
 * <p>For every command that changes something, one case of each distinct effect is taken from the
 * matrix. It is run once undisturbed to learn its steps: each write statement, the commit, each
 * message sent, each call to an outside system. Then it is run again once per step with that step
 * failing, and three things are checked, none of which needs to know the business:
 * <ul>
 * <li>all or nothing: the data is as before the call, or as after the undisturbed call, never in between;</li>
 * <li>what was sent matches what was kept: nothing went out for a change that was rolled back;</li>
 * <li>trying again converges: the same call, repeated, ends where the undisturbed call ended.</li>
 * </ul>
 * Nothing here fails the build: the differences are the result.
 */
@EnabledIfSystemProperty(named = "audit.run", matches = "faults")
class FaultRunner {

    private static final int MAX_WRITES = 12;
    private static ResultWriter results;

    @AfterAll
    static void finish() {
        if (results != null) {
            results.close();
        }
    }

    @TestFactory
    Collection<DynamicTest> cases() throws Exception {
        results = ResultWriter.open("faults");
        final Scenario scenario = new Scenario(new SpringDriver(AuditContext.get()));
        JsonNode matrix = AuditConfig.JSON.readTree(new File(new File(AuditConfig.load().dir(), "model"), "matrix.json"));
        String onlyCase = System.getProperty("audit.case");
        String onlyCommand = System.getProperty("audit.command");
        results.note("only_case", onlyCase);
        results.note("only_command", onlyCommand);
        List<DynamicTest> tests = new ArrayList<DynamicTest>();
        Set<String> taken = new HashSet<String>();
        int commands = 0;
        Set<String> seenCommands = new HashSet<String>();
        for (final JsonNode cell : matrix.get("cells")) {
            JsonNode expected = cell.path("expected");
            if (!cell.hasNonNull("with") || cell.hasNonNull("unreachable")
                    || !"allowed".equals(expected.path("outcome").asText())) {
                continue;
            }
            if (expected.path("written").size() + expected.path("created").size() + expected.path("deleted").size()
                    + expected.path("published").size() == 0) {
                continue;       // nothing to lose
            }
            if (onlyCase != null && !onlyCase.equals(cell.get("id").asText())) {
                continue;
            }
            if (onlyCommand != null && !onlyCommand.equals(cell.get("command").asText())) {
                continue;
            }
            // One case for each distinct effect of a command: the same writes failing the same way teach nothing new.
            String shape = cell.get("command").asText() + expected.path("written") + expected.path("created")
                    + expected.path("deleted") + expected.path("published").size();
            if (!taken.add(shape)) {
                continue;
            }
            if (seenCommands.add(cell.get("command").asText())) {
                commands++;
            }
            tests.add(DynamicTest.dynamicTest("FAULT-" + cell.get("id").asText(), new Executable() {
                @Override
                public void execute() {
                    run(scenario, cell);
                }
            }));
        }
        results.note("cases", tests.size());
        results.note("commands", commands);
        return tests;
    }

    static void run(Scenario scenario, JsonNode cell) {
        String cellId = cell.get("id").asText();
        String number = cellId.replace("CELL-", "");
        Map<String, Object> base = new LinkedHashMap<String, Object>();
        base.put("cell", cellId);
        base.put("key", cell.path("key").asText(null));
        base.put("sha", cell.path("sha").asText(null));
        base.put("command", cell.get("command").asText());
        try {
            JsonNode command = scenario.command(cell.get("command").asText());
            ObjectNode context = scenario.prepare(cell.path("fixture").asText(null), cell.get("steps"));
            Scenario.Observation clean = scenario.observe(command, cell.get("with"), context);
            if (!Outcome.ALLOWED.equals(clean.kind)) {
                Map<String, Object> detail = new LinkedHashMap<String, Object>(base);
                detail.put("why", "not-allowed");
                detail.put("reason", "the undisturbed call ended " + clean.kind + ", so there is no success to disturb");
                results.record("FAULT-" + number, ResultWriter.SKIPPED, null, null, detail, null);
                return;
            }
            Map<String, List<Map<String, String>>> wanted = Snapshot.withoutKeys(Snapshot.take());
            List<String> cleanSent = goesOut(clean);

            // The steps of the undisturbed call, in order of kind.
            Map<String, Integer> steps = new LinkedHashMap<String, Integer>();
            Map<String, List<String>> what = new LinkedHashMap<String, List<String>>();
            for (String kind : new String[] {Faults.WRITE, Faults.COMMIT, Faults.PUB, Faults.EXT}) {
                steps.put(kind, 0);
                what.put(kind, new ArrayList<String>());
            }
            for (EffectRecorder.Effect e : clean.effects) {
                String kind = null;
                String label = e.detail;
                if ("SQL".equals(e.kind) && e.more != null && Faults.isWrite(String.valueOf(e.more.get("verb")))) {
                    kind = Faults.WRITE;
                    label = e.more.get("verb") + " " + e.more.get("table");
                } else if ("COMMIT".equals(e.kind) || "PUB".equals(e.kind) || "EXT".equals(e.kind)) {
                    kind = e.kind;
                    label = "COMMIT".equals(e.kind) ? "commit" : e.detail;
                }
                if (kind != null) {
                    steps.put(kind, steps.get(kind) + 1);
                    what.get(kind).add(label);
                }
            }

            for (String kind : steps.keySet()) {
                int count = steps.get(kind);
                for (int n = 1; n <= count; n++) {
                    String id = "FAULT-" + number + "-" + kind + "-" + n;
                    Map<String, Object> detail = new LinkedHashMap<String, Object>(base);
                    detail.put("step", kind);
                    detail.put("ordinal", n);
                    detail.put("of", count);
                    detail.put("fails", what.get(kind).get(n - 1));
                    if (Faults.WRITE.equals(kind) && n > MAX_WRITES) {
                        detail.put("why", "over-budget");
                        detail.put("reason", "only the first " + MAX_WRITES + " write statements of a call are made to fail");
                        results.record(id, ResultWriter.SKIPPED, null, null, detail, null);
                        continue;
                    }
                    one(scenario, cell, command, kind, n, id, detail, wanted, cleanSent);
                }
            }
        } catch (Throwable e) {
            Map<String, Object> detail = new LinkedHashMap<String, Object>(base);
            detail.put("why", e instanceof Scenario.NotReached ? "steps-failed" : "runner-failed");
            detail.put("reason", e.getClass().getSimpleName() + ": " + e.getMessage());
            results.record("FAULT-" + number, e instanceof Scenario.NotReached ? ResultWriter.SKIPPED : ResultWriter.ERROR,
                    null, null, detail, null);
        }
    }

    private static void one(Scenario scenario, JsonNode cell, JsonNode command, String kind, int n, String id,
                            Map<String, Object> detail, Map<String, List<Map<String, String>>> wanted,
                            List<String> cleanSent) throws Exception {
        ObjectNode context = scenario.prepare(cell.path("fixture").asText(null), cell.get("steps"));
        Scenario.Observation hurt;
        boolean fired;
        Faults.arm(kind, n);
        try {
            hurt = scenario.observe(command, cell.get("with"), context);
        } finally {
            fired = Faults.fired();
            Faults.disarm();
        }
        if (!fired) {
            detail.put("why", "step-not-reached");
            detail.put("reason", "the call did not reach this step the second time");
            results.record(id, ResultWriter.SKIPPED, null, null, detail, null);
            return;
        }
        Map<String, List<Map<String, String>>> left = Snapshot.withoutKeys(Snapshot.take());
        String state = hurt.changes.isEmpty() ? "as-before" : (left.equals(wanted) ? "as-after-success" : "in-between");
        List<String> sent = goesOut(hurt);

        // Try again, undisturbed.
        Scenario.Observation again = scenario.observe(command, cell.get("with"), context);
        boolean converges = Snapshot.withoutKeys(Snapshot.take()).equals(wanted);
        List<String> sentInAll = new ArrayList<String>(sent);
        sentInAll.addAll(goesOut(again));

        List<String> differs = new ArrayList<String>();
        if ("in-between".equals(state)) {
            differs.add("atomicity");
        }
        if ("as-before".equals(state) && !sent.isEmpty()) {
            differs.add("sent-not-kept");
        }
        if ("as-after-success".equals(state) && !sent.containsAll(cleanSent)) {
            differs.add("kept-not-sent");
        }
        if (Outcome.ALLOWED.equals(hurt.kind) && !"as-after-success".equals(state)) {
            differs.add("reported-success");
        }
        if (!converges) {
            differs.add("retry-diverges");
        }
        if (sentInAll.size() > cleanSent.size()) {
            differs.add("sent-again-on-retry");
        }

        Map<String, Object> expected = new LinkedHashMap<String, Object>();
        expected.put("state_after_failure", "as-before or as-after-success");
        expected.put("sent_matches_kept", true);
        expected.put("retry_converges", true);
        Map<String, Object> observed = new LinkedHashMap<String, Object>();
        observed.put("outcome_of_failed_call", hurt.kind);
        observed.put("code", hurt.outcome.code);
        observed.put("state_after_failure", state);
        observed.put("changed", new ArrayList<String>(hurt.written()));
        observed.put("created", new ArrayList<String>(hurt.rows("added")));
        observed.put("sent_in_failed_call", sent);
        observed.put("retry_outcome", again.kind);
        observed.put("retry_code", again.outcome.code);
        observed.put("retry_converges", converges);
        observed.put("sent_on_retry", goesOut(again));
        observed.put("sent_by_success", cleanSent);
        detail.put("differs", differs);

        Map<String, Object> evidence = new LinkedHashMap<String, Object>();
        evidence.put("params", hurt.params);
        evidence.put("changes_after_failure", hurt.changes.size() > 200 ? hurt.changes.subList(0, 200) : hurt.changes);
        List<Map<String, Object>> effects = new ArrayList<Map<String, Object>>();
        for (EffectRecorder.Effect effect : hurt.effects) {
            effects.add(effect.toMap());
        }
        evidence.put("effects_of_failed_call", effects);
        if (hurt.outcome.error != null) {
            evidence.put("error", hurt.outcome.error);
        }
        results.record(id, differs.isEmpty() ? ResultWriter.MATCH : ResultWriter.MISMATCH, expected, observed, detail, evidence);
    }

    /**
     * What a call sent that cannot be taken back: messages, and HTTP calls that are not reads.
     * A call on a replaced client type is not counted: nothing says whether it reads or writes.
     */
    private static List<String> goesOut(Scenario.Observation o) {
        List<String> out = new ArrayList<String>(o.sent("PUB"));
        for (String call : o.sent("EXT")) {
            if (call.matches("^(POST|PUT|PATCH|DELETE) .*")) {
                out.add(call);
            }
        }
        return out;
    }
}
