package auditkit;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.io.File;
import java.io.IOException;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeSet;

/**
 * Executes what the model describes: a fixture (commands that bring the system to a state), then one
 * command under observation. Every state is reached through commands only, never by writing rows.
 */
public final class Scenario {

    /** Thrown when the steps before the command under test did not go as the model says. */
    public static final class NotReached extends RuntimeException {
        public NotReached(String message) {
            super(message);
        }
    }

    /** What one call of the command under test did. */
    public static final class Observation {
        public Outcome outcome;
        public String kind;
        public Map<String, String> given = new LinkedHashMap<String, String>();
        public Map<String, String> after = new LinkedHashMap<String, String>();
        public List<Map<String, Object>> changes = new ArrayList<Map<String, Object>>();
        public List<EffectRecorder.Effect> effects = new ArrayList<EffectRecorder.Effect>();
        public List<String> flags = new ArrayList<String>();
        public JsonNode params;

        /** table.column for every column whose value changed in an existing row. */
        public TreeSet<String> written() {
            TreeSet<String> out = new TreeSet<String>();
            for (Map<String, Object> c : changes) {
                if ("modified".equals(c.get("change"))) {
                    out.add(c.get("table") + "." + c.get("column"));
                }
            }
            return out;
        }

        public TreeSet<String> rows(String change) {
            TreeSet<String> out = new TreeSet<String>();
            for (Map<String, Object> c : changes) {
                if (change.equals(c.get("change"))) {
                    out.add(String.valueOf(c.get("table")));
                }
            }
            return out;
        }

        /** What was sent out, in order: the call and what it was given (a destination, a payload). */
        public List<String> sent(String effectKind) {
            List<String> out = new ArrayList<String>();
            for (EffectRecorder.Effect e : effects) {
                if (effectKind.equals(e.kind)) {
                    StringBuilder text = new StringBuilder(e.detail);
                    Object args = e.more == null ? null : e.more.get("args");
                    if (args instanceof List) {
                        for (Object arg : (List<?>) args) {
                            text.append(' ').append(sameForSameContent(String.valueOf(arg)));
                        }
                    }
                    out.add(text.toString());
                }
            }
            return out;
        }
    }

    /**
     * A JSON object says the same thing whatever the order of its keys, and an application may
     * write them in a different order on every start. Keys are sorted, so that the same content
     * always reads the same.
     */
    static String sameForSameContent(String text) {
        String t = text.trim();
        if (!(t.startsWith("{") && t.endsWith("}")) && !(t.startsWith("[") && t.endsWith("]"))) {
            return text;
        }
        try {
            Object value = AuditConfig.JSON.readValue(t, Object.class);
            return SORTED.writeValueAsString(value);
        } catch (IOException e) {
            return text;
        }
    }

    private static final com.fasterxml.jackson.databind.ObjectMapper SORTED = new com.fasterxml.jackson.databind.ObjectMapper()
            .configure(com.fasterxml.jackson.databind.SerializationFeature.ORDER_MAP_ENTRIES_BY_KEYS, true);

    private final SutDriver driver;
    private final LocalDate today;
    private final Map<String, JsonNode> commands = new LinkedHashMap<String, JsonNode>();
    private final JsonNode fixtures;

    public Scenario(SutDriver driver) {
        this.driver = driver;
        this.today = AuditClock.baseDate();
        File model = new File(AuditConfig.load().dir(), "model");
        try {
            for (JsonNode c : AuditConfig.JSON.readTree(new File(model, "commands.json")).get("commands")) {
                commands.put(c.get("id").asText(), c);
            }
            File f = new File(model, "fixtures.json");
            fixtures = f.isFile() ? AuditConfig.JSON.readTree(f).get("fixtures") : AuditConfig.JSON.createObjectNode();
        } catch (IOException e) {
            throw new IllegalStateException("cannot read the model under " + model, e);
        }
    }

    public JsonNode command(String id) {
        JsonNode c = commands.get(id);
        if (c == null) {
            throw new IllegalArgumentException("the model has no command " + id);
        }
        return c;
    }

    /** Resets the database and the doubles, then runs the named fixture and the extra steps. Returns what they bound. */
    public ObjectNode prepare(String fixture, JsonNode steps) throws Exception {
        DbReset.reset();
        EdgeDoubles.clearCalls();
        AuditClock.reset();
        ObjectNode context = AuditConfig.JSON.createObjectNode();
        if (fixture != null) {
            runFixture(fixture, context, 0);
        }
        runSteps(steps, context, "the cell's own steps");
        return context;
    }

    private void runFixture(String name, ObjectNode context, int depth) throws Exception {
        JsonNode fixture = fixtures.get(name);
        if (fixture == null) {
            throw new NotReached("the model has no fixture '" + name + "'");
        }
        if (depth > 20) {
            throw new NotReached("fixture '" + name + "' refers back to itself");
        }
        if (fixture.hasNonNull("from")) {
            runFixture(fixture.get("from").asText(), context, depth + 1);
        }
        runSteps(fixture.get("steps"), context, "fixture '" + name + "'");
    }

    private void runSteps(JsonNode steps, ObjectNode context, String where) throws Exception {
        if (steps == null) {
            return;
        }
        int n = 0;
        for (JsonNode step : steps) {
            n++;
            if (step.hasNonNull("set_today")) {
                // Time passes between two commands. ${today} keeps meaning the date the case started on.
                try {
                    AuditClock.moveTo(LocalDate.parse(Expr.fill(step.get("set_today"), context, today).asText()));
                } catch (IllegalStateException e) {
                    throw new NotReached(where + ", step " + n + ": " + e.getMessage());
                }
                continue;
            }
            JsonNode command = command(step.get("do").asText());
            JsonNode params = Expr.fill(step.get("with"), context, today);
            Outcome outcome = driver.invoke(command, params);
            boolean mayFail = step.has("may_fail") && step.get("may_fail").asBoolean();
            if (Outcome.ERROR.equals(outcome.kind) || (Outcome.REJECTED.equals(outcome.kind) && !mayFail)) {
                throw new NotReached(where + ", step " + n + " (" + step.get("do").asText() + ") ended "
                        + outcome.kind + (outcome.code != null ? " " + outcome.code : "")
                        + (outcome.error != null ? ": " + outcome.error : ""));
            }
            if (step.hasNonNull("as") && outcome.response != null) {
                context.set(step.get("as").asText(), outcome.response);
            }
        }
    }

    /**
     * The value of each variable of a command that has a query: a state variable read from the
     * database, or the class a parameter falls in, worked out from the parameter and the rows it is
     * compared with. The queries see the command's parameters by name, and :today, which is the
     * date the application sees when the query runs (a step may have moved it).
     */
    public Map<String, String> state(JsonNode command, JsonNode params) {
        Map<String, String> out = new LinkedHashMap<String, String>();
        if (command.has("vars")) {
            ObjectNode named = params != null && params.isObject()
                    ? ((ObjectNode) params).deepCopy() : AuditConfig.JSON.createObjectNode();
            if (!named.has("today")) {
                named.put("today", AuditClock.currentDate().toString());      // the application's date at this moment
            }
            for (JsonNode v : command.get("vars")) {
                if (v.hasNonNull("sql")) {
                    out.put(v.get("name").asText(), Sql.scalar(v.get("sql").asText(), named));
                }
            }
        }
        return out;
    }

    /** Calls the command once and records everything it did. */
    public Observation observe(JsonNode command, JsonNode withTemplate, ObjectNode context) throws Exception {
        Observation o = new Observation();
        o.params = Expr.fill(withTemplate, context, today);
        o.given = state(command, o.params);
        Map<String, List<Map<String, String>>> before = Snapshot.take();
        int doubleCallsBefore = EdgeDoubles.calls().size();
        EffectRecorder.start();
        try {
            o.outcome = driver.invoke(command, o.params);
        } finally {
            o.effects = EffectRecorder.stop();
        }
        o.changes = Snapshot.diff(before, Snapshot.take());
        o.after = state(command, o.params);

        boolean readsOnly = command.path("reads_only").asBoolean(false);
        boolean sentSomething = !o.sent("PUB").isEmpty() || !o.sent("EXT").isEmpty();
        o.kind = o.outcome.kind;
        if (Outcome.ALLOWED.equals(o.kind) && !readsOnly && o.changes.isEmpty() && !sentSomething) {
            o.kind = Outcome.IGNORED;
        }
        if (!o.changes.isEmpty() && (Outcome.REJECTED.equals(o.kind) || Outcome.ERROR.equals(o.kind))) {
            o.flags.add("changed-although-" + o.kind.toLowerCase());
        }
        if (readsOnly && !o.changes.isEmpty()) {
            o.flags.add("read-path-wrote");
        }
        for (EffectRecorder.Effect e : o.effects) {
            if (e.more != null && (Boolean.TRUE.equals(e.more.get("unconfigured")))) {
                o.flags.add("unconfigured-double");
                break;
            }
        }
        if (EdgeDoubles.calls().size() < doubleCallsBefore) {
            o.flags.add("double-calls-cleared");
        }
        return o;
    }
}
