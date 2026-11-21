package auditkit;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.mockito.Mockito;
import org.mockito.invocation.InvocationOnMock;
import org.mockito.stubbing.Answer;

/**
 * Stand-ins for the systems outside the application (a message broker connection, an external
 * service client). Every call made on one is recorded as an effect, and a call can be told to fail.
 *
 * <p>A call answers nothing (null, zero, an empty collection) unless harness.json gives the method an
 * answer under the double's "answers": a value the method's return type is read from, or an exception
 * to throw. An answer may use the call's arguments ("${args[0].orderNo}"), so that a case decides what
 * the outside system says by what it sends.
 */
public final class EdgeDoubles {

    /** One recorded call on a double. */
    public static final class Call {
        public final String type;
        public final String kind;
        public final String method;
        public final List<String> args;
        public final boolean defaulted;

        Call(String type, String kind, String method, List<String> args, boolean defaulted) {
            this.type = type;
            this.kind = kind;
            this.method = method;
            this.args = args;
            this.defaulted = defaulted;
        }
    }

    /** Decides whether a call on a double should fail. Set by the fault runner; null means never. */
    public interface FaultHook {
        RuntimeException before(String type, String method);
    }

    private static final List<Call> CALLS = Collections.synchronizedList(new ArrayList<Call>());
    private static final Map<String, Object> DOUBLES = Collections.synchronizedMap(new LinkedHashMap<String, Object>());
    private static volatile FaultHook faultHook;

    private EdgeDoubles() {
    }

    /** Creates the double for a type. kind is "messaging" or "external". */
    public static Object create(final Class<?> type, final String kind) {
        Object existing = DOUBLES.get(type.getName());
        if (existing != null) {
            return existing;
        }
        Object mock = recording(type, kind, type.getName(), "");
        DOUBLES.put(type.getName(), mock);
        return mock;
    }

    /**
     * A recording stand-in for a type. root and prefix say where it hangs: an object a double hands out
     * (a sub-service) is a stand-in too, and its methods are answered under the double it came from as
     * "getSubService.method".
     */
    private static Object recording(final Class<?> type, final String kind, final String root, final String prefix) {
        final Map<String, Object> nested = new LinkedHashMap<String, Object>();
        return Mockito.mock(type, Mockito.withSettings().stubOnly().defaultAnswer(new Answer<Object>() {
            @Override
            public Object answer(InvocationOnMock invocation) throws Throwable {
                Method m = invocation.getMethod();
                if (m.getDeclaringClass() == Object.class) {
                    return Mockito.RETURNS_DEFAULTS.answer(invocation);
                }
                FaultHook hook = faultHook;
                if (hook != null) {
                    RuntimeException failure = hook.before(type.getName(), m.getName());
                    if (failure != null) {
                        throw failure;
                    }
                }
                Faults.at("messaging".equals(kind) ? Faults.PUB : Faults.EXT, type.getSimpleName() + "." + m.getName());
                Class<?> returns = m.getReturnType();
                boolean returnsValue = returns != void.class;
                List<String> args = new ArrayList<String>();
                for (Object arg : invocation.getArguments()) {
                    args.add(render(arg));
                }
                JsonNode configured = answerFor(root, prefix + m.getName());
                Object answered = null;
                Throwable thrown = null;
                String unreadable = null;
                if (configured != null) {
                    try {
                        if (configured.hasNonNull("throws")) {
                            thrown = failure(configured.get("throws").asText(), configured.path("message").asText("refused by the audit's stand-in"));
                        } else if (returnsValue) {
                            answered = read(configured, invocation.getArguments(), m);
                        }
                    } catch (Exception e) {
                        unreadable = String.valueOf(e.getMessage());
                        configured = null;          // falls back to answering nothing, and says so
                    }
                }
                boolean defaulted = returnsValue && configured == null;
                CALLS.add(new Call(type.getName(), kind, m.getName(), args, defaulted));
                Map<String, Object> more = new LinkedHashMap<String, Object>();
                more.put("double", type.getName());
                more.put("args", args);
                if (defaulted) {
                    more.put("defaulted", true);
                }
                if (unreadable != null) {
                    more.put("answer_unreadable", unreadable);
                }
                if (thrown != null) {
                    more.put("answered", "throws " + thrown.getClass().getName());
                }
                EffectRecorder.add("messaging".equals(kind) ? "PUB" : "EXT", null, m.getName(), more);
                if (thrown != null) {
                    throw thrown;
                }
                if (!returnsValue) {
                    return null;
                }
                if (configured != null) {
                    return answered;
                }
                if (java.util.concurrent.Future.class.isAssignableFrom(returns)) {
                    return java.util.concurrent.CompletableFuture.completedFuture(null);
                }
                // An object handed out by a double (a dispatcher, a subscription) records its calls too.
                if (returns.isInterface() && !returns.getName().startsWith("java.")) {
                    synchronized (nested) {
                        Object child = nested.get(m.getName());
                        if (child == null) {
                            child = recording(returns, kind, root, prefix + m.getName() + ".");
                            nested.put(m.getName(), child);
                        }
                        return child;
                    }
                }
                return Mockito.RETURNS_DEFAULTS.answer(invocation);
            }
        }));
    }

    /** The answer harness.json gives a method of a double, or null when it gives none. */
    static JsonNode answerFor(String typeName, String method) {
        for (JsonNode d : AuditConfig.load().nodes("doubles")) {
            if (typeName.equals(d.path("type").asText())) {
                for (JsonNode a : d.path("answers")) {
                    if (method.equals(a.path("method").asText())) {
                        return a;
                    }
                }
            }
        }
        return null;
    }

    /**
     * The configured value, filled from the call's arguments and read as the method's return type, or as
     * the class the answer names under "as" when the method's own return type does not say (a generic one).
     */
    static Object read(JsonNode answer, Object[] arguments, Method m) throws ClassNotFoundException {
        JsonNode returns = answer.get("returns");
        com.fasterxml.jackson.databind.JavaType target = answer.hasNonNull("as")
                ? answers().getTypeFactory().constructType(Class.forName(answer.get("as").asText()))
                : answers().getTypeFactory().constructType(m.getGenericReturnType());
        if (returns == null || returns.isNull()) {
            return answers().convertValue(AuditConfig.JSON.getNodeFactory().nullNode(), target);
        }
        if (!answer.hasNonNull("as") && returns.isContainerNode()
                && (m.getGenericReturnType() instanceof java.lang.reflect.TypeVariable || m.getReturnType() == Object.class)) {
            throw new IllegalArgumentException("the method's return type is generic, so the answer must name the class the "
                    + "application reads the result as: \"as\": \"<class name>\"");
        }
        ObjectNode context = AuditConfig.JSON.createObjectNode();
        ArrayNode args = context.putArray("args");
        for (Object arg : arguments == null ? new Object[0] : arguments) {
            args.add(asJson(arg));
        }
        JsonNode filled = Expr.fillLenient(returns, context);
        return answers().convertValue(filled, target);
    }

    private static com.fasterxml.jackson.databind.ObjectMapper answerMapper;

    /** Reads answers into the application's types: with the date and time support the class path offers. */
    private static synchronized com.fasterxml.jackson.databind.ObjectMapper answers() {
        if (answerMapper == null) {
            answerMapper = new com.fasterxml.jackson.databind.ObjectMapper().findAndRegisterModules();
        }
        return answerMapper;
    }

    /**
     * An argument as the templates see it: text that is JSON is read as JSON, text that is XML as an
     * object with one property for each element under the root (by the element's name), and an object
     * by its properties.
     */
    static JsonNode asJson(Object arg) {
        if (arg == null) {
            return AuditConfig.JSON.getNodeFactory().nullNode();
        }
        if (arg instanceof byte[]) {
            arg = new String((byte[]) arg, StandardCharsets.UTF_8);
        }
        if (arg instanceof CharSequence) {
            String text = arg.toString().trim();
            if (text.startsWith("{") || text.startsWith("[")) {
                try {
                    return AuditConfig.JSON.readTree(text);
                } catch (Exception e) {
                    // not JSON after all
                }
            }
            if (text.startsWith("<")) {
                try {
                    return fromXml(text);
                } catch (Exception e) {
                    // not XML after all
                }
            }
            return AuditConfig.JSON.getNodeFactory().textNode(arg.toString());
        }
        try {
            return AuditConfig.JSON.valueToTree(arg);
        } catch (RuntimeException e) {
            return AuditConfig.JSON.getNodeFactory().textNode(String.valueOf(arg));
        }
    }

    /** The elements under an XML document's root as an object; nothing outside the text itself is ever fetched. */
    static JsonNode fromXml(String text) throws Exception {
        javax.xml.parsers.DocumentBuilderFactory f = javax.xml.parsers.DocumentBuilderFactory.newInstance();
        f.setFeature("http://apache.org/xml/features/disallow-doctype-decl", true);
        f.setXIncludeAware(false);
        f.setExpandEntityReferences(false);
        org.w3c.dom.Document doc = f.newDocumentBuilder().parse(new org.xml.sax.InputSource(new java.io.StringReader(text)));
        return element(doc.getDocumentElement());
    }

    private static JsonNode element(org.w3c.dom.Element e) {
        ObjectNode out = AuditConfig.JSON.createObjectNode();
        org.w3c.dom.NodeList children = e.getChildNodes();
        boolean any = false;
        for (int i = 0; i < children.getLength(); i++) {
            if (children.item(i) instanceof org.w3c.dom.Element) {
                org.w3c.dom.Element child = (org.w3c.dom.Element) children.item(i);
                out.set(child.getTagName(), element(child));
                any = true;
            }
        }
        return any ? out : AuditConfig.JSON.getNodeFactory().textNode(e.getTextContent().trim());
    }

    private static Throwable failure(String className, String message) throws Exception {
        Class<?> type = Class.forName(className);
        if (!Throwable.class.isAssignableFrom(type)) {
            throw new IllegalArgumentException(className + " is not an exception");
        }
        try {
            return (Throwable) type.getConstructor(String.class).newInstance(message);
        } catch (NoSuchMethodException e) {
            return (Throwable) type.getConstructor().newInstance();
        }
    }

    /**
     * What is wrong with the answers harness.json gives a double, or an empty list: a method the type
     * does not have, a value its return type cannot be read from, an exception class that does not exist.
     */
    public static List<String> answerProblems(Class<?> type, JsonNode doubleNode) {
        List<String> problems = new ArrayList<String>();
        for (JsonNode a : doubleNode.path("answers")) {
            String name = a.path("method").asText("");
            String[] path = name.split("\\.");
            Class<?> owner = type;                       // "getSubService.method": through the objects the double hands out
            for (int i = 0; i < path.length - 1 && owner != null; i++) {
                List<Method> step = methodsNamed(owner, path[i]);
                owner = step.isEmpty() ? null : step.get(0).getReturnType();
            }
            List<Method> named = owner == null ? new ArrayList<Method>() : methodsNamed(owner, path[path.length - 1]);
            if (named.isEmpty()) {
                problems.add(type.getName() + " has no method " + name);
                continue;
            }
            try {
                if (a.hasNonNull("throws")) {
                    failure(a.get("throws").asText(), "check");
                } else {
                    for (Method m : named) {
                        if (m.getReturnType() != void.class) {
                            read(a, new Object[m.getParameterTypes().length], m);
                        }
                    }
                }
            } catch (Exception e) {
                problems.add("the answer for " + type.getSimpleName() + "." + name + " cannot be used: " + e.getMessage());
            }
        }
        return problems;
    }

    private static List<Method> methodsNamed(Class<?> type, String name) {
        List<Method> named = new ArrayList<Method>();
        for (Method m : type.getMethods()) {
            if (m.getName().equals(name)) {
                named.add(m);
            }
        }
        return named;
    }

    public static Map<String, Object> all() {
        synchronized (DOUBLES) {
            return new LinkedHashMap<String, Object>(DOUBLES);
        }
    }

    public static List<Call> calls() {
        synchronized (CALLS) {
            return new ArrayList<Call>(CALLS);
        }
    }

    public static void clearCalls() {
        CALLS.clear();
    }

    public static void setFaultHook(FaultHook hook) {
        faultHook = hook;
    }

    /** A double sits at the edge when its type is not one of the application's own classes. */
    public static boolean isEdge(String typeName, String basePackage) {
        return basePackage == null || basePackage.isEmpty() || !typeName.startsWith(basePackage + ".");
    }

    /**
     * Shows that calls on a double are recorded, by calling one getter (a method without parameters
     * whose name starts with get or is). Returns the method's name, or null when the type has none.
     * The probe's own call is removed from the record again.
     */
    public static String probe(Object mock, Class<?> type) throws Exception {
        for (Method m : type.getMethods()) {
            boolean getter = m.getName().startsWith("get") || m.getName().startsWith("is");
            if (getter && m.getParameterTypes().length == 0 && Modifier.isPublic(m.getModifiers())
                    && m.getDeclaringClass() != Object.class && !Modifier.isStatic(m.getModifiers())) {
                int before = CALLS.size();
                m.invoke(mock);
                synchronized (CALLS) {
                    boolean recorded = CALLS.size() == before + 1;
                    while (CALLS.size() > before) {
                        CALLS.remove(CALLS.size() - 1);
                    }
                    return recorded ? m.getName() : null;
                }
            }
        }
        return null;
    }

    static String render(Object arg) {
        if (arg == null) {
            return "null";
        }
        if (arg instanceof byte[]) {
            byte[] bytes = (byte[]) arg;
            String text = new String(bytes, StandardCharsets.UTF_8);
            return text.length() > 2000 ? text.substring(0, 2000) + "..." : text;
        }
        if (Mockito.mockingDetails(arg).isMock()) {
            return "(double)";
        }
        String text = String.valueOf(arg);
        if (text.matches("^[\\w.$]+@[0-9a-f]+$")) {
            return "(object) " + arg.getClass().getName();      // the default toString: keep the class, drop the address
        }
        return text.length() > 2000 ? text.substring(0, 2000) + "..." : text;
    }
}
