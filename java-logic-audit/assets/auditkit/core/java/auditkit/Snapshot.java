package auditkit;

import java.math.BigDecimal;
import java.sql.Connection;
import java.sql.DatabaseMetaData;
import java.sql.ResultSet;
import java.sql.ResultSetMetaData;
import java.sql.SQLException;
import java.sql.Statement;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.TreeMap;

/**
 * The content of every application table, in a stable order and a canonical text form, so that two
 * snapshots can be compared and the difference between them read as "which columns changed".
 */
public final class Snapshot {

    private Snapshot() {
    }

    /** table -> rows (ordered by primary key) -> column -> value as text, or null. */
    public static Map<String, List<Map<String, String>>> take() {
        Map<String, List<Map<String, String>>> out = new TreeMap<String, List<Map<String, String>>>();
        try {
            Connection c = EffectRecorder.raw().getConnection();
            try {
                for (String table : DbReset.tables(c)) {
                    out.put(table.toLowerCase(Locale.ROOT), rows(c, table));
                }
            } finally {
                c.close();
            }
        } catch (SQLException e) {
            throw new IllegalStateException("snapshot failed", e);
        }
        return out;
    }

    /** Columns that two executions of the same thing were seen to disagree in, as "table.column" ("table.*": the whole table). */
    private static final java.util.Set<String> SET_ASIDE = java.util.Collections.synchronizedSet(new java.util.TreeSet<String>());
    private static final Map<String, java.util.Set<String>> KEYS = new java.util.concurrent.ConcurrentHashMap<String, java.util.Set<String>>();
    private static boolean timesLooked;

    /**
     * The form two snapshots are compared in: without key columns (primary keys and the columns that
     * refer to them: after a failed attempt the database has handed out numbers it will not hand out
     * again, so a second attempt gets other keys), and without the columns that cannot be the same in
     * two executions of the same thing — a time read from a clock the audit does not hold, a number
     * drawn at random. Which those are is learned (see learn) and, where the application's clock is
     * not the audit's, every date and time column is one.
     */
    public static Map<String, List<Map<String, String>>> withoutKeys(Map<String, List<Map<String, String>>> snapshot) {
        Map<String, List<Map<String, String>>> bare = keysRemoved(snapshot);
        java.util.Set<String> aside = setAside();
        if (aside.isEmpty()) {
            return bare;
        }
        Map<String, List<Map<String, String>>> out = new TreeMap<String, List<Map<String, String>>>();
        for (Map.Entry<String, List<Map<String, String>>> t : bare.entrySet()) {
            if (aside.contains(t.getKey() + ".*")) {
                continue;
            }
            List<Map<String, String>> rows = new ArrayList<Map<String, String>>();
            for (Map<String, String> row : t.getValue()) {
                Map<String, String> kept = new LinkedHashMap<String, String>();
                for (Map.Entry<String, String> col : row.entrySet()) {
                    if (!aside.contains(t.getKey() + "." + col.getKey())) {
                        kept.put(col.getKey(), col.getValue());
                    }
                }
                rows.add(kept);
            }
            out.put(t.getKey(), rows);
        }
        return out;
    }

    /**
     * Learns from two snapshots taken after the same thing was done twice, from the same start, which
     * columns do not come out the same: they are left out of every later comparison. Without this, an
     * application that stamps rows with the time of day would differ from itself and every comparison
     * with a disturbed run would report a difference that is not one.
     */
    public static void learn(Map<String, List<Map<String, String>>> once, Map<String, List<Map<String, String>>> again) {
        Map<String, List<Map<String, String>>> a = keysRemoved(once);
        Map<String, List<Map<String, String>>> b = keysRemoved(again);
        for (Map.Entry<String, List<Map<String, String>>> t : a.entrySet()) {
            List<Map<String, String>> other = b.get(t.getKey());
            if (other == null || other.size() != t.getValue().size()) {
                SET_ASIDE.add(t.getKey() + ".*");
                continue;
            }
            for (int i = 0; i < other.size(); i++) {
                for (Map.Entry<String, String> col : t.getValue().get(i).entrySet()) {
                    String theirs = other.get(i).get(col.getKey());
                    if (col.getValue() == null ? theirs != null : !col.getValue().equals(theirs)) {
                        SET_ASIDE.add(t.getKey() + "." + col.getKey());
                    }
                }
            }
        }
    }

    /** What comparisons leave out besides keys, as "table.column". */
    public static java.util.Set<String> setAside() {
        synchronized (Snapshot.class) {
            if (!timesLooked) {
                timesLooked = true;
                if (!AuditClock.controlled()) {
                    SET_ASIDE.addAll(temporalColumns());
                }
            }
        }
        synchronized (SET_ASIDE) {
            return new java.util.TreeSet<String>(SET_ASIDE);
        }
    }

    private static java.util.Set<String> temporalColumns() {
        java.util.Set<String> out = new java.util.TreeSet<String>();
        try {
            Connection c = EffectRecorder.raw().getConnection();
            try {
                String schema = null;
                try {
                    schema = c.getSchema();
                } catch (SQLException e) {
                    // unsupported
                } catch (AbstractMethodError e) {
                    // unsupported
                }
                for (String table : DbReset.tables(c)) {
                    ResultSet rs = c.getMetaData().getColumns(c.getCatalog(), schema, table, "%");
                    try {
                        while (rs.next()) {
                            int type = rs.getInt("DATA_TYPE");
                            if (type == java.sql.Types.DATE || type == java.sql.Types.TIME || type == java.sql.Types.TIMESTAMP
                                    || type == 2013 || type == 2014) {          // the two "with time zone" types of JDBC 4.2
                                out.add(table.toLowerCase(Locale.ROOT) + "." + rs.getString("COLUMN_NAME").toLowerCase(Locale.ROOT));
                            }
                        }
                    } finally {
                        rs.close();
                    }
                }
            } finally {
                c.close();
            }
        } catch (SQLException e) {
            throw new IllegalStateException("reading the column types failed", e);
        }
        return out;
    }

    /** The key columns of a table: its primary key, declared references, and columns named after another table. */
    private static java.util.Set<String> keyColumns(Connection c, String table, List<String> tables) throws SQLException {
        java.util.Set<String> known = KEYS.get(table);
        if (known != null) {
            return known;
        }
        java.util.Set<String> keys = new java.util.HashSet<String>();
        for (String k : primaryKey(c, table)) {
            keys.add(k.toLowerCase(Locale.ROOT));
        }
        String schema = null;
        try {
            schema = c.getSchema();
        } catch (SQLException e) {
            // unsupported
        } catch (AbstractMethodError e) {
            // unsupported
        }
        ResultSet rs = c.getMetaData().getImportedKeys(c.getCatalog(), schema, table);
        try {
            while (rs.next()) {
                keys.add(rs.getString("FKCOLUMN_NAME").toLowerCase(Locale.ROOT));
            }
        } finally {
            rs.close();
        }
        // Many schemas declare no references at all. A column named after another table and "_id"
        // (order_id beside a table "order" or "shop_order") is taken for a reference to it.
        rs = c.getMetaData().getColumns(c.getCatalog(), schema, table, "%");
        try {
            while (rs.next()) {
                String column = rs.getString("COLUMN_NAME").toLowerCase(Locale.ROOT);
                if (column.endsWith("_id") && column.length() > 3) {
                    String stem = column.substring(0, column.length() - 3);
                    for (String other : tables) {
                        String name = other.toLowerCase(Locale.ROOT);
                        if (name.equals(stem) || name.endsWith("_" + stem)) {
                            keys.add(column);
                            break;
                        }
                    }
                }
            }
        } finally {
            rs.close();
        }
        KEYS.put(table, keys);
        return keys;
    }

    private static Map<String, List<Map<String, String>>> keysRemoved(Map<String, List<Map<String, String>>> snapshot) {
        Map<String, List<Map<String, String>>> out = new TreeMap<String, List<Map<String, String>>>();
        try {
            Connection c = EffectRecorder.raw().getConnection();
            try {
                List<String> tables = DbReset.tables(c);
                for (String table : tables) {
                    java.util.Set<String> keys = keyColumns(c, table, tables);
                    List<Map<String, String>> rows = new ArrayList<Map<String, String>>();
                    List<Map<String, String>> original = snapshot.get(table.toLowerCase(Locale.ROOT));
                    for (Map<String, String> row : original == null ? new ArrayList<Map<String, String>>() : original) {
                        Map<String, String> kept = new LinkedHashMap<String, String>(row);
                        kept.keySet().removeAll(keys);
                        rows.add(kept);
                    }
                    out.put(table.toLowerCase(Locale.ROOT), rows);
                }
            } finally {
                c.close();
            }
        } catch (SQLException e) {
            throw new IllegalStateException("snapshot failed", e);
        }
        return out;
    }

    private static List<Map<String, String>> rows(Connection c, String table) throws SQLException {
        List<String> keys = primaryKey(c, table);
        StringBuilder sql = new StringBuilder("SELECT * FROM ").append(quote(c, table));
        if (!keys.isEmpty()) {
            sql.append(" ORDER BY ");
            for (int i = 0; i < keys.size(); i++) {
                sql.append(i == 0 ? "" : ", ").append(quote(c, keys.get(i)));
            }
        }
        List<Map<String, String>> rows = new ArrayList<Map<String, String>>();
        Statement st = c.createStatement();
        try {
            ResultSet rs = st.executeQuery(sql.toString());
            try {
                ResultSetMetaData meta = rs.getMetaData();
                while (rs.next()) {
                    Map<String, String> row = new LinkedHashMap<String, String>();
                    for (int i = 1; i <= meta.getColumnCount(); i++) {
                        row.put(meta.getColumnLabel(i).toLowerCase(Locale.ROOT), text(rs.getObject(i)));
                    }
                    rows.add(row);
                }
            } finally {
                rs.close();
            }
        } finally {
            st.close();
        }
        return rows;
    }

    static List<String> primaryKey(Connection c, String table) throws SQLException {
        Map<Integer, String> ordered = new TreeMap<Integer, String>();
        DatabaseMetaData meta = c.getMetaData();
        String schema = null;
        try {
            schema = c.getSchema();
        } catch (SQLException e) {
            // unsupported
        } catch (AbstractMethodError e) {
            // unsupported
        }
        ResultSet rs = meta.getPrimaryKeys(c.getCatalog(), schema, table);
        try {
            while (rs.next()) {
                ordered.put((int) rs.getShort("KEY_SEQ"), rs.getString("COLUMN_NAME"));
            }
        } finally {
            rs.close();
        }
        return new ArrayList<String>(ordered.values());
    }

    private static String quote(Connection c, String identifier) throws SQLException {
        String q = c.getMetaData().getIdentifierQuoteString();
        q = q == null || q.trim().isEmpty() ? "" : q;
        return q + identifier + q;
    }

    /** Numbers keep the scale the database reports; times are ISO text; binary is its length. */
    static String text(Object value) {
        if (value == null) {
            return null;
        }
        if (value instanceof BigDecimal) {
            return ((BigDecimal) value).toPlainString();
        }
        if (value instanceof byte[]) {
            return "(binary, " + ((byte[]) value).length + " bytes)";
        }
        return String.valueOf(value);
    }

    /** table -> list of {key, column, before, after} for every cell that differs, plus added and removed rows. */
    public static List<Map<String, Object>> diff(Map<String, List<Map<String, String>>> before,
                                                 Map<String, List<Map<String, String>>> after) {
        List<Map<String, Object>> out = new ArrayList<Map<String, Object>>();
        for (String table : after.keySet()) {
            List<Map<String, String>> a = before.containsKey(table) ? before.get(table) : new ArrayList<Map<String, String>>();
            List<Map<String, String>> b = after.get(table);
            int n = Math.max(a.size(), b.size());
            for (int i = 0; i < n; i++) {
                Map<String, String> ra = i < a.size() ? a.get(i) : null;
                Map<String, String> rb = i < b.size() ? b.get(i) : null;
                if (ra == null || rb == null) {
                    Map<String, Object> change = new LinkedHashMap<String, Object>();
                    change.put("table", table);
                    change.put("row", i);
                    change.put("change", ra == null ? "added" : "removed");
                    change.put("values", ra == null ? rb : ra);
                    out.add(change);
                    continue;
                }
                for (String column : rb.keySet()) {
                    String va = ra.get(column);
                    String vb = rb.get(column);
                    if (va == null ? vb != null : !va.equals(vb)) {
                        Map<String, Object> change = new LinkedHashMap<String, Object>();
                        change.put("table", table);
                        change.put("row", i);
                        change.put("change", "modified");
                        change.put("column", column);
                        change.put("before", va);
                        change.put("after", vb);
                        out.add(change);
                    }
                }
            }
        }
        return out;
    }
}
