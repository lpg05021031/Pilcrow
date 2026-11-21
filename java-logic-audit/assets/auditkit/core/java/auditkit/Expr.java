package auditkit;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.time.LocalDate;
import java.util.Iterator;
import java.util.Map;

/**
 * Fills the templates in the model: a JSON value in which a string may hold ${...}. Inside the
 * braces: a path into what earlier steps returned (order.lines[0].id), whole numbers, + and -,
 * and the word today (today+10 is a date ten days from the audit's "today").
 *
 * <p>A string that is exactly one ${...} takes the type of its value (a number stays a number);
 * otherwise the values are written into the text.
 */
public final class Expr {

    private final String text;
    private final JsonNode context;
    private final LocalDate today;
    private final boolean lenient;
    private int pos;

    private Expr(String text, JsonNode context, LocalDate today, boolean lenient) {
        this.text = text;
        this.context = context;
        this.today = today;
        this.lenient = lenient;
    }

    /** Returns a copy of the template with every ${...} replaced. A path that leads nowhere is an error. */
    public static JsonNode fill(JsonNode template, JsonNode context, LocalDate today) {
        return fill(template, context, today, false);
    }

    /**
     * As fill, but a path that leads nowhere gives null. Used for a command's binding, where a
     * parameter the cell deliberately leaves out must reach the application as a missing value.
     */
    public static JsonNode fillLenient(JsonNode template, JsonNode context) {
        return fill(template, context, null, true);
    }

    private static JsonNode fill(JsonNode template, JsonNode context, LocalDate today, boolean lenient) {
        if (template == null || template.isNull()) {
            return template;
        }
        if (template.isTextual()) {
            return fillText(template.asText(), context, today, lenient);
        }
        if (template.isObject()) {
            ObjectNode out = AuditConfig.JSON.createObjectNode();
            Iterator<Map.Entry<String, JsonNode>> it = template.fields();
            while (it.hasNext()) {
                Map.Entry<String, JsonNode> e = it.next();
                out.set(e.getKey(), fill(e.getValue(), context, today, lenient));
            }
            return out;
        }
        if (template.isArray()) {
            ArrayNode out = AuditConfig.JSON.createArrayNode();
            for (JsonNode item : template) {
                out.add(fill(item, context, today, lenient));
            }
            return out;
        }
        return template;
    }

    private static JsonNode fillText(String s, JsonNode context, LocalDate today, boolean lenient) {
        if (s.startsWith("${") && s.endsWith("}") && s.indexOf("${", 2) < 0) {
            return toNode(new Expr(s.substring(2, s.length() - 1), context, today, lenient).evaluate());
        }
        StringBuilder out = new StringBuilder();
        int at = 0;
        while (true) {
            int open = s.indexOf("${", at);
            if (open < 0) {
                out.append(s.substring(at));
                break;
            }
            int close = s.indexOf('}', open);
            if (close < 0) {
                throw new IllegalArgumentException("unclosed ${ in: " + s);
            }
            out.append(s, at, open);
            Object value = new Expr(s.substring(open + 2, close), context, today, lenient).evaluate();
            out.append(value instanceof JsonNode && ((JsonNode) value).isValueNode() ? ((JsonNode) value).asText() : String.valueOf(value));
            at = close + 1;
        }
        return AuditConfig.JSON.getNodeFactory().textNode(out.toString());
    }

    private static JsonNode toNode(Object value) {
        if (value instanceof JsonNode) {
            return (JsonNode) value;
        }
        if (value instanceof Long) {
            return AuditConfig.JSON.getNodeFactory().numberNode((Long) value);
        }
        return AuditConfig.JSON.getNodeFactory().textNode(String.valueOf(value));
    }

    private Object evaluate() {
        Object value = term();
        skipSpaces();
        while (pos < text.length() && (text.charAt(pos) == '+' || text.charAt(pos) == '-')) {
            char op = text.charAt(pos++);
            Object right = term();
            long amount = number(right);
            if (value instanceof LocalDate) {
                value = op == '+' ? ((LocalDate) value).plusDays(amount) : ((LocalDate) value).minusDays(amount);
            } else {
                value = op == '+' ? number(value) + amount : number(value) - amount;
            }
            skipSpaces();
        }
        if (pos != text.length()) {
            throw new IllegalArgumentException("cannot read '" + text + "' at position " + pos);
        }
        return value;
    }

    private Object term() {
        skipSpaces();
        int start = pos;
        if (pos < text.length() && Character.isDigit(text.charAt(pos))) {
            while (pos < text.length() && Character.isDigit(text.charAt(pos))) {
                pos++;
            }
            return Long.parseLong(text.substring(start, pos));
        }
        while (pos < text.length() && (Character.isLetterOrDigit(text.charAt(pos)) || "_.[]".indexOf(text.charAt(pos)) >= 0)) {
            pos++;
        }
        String path = text.substring(start, pos);
        if (path.isEmpty()) {
            throw new IllegalArgumentException("cannot read '" + text + "' at position " + pos);
        }
        if (path.equals("today")) {
            return today;
        }
        JsonNode node = context;
        for (String part : path.replace("[", ".").replace("]", "").split("\\.")) {
            if (part.isEmpty()) {
                continue;
            }
            node = node == null ? null : (part.matches("\\d+") ? node.get(Integer.parseInt(part)) : node.get(part));
        }
        if (node == null || node.isMissingNode()) {
            if (lenient) {
                return AuditConfig.JSON.getNodeFactory().nullNode();
            }
            throw new IllegalArgumentException("nothing at '" + path + "' in what the earlier steps returned");
        }
        return node;
    }

    private static long number(Object value) {
        if (value instanceof Long) {
            return (Long) value;
        }
        if (value instanceof JsonNode && ((JsonNode) value).isNumber()) {
            return ((JsonNode) value).asLong();
        }
        throw new IllegalArgumentException("not a whole number: " + value);
    }

    private void skipSpaces() {
        while (pos < text.length() && text.charAt(pos) == ' ') {
            pos++;
        }
    }
}
