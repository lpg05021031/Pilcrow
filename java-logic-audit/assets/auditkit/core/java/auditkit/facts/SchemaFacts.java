package auditkit.facts;

import auditkit.DbReset;
import auditkit.EffectRecorder;
import java.sql.Connection;
import java.sql.DatabaseMetaData;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.TreeMap;

/** The schema as the database reports it after the application's own migrations ran. */
public final class SchemaFacts {

    private SchemaFacts() {
    }

    /**
     * The isolation level the server gives a new connection. Asked of the server itself: what a driver's
     * metadata answers here is a constant of the driver (one MySQL driver says READ COMMITTED whatever
     * the server is set to), and a wrong answer would read as a difference from production.
     */
    static String serverDefault(Connection c, DatabaseMetaData meta) throws SQLException {
        String product = String.valueOf(meta.getDatabaseProductName()).toLowerCase(Locale.ROOT);
        String[] asks = product.contains("mysql") || product.contains("mariadb")
                ? new String[] {"select @@global.transaction_isolation", "select @@global.tx_isolation"}
                : product.contains("postgres") ? new String[] {"show default_transaction_isolation"} : new String[0];
        for (String ask : asks) {
            try {
                java.sql.Statement st = c.createStatement();
                try {
                    ResultSet rs = st.executeQuery(ask);
                    try {
                        if (rs.next() && rs.getString(1) != null) {
                            return rs.getString(1).trim().toUpperCase(Locale.ROOT).replace('-', '_').replace(' ', '_');
                        }
                    } finally {
                        rs.close();
                    }
                } finally {
                    st.close();
                }
            } catch (SQLException e) {
                // an older server does not know this name: try the next, then the driver's answer
            }
        }
        return isolation(meta.getDefaultTransactionIsolation());
    }

    public static Map<String, Object> collect() throws SQLException {
        Map<String, Object> out = new LinkedHashMap<String, Object>();
        Connection c = EffectRecorder.raw().getConnection();
        try {
            DatabaseMetaData meta = c.getMetaData();
            Map<String, Object> engine = new LinkedHashMap<String, Object>();
            engine.put("product", meta.getDatabaseProductName());
            engine.put("version", meta.getDatabaseProductVersion());
            engine.put("major", meta.getDatabaseMajorVersion());
            engine.put("minor", meta.getDatabaseMinorVersion());
            engine.put("default_isolation", serverDefault(c, meta));
            engine.put("connection_isolation", isolation(c.getTransactionIsolation()));
            out.put("engine", engine);

            String schema = null;
            try {
                schema = c.getSchema();
            } catch (SQLException e) {
                // unsupported
            } catch (AbstractMethodError e) {
                // unsupported
            }
            Map<String, Object> tables = new TreeMap<String, Object>();
            for (String table : DbReset.tables(c)) {
                Map<String, Object> t = new LinkedHashMap<String, Object>();
                List<Map<String, Object>> columns = new ArrayList<Map<String, Object>>();
                ResultSet rs = meta.getColumns(c.getCatalog(), schema, table, "%");
                try {
                    while (rs.next()) {
                        Map<String, Object> col = new LinkedHashMap<String, Object>();
                        col.put("name", rs.getString("COLUMN_NAME").toLowerCase(Locale.ROOT));
                        col.put("type", rs.getString("TYPE_NAME"));
                        col.put("size", rs.getInt("COLUMN_SIZE"));
                        col.put("scale", rs.getInt("DECIMAL_DIGITS"));
                        col.put("nullable", rs.getInt("NULLABLE") != DatabaseMetaData.columnNoNulls);
                        col.put("default", rs.getString("COLUMN_DEF"));
                        columns.add(col);
                    }
                } finally {
                    rs.close();
                }
                t.put("columns", columns);

                List<String> pk = new ArrayList<String>();
                rs = meta.getPrimaryKeys(c.getCatalog(), schema, table);
                try {
                    Map<Integer, String> ordered = new TreeMap<Integer, String>();
                    while (rs.next()) {
                        ordered.put((int) rs.getShort("KEY_SEQ"), rs.getString("COLUMN_NAME").toLowerCase(Locale.ROOT));
                    }
                    pk.addAll(ordered.values());
                } finally {
                    rs.close();
                }
                t.put("primary_key", pk);

                List<Map<String, Object>> fks = new ArrayList<Map<String, Object>>();
                rs = meta.getImportedKeys(c.getCatalog(), schema, table);
                try {
                    while (rs.next()) {
                        Map<String, Object> fk = new LinkedHashMap<String, Object>();
                        fk.put("column", rs.getString("FKCOLUMN_NAME").toLowerCase(Locale.ROOT));
                        fk.put("references_table", rs.getString("PKTABLE_NAME").toLowerCase(Locale.ROOT));
                        fk.put("references_column", rs.getString("PKCOLUMN_NAME").toLowerCase(Locale.ROOT));
                        fks.add(fk);
                    }
                } finally {
                    rs.close();
                }
                t.put("foreign_keys", fks);

                Map<String, List<String>> unique = new TreeMap<String, List<String>>();
                rs = meta.getIndexInfo(c.getCatalog(), schema, table, true, false);
                try {
                    while (rs.next()) {
                        String index = rs.getString("INDEX_NAME");
                        String column = rs.getString("COLUMN_NAME");
                        if (index == null || column == null) {
                            continue;
                        }
                        if (!unique.containsKey(index)) {
                            unique.put(index, new ArrayList<String>());
                        }
                        unique.get(index).add(column.toLowerCase(Locale.ROOT));
                    }
                } finally {
                    rs.close();
                }
                t.put("unique", new ArrayList<List<String>>(unique.values()));
                tables.put(table.toLowerCase(Locale.ROOT), t);
            }
            out.put("tables", tables);
        } finally {
            c.close();
        }
        return out;
    }

    static String isolation(int level) {
        switch (level) {
            case Connection.TRANSACTION_READ_UNCOMMITTED:
                return "READ_UNCOMMITTED";
            case Connection.TRANSACTION_READ_COMMITTED:
                return "READ_COMMITTED";
            case Connection.TRANSACTION_REPEATABLE_READ:
                return "REPEATABLE_READ";
            case Connection.TRANSACTION_SERIALIZABLE:
                return "SERIALIZABLE";
            default:
                return "NONE";
        }
    }
}
