package auditkit.spring;

import auditkit.AuditConfig;
import auditkit.EffectRecorder;
import auditkit.Outcome;
import auditkit.ResultWriter;
import auditkit.Scenario;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.io.File;
import java.sql.Connection;
import java.sql.ResultSet;
import java.sql.ResultSetMetaData;
import java.sql.SQLException;
import java.sql.Statement;
import java.util.ArrayList;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Random;
import java.util.Set;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfSystemProperty;

/**
 * Calls the commands in random order, many times, and checks after every call that each invariant
 * still holds.
 *
 * <p>The matrix changes one thing at a time; this runner reaches the combinations it does not. The
 * steps are the ones the model already uses to reach its cases (every step of a fixture or a recipe,
 * with its parameters still written as references), drawn at random on top of a starting fixture.
 * A step whose references cannot be filled in the current sequence is passed over. Refusals are
 * expected and are not findings; an error that is not a refusal is.
 *
 * <p>An invariant is a query that returns the rows breaking it. When one is broken, the sequence is
 * cut down to the fewest steps that still break it and run twice more, so that what is reported can
 * be replayed. Everything follows from the seed (-Daudit.seed), which is recorded.
 */
@EnabledIfSystemProperty(named = "audit.run", matches = "sequences")
class SequenceRunner {

    private static final int SHRINK_BUDGET = 60;

    static final class Step {
        String command;
        JsonNode with;
        String as;
        JsonNode setToday;

        String key() {
            return command + "|" + with + "|" + as + "|" + setToday;
        }
    }

    static final class Invariant {
        String id;
        String sql;
        String applies;
        String status;
        JsonNode definition;
        long applied;
        long violated;
        boolean active = true;
        String broken;
        String root;
        List<Step> trace;
        int sequence;
    }

    interface Check {
        boolean holdsAgainst(Outcome last, Step step, Connection c) throws SQLException;
    }

    private Scenario scenario;
    private final List<Step> pool = new ArrayList<Step>();
    /** Step lists as the model wrote them (propose, confirm, commit): played in order, they get further than single draws. */
    private final List<List<Step>> runs = new ArrayList<List<Step>>();
    private final List<String> roots = new ArrayList<String>();
    private int replays;

    @Test
    void sequences() throws Exception {
        ResultWriter results = ResultWriter.open("sequences");
        try {
            run(results);
        } finally {
            results.close();
        }
    }

    private void run(ResultWriter results) throws Exception {
        scenario = new Scenario(new SpringDriver(AuditContext.get()));
        File model = new File(AuditConfig.load().dir(), "model");
        long seed = Long.parseLong(System.getProperty("audit.seed", "1"));
        int sequences = Integer.parseInt(System.getProperty("audit.sequences", "50"));
        int length = Integer.parseInt(System.getProperty("audit.steps", "20"));
        loadPool(model);
        List<Invariant> invariants = loadInvariants(model);

        Map<String, Map<String, Integer>> calls = new LinkedHashMap<String, Map<String, Integer>>();
        Map<String, Object[]> errors = new LinkedHashMap<String, Object[]>();   // command|code -> root, trace, sequence
        Random random = new Random(seed);
        long states = 0;
        for (int n = 1; n <= sequences && !roots.isEmpty(); n++) {
            String root = roots.get(random.nextInt(roots.size()));
            ObjectNode context;
            try {
                context = scenario.prepare(root, null);
            } catch (Scenario.NotReached e) {
                continue;
            }
            List<Step> trace = new ArrayList<Step>();
            Connection c = EffectRecorder.raw().getConnection();
            try {
                states++;
                check(invariants, c, root, trace, n);
                List<Step> queued = new ArrayList<Step>();
                for (int i = 0; i < length; i++) {
                    Step step = null;
                    Outcome outcome = null;
                    for (int attempt = 0; attempt < 25 && outcome == null; attempt++) {
                        if (queued.isEmpty() && !runs.isEmpty() && random.nextInt(3) == 0) {
                            queued.addAll(runs.get(random.nextInt(runs.size())));
                        }
                        boolean fromRun = !queued.isEmpty();
                        step = fromRun ? queued.remove(0) : pool.get(random.nextInt(pool.size()));
                        if (step.setToday != null) {
                            if (scenario.moveToday(step.setToday, context)) {
                                outcome = new Outcome(Outcome.ALLOWED, null, null, null, null);
                            }
                        } else {
                            outcome = scenario.tryStep(step.command, step.with, step.as, context);
                        }
                        if (outcome == null && fromRun) {
                            queued.clear();     // the rest of that list refers to what this step would have made
                        }
                    }
                    if (outcome == null) {
                        break;      // nothing in the pool can be called from here
                    }
                    trace.add(step);
                    if (step.command != null) {
                        Map<String, Integer> byKind = calls.get(step.command);
                        if (byKind == null) {
                            byKind = new LinkedHashMap<String, Integer>();
                            calls.put(step.command, byKind);
                        }
                        Integer seen = byKind.get(outcome.kind);
                        byKind.put(outcome.kind, seen == null ? 1 : seen + 1);
                        if (Outcome.ERROR.equals(outcome.kind)) {
                            String key = step.command + "|" + outcome.code;
                            if (!errors.containsKey(key)) {
                                errors.put(key, new Object[] {root, new ArrayList<Step>(trace), n, outcome.error});
                            }
                        }
                    }
                    states++;
                    check(invariants, c, root, trace, n);
                }
            } finally {
                c.close();
            }
        }

        results.note("seed", seed);
        results.note("sequences", sequences);
        results.note("steps", length);
        results.note("states", states);
        results.note("pool", pool.size());
        results.note("step_lists", runs.size());
        results.note("roots", roots);
        results.note("calls", calls);

        for (final Invariant inv : invariants) {
            Map<String, Object> detail = new LinkedHashMap<String, Object>();
            detail.put("class", inv.definition.path("class").asText(null));
            detail.put("statement", inv.definition.path("statement").asText(null));
            detail.put("status", inv.status);
            Map<String, Object> observed = new LinkedHashMap<String, Object>();
            observed.put("applied", inv.applied);
            observed.put("violated", inv.violated);
            if (inv.broken != null) {
                detail.put("why", "query-failed");
                detail.put("reason", inv.broken);
                results.record(inv.id, ResultWriter.ERROR, null, observed, detail, null);
            } else if (inv.violated == 0) {
                if (inv.applied == 0) {
                    detail.put("why", "never-applied");
                    detail.put("reason", "no state held rows this applies to");
                }
                results.record(inv.id, inv.applied == 0 ? ResultWriter.SKIPPED : ResultWriter.MATCH, "no row breaks it", observed, detail, null);
            } else {
                Map<String, Object> evidence = null;
                // Cutting a sequence down costs replays: done for what a person confirmed, and for what almost always held.
                boolean worth = "confirmed".equals(inv.status) || inv.violated * 20 <= inv.applied;
                if (worth) {
                    evidence = minimise(inv.root, inv.trace, new Check() {
                        @Override
                        public boolean holdsAgainst(Outcome last, Step step, Connection c) throws SQLException {
                            return !any(c, inv.sql);
                        }
                    }, inv.sql);
                    observed.put("replays_violating", evidence.get("replays_violating"));
                    observed.put("steps_to_reproduce", ((List<?>) evidence.get("steps")).size());
                }
                observed.put("first_seen_in_sequence", inv.sequence);
                results.record(inv.id, ResultWriter.MISMATCH, "no row breaks it", observed, detail, evidence);
            }
        }
        int k = 0;
        for (Map.Entry<String, Object[]> e : errors.entrySet()) {
            k++;
            final String command = e.getKey().substring(0, e.getKey().indexOf('|'));
            final String code = e.getKey().substring(e.getKey().indexOf('|') + 1);
            @SuppressWarnings("unchecked")
            List<Step> trace = (List<Step>) e.getValue()[1];
            Map<String, Object> evidence = minimise((String) e.getValue()[0], trace, new Check() {
                @Override
                public boolean holdsAgainst(Outcome last, Step step, Connection c) {
                    return !(last != null && step.command != null && step.command.equals(command)
                            && Outcome.ERROR.equals(last.kind) && code.equals(String.valueOf(last.code)));
                }
            }, null);
            Map<String, Object> detail = new LinkedHashMap<String, Object>();
            detail.put("class", "error");
            detail.put("command", command);
            detail.put("code", code);
            detail.put("statement", command + " ends in an error that is not a refusal (" + code + ")");
            Map<String, Object> observed = new LinkedHashMap<String, Object>();
            observed.put("error", e.getValue()[3]);
            observed.put("first_seen_in_sequence", e.getValue()[2]);
            observed.put("replays_violating", evidence.get("replays_violating"));
            observed.put("steps_to_reproduce", ((List<?>) evidence.get("steps")).size());
            results.record(String.format("SEQERR-%03d", k), ResultWriter.MISMATCH, "no error", observed, detail, evidence);
        }
        results.note("shrink_replays", replays);
    }

    // ---- loading

    private void loadPool(File model) throws Exception {
        Set<String> seen = new LinkedHashSet<String>();
        JsonNode fixtures = AuditConfig.JSON.readTree(new File(model, "fixtures.json")).path("fixtures");
        Iterator<Map.Entry<String, JsonNode>> it = fixtures.fields();
        while (it.hasNext()) {
            Map.Entry<String, JsonNode> f = it.next();
            if (!f.getValue().hasNonNull("from") && f.getValue().path("steps").size() > 0) {
                roots.add(f.getKey());
            }
            addSteps(f.getValue().path("steps"), seen);
            if (f.getValue().hasNonNull("from")) {
                addRun(f.getValue().path("steps"));
            }
        }
        for (JsonNode cell : AuditConfig.JSON.readTree(new File(model, "matrix.json")).path("cells")) {
            if (!cell.hasNonNull("with") || cell.hasNonNull("unreachable")) {
                continue;
            }
            addSteps(cell.path("steps"), seen);
            Step own = new Step();
            own.command = cell.get("command").asText();
            own.with = cell.get("with");
            if (seen.add(own.key())) {
                pool.add(own);
            }
            List<Step> whole = addRun(cell.path("steps"));
            if (whole != null) {
                whole.add(own);
            }
        }
    }

    private static Step stepOf(JsonNode s) {
        Step step = new Step();
        if (s.hasNonNull("set_today")) {
            step.setToday = s.get("set_today");
        } else {
            step.command = s.path("do").asText(null);
            step.with = s.get("with");
            step.as = s.path("as").asText(null);
        }
        return step.command != null || step.setToday != null ? step : null;
    }

    private void addSteps(JsonNode steps, Set<String> seen) {
        for (JsonNode s : steps) {
            Step step = stepOf(s);
            if (step != null && seen.add(step.key())) {
                pool.add(step);
            }
        }
    }

    /** Keeps a list of two or more steps to be played in order. Returns it, or null when there was nothing to keep. */
    private List<Step> addRun(JsonNode steps) {
        List<Step> run = new ArrayList<Step>();
        for (JsonNode s : steps) {
            Step step = stepOf(s);
            if (step != null) {
                run.add(step);
            }
        }
        if (run.isEmpty()) {
            return null;
        }
        runs.add(run);
        return run;
    }

    private List<Invariant> loadInvariants(File model) throws Exception {
        List<Invariant> out = new ArrayList<Invariant>();
        File f = new File(model, "invariants.json");
        if (!f.isFile()) {
            return out;
        }
        for (JsonNode n : AuditConfig.JSON.readTree(f).path("invariants")) {
            String status = n.path("status").asText("candidate");
            if ("rejected".equals(status) || "discarded".equals(status) || !n.hasNonNull("sql")) {
                continue;
            }
            Invariant inv = new Invariant();
            inv.id = n.get("id").asText();
            inv.sql = n.get("sql").asText();
            inv.applies = n.path("applies").asText(null);
            inv.status = status;
            inv.definition = n;
            String shape = inv.sql.trim().toLowerCase();
            if (!shape.startsWith("select") || shape.indexOf(';') >= 0) {
                inv.active = false;
                inv.broken = "an invariant's query must be one SELECT";
            }
            out.add(inv);
        }
        return out;
    }

    // ---- checking

    private static boolean any(Connection c, String sql) throws SQLException {
        Statement st = c.createStatement();
        try {
            st.setMaxRows(1);
            ResultSet rs = st.executeQuery(sql);
            try {
                return rs.next();
            } finally {
                rs.close();
            }
        } finally {
            st.close();
        }
    }

    private void check(List<Invariant> invariants, Connection c, String root, List<Step> trace, int sequence) {
        for (Invariant inv : invariants) {
            if (!inv.active) {
                continue;
            }
            try {
                if (inv.applies != null && !any(c, inv.applies)) {
                    continue;
                }
                inv.applied++;
                if (any(c, inv.sql)) {
                    inv.violated++;
                    if (inv.trace == null) {
                        inv.root = root;
                        inv.trace = new ArrayList<Step>(trace);
                        inv.sequence = sequence;
                    }
                    // A candidate from a template that breaks often is simply not a rule: stop spending on it.
                    if (!"confirmed".equals(inv.status) && inv.violated >= 5 && inv.violated * 5 > inv.applied) {
                        inv.active = false;
                    }
                }
            } catch (SQLException e) {
                inv.active = false;
                inv.broken = e.getMessage();
            }
        }
    }

    // ---- cutting a sequence down

    /** Replays root + steps. Returns the index of the step after which the check fails (0 = right after the root), or -1. */
    private int failsAt(String root, List<Step> steps, Check check, List<Map<String, Object>> log) throws Exception {
        replays++;
        ObjectNode context;
        try {
            context = scenario.prepare(root, null);
        } catch (Scenario.NotReached e) {
            return -1;
        }
        Connection c = EffectRecorder.raw().getConnection();
        try {
            if (!check.holdsAgainst(null, null, c)) {
                return 0;
            }
            for (int i = 0; i < steps.size(); i++) {
                Step step = steps.get(i);
                Outcome outcome;
                Map<String, Object> line = new LinkedHashMap<String, Object>();
                if (step.setToday != null) {
                    scenario.moveToday(step.setToday, context);
                    outcome = null;
                    line.put("set_today", step.setToday);
                } else {
                    outcome = scenario.tryStep(step.command, step.with, step.as, context);
                    line.put("do", step.command);
                    line.put("with", step.with);
                    if (step.as != null) {
                        line.put("as", step.as);
                    }
                    if (outcome == null) {
                        line.put("outcome", "not callable here");
                    } else {
                        line.put("called_with", scenario.lastParams());
                        line.put("outcome", outcome.kind + (outcome.code != null ? " " + outcome.code : ""));
                    }
                }
                if (log != null) {
                    log.add(line);
                }
                if (!check.holdsAgainst(outcome, step, c)) {
                    return i + 1;
                }
            }
            return -1;
        } finally {
            c.close();
        }
    }

    private Map<String, Object> minimise(String root, List<Step> trace, Check check, String rowsSql) throws Exception {
        List<Step> steps = new ArrayList<Step>(trace);
        int budget = SHRINK_BUDGET;
        int at = failsAt(root, steps, check, null);
        if (at >= 0) {
            steps = new ArrayList<Step>(steps.subList(0, at));
        }
        for (int i = steps.size() - 1; i >= 0 && budget > 0; i--) {
            List<Step> without = new ArrayList<Step>(steps);
            without.remove(i);
            budget--;
            int fails = failsAt(root, without, check, null);
            if (fails >= 0) {
                steps = new ArrayList<Step>(without.subList(0, fails));
                i = Math.min(i, steps.size());
            }
        }
        int violating = 0;
        List<Map<String, Object>> log = null;
        for (int run = 0; run < 3; run++) {
            List<Map<String, Object>> thisLog = new ArrayList<Map<String, Object>>();
            if (failsAt(root, steps, check, thisLog) >= 0) {
                violating++;
            }
            log = thisLog;
        }
        Map<String, Object> evidence = new LinkedHashMap<String, Object>();
        evidence.put("fixture", root);
        evidence.put("steps", log);
        evidence.put("replays_violating", violating);
        evidence.put("of_replays", 3);
        if (rowsSql != null) {
            evidence.put("rows_breaking_it", rows(rowsSql));
        }
        return evidence;
    }

    private static List<Map<String, String>> rows(String sql) {
        List<Map<String, String>> out = new ArrayList<Map<String, String>>();
        try {
            Connection c = EffectRecorder.raw().getConnection();
            try {
                Statement st = c.createStatement();
                st.setMaxRows(3);
                ResultSet rs = st.executeQuery(sql);
                ResultSetMetaData meta = rs.getMetaData();
                while (rs.next()) {
                    Map<String, String> row = new LinkedHashMap<String, String>();
                    for (int i = 1; i <= meta.getColumnCount(); i++) {
                        row.put(meta.getColumnLabel(i), String.valueOf(rs.getObject(i)));
                    }
                    out.add(row);
                }
                rs.close();
                st.close();
            } finally {
                c.close();
            }
        } catch (SQLException e) {
            // the rows are an illustration; the result does not depend on them
        }
        return out;
    }
}
