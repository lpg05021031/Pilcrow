package auditkit.spring;

import org.junit.jupiter.api.function.Executable;
import org.junit.jupiter.api.TestFactory;
import org.junit.jupiter.api.DynamicTest;
import java.util.Collection;
import auditkit.AuditConfig;
import auditkit.ConnectionGuard;
import auditkit.DbReset;
import auditkit.EdgeDoubles;
import auditkit.EffectRecorder;
import auditkit.ResultWriter;
import auditkit.Snapshot;
import auditkit.facts.BytecodeFacts;
import auditkit.facts.SchemaFacts;
import com.fasterxml.jackson.databind.JsonNode;
import java.io.File;
import java.lang.reflect.Method;
import java.sql.Connection;
import java.sql.Statement;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import javax.sql.DataSource;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.Assertions;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.condition.EnabledIfSystemProperty;
import org.springframework.context.ConfigurableApplicationContext;

/**
 * Six checks that the harness works on this application. Each writes one line to the run's
 * results file; a script reads those lines to decide what the audit can prove here.
 *
 * <p>Run with -Daudit.run=smoke. Without that property this class does nothing.
 */
@EnabledIfSystemProperty(named = "audit.run", matches = "smoke")
class SmokeRunner {

    private static ResultWriter results;
    private static ConfigurableApplicationContext context;
    private static Throwable startFailure;

    @BeforeAll
    static void start() {
        results = ResultWriter.open("smoke");
        try {
            context = AuditContext.get();
        } catch (Throwable e) {
            startFailure = e;
        }
    }

    @AfterAll
    static void finish() {
        results.close();
    }

    private interface Check {
        Map<String, Object> run() throws Exception;
    }

    /** Runs a check, records MATCH with its details or MISMATCH with the failure, and fails the test on MISMATCH. */
    private static void record(String id, String title, Check check) {
        Map<String, Object> detail = new LinkedHashMap<String, Object>();
        detail.put("check", title);
        try {
            if (context == null && !id.equals("SMOKE-1")) {
                throw new IllegalStateException("the application context did not start");
            }
            detail.putAll(check.run());
            results.record(id, ResultWriter.MATCH, null, null, detail, null);
        } catch (Throwable e) {
            Throwable root = e;
            while (root.getCause() != null && root.getCause() != root) {
                root = root.getCause();
            }
            detail.put("error", root.getClass().getName() + ": " + root.getMessage());
            results.record(id, ResultWriter.MISMATCH, null, null, detail, null);
            Assertions.fail(title + ": " + detail.get("error"), e);
        }
    }

    /** The six checks, in order: a later one builds on what an earlier one showed. */
    @TestFactory
    Collection<DynamicTest> checks() {
        List<DynamicTest> tests = new ArrayList<DynamicTest>();
        tests.add(DynamicTest.dynamicTest("SMOKE-1", new Executable() {
            @Override
            public void execute() {
                contextStarts();
            }
        }));
        tests.add(DynamicTest.dynamicTest("SMOKE-2", new Executable() {
            @Override
            public void execute() {
                databaseIsLocal();
            }
        }));
        tests.add(DynamicTest.dynamicTest("SMOKE-3", new Executable() {
            @Override
            public void execute() {
                sqlIsSeen();
            }
        }));
        tests.add(DynamicTest.dynamicTest("SMOKE-4", new Executable() {
            @Override
            public void execute() {
                resetIsRepeatable();
            }
        }));
        tests.add(DynamicTest.dynamicTest("SMOKE-5", new Executable() {
            @Override
            public void execute() {
                doublesRecord();
            }
        }));
        tests.add(DynamicTest.dynamicTest("SMOKE-6", new Executable() {
            @Override
            public void execute() {
                factsAreWritten();
            }
        }));
        return tests;
    }

    void contextStarts() {
        record("SMOKE-1", "the application context starts", new Check() {
            @Override
            public Map<String, Object> run() throws Exception {
                if (startFailure != null) {
                    throw new IllegalStateException(startFailure);
                }
                Map<String, Object> d = new LinkedHashMap<String, Object>();
                d.put("application", AuditConfig.load().text("application"));
                d.put("beans", context.getBeanDefinitionCount());
                d.put("scheduled_tasks_disabled", !context.containsBeanDefinition(AuditSpringConfig.SCHEDULED_PROCESSOR));
                return d;
            }
        });
    }

    void databaseIsLocal() {
        record("SMOKE-2", "the database is local or a container, and a remote address is refused", new Check() {
            @Override
            public Map<String, Object> run() throws Exception {
                String url = ConnectionGuard.check(EffectRecorder.raw());
                if (ConnectionGuard.isAllowed("db.prod.example.com", false)) {
                    throw new IllegalStateException("the connection guard accepted a remote host");
                }
                Map<String, Object> d = new LinkedHashMap<String, Object>();
                d.put("host", ConnectionGuard.hostOf(url));
                d.put("engine", SchemaFacts.collect().get("engine"));
                d.put("source", System.getenv("AUDIT_DB_URL") != null ? "offered" : "project");
                return d;
            }
        });
    }

    void sqlIsSeen() {
        record("SMOKE-3", "statements and commits pass through the proxy", new Check() {
            @Override
            public Map<String, Object> run() throws Exception {
                DataSource ds = context.getBean(DataSource.class);
                EffectRecorder.start();
                Connection c = ds.getConnection();
                try {
                    c.setAutoCommit(false);
                    Statement st = c.createStatement();
                    try {
                        st.execute("SELECT 1");
                    } finally {
                        st.close();
                    }
                    c.commit();
                } finally {
                    c.close();
                }
                List<EffectRecorder.Effect> effects = EffectRecorder.stop();
                List<String> kinds = new ArrayList<String>();
                for (EffectRecorder.Effect e : effects) {
                    kinds.add(e.kind);
                }
                if (!kinds.contains("SQL") || !kinds.contains("COMMIT") || !kinds.contains("BEGIN")) {
                    throw new IllegalStateException("expected BEGIN, SQL and COMMIT, saw " + kinds);
                }
                Map<String, Object> d = new LinkedHashMap<String, Object>();
                d.put("effects", kinds);
                return d;
            }
        });
    }

    void resetIsRepeatable() {
        record("SMOKE-4", "two resets leave the database in the same state", new Check() {
            @Override
            public Map<String, Object> run() throws Exception {
                List<String> tables = DbReset.reset();
                Object first = Snapshot.take();
                DbReset.reset();
                Object second = Snapshot.take();
                if (!first.equals(second)) {
                    throw new IllegalStateException("the snapshot after a second reset differs from the first");
                }
                Map<String, Object> d = new LinkedHashMap<String, Object>();
                d.put("tables", tables);
                // The users harness.json signs in must be able to, from the data a reset leaves: a case starts there.
                d.put("signed_in", new SpringDriver(context).signIn());
                return d;
            }
        });
    }

    void doublesRecord() {
        record("SMOKE-5", "every outside system named in harness.json is replaced by a recording double", new Check() {
            @Override
            public Map<String, Object> run() throws Exception {
                AuditConfig config = AuditConfig.load();
                List<Map<String, Object>> doubles = new ArrayList<Map<String, Object>>();
                for (JsonNode node : config.nodes("doubles")) {
                    String typeName = node.get("type").asText();
                    Class<?> type = Class.forName(typeName);
                    Object bean = context.getBean(type);
                    if (bean != EdgeDoubles.all().get(typeName)) {
                        throw new IllegalStateException("the bean of type " + typeName + " is not the audit's double");
                    }
                    List<String> wrong = EdgeDoubles.answerProblems(type, node);
                    if (!wrong.isEmpty()) {
                        throw new IllegalStateException(wrong.toString());
                    }
                    String probed = EdgeDoubles.probe(bean, type);
                    Map<String, Object> row = new LinkedHashMap<String, Object>();
                    row.put("type", typeName);
                    row.put("kind", node.has("kind") ? node.get("kind").asText() : "external");
                    row.put("edge", EdgeDoubles.isEdge(typeName, config.basePackage()));
                    row.put("probe", probed);
                    row.put("call_recorded", probed != null);
                    row.put("answers", node.path("answers").size());
                    doubles.add(row);
                }
                Map<String, Object> d = new LinkedHashMap<String, Object>();
                d.put("doubles", doubles);
                d.put("http_rules", config.nodes("http_doubles").size());
                try {
                    Class<?> rest = Class.forName("auditkit.spring.restclient.RestClientDouble");
                    Method selfTest = rest.getMethod("selfTest", org.springframework.context.ApplicationContext.class);
                    @SuppressWarnings("unchecked")
                    Map<String, Object> http = (Map<String, Object>) selfTest.invoke(null, context);
                    d.put("http_restclient", http.get("reached"));
                    d.put("http_examples", http.get("examples"));
                } catch (ClassNotFoundException e) {
                    d.put("http_restclient", null);
                }
                for (Map<String, Object> row : doubles) {
                    // A type with no getter to probe is still the audit's double (checked above); that is enough.
                    if (row.get("probe") == null && hasGetter(Class.forName((String) row.get("type")))) {
                        throw new IllegalStateException("no call was recorded on the double for " + row.get("type"));
                    }
                }
                if (Boolean.FALSE.equals(d.get("http_restclient"))) {
                    throw new IllegalStateException("a request sent through the application's RestClient builder did not reach the HTTP double");
                }
                if (d.get("http_examples") != null) {
                    for (Object item : (List<?>) d.get("http_examples")) {
                        Map<?, ?> row = (Map<?, ?>) item;
                        if (row.get("example") == null) {
                            throw new IllegalStateException("the HTTP rule " + row.get("url") + " has no example request to prove it with");
                        }
                        if (!Boolean.TRUE.equals(row.get("answered"))) {
                            throw new IllegalStateException("the HTTP rule " + row.get("url") + " does not answer its own example " + row.get("example"));
                        }
                    }
                }
                return d;
            }
        });
    }

    void factsAreWritten() {
        record("SMOKE-6", "schema, context, mapping and bytecode facts are written", new Check() {
            @Override
            public Map<String, Object> run() throws Exception {
                AuditConfig config = AuditConfig.load();
                File dir = new File(config.dir(), "facts");
                if (!dir.isDirectory() && !dir.mkdirs()) {
                    throw new IllegalStateException("cannot create " + dir);
                }
                Map<String, Object> written = new LinkedHashMap<String, Object>();
                written.put("schema.json", write(dir, "schema.json", SchemaFacts.collect()));
                written.put("context.json", write(dir, "context.json", ContextFacts.collect(context, config.basePackage())));
                written.put("jpa.json", write(dir, "jpa.json", JpaFacts.collect(context)));
                written.put("bytecode.json", write(dir, "bytecode.json", BytecodeFacts.collect(config.basePackage())));
                Map<String, Object> d = new LinkedHashMap<String, Object>();
                d.put("files", written);
                return d;
            }
        });
    }

    private static boolean hasGetter(Class<?> type) {
        for (Method m : type.getMethods()) {
            if ((m.getName().startsWith("get") || m.getName().startsWith("is")) && m.getParameterTypes().length == 0
                    && m.getDeclaringClass() != Object.class && !java.lang.reflect.Modifier.isStatic(m.getModifiers())) {
                return true;
            }
        }
        return false;
    }

    private static long write(File dir, String name, Object value) throws Exception {
        File file = new File(dir, name);
        AuditConfig.JSON.writerWithDefaultPrettyPrinter().writeValue(file, value);
        if (file.length() < 3) {
            throw new IllegalStateException(name + " is empty");
        }
        return file.length();
    }
}
