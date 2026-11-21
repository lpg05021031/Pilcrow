package auditkit.spring;

import auditkit.AuditClock;
import auditkit.AuditConfig;
import auditkit.ConnectionGuard;
import auditkit.EffectRecorder;
import auditkit.Outcome;
import auditkit.ResultWriter;
import auditkit.Scenario;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.io.File;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.Assertions;
import org.junit.jupiter.api.DynamicTest;
import org.junit.jupiter.api.TestFactory;
import org.junit.jupiter.api.condition.EnabledIfSystemProperty;
import org.junit.jupiter.api.function.Executable;

/**
 * Executes the matrix: for every cell, bring the system to the cell's state through commands, call
 * the command once, and record what happened beside what was expected.
 *
 * <p>-Daudit.mode=record (the default) compares with what reading the code predicted, and never
 * fails the build: the differences are the result. -Daudit.mode=verify compares with the pinned
 * expectation and fails on a difference. -Daudit.case=CELL-0012 runs one cell;
 * -Daudit.command=CMD-x runs one command's cells.
 */
@EnabledIfSystemProperty(named = "audit.run", matches = "matrix")
class MatrixRunner {

    private static ResultWriter results;

    @AfterAll
    static void finish() {
        if (results != null) {
            results.close();
        }
    }

    @TestFactory
    Collection<DynamicTest> cells() throws Exception {
        results = ResultWriter.open("matrix");
        final boolean verify = "verify".equals(System.getProperty("audit.mode"));
        final Scenario scenario = new Scenario(new SpringDriver(AuditContext.get()));
        JsonNode matrix = AuditConfig.JSON.readTree(new File(new File(AuditConfig.load().dir(), "model"), "matrix.json"));
        String onlyCase = System.getProperty("audit.case");
        String onlyCommand = System.getProperty("audit.command");
        results.note("mode", verify ? "verify" : "record");
        results.note("only_case", onlyCase);
        results.note("only_command", onlyCommand);
        results.note("matrix_cells", matrix.get("cells").size());
        List<DynamicTest> tests = new ArrayList<DynamicTest>();
        for (final JsonNode cell : matrix.get("cells")) {
            final String id = cell.get("id").asText();
            if (onlyCase != null && !onlyCase.equals(id)) {
                continue;
            }
            if (onlyCommand != null && !java.util.Arrays.asList(onlyCommand.split(",")).contains(cell.get("command").asText())) {
                continue;       // audit.command names one command, or several separated by commas
            }
            tests.add(DynamicTest.dynamicTest(id, new Executable() {
                @Override
                public void execute() {
                    String result = run(scenario, cell, verify);
                    if (verify && ResultWriter.MISMATCH.equals(result)) {
                        JsonNode expected = cell.path("expected");
                        String source = expected.path("source").asText();
                        Assertions.fail("decision".equals(source)
                                ? id + ": the command still does what decision " + expected.path("decision").asText()
                                        + " says it must not; expected until the code changes"
                                : "spec".equals(source)
                                ? id + ": the command does something else than clause " + expected.path("clause").asText()
                                        + " of the specification says; expected until the code or the specification changes"
                                : id + ": what the command did differs from what was pinned; see the run's results.jsonl");
                    }
                }
            }));
        }
        return tests;
    }

    static String run(Scenario scenario, JsonNode cell, boolean verify) {
        String id = cell.get("id").asText();
        JsonNode expected = verify && cell.hasNonNull("expected") ? cell.get("expected") : cell.get("static");
        Map<String, Object> detail = new LinkedHashMap<String, Object>();
        detail.put("command", cell.get("command").asText());
        detail.put("deviation", cell.path("deviation").isNull() ? null : cell.path("deviation").asText(null));
        detail.put("key", cell.path("key").asText(null));
        detail.put("sha", cell.path("sha").asText(null));      // which definition of the cell this result is for
        try {
            if (cell.hasNonNull("unreachable")) {
                detail.put("why", "unreachable");
                detail.put("reason", "the model says this state cannot be reached: " + cell.get("unreachable").asText());
                results.record(id, ResultWriter.SKIPPED, expected, null, detail, null);
                return ResultWriter.SKIPPED;
            }
            if (!cell.hasNonNull("with")) {
                detail.put("why", "no-recipe");
                detail.put("reason", "no recipe: the model does not say how to reach this state");
                results.record(id, ResultWriter.SKIPPED, expected, null, detail, null);
                return ResultWriter.SKIPPED;
            }
            JsonNode command = scenario.command(cell.get("command").asText());
            ObjectNode context = scenario.prepare(cell.path("fixture").asText(null), cell.get("steps"));
            Scenario.Observation o = scenario.observe(command, cell.get("with"), context);

            Map<String, Object> observed = new LinkedHashMap<String, Object>();
            observed.put("outcome", o.kind);
            observed.put("code", o.outcome.code);
            observed.put("status", o.outcome.status);
            observed.put("given", o.given);
            observed.put("after", o.after);
            observed.put("written", new ArrayList<String>(o.written()));
            observed.put("created", new ArrayList<String>(o.rows("added")));
            observed.put("deleted", new ArrayList<String>(o.rows("removed")));
            observed.put("published", o.sent("PUB"));
            observed.put("external", o.sent("EXT"));
            observed.put("response_sha", Scenario.fingerprint(o.outcome.response));     // what the caller was answered
            observed.put("flags", o.flags);
            if (o.outcome.error != null) {
                observed.put("error", o.outcome.error);
            }

            // Did the fixture bring the system to the state the cell is about?
            // A parameter's class is worked out against the rows it is compared with; where those rows do
            // not exist it has no class, and the cell's word for it is not contradicted.
            List<String> unclassified = new ArrayList<String>();
            for (JsonNode v : command.path("vars")) {
                if ("param".equals(v.path("kind").asText()) && "null".equals(o.given.get(v.get("name").asText()))) {
                    unclassified.add(v.get("name").asText());
                }
            }
            List<String> off = new ArrayList<String>();
            Iterator<Map.Entry<String, JsonNode>> given = cell.get("given").fields();
            while (given.hasNext()) {
                Map.Entry<String, JsonNode> g = given.next();
                if (o.given.containsKey(g.getKey()) && !unclassified.contains(g.getKey())
                        && !o.given.get(g.getKey()).equals(g.getValue().asText())) {
                    off.add(g.getKey() + " is " + o.given.get(g.getKey()) + ", the cell says " + g.getValue().asText());
                }
            }
            Map<String, Object> evidence = evidence(o);
            if (!off.isEmpty()) {
                detail.put("why", "state-not-reached");
                detail.put("reason", "state not reached: " + off);
                results.record(id, ResultWriter.SKIPPED, expected, observed, detail, evidence);
                return ResultWriter.SKIPPED;
            }

            List<String> differs = differences(expected, o, observed);
            String result = differs.isEmpty() ? ResultWriter.MATCH : ResultWriter.MISMATCH;
            if (!differs.isEmpty()) {
                detail.put("differs", differs);
            }
            if (ResultWriter.MISMATCH.equals(result) && Outcome.ERROR.equals(o.kind) && o.flags.contains("unconfigured-double")) {
                result = ResultWriter.HARNESS_GAP;
                detail.put("why", "unanswered-outside-call");
                detail.put("reason", "an outside call had no configured answer; this says nothing about the application");
            }
            results.record(id, result, expected, observed, detail, evidence);
            return result;
        } catch (Scenario.NotReached e) {
            detail.put("why", "steps-failed");
            detail.put("reason", e.getMessage());
            results.record(id, ResultWriter.SKIPPED, expected, null, detail, null);
            return ResultWriter.SKIPPED;
        } catch (Throwable e) {
            detail.put("why", "runner-failed");
            detail.put("reason", "the runner failed: " + e.getClass().getName() + ": " + e.getMessage());
            results.record(id, ResultWriter.ERROR, expected, null, detail, null);
            return ResultWriter.ERROR;
        }
    }

    /** The parts of an expectation that are compared when it names them. */
    private static final String[] COMPARED = {"after", "written", "created", "deleted", "published", "external", "response_sha"};

    /**
     * Where what happened differs from what was expected. The outcome word must agree; the code and
     * every other part are compared only when the expectation names them, so a prediction from
     * reading the code (an outcome and perhaps a code) and a pinned observation (everything) are
     * both checked by the same rule.
     */
    static List<String> differences(JsonNode expected, Scenario.Observation o, Map<String, Object> observed) {
        List<String> out = new ArrayList<String>();
        if (expected == null) {
            out.add("nothing was expected for this cell");
            return out;
        }
        if (expected.hasNonNull("outcome") && !expected.get("outcome").asText().toUpperCase().equals(o.kind)) {
            out.add("outcome");
        }
        // A person may have decided only what must not happen ("this must not end in an error").
        if (expected.hasNonNull("outcome_not") && expected.get("outcome_not").asText().toUpperCase().equals(o.kind)) {
            out.add("outcome");
        }
        if (expected.has("code")) {
            String want = expected.get("code").isNull() ? null : expected.get("code").asText();
            if (want == null ? o.outcome.code != null : !want.equals(o.outcome.code)) {
                out.add("code");
            }
        }
        JsonNode seen = AuditConfig.JSON.valueToTree(observed);
        // A branch the code is predicted to take names columns it must have written.
        for (JsonNode column : expected.path("writes_include")) {
            String name = column.asText();
            String table = name.contains(".") ? name.substring(0, name.indexOf('.')) : name;
            if (!o.written().contains(name) && !o.rows("added").contains(table)) {
                out.add("writes");
                break;
            }
        }
        // ... and may name what must have gone out: a piece of the subject, the URL, the payload.
        for (JsonNode piece : expected.path("sends_include")) {
            boolean found = false;
            for (String kind : new String[] {"PUB", "EXT"}) {
                for (String sent : o.sent(kind)) {
                    found = found || sent.contains(piece.asText());
                }
            }
            if (!found) {
                out.add("sends");
                break;
            }
        }
        for (String part : COMPARED) {
            if (expected.has(part) && !expected.get(part).equals(seen.get(part))) {
                out.add(part);
            }
        }
        return out;
    }

    private static String engine;

    /** The database product as the connection reports it, e.g. "PostgreSQL 16.4". */
    private static synchronized String engine() {
        if (engine == null) {
            try {
                java.sql.Connection c = EffectRecorder.raw().getConnection();
                try {
                    engine = c.getMetaData().getDatabaseProductName() + " " + c.getMetaData().getDatabaseProductVersion();
                } finally {
                    c.close();
                }
            } catch (java.sql.SQLException e) {
                engine = "unknown";
            }
        }
        return engine;
    }

    private static Map<String, Object> evidence(Scenario.Observation o) {
        Map<String, Object> e = new LinkedHashMap<String, Object>();
        e.put("params", o.params);
        e.put("response", o.outcome.response);
        e.put("database_host", ConnectionGuard.hostOf(ConnectionGuard.check(EffectRecorder.raw())));
        e.put("database_engine", engine());
        e.put("database_offered", System.getenv("AUDIT_DB_URL") != null);
        e.put("time_controlled", AuditClock.controlled());
        e.put("state_built_by_commands_only", true);
        List<Map<String, Object>> effects = new ArrayList<Map<String, Object>>();
        for (EffectRecorder.Effect effect : o.effects) {
            effects.add(effect.toMap());
        }
        e.put("effects", effects);
        e.put("changes", o.changes.size() > 200 ? o.changes.subList(0, 200) : o.changes);
        return e;
    }
}
