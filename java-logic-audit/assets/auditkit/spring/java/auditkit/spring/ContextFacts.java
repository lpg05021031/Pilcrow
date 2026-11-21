package auditkit.spring;

import auditkit.EdgeDoubles;
import java.lang.annotation.Annotation;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import java.util.TreeSet;
import org.springframework.aop.framework.Advised;
import org.springframework.aop.support.AopUtils;
import org.springframework.context.ConfigurableApplicationContext;
import org.springframework.core.MethodParameter;
import org.springframework.core.annotation.AnnotatedElementUtils;
import org.springframework.core.annotation.AnnotationUtils;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.interceptor.TransactionAttribute;
import org.springframework.transaction.interceptor.TransactionAttributeSource;
import org.springframework.util.ClassUtils;
import org.springframework.web.method.HandlerMethod;
import org.springframework.web.servlet.mvc.method.RequestMappingInfo;
import org.springframework.web.servlet.mvc.method.annotation.RequestMappingHandlerMapping;

/**
 * What the running application context says about itself: the HTTP endpoints it registered, the
 * methods that are scheduled or listen for messages, the transaction settings that really apply
 * to each method, and which beans are wrapped by which advice.
 */
public final class ContextFacts {

    private static final String[] LISTENER_ANNOTATIONS = {
        "KafkaListener", "RabbitListener", "JmsListener", "EventListener", "TransactionalEventListener",
        "SqsListener", "StreamListener", "NatsListener"
    };

    private ContextFacts() {
    }

    public static Map<String, Object> collect(ConfigurableApplicationContext context, String basePackage) {
        Map<String, Object> out = new LinkedHashMap<String, Object>();
        out.put("base_package", basePackage);
        out.put("http", http(context));

        List<Map<String, Object>> scheduled = new ArrayList<Map<String, Object>>();
        List<Map<String, Object>> listeners = new ArrayList<Map<String, Object>>();
        List<Map<String, Object>> transactions = new ArrayList<Map<String, Object>>();
        Map<String, Object> beans = new TreeMap<String, Object>();
        Map<String, Object> advised = new TreeMap<String, Object>();

        TransactionAttributeSource txSource = null;
        try {
            txSource = context.getBean(TransactionAttributeSource.class);
        } catch (RuntimeException e) {
            // no transaction management in this context
        }

        String[] names = context.getBeanDefinitionNames();
        Arrays.sort(names);
        for (String name : names) {
            Class<?> type;
            try {
                type = context.getType(name);
            } catch (RuntimeException e) {
                continue;
            }
            if (type == null) {
                continue;
            }
            Class<?> user = ClassUtils.getUserClass(type);
            if (!user.getName().startsWith(basePackage + ".") || user.getName().startsWith("auditkit.")) {
                continue;
            }
            Map<String, Object> bean = new LinkedHashMap<String, Object>();
            bean.put("class", user.getName());
            TreeSet<String> stereotypes = new TreeSet<String>();
            for (Annotation a : user.getAnnotations()) {
                stereotypes.add(a.annotationType().getName());
            }
            bean.put("annotations", new ArrayList<String>(stereotypes));
            beans.put(name, bean);

            for (Method m : user.getDeclaredMethods()) {
                if (m.isSynthetic() || m.isBridge()) {
                    continue;
                }
                String id = user.getName() + "#" + m.getName();
                Scheduled s = AnnotatedElementUtils.findMergedAnnotation(m, Scheduled.class);
                if (s != null) {
                    Map<String, Object> row = new LinkedHashMap<String, Object>();
                    row.put("bean", name);
                    row.put("method", id);
                    row.put("cron", s.cron());
                    row.put("fixed_delay", s.fixedDelayString().isEmpty() ? String.valueOf(s.fixedDelay()) : s.fixedDelayString());
                    row.put("fixed_rate", s.fixedRateString().isEmpty() ? String.valueOf(s.fixedRate()) : s.fixedRateString());
                    scheduled.add(row);
                }
                for (Annotation a : m.getAnnotations()) {
                    String simple = a.annotationType().getSimpleName();
                    if (Arrays.asList(LISTENER_ANNOTATIONS).contains(simple)) {
                        Map<String, Object> row = new LinkedHashMap<String, Object>();
                        row.put("bean", name);
                        row.put("method", id);
                        row.put("annotation", a.annotationType().getName());
                        row.put("attributes", attributes(a));
                        row.put("parameters", parameterTypes(m));
                        listeners.add(row);
                    }
                }
                if (txSource != null && Modifier.isPublic(m.getModifiers())) {
                    TransactionAttribute attr = txSource.getTransactionAttribute(m, user);
                    if (attr != null) {
                        Map<String, Object> row = new LinkedHashMap<String, Object>();
                        row.put("method", id);
                        row.put("propagation", propagation(attr.getPropagationBehavior()));
                        row.put("isolation", isolation(attr.getIsolationLevel()));
                        row.put("read_only", attr.isReadOnly());
                        row.put("timeout", attr.getTimeout());
                        row.put("rolls_back_on_checked_exception", attr.rollbackOn(new Exception()));
                        transactions.add(row);
                    }
                }
            }

            // Which advice wraps this bean. Only beans that already exist are looked at: asking for a
            // lazy bean here would create it.
            if (context.getBeanFactory().containsSingleton(name)) {
                Object instance = context.getBeanFactory().getSingleton(name);
                if (instance != null && AopUtils.isAopProxy(instance) && instance instanceof Advised) {
                    TreeSet<String> advice = new TreeSet<String>();
                    for (org.springframework.aop.Advisor advisor : ((Advised) instance).getAdvisors()) {
                        advice.add(advisor.getAdvice().getClass().getName());
                    }
                    advised.put(name, new ArrayList<String>(advice));
                }
            }
        }
        out.put("scheduled", scheduled);
        out.put("listeners", listeners);
        out.put("transactions", transactions);
        out.put("beans", beans);
        out.put("advised", advised);
        out.put("message_handlers", messageHandlers(context, basePackage));
        out.put("double_calls_at_startup", doubleCalls());
        out.put("clock_bean", context.getBeanNamesForType(java.time.Clock.class).length > 0);
        return out;
    }

    private static List<Map<String, Object>> http(ConfigurableApplicationContext context) {
        List<Map<String, Object>> out = new ArrayList<Map<String, Object>>();
        for (RequestMappingHandlerMapping mapping : context.getBeansOfType(RequestMappingHandlerMapping.class).values()) {
            for (Map.Entry<RequestMappingInfo, HandlerMethod> e : mapping.getHandlerMethods().entrySet()) {
                HandlerMethod handler = e.getValue();
                Class<?> owner = ClassUtils.getUserClass(handler.getBeanType());
                Map<String, Object> row = new LinkedHashMap<String, Object>();
                TreeSet<String> methods = new TreeSet<String>();
                for (Object m : e.getKey().getMethodsCondition().getMethods()) {
                    methods.add(String.valueOf(m));
                }
                row.put("methods", new ArrayList<String>(methods));
                row.put("patterns", new ArrayList<String>(new TreeSet<String>(e.getKey().getPatternValues())));
                row.put("handler", owner.getName() + "#" + handler.getMethod().getName());
                List<Map<String, Object>> params = new ArrayList<Map<String, Object>>();
                for (MethodParameter p : handler.getMethodParameters()) {
                    Map<String, Object> param = new LinkedHashMap<String, Object>();
                    param.put("type", p.getParameterType().getName());
                    param.put("name", p.getParameter().isNamePresent() ? p.getParameter().getName() : null);
                    List<String> annotations = new ArrayList<String>();
                    for (Annotation a : p.getParameterAnnotations()) {
                        annotations.add(a.annotationType().getSimpleName());
                    }
                    param.put("annotations", annotations);
                    params.add(param);
                }
                row.put("parameters", params);
                row.put("returns", handler.getMethod().getReturnType().getName());
                out.add(row);
            }
        }
        java.util.Collections.sort(out, new java.util.Comparator<Map<String, Object>>() {
            @Override
            public int compare(Map<String, Object> a, Map<String, Object> b) {
                return (a.get("patterns") + " " + a.get("methods")).compareTo(b.get("patterns") + " " + b.get("methods"));
            }
        });
        return out;
    }

    /** Beans that handle messages by implementing a client library's handler interface (for example NATS). */
    private static List<Map<String, Object>> messageHandlers(ConfigurableApplicationContext context, String basePackage) {
        List<Map<String, Object>> out = new ArrayList<Map<String, Object>>();
        for (String iface : new String[] {"io.nats.client.MessageHandler", "org.springframework.amqp.core.MessageListener",
            "org.springframework.kafka.listener.MessageListener", "javax.jms.MessageListener", "jakarta.jms.MessageListener"}) {
            Class<?> type;
            try {
                type = Class.forName(iface);
            } catch (ClassNotFoundException e) {
                continue;
            }
            String[] names = context.getBeanNamesForType(type, true, false);
            Arrays.sort(names);
            for (String name : names) {
                Class<?> beanType = context.getType(name);
                if (beanType == null) {
                    continue;
                }
                Class<?> user = ClassUtils.getUserClass(beanType);
                if (!user.getName().startsWith(basePackage + ".")) {
                    continue;
                }
                Map<String, Object> row = new LinkedHashMap<String, Object>();
                row.put("bean", name);
                row.put("class", user.getName());
                row.put("interface", iface);
                List<String> publicMethods = new ArrayList<String>();
                for (Method m : user.getDeclaredMethods()) {
                    if (Modifier.isPublic(m.getModifiers()) && !m.isSynthetic()) {
                        publicMethods.add(m.getName() + parameterTypes(m));
                    }
                }
                java.util.Collections.sort(publicMethods);
                row.put("public_methods", publicMethods);
                out.add(row);
            }
        }
        return out;
    }

    /** What the application did with the doubles while starting: this is where subscriptions show up. */
    private static List<Map<String, Object>> doubleCalls() {
        List<Map<String, Object>> out = new ArrayList<Map<String, Object>>();
        for (EdgeDoubles.Call call : EdgeDoubles.calls()) {
            Map<String, Object> row = new LinkedHashMap<String, Object>();
            row.put("type", call.type);
            row.put("kind", call.kind);
            row.put("method", call.method);
            row.put("args", call.args);
            out.add(row);
        }
        return out;
    }

    private static Map<String, Object> attributes(Annotation a) {
        Map<String, Object> out = new TreeMap<String, Object>();
        for (Map.Entry<String, Object> e : AnnotationUtils.getAnnotationAttributes(a).entrySet()) {
            Object v = e.getValue();
            if (v instanceof Object[]) {
                List<String> items = new ArrayList<String>();
                for (Object item : (Object[]) v) {
                    items.add(item instanceof Class ? ((Class<?>) item).getName() : String.valueOf(item));
                }
                if (!items.isEmpty()) {
                    out.put(e.getKey(), items);
                }
            } else if (v instanceof Class) {
                out.put(e.getKey(), ((Class<?>) v).getName());
            } else if (v != null && !String.valueOf(v).isEmpty()) {
                out.put(e.getKey(), String.valueOf(v));
            }
        }
        return out;
    }

    private static List<String> parameterTypes(Method m) {
        List<String> out = new ArrayList<String>();
        for (Class<?> p : m.getParameterTypes()) {
            out.add(p.getName());
        }
        return out;
    }

    private static String propagation(int value) {
        switch (value) {
            case TransactionDefinition.PROPAGATION_REQUIRED:
                return "REQUIRED";
            case TransactionDefinition.PROPAGATION_SUPPORTS:
                return "SUPPORTS";
            case TransactionDefinition.PROPAGATION_MANDATORY:
                return "MANDATORY";
            case TransactionDefinition.PROPAGATION_REQUIRES_NEW:
                return "REQUIRES_NEW";
            case TransactionDefinition.PROPAGATION_NOT_SUPPORTED:
                return "NOT_SUPPORTED";
            case TransactionDefinition.PROPAGATION_NEVER:
                return "NEVER";
            case TransactionDefinition.PROPAGATION_NESTED:
                return "NESTED";
            default:
                return String.valueOf(value);
        }
    }

    private static String isolation(int value) {
        switch (value) {
            case TransactionDefinition.ISOLATION_DEFAULT:
                return "DEFAULT";
            case TransactionDefinition.ISOLATION_READ_UNCOMMITTED:
                return "READ_UNCOMMITTED";
            case TransactionDefinition.ISOLATION_READ_COMMITTED:
                return "READ_COMMITTED";
            case TransactionDefinition.ISOLATION_REPEATABLE_READ:
                return "REPEATABLE_READ";
            case TransactionDefinition.ISOLATION_SERIALIZABLE:
                return "SERIALIZABLE";
            default:
                return String.valueOf(value);
        }
    }
}
