package auditkit.spring;

import auditkit.AuditConfig;
import auditkit.Outcome;
import auditkit.SutDriver;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.lang.reflect.Array;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.Iterator;
import java.util.List;
import java.util.Map;
import org.springframework.context.ConfigurableApplicationContext;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.mock.web.MockHttpServletResponse;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;
import org.springframework.test.web.servlet.request.MockMvcRequestBuilders;
import org.springframework.test.web.servlet.setup.DefaultMockMvcBuilder;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import org.springframework.web.context.WebApplicationContext;

/**
 * Calls a command the way its binding in commands.json says: an HTTP request through the
 * application's own web layer (in this thread, so everything it does is seen as part of the call),
 * or a method of a bean for a message handler or a scheduled task.
 */
public final class SpringDriver implements SutDriver {

    private final ConfigurableApplicationContext context;
    private final MockMvc mvc;
    private final ObjectMapper mapper;
    private final List<String> rejections;
    private final String codePath;

    public SpringDriver(ConfigurableApplicationContext context) {
        this.context = context;
        this.mvc = buildMvc(context);
        ObjectMapper own = null;
        try {
            own = context.getBean(ObjectMapper.class);
        } catch (RuntimeException e) {
            // the application has no mapper bean
        }
        this.mapper = own != null ? own : AuditConfig.JSON;
        this.rejections = AuditConfig.load().texts("rejections");
        String path = AuditConfig.load().text("error_code_path");
        this.codePath = path == null ? "code" : path.replaceFirst("^\\$\\.", "");
    }

    private static MockMvc buildMvc(ConfigurableApplicationContext context) {
        if (!(context instanceof WebApplicationContext)) {
            return null;
        }
        DefaultMockMvcBuilder builder = MockMvcBuilders.webAppContextSetup((WebApplicationContext) context);
        // The application's servlet filters (security among them) belong to its entry points.
        for (String name : new String[] {"jakarta.servlet.Filter", "javax.servlet.Filter"}) {
            try {
                Class<?> filterType = Class.forName(name);
                Map<String, ?> filters = context.getBeansOfType(filterType);
                if (!filters.isEmpty()) {
                    Object array = Array.newInstance(filterType, filters.size());
                    int i = 0;
                    for (Object f : filters.values()) {
                        Array.set(array, i++, f);
                    }
                    Method add = builder.getClass().getMethod("addFilters", array.getClass());
                    add.invoke(builder, array);
                }
                break;
            } catch (ClassNotFoundException e) {
                // the other servlet namespace
            } catch (ReflectiveOperationException e) {
                break;      // build without the filters rather than not at all
            }
        }
        return builder.build();
    }

    @Override
    public Outcome invoke(JsonNode command, JsonNode params) throws Exception {
        JsonNode binding = command.get("binding");
        String kind = binding.path("kind").asText();
        if ("http".equals(kind)) {
            return http(binding, params);
        }
        if ("bean".equals(kind)) {
            return bean(binding, params);
        }
        throw new IllegalArgumentException("unknown binding kind '" + kind + "' in " + command.path("id").asText());
    }

    private Outcome http(JsonNode binding, JsonNode params) throws Exception {
        if (mvc == null) {
            throw new IllegalStateException("this application has no web layer to send an HTTP request through");
        }
        String path = binding.get("path").asText();
        List<Object> pathValues = new ArrayList<Object>();
        java.util.regex.Matcher m = java.util.regex.Pattern.compile("\\{([^}/]+)\\}").matcher(path);
        while (m.find()) {
            JsonNode v = params == null ? null : params.get(m.group(1));
            if (v == null || v.isNull()) {
                throw new IllegalArgumentException("no value for {" + m.group(1) + "} of " + path);
            }
            pathValues.add(v.asText());
        }
        MockHttpServletRequestBuilder request = MockMvcRequestBuilders.request(
                HttpMethod.valueOf(binding.get("method").asText().toUpperCase()), path, pathValues.toArray());
        JsonNode headers = AuditConfig.load().harness().path("auth").path("headers");
        Iterator<Map.Entry<String, JsonNode>> hs = headers.fields();
        while (hs.hasNext()) {
            Map.Entry<String, JsonNode> h = hs.next();
            request = request.header(h.getKey(), h.getValue().asText());
        }
        if (binding.hasNonNull("query")) {
            JsonNode query = auditkit.Expr.fillLenient(binding.get("query"), params);
            Iterator<Map.Entry<String, JsonNode>> qs = query.fields();
            while (qs.hasNext()) {
                Map.Entry<String, JsonNode> q = qs.next();
                if (!q.getValue().isNull()) {
                    request = request.param(q.getKey(), q.getValue().asText());
                }
            }
        }
        if (binding.hasNonNull("body")) {
            JsonNode body = binding.get("body").isTextual() && binding.get("body").asText().equals("$")
                    ? params : auditkit.Expr.fillLenient(binding.get("body"), params);
            request = request.contentType(MediaType.APPLICATION_JSON).content(AuditConfig.JSON.writeValueAsBytes(body));
        }
        MockHttpServletResponse response;
        try {
            response = mvc.perform(request).andReturn().getResponse();
        } catch (Exception e) {
            return failure(e);
        }
        int status = response.getStatus();
        String text = response.getContentAsString();
        JsonNode json = null;
        if (text != null && !text.trim().isEmpty()) {
            try {
                json = AuditConfig.JSON.readTree(text);
            } catch (Exception e) {
                json = AuditConfig.JSON.getNodeFactory().textNode(text.length() > 2000 ? text.substring(0, 2000) : text);
            }
        }
        if (status >= 200 && status < 400) {
            return new Outcome(Outcome.ALLOWED, null, status, json, null);
        }
        String code = json != null && json.isObject() && json.path(codePath).isValueNode() && !json.path(codePath).isNull()
                ? json.path(codePath).asText() : String.valueOf(status);
        if (status < 500) {
            return new Outcome(Outcome.REJECTED, code, status, json, null);
        }
        return new Outcome(Outcome.ERROR, code, status, json, "HTTP " + status);
    }

    private Outcome bean(JsonNode binding, JsonNode params) throws Exception {
        Object target = context.getBean(binding.get("bean").asText());
        String name = binding.get("method").asText();
        JsonNode args = binding.has("args") ? binding.get("args") : AuditConfig.JSON.createArrayNode();
        Method method = null;
        for (Method candidate : org.springframework.util.ClassUtils.getUserClass(target).getMethods()) {
            if (candidate.getName().equals(name) && candidate.getParameterTypes().length == args.size()) {
                method = candidate;
                break;
            }
        }
        if (method == null) {
            throw new IllegalArgumentException("bean " + binding.get("bean").asText() + " has no method " + name
                    + " with " + args.size() + " parameters");
        }
        Object[] values = new Object[args.size()];
        for (int i = 0; i < values.length; i++) {
            JsonNode filled = auditkit.Expr.fillLenient(args.get(i), params);
            values[i] = mapper.convertValue(filled, mapper.constructType(method.getGenericParameterTypes()[i]));
        }
        try {
            Object result = method.invoke(target, values);
            return new Outcome(Outcome.ALLOWED, null, null, result == null ? null : mapper.valueToTree(result), null);
        } catch (InvocationTargetException e) {
            return failure(e.getCause() == null ? e : e.getCause());
        }
    }

    /** An exception the application listed as a business refusal is a refusal; anything else is an error. */
    private Outcome failure(Throwable thrown) {
        Throwable t = thrown;
        for (int depth = 0; t != null && depth < 10; depth++, t = t.getCause()) {
            for (String rejection : rejections) {
                if (isA(t.getClass(), rejection)) {
                    String code = t.getClass().getSimpleName();
                    try {
                        Object value = t.getClass().getMethod("getCode").invoke(t);
                        if (value != null) {
                            code = String.valueOf(value);
                        }
                    } catch (ReflectiveOperationException e) {
                        // this refusal carries no code of its own
                    }
                    return new Outcome(Outcome.REJECTED, code, null, null, null);
                }
            }
        }
        Throwable root = thrown;
        while (root.getCause() != null && root.getCause() != root) {
            root = root.getCause();
        }
        return new Outcome(Outcome.ERROR, root.getClass().getSimpleName(), null, null,
                root.getClass().getName() + ": " + root.getMessage());
    }

    private static boolean isA(Class<?> type, String name) {
        for (Class<?> c = type; c != null; c = c.getSuperclass()) {
            if (c.getName().equals(name)) {
                return true;
            }
        }
        return false;
    }
}
