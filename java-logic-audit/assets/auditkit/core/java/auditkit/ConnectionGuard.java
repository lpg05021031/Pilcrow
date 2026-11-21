package auditkit;

import java.lang.reflect.Method;
import java.sql.Connection;
import java.util.HashSet;
import java.util.Locale;
import java.util.Set;
import javax.sql.DataSource;

/**
 * Refuses to run the audit against a database that is not on this machine or in a container.
 * The audit empties tables and writes test data, so this check is code, not a convention.
 */
public final class ConnectionGuard {

    private ConnectionGuard() {
    }

    /** Throws when the data source points anywhere but a local or container address. */
    public static String check(DataSource dataSource) {
        String url = urlOf(dataSource);
        String host = hostOf(url);
        if (!isAllowed(host)) {
            throw new IllegalStateException("java-logic-audit refuses to run: the database address '" + host
                    + "' is not local or a container. The audit empties tables. If this really is a throwaway"
                    + " database, name the host in the environment variable AUDIT_ALLOW_DB_HOSTS.");
        }
        return url;
    }

    static String urlOf(DataSource dataSource) {
        for (String getter : new String[] {"getJdbcUrl", "getUrl", "getURL"}) {
            try {
                Method m = dataSource.getClass().getMethod(getter);
                Object value = m.invoke(dataSource);
                if (value != null && !value.toString().isEmpty()) {
                    return value.toString();
                }
            } catch (ReflectiveOperationException e) {
                // try the next way
            } catch (RuntimeException e) {
                // try the next way
            }
        }
        try {
            Connection c = dataSource.getConnection();
            try {
                return c.getMetaData().getURL();
            } finally {
                c.close();
            }
        } catch (Exception e) {
            throw new IllegalStateException("cannot find out where the data source points", e);
        }
    }

    /** The host of a JDBC URL; "(embedded)" for an in-process database. */
    public static String hostOf(String url) {
        if (url == null) {
            return "";
        }
        String u = url.trim();
        if (u.matches("(?i)^jdbc:(h2|hsqldb|derby):(?!.*//).*")) {
            return "(embedded)";
        }
        String rest = u.replaceFirst("(?i)^jdbc:[a-z0-9]+:(thin:)?@?", "");
        rest = rest.replaceFirst("^/+", "");
        String authority = rest.split("[/;?]", 2)[0];
        int at = authority.lastIndexOf('@');
        if (at >= 0) {
            authority = authority.substring(at + 1);
        }
        authority = authority.split(",", 2)[0];
        if (authority.startsWith("[")) {
            int end = authority.indexOf(']');
            return end < 0 ? authority : authority.substring(0, end + 1);
        }
        return authority.replaceFirst(":\\d+$", "");
    }

    public static boolean isAllowed(String host) {
        return isAllowed(host, true);
    }

    /** askContainers=false leaves out the question to Testcontainers, which needs a Docker daemon to answer. */
    public static boolean isAllowed(String host, boolean askContainers) {
        if (host == null || host.isEmpty()) {
            return false;
        }
        String h = host.toLowerCase(Locale.ROOT);
        if (h.equals("(embedded)") || isLocal(h)) {
            return true;
        }
        Set<String> extra = new HashSet<String>();
        String allow = System.getenv("AUDIT_ALLOW_DB_HOSTS");
        if (allow != null) {
            for (String item : allow.split(",")) {
                extra.add(item.trim().toLowerCase(Locale.ROOT));
            }
        }
        String override = System.getenv("TESTCONTAINERS_HOST_OVERRIDE");
        if (override != null) {
            extra.add(override.trim().toLowerCase(Locale.ROOT));
        }
        if (extra.contains(h)) {
            return true;
        }
        return askContainers && h.equals(testcontainersHost());
    }

    public static boolean isLocal(String host) {
        String h = host == null ? "" : host.toLowerCase(Locale.ROOT);
        return h.equals("localhost") || h.equals("127.0.0.1") || h.equals("::1") || h.equals("[::1]")
                || h.equals("host.docker.internal") || h.startsWith("127.");
    }

    /** The address Testcontainers publishes containers on, when Testcontainers is present and Docker answers. */
    private static String testcontainersHost() {
        try {
            Class<?> factory = Class.forName("org.testcontainers.DockerClientFactory");
            Object instance = factory.getMethod("instance").invoke(null);
            Object host = factory.getMethod("dockerHostIpAddress").invoke(instance);
            return host == null ? null : host.toString().toLowerCase(Locale.ROOT);
        } catch (Throwable e) {
            return null;
        }
    }
}
