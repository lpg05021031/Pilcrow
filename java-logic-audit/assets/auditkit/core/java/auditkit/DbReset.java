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
 *
 * <p>Two things for applications that do not create their own schema: the scripts harness.json lists
 * under "schema" are run once, when the database has no table yet; and the tables it lists under
 * "keep_tables" (reference data those scripts load, which no command changes) are never emptied.
 */
public final class DbReset {

    private static final String[] MIGRATION_TABLES = {"flyway_schema_history", "databasechangelog", "databasechangeloglock"};
    private static boolean schemaLooked;

    private DbReset() {
    }

    /**
     * For an application that reads its tables while it starts (settings kept in the database): creates
     * the schema and loads the base data before the application is started. Only for a database a person
     * offered through the AUDIT_DB_* variables, and only when harness.json names schema scripts; the
     * address is checked like any other before anything is sent to it.
     */
    public static void beforeStart() {
        AuditConfig config = AuditConfig.load();
        String url = System.getenv("AUDIT_DB_URL");
        if (url == null || url.trim().isEmpty() || config.texts("schema").isEmpty()) {
            return;
        }
        if (!ConnectionGuard.isAllowed(ConnectionGuard.hostOf(url.trim()))) {
            throw new IllegalStateException("AUDIT_DB_URL names a database that is neither local nor a container: refused");
        }
        try {
            Connection c = java.sql.DriverManager.getConnection(url.trim(), System.getenv("AUDIT_DB_USER"),
                    System.getenv("AUDIT_DB_PASSWORD"));
            try {
                resetOn(c, config);
            } finally {
                c.close();
            }
        } catch (SQLException e) {
            throw new IllegalStateException("could not prepare the database before the application starts", e);
        }
    }

    public static List<String> reset() {
        AuditConfig config = AuditConfig.load();
        DataSource ds = EffectRecorder.raw();
        try {
            Connection c = ds.getConnection();
            try {
                return resetOn(c, config);
            } finally {
                c.close();
            }
        } catch (SQLException e) {
            throw new IllegalStateException("database reset failed", e);
        }
    }

    private static List<String> resetOn(Connection c, AuditConfig config) throws SQLException {
        c.setAutoCommit(true);
        List<String> tables = tables(c);
        String product = c.getMetaData().getDatabaseProductName().toLowerCase(Locale.ROOT);
        Statement st = c.createStatement();
        try {
            if (!schemaLooked) {
                schemaLooked = true;
                if (tables.isEmpty()) {
                    for (String file : config.texts("schema")) {
                        runScript(st, find(config, file));
                    }
                    tables = tables(c);
                }
            }
            List<String> kept = config.texts("keep_tables");
            List<String> emptied = new ArrayList<String>();
            for (String t : tables) {
                boolean keep = false;
                for (String k : kept) {
                    keep = keep || k.equalsIgnoreCase(t);
                }
                if (!keep) {
                    emptied.add(t);
                }
            }
            tables = emptied;
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
                runScript(st, find(config, file));
            }
        } finally {
            st.close();
        }
        return tables;
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

    /** A script named relative to audit/<system>/, or to the workspace root when it is one of the project's own. */
    private static File find(AuditConfig config, String name) {
        File own = new File(config.dir(), name);
        if (own.isFile()) {
            return own;
        }
        File root = config.dir().getAbsoluteFile().getParentFile();
        root = root == null ? null : root.getParentFile();
        File inProject = root == null ? own : new File(root, name);
        return inProject.isFile() ? inProject : own;
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
