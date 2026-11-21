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

    /**
     * The same snapshot without key columns: primary keys and the columns that refer to them.
     * After a failed attempt the database has handed out numbers it will not hand out again, so a
     * second attempt gets other keys; what must be the same is everything else.
     */
    public static Map<String, List<Map<String, String>>> withoutKeys(Map<String, List<Map<String, String>>> snapshot) {
        Map<String, List<Map<String, String>>> out = new TreeMap<String, List<Map<String, String>>>();
        try {
            Connection c = EffectRecorder.raw().getConnection();
            try {
                for (String table : DbReset.tables(c)) {
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
