package auditkit;

import com.fasterxml.jackson.databind.JsonNode;
import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Runs the model's small read-only queries: one value, with :name parameters taken from a command's parameters. */
public final class Sql {

    private static final Pattern NAMED = Pattern.compile("(?<![:\\w]):([A-Za-z_][A-Za-z0-9_]*)");

    private Sql() {
    }

    /**
     * The first column of the first row, as text; "null" when there is no row or the value is null.
     * Booleans come back as "true" or "false" whatever the engine calls them.
     */
    public static String scalar(String sql, JsonNode params) {
        List<String> names = new ArrayList<String>();
        Matcher m = NAMED.matcher(sql);
        StringBuffer jdbc = new StringBuffer();
        while (m.find()) {
            names.add(m.group(1));
            m.appendReplacement(jdbc, "?");
        }
        m.appendTail(jdbc);
        try {
            Connection c = EffectRecorder.raw().getConnection();
            try {
                c.setReadOnly(true);
                PreparedStatement ps = c.prepareStatement(jdbc.toString());
                try {
                    for (int i = 0; i < names.size(); i++) {
                        JsonNode v = params == null ? null : params.get(names.get(i));
                        if (v == null || v.isNull()) {
                            ps.setObject(i + 1, null);
                        } else if (v.isIntegralNumber()) {
                            ps.setLong(i + 1, v.asLong());
                        } else if (v.isNumber()) {
                            ps.setBigDecimal(i + 1, v.decimalValue());
                        } else if (v.isBoolean()) {
                            ps.setBoolean(i + 1, v.asBoolean());
                        } else {
                            ps.setString(i + 1, v.asText());
                        }
                    }
                    ResultSet rs = ps.executeQuery();
                    try {
                        if (!rs.next()) {
                            return "null";
                        }
                        Object value = rs.getObject(1);
                        if (value == null) {
                            return "null";
                        }
                        if (value instanceof Boolean) {
                            return String.valueOf(value);
                        }
                        String text = Snapshot.text(value);
                        return text.equals("t") ? "true" : text.equals("f") ? "false" : text;
                    } finally {
                        rs.close();
                    }
                } finally {
                    ps.close();
                }
            } finally {
                c.setReadOnly(false);
                c.close();
            }
        } catch (SQLException e) {
            throw new IllegalStateException("query failed: " + sql + " (" + e.getMessage() + ")", e);
        }
    }
}
