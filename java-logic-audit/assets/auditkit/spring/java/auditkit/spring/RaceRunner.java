package auditkit.spring;

import auditkit.AuditConfig;
import auditkit.EffectRecorder;
import auditkit.Interleave;
import auditkit.Outcome;
import auditkit.ResultWriter;
import auditkit.Scenario;
import auditkit.Snapshot;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.io.File;
import java.sql.Connection;
import java.sql.ResultSet;
import java.sql.Statement;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeSet;
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfSystemProperty;

/**
 * Runs two commands at the same time on the same data and decides where one of them stops to let
 * the other pass.
 *
 * <p>A pair is two cases of the matrix that start from the same prepared situation and both change
 * something (a case with itself is the same request sent twice). Each is first run after the other,
 * in both orders: those are the two results an observer could accept. Then, for every database step
 * of the first command, it is stopped before that step, the second runs as far as it can, and the
 * first is let go. One stop per schedule. A second command that cannot finish while the first is
 * stopped is waiting on a lock; that is noted, not a finding.
 *
 * <p>After each schedule: is the data what one of the two orders gives, with the same answers to
 * both callers? Does every rule a person confirmed still hold? Did both come back?
 */
@EnabledIfSystemProperty(named = "audit.run", matches = "races")
class RaceRunner {

    private static final int MAX_STOPS = 16;
    private static final long BLOCKED_AFTER_MS = 400;
    private static final long GIVE_UP_AFTER_MS = 15000;

    static final class Pair {
        JsonNode a;
        JsonNode b;
        int shared;
    }

    static final class Serial {
        String first;
        String second;
        Map<String, List<Map<String, String>>> state;
        int stepsOfFirst;
    }

    private Scenario scenario;
    private final ExecutorService pool = Executors.newCachedThreadPool();

    @Test
    void races() throws Exception {
        ResultWriter results = ResultWriter.open("races");
        try {
            run(results);
        } finally {
            results.close();
            pool.shutdownNow();
        }
    }

    private void run(ResultWriter results) throws Exception {
        scenario = new Scenario(new SpringDriver(AuditContext.get()));
        File model = new File(AuditConfig.load().dir(), "model");
        int budget = Integer.parseInt(System.getProperty("audit.pairs", "10"));
        List<Pair> pairs = choose(AuditConfig.JSON.readTree(new File(model, "matrix.json")), budget);
        List<String[]> rules = confirmedRules(model);
        results.note("pairs", pairs.size());
        results.note("rules_checked", rules.size());
        int n = 0;
        for (Pair pair : pairs) {
            n++;
            Map<String, Object> base = new LinkedHashMap<String, Object>();
            base.put("a", pair.a.path("key").asText());
            base.put("b", pair.b.path("key").asText());
            base.put("a_command", pair.a.get("command").asText());
            base.put("b_command", pair.b.get("command").asText());
            base.put("a_sha", pair.a.path("sha").asText(null));
            base.put("b_sha", pair.b.path("sha").asText(null));
            try {
                Serial ab = serial(pair.a, pair.b);
                Serial ba = serial(pair.b, pair.a);
                for (int side = 0; side < 2; side++) {
                    JsonNode first = side == 0 ? pair.a : pair.b;
                    JsonNode second = side == 0 ? pair.b : pair.a;
                    int steps = side == 0 ? ab.stepsOfFirst : ba.stepsOfFirst;
                    int stride = Math.max(1, (steps + MAX_STOPS - 1) / MAX_STOPS);
                    for (int stop = 0; stop < steps; stop += stride) {
                        String id = String.format("RACE-%02d-%s%02d", n, side == 0 ? "A" : "B", stop);
                        Map<String, Object> detail = new LinkedHashMap<String, Object>(base);
                        detail.put("first", side == 0 ? "a" : "b");
                        detail.put("stopped_after_steps", stop);
                        detail.put("of_steps", steps);
                        one(results, id, detail, first, second, stop, side == 0 ? ab : ba, side == 0 ? ba : ab, rules);
                    }
                }
            } catch (Throwable e) {
                Map<String, Object> detail = new LinkedHashMap<String, Object>(base);
                detail.put("why", e instanceof Scenario.NotReached ? "steps-failed" : "runner-failed");
                detail.put("reason", e.getClass().getSimpleName() + ": " + e.getMessage());
                results.record(String.format("RACE-%02d", n), e instanceof Scenario.NotReached ? ResultWriter.SKIPPED : ResultWriter.ERROR,
                        null, null, detail, null);
            }
        }
    }

    /** Pairs of cases that start from the same situation and both change something; most overlap first. */
    private static List<Pair> choose(JsonNode matrix, int budget) {
        Map<String, List<JsonNode>> bySituation = new LinkedHashMap<String, List<JsonNode>>();
        for (JsonNode cell : matrix.path("cells")) {
            JsonNode e = cell.path("expected");
            if (!cell.hasNonNull("with") || cell.hasNonNull("unreachable") || !"allowed".equals(e.path("outcome").asText())
                    || e.path("written").size() + e.path("created").size() + e.path("deleted").size() == 0) {
                continue;
            }
            String situation = cell.path("fixture").asText("") + "|" + cell.path("steps");
            List<JsonNode> list = bySituation.get(situation);
            if (list == null) {
                list = new ArrayList<JsonNode>();
                bySituation.put(situation, list);
            }
            list.add(cell);
        }
        List<Pair> pairs = new ArrayList<Pair>();
        for (List<JsonNode> cells : bySituation.values()) {
            for (int i = 0; i < cells.size(); i++) {
                for (int j = i; j < cells.size(); j++) {
                    TreeSet<String> touched = touches(cells.get(i));
                    touched.retainAll(touches(cells.get(j)));
                    if (touched.isEmpty()) {
                        continue;
                    }
                    Pair p = new Pair();
                    p.a = cells.get(i);
                    p.b = cells.get(j);
                    p.shared = touched.size() + (i == j ? 0 : 100);     // two different commands before one sent twice
                    pairs.add(p);
                }
            }
        }
        Collections.sort(pairs, new Comparator<Pair>() {
            @Override
            public int compare(Pair x, Pair y) {
                return y.shared != x.shared ? y.shared - x.shared
                        : (x.a.path("key").asText() + x.b.path("key").asText()).compareTo(y.a.path("key").asText() + y.b.path("key").asText());
            }
        });
        // One pair for each pair of commands: the same two commands in another case teach little more.
        List<Pair> chosen = new ArrayList<Pair>();
        TreeSet<String> seen = new TreeSet<String>();
        for (Pair p : pairs) {
            if (chosen.size() < budget && seen.add(p.a.get("command").asText() + "+" + p.b.get("command").asText())) {
                chosen.add(p);
            }
        }
        return chosen;
    }

    private static TreeSet<String> touches(JsonNode cell) {
        TreeSet<String> out = new TreeSet<String>();
        for (String part : new String[] {"written", "created", "deleted"}) {
            for (JsonNode x : cell.path("expected").path(part)) {
                String name = x.asText();
                out.add(name.contains(".") ? name.substring(0, name.indexOf('.')) : name);
            }
        }
        return out;
    }

    private static List<String[]> confirmedRules(File model) throws Exception {
        List<String[]> out = new ArrayList<String[]>();
        File f = new File(model, "invariants.json");
        if (f.isFile()) {
            for (JsonNode n : AuditConfig.JSON.readTree(f).path("invariants")) {
                String sql = n.path("sql").asText("");
                if ("confirmed".equals(n.path("status").asText()) && sql.trim().toLowerCase().startsWith("select") && sql.indexOf(';') < 0) {
                    out.add(new String[] {n.get("id").asText(), sql, n.path("statement").asText()});
                }
            }
        }
        return out;
    }

    private static String answer(Outcome o) {
        return o == null ? "not callable" : o.kind + (o.code != null ? " " + o.code : "");
    }

    /** One command after the other, each to its end. */
    private Serial serial(JsonNode first, JsonNode second) throws Exception {
        ObjectNode context = scenario.prepare(first.path("fixture").asText(null), first.get("steps"));
        Serial s = new Serial();
        Interleave.Lane lane = new Interleave.Lane(-1);
        Interleave.enter(lane);
        try {
            s.first = answer(scenario.tryStep(first.get("command").asText(), first.get("with"), null, context));
        } finally {
            Interleave.leave();
        }
        s.stepsOfFirst = lane.steps;
        s.second = answer(scenario.tryStep(second.get("command").asText(), second.get("with"), null, context));
        s.state = Snapshot.withoutKeys(Snapshot.take());
        return s;
    }

    private Future<Outcome> start(final JsonNode cell, final Interleave.Lane lane, final ObjectNode context) {
        return pool.submit(new Callable<Outcome>() {
            @Override
            public Outcome call() {
                Interleave.enter(lane);
                try {
                    return scenario.tryStep(cell.get("command").asText(), cell.get("with"), null, context);
                } catch (Throwable e) {
                    return new Outcome(Outcome.ERROR, e.getClass().getSimpleName(), null, null, String.valueOf(e.getMessage()));
                } finally {
                    Interleave.leave();
                }
            }
        });
    }

    private static boolean within(long ms, Interleave.Lane lane, boolean orPaused) throws InterruptedException {
        long end = System.currentTimeMillis() + ms;
        while (System.currentTimeMillis() < end) {
            if (lane.done || (orPaused && lane.paused)) {
                return true;
            }
            Thread.sleep(2);
        }
        return lane.done || (orPaused && lane.paused);
    }

    private void one(ResultWriter results, String id, Map<String, Object> detail, JsonNode first, JsonNode second, int stop,
                     Serial firstThenSecond, Serial secondThenFirst, List<String[]> rules) throws Exception {
        ObjectNode context = scenario.prepare(first.path("fixture").asText(null), first.get("steps"));
        Interleave.Lane one = new Interleave.Lane(stop);
        Future<Outcome> f1 = start(first, one, context);
        within(GIVE_UP_AFTER_MS, one, true);
        Interleave.Lane two = new Interleave.Lane(-1);
        Future<Outcome> f2 = start(second, two, context);
        boolean waited = !within(BLOCKED_AFTER_MS, two, false);
        one.release();
        String a1;
        String a2;
        boolean hung = false;
        try {
            a1 = answer(f1.get(GIVE_UP_AFTER_MS, TimeUnit.MILLISECONDS));
            a2 = answer(f2.get(GIVE_UP_AFTER_MS, TimeUnit.MILLISECONDS));
        } catch (java.util.concurrent.TimeoutException e) {
            hung = true;
            a1 = f1.isDone() ? answer(f1.get()) : "did not come back";
            a2 = f2.isDone() ? answer(f2.get()) : "did not come back";
            f1.cancel(true);
            f2.cancel(true);
        }
        Map<String, List<Map<String, String>>> state = Snapshot.withoutKeys(Snapshot.take());
        boolean asFirstThenSecond = state.equals(firstThenSecond.state) && a1.equals(firstThenSecond.first) && a2.equals(firstThenSecond.second);
        boolean asSecondThenFirst = state.equals(secondThenFirst.state) && a2.equals(secondThenFirst.first) && a1.equals(secondThenFirst.second);
        List<String> broken = new ArrayList<String>();
        Connection c = EffectRecorder.raw().getConnection();
        try {
            for (String[] rule : rules) {
                Statement st = c.createStatement();
                st.setMaxRows(1);
                ResultSet rs = st.executeQuery(rule[1]);
                if (rs.next()) {
                    broken.add(rule[0] + ": " + rule[2]);
                }
                rs.close();
                st.close();
            }
        } finally {
            c.close();
        }
        List<String> differs = new ArrayList<String>();
        if (hung) {
            differs.add("did-not-come-back");
        }
        if (!broken.isEmpty()) {
            differs.add("rule-broken");
        }
        if (!hung && !asFirstThenSecond && !asSecondThenFirst) {
            boolean stateOk = state.equals(firstThenSecond.state) || state.equals(secondThenFirst.state);
            differs.add(stateOk ? "answers-of-no-order" : "data-of-no-order");
        }
        detail.put("second_waited", waited);
        detail.put("differs", differs);
        Map<String, Object> expected = new LinkedHashMap<String, Object>();
        expected.put("first_then_second", firstThenSecond.first + " / " + firstThenSecond.second);
        expected.put("second_then_first", secondThenFirst.second + " / " + secondThenFirst.first);
        Map<String, Object> observed = new LinkedHashMap<String, Object>();
        observed.put("first", a1);
        observed.put("second", a2);
        observed.put("same_as", asFirstThenSecond ? "first-then-second" : asSecondThenFirst ? "second-then-first" : "neither");
        observed.put("rules_broken", broken);
        results.record(id, differs.isEmpty() ? ResultWriter.MATCH : ResultWriter.MISMATCH, expected, observed, detail, null);
    }
}
