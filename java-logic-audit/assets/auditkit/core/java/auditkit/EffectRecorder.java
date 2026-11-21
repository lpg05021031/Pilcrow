package auditkit;

import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import javax.sql.DataSource;
import net.ttddyy.dsproxy.ExecutionInfo;
import net.ttddyy.dsproxy.QueryInfo;
import net.ttddyy.dsproxy.listener.MethodExecutionContext;
import net.ttddyy.dsproxy.listener.MethodExecutionListener;
import net.ttddyy.dsproxy.listener.QueryExecutionListener;
import net.ttddyy.dsproxy.support.ProxyDataSource;
import net.ttddyy.dsproxy.support.ProxyDataSourceBuilder;

/**
 * Records, in order, what an operation does to the outside world: transaction boundaries and SQL
 * (through a proxy around the data source), and the calls made on the edge doubles.
 */
public final class EffectRecorder {

    /** One thing that happened. kind is BEGIN, SQL, COMMIT, ROLLBACK, PUB or EXT. */
    public static final class Effect {
        public final int seq;
        public final String kind;
        public final String thread;
        public final String connection;
        public final String detail;
        public final Map<String, Object> more;

        Effect(int seq, String kind, String connection, String detail, Map<String, Object> more) {
            this.seq = seq;
            this.kind = kind;
            this.thread = Thread.currentThread().getName();
            this.connection = connection;
            this.detail = detail;
            this.more = more;
        }

        public Map<String, Object> toMap() {
            Map<String, Object> m = new LinkedHashMap<String, Object>();
            m.put("seq", seq);
            m.put("kind", kind);
            m.put("thread", thread);
            if (connection != null) {
                m.put("connection", connection);
            }
            m.put("detail", detail);
            if (more != null) {
                m.putAll(more);
            }
            return m;
        }
    }

    private static final List<Effect> EFFECTS = Collections.synchronizedList(new ArrayList<Effect>());
    private static volatile boolean recording;
    private static volatile DataSource raw;

    private static final Pattern TABLE = Pattern.compile(
            "(?is)^\\s*(?:insert\\s+into|update|delete\\s+from|truncate(?:\\s+table)?|merge\\s+into)\\s+[`\"]?([\\w.]+)");

    private EffectRecorder() {
    }

    /** Wraps the data source so that every statement and transaction boundary passes through here. */
    public static DataSource wrap(DataSource dataSource) {
        if (dataSource instanceof ProxyDataSource) {
            return dataSource;
        }
        raw = dataSource;
        return ProxyDataSourceBuilder.create(dataSource)
                .name("java-logic-audit")
                .listener(new QueryExecutionListener() {
                    @Override
                    public void beforeQuery(ExecutionInfo info, List<QueryInfo> queries) {
                        for (QueryInfo q : queries) {
                            if (Faults.isWrite(verbOf(q.getQuery()))) {
                                Faults.at(Faults.WRITE, "a write to " + tableOf(q.getQuery()));
                            }
                        }
                    }

                    @Override
                    public void afterQuery(ExecutionInfo info, List<QueryInfo> queries) {
                        if (!recording) {
                            return;
                        }
                        for (QueryInfo q : queries) {
                            Map<String, Object> more = new LinkedHashMap<String, Object>();
                            String sql = q.getQuery();
                            more.put("verb", verbOf(sql));
                            String table = tableOf(sql);
                            if (table != null) {
                                more.put("table", table);
                            }
                            more.put("ok", info.isSuccess());
                            Object result = info.getResult();
                            if (result instanceof Integer) {
                                more.put("rows", result);
                            }
                            add("SQL", info.getConnectionId(), sql, more);
                        }
                    }
                })
                .methodListener(new MethodExecutionListener() {
                    @Override
                    public void beforeMethod(MethodExecutionContext context) {
                        if (context.getTarget() instanceof java.sql.Connection && context.getMethod().getName().equals("commit")) {
                            Faults.at(Faults.COMMIT, "the commit");
                        }
                    }

                    @Override
                    public void afterMethod(MethodExecutionContext context) {
                        if (!recording || !(context.getTarget() instanceof java.sql.Connection)) {
                            return;
                        }
                        Method m = context.getMethod();
                        String name = m.getName();
                        String connection = context.getConnectionInfo() == null ? null
                                : context.getConnectionInfo().getConnectionId();
                        if (name.equals("commit")) {
                            add("COMMIT", connection, "", null);
                        } else if (name.equals("rollback")) {
                            add("ROLLBACK", connection, "", null);
                        } else if (name.equals("setAutoCommit") && context.getMethodArgs() != null
                                && context.getMethodArgs().length == 1 && Boolean.FALSE.equals(context.getMethodArgs()[0])) {
                            add("BEGIN", connection, "", null);
                        }
                    }
                })
                .build();
    }

    /** The data source as it was before wrapping: used for resets and snapshots, which are not effects. */
    public static DataSource raw() {
        if (raw == null) {
            throw new IllegalStateException("no data source has been wrapped yet");
        }
        return raw;
    }

    public static void start() {
        EFFECTS.clear();
        recording = true;
    }

    public static List<Effect> stop() {
        recording = false;
        synchronized (EFFECTS) {
            return new ArrayList<Effect>(EFFECTS);
        }
    }

    public static boolean isRecording() {
        return recording;
    }

    /** Called by the edge doubles: kind is PUB for messaging and EXT for an external call. */
    public static void add(String kind, String connection, String detail, Map<String, Object> more) {
        if (!recording) {
            return;
        }
        synchronized (EFFECTS) {
            EFFECTS.add(new Effect(EFFECTS.size() + 1, kind, connection, detail, more));
        }
    }

    static String verbOf(String sql) {
        String s = sql == null ? "" : sql.trim();
        int end = 0;
        while (end < s.length() && Character.isLetter(s.charAt(end))) {
            end++;
        }
        return s.substring(0, end).toUpperCase(Locale.ROOT);
    }

    static String tableOf(String sql) {
        Matcher m = TABLE.matcher(sql == null ? "" : sql);
        return m.find() ? m.group(1).toLowerCase(Locale.ROOT) : null;
    }
}
