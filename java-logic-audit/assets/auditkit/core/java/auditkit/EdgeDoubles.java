package auditkit;

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
        Object mock = recording(type, kind);
        DOUBLES.put(type.getName(), mock);
        return mock;
    }

    private static Object recording(final Class<?> type, final String kind) {
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
                CALLS.add(new Call(type.getName(), kind, m.getName(), args, returnsValue));
                Map<String, Object> more = new LinkedHashMap<String, Object>();
                more.put("double", type.getName());
                more.put("args", args);
                if (returnsValue) {
                    more.put("defaulted", true);
                }
                EffectRecorder.add("messaging".equals(kind) ? "PUB" : "EXT", null, m.getName(), more);
                if (!returnsValue) {
                    return null;
                }
                if (java.util.concurrent.Future.class.isAssignableFrom(returns)) {
                    return java.util.concurrent.CompletableFuture.completedFuture(null);
                }
                // An object handed out by a double (a dispatcher, a subscription) records its calls too.
                if (returns.isInterface() && !returns.getName().startsWith("java.")) {
                    synchronized (nested) {
                        Object child = nested.get(m.getName());
                        if (child == null) {
                            child = recording(returns, kind);
                            nested.put(m.getName(), child);
                        }
                        return child;
                    }
                }
                return Mockito.RETURNS_DEFAULTS.answer(invocation);
            }
        }));
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
            return text.length() > 2000 ? text.substring(0, 2000) + "…" : text;
        }
        if (Mockito.mockingDetails(arg).isMock()) {
            return "(double)";
        }
        String text = String.valueOf(arg);
        if (text.matches("^[\\w.$]+@[0-9a-f]+$")) {
            return "(object) " + arg.getClass().getName();      // the default toString: keep the class, drop the address
        }
        return text.length() > 2000 ? text.substring(0, 2000) + "…" : text;
    }
}
