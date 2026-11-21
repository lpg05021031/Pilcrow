package auditkit;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.sql.Connection;
import java.sql.DatabaseMetaData;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import javax.sql.DataSource;

/**
 * Puts the database back to the same starting point before every case: all tables emptied,
 * generated keys restarted, then the base data loaded. Real commits are used throughout (never a
 * rolled-back transaction), because effect traces and fault injection need real commits.
 */
public final class DbReset {

    private static final String[] MIGRATION_TABLES = {"flyway_schema_history", "databasechangelog", "databasechangeloglock"};

    private DbReset() {
    }

    public static List<String> reset() {
        AuditConfig config = AuditConfig.load();
        DataSource ds = EffectRecorder.raw();
        try {
            Connection c = ds.getConnection();
            try {
                c.setAutoCommit(true);
                List<String> tables = tables(c);
                String product = c.getMetaData().getDatabaseProductName().toLowerCase(Locale.ROOT);
                Statement st = c.createStatement();
                try {
                    if (tables.isEmpty()) {
                        // nothing to empty
                    } else if (product.contains("postgres")) {
                        st.execute("TRUNCATE TABLE " + join(tables, "\"") + " RESTART IDENTITY CASCADE");
                    } else if (product.contains("mysql") || product.contains("mariadb")) {
                        st.execute("SET FOREIGN_KEY_CHECKS = 0");
                        for (String t : tables) {
                            st.execute("TRUNCATE TABLE `" + t + "`");
                        }
                        st.execute("SET FOREIGN_KEY_CHECKS = 1");
                    } else {
                        throw new IllegalStateException("no reset strategy for database engine: " + product);
                    }
                    for (String file : config.texts("base_data")) {
                        runScript(st, new File(config.dir(), file));
                    }
                } finally {
                    st.close();
                }
                return tables;
            } finally {
                c.close();
            }
        } catch (SQLException e) {
            throw new IllegalStateException("database reset failed", e);
        }
    }

    /** The application's own tables in the connection's schema, without the migration tool's bookkeeping. */
    public static List<String> tables(Connection c) throws SQLException {
        List<String> out = new ArrayList<String>();
        DatabaseMetaData meta = c.getMetaData();
        String schema = null;
        try {
            schema = c.getSchema();
        } catch (SQLException e) {
            // some drivers do not support it
        } catch (AbstractMethodError e) {
            // very old drivers
        }
        ResultSet rs = meta.getTables(c.getCatalog(), schema, "%", new String[] {"TABLE"});
        try {
            while (rs.next()) {
                String name = rs.getString("TABLE_NAME");
                boolean skip = false;
                for (String m : MIGRATION_TABLES) {
                    if (m.equalsIgnoreCase(name)) {
                        skip = true;
                    }
                }
                if (!skip) {
                    out.add(name);
                }
            }
        } finally {
            rs.close();
        }
        java.util.Collections.sort(out);
        return out;
    }

    private static void runScript(Statement st, File file) throws SQLException {
        String sql;
        try {
            sql = new String(Files.readAllBytes(file.toPath()), StandardCharsets.UTF_8);
        } catch (java.io.IOException e) {
            throw new IllegalStateException("cannot read base data: " + file, e);
        }
        for (String part : sql.split(";\\s*(\\r?\\n|$)")) {
            String statement = part.replaceAll("(?m)^\\s*--.*$", "").trim();
            if (!statement.isEmpty()) {
                st.execute(statement);
            }
        }
    }

    private static String join(List<String> names, String quote) {
        StringBuilder sb = new StringBuilder();
        for (String n : names) {
            if (sb.length() > 0) {
                sb.append(", ");
            }
            sb.append(quote).append(n).append(quote);
        }
        return sb.toString();
    }
}
