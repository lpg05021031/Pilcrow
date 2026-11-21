package auditkit.spring;

import auditkit.AuditConfig;
import auditkit.Outcome;
import auditkit.SutDriver;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.lang.reflect.Array;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.Iterator;
import java.util.LinkedHashMap;
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
    private final String resultPath;                    // JSON pointer to the code in the body, or null
    private final List<String> resultAllowed;
    private final List<String> resultError;
    private final List<JsonNode> sessions;
    private final Map<String, JsonNode> signedIn = new LinkedHashMap<String, JsonNode>();

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
        JsonNode result = AuditConfig.load().harness().path("result_code");
        this.resultPath = result.hasNonNull("path")
                ? "/" + result.get("path").asText().replaceFirst("^\\$\\.", "").replace('.', '/') : null;
        this.resultAllowed = texts(result.path("allowed"));
        this.resultError = texts(result.path("error"));
        this.sessions = new ArrayList<JsonNode>();
        for (JsonNode session : AuditConfig.load().harness().path("auth").path("sessions")) {
            this.sessions.add(session);
        }
    }

    private static List<String> texts(JsonNode array) {
        List<String> out = new ArrayList<String>();
        for (JsonNode x : array) {
            out.add(x.asText());
        }
        return out;
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

    /**
     * Signs in every user harness.json names under auth.sessions, with the application's own sign-in
     * request, and keeps each answer: the headers of later requests are built from it. Signing in
     * happens here, after the reset and before the first step, so it is never part of an observed call.
     */
    @Override
    public void beginScenario() throws Exception {
        signIn();
    }

    /** Signs the named users in and says who was. */
    public List<String> signIn() throws Exception {
        signedIn.clear();
        if (mvc == null) {
            return new ArrayList<String>(signedIn.keySet());
        }
        for (JsonNode session : sessions) {
            JsonNode login = session.path("login");
            if (!login.hasNonNull("path")) {
                continue;
            }
            String name = session.path("name").asText("default");
            // A password that is not part of the repository's own test data is named as ${env.AUDIT_...}: never written down.
            ObjectNode ctx = AuditConfig.JSON.createObjectNode();
            ObjectNode env = ctx.putObject("env");
            for (Map.Entry<String, String> e : System.getenv().entrySet()) {
                if (e.getKey().startsWith("AUDIT_")) {
                    env.put(e.getKey(), e.getValue());
                }
            }
            MockHttpServletRequestBuilder request = MockMvcRequestBuilders.request(
                    HttpMethod.valueOf(login.path("method").asText("POST").toUpperCase()), login.get("path").asText());
            if (login.hasNonNull("query")) {
                Iterator<Map.Entry<String, JsonNode>> qs = auditkit.Expr.fillLenient(login.get("query"), ctx).fields();
                while (qs.hasNext()) {
                    Map.Entry<String, JsonNode> q = qs.next();
                    request = request.param(q.getKey(), q.getValue().asText());
                }
            }
            if (login.hasNonNull("body")) {
                request = request.contentType(MediaType.APPLICATION_JSON)
                        .content(AuditConfig.JSON.writeValueAsBytes(auditkit.Expr.fillLenient(login.get("body"), ctx)));
            }
            Outcome answer = outcomeOf(mvc.perform(request).andReturn().getResponse());
            if (!Outcome.ALLOWED.equals(answer.kind)) {
                throw new IllegalStateException("signing in as '" + name + "' (" + login.get("path").asText() + ") was "
                        + answer.kind + (answer.code == null ? "" : " " + answer.code)
                        + ": the user must exist in the base data, and harness.json must name the request that signs it in");
            }
            ObjectNode kept = AuditConfig.JSON.createObjectNode();
            kept.set("login", answer.response == null ? AuditConfig.JSON.createObjectNode() : answer.response);
            signedIn.put(name, kept);
        }
        return new ArrayList<String>(signedIn.keySet());
    }

    /** The headers a request carries: the fixed ones, those of the signed-in user it is sent as, and the binding's own. */
    private MockHttpServletRequestBuilder withHeaders(MockHttpServletRequestBuilder request, JsonNode binding, JsonNode params,
                                                      String path) {
        Iterator<Map.Entry<String, JsonNode>> hs = AuditConfig.load().harness().path("auth").path("headers").fields();
        while (hs.hasNext()) {
            Map.Entry<String, JsonNode> h = hs.next();
            request = request.header(h.getKey(), h.getValue().asText());
        }
        // "_session" in a case's parameters says as whom the call is made: a session's name, or "none" for nobody.
        String wanted = params != null && params.hasNonNull("_session") ? params.get("_session").asText() : null;
        for (JsonNode session : sessions) {
            String name = session.path("name").asText("default");
            if ("none".equals(wanted)) {
                break;
            }
            boolean mine = wanted != null ? wanted.equals(name)
                    : !session.hasNonNull("paths") || java.util.regex.Pattern.compile(session.get("paths").asText()).matcher(path).find();
            if (!mine || !signedIn.containsKey(name)) {
                continue;
            }
            Iterator<Map.Entry<String, JsonNode>> own = auditkit.Expr.fillLenient(session.path("headers"), signedIn.get(name)).fields();
            while (own.hasNext()) {
                Map.Entry<String, JsonNode> h = own.next();
                if (!h.getValue().isNull()) {
                    request = request.header(h.getKey(), h.getValue().asText());
                }
            }
            break;          // one user per request
        }
        if (binding.hasNonNull("headers")) {
            Iterator<Map.Entry<String, JsonNode>> own = auditkit.Expr.fillLenient(binding.get("headers"), params).fields();
            while (own.hasNext()) {
                Map.Entry<String, JsonNode> h = own.next();
                if (!h.getValue().isNull()) {
                    request = request.header(h.getKey(), h.getValue().asText());
                }
            }
        }
        return request;
    }

    private static boolean isAbsent(JsonNode value) {
        return auditkit.Sql.isAbsent(value);
    }

    /**
     * The parameters that are sent: without the ones that only steer the harness (their names start
     * with an underscore) and without the ones the case leaves out of the request.
     */
    private static JsonNode withoutReserved(JsonNode params) {
        if (params == null || !params.isObject()) {
            return params;
        }
        ObjectNode out = AuditConfig.JSON.createObjectNode();
        Iterator<Map.Entry<String, JsonNode>> fields = params.fields();
        while (fields.hasNext()) {
            Map.Entry<String, JsonNode> f = fields.next();
            if (!f.getKey().startsWith("_") && !isAbsent(f.getValue())) {
                out.set(f.getKey(), f.getValue());
            }
        }
        return out;
    }

    /**
     * A body template without the fields that stand for a parameter the case leaves out: the key is
     * then not in the request, which a hand-written reader of the body tells from a key sent as null.
     */
    private static JsonNode withoutAbsent(JsonNode template, JsonNode params) {
        if (template == null || !template.isObject() || params == null || !params.isObject()) {
            return template;
        }
        ObjectNode out = AuditConfig.JSON.createObjectNode();
        Iterator<Map.Entry<String, JsonNode>> fields = template.fields();
        while (fields.hasNext()) {
            Map.Entry<String, JsonNode> f = fields.next();
            JsonNode v = f.getValue();
            if (v.isTextual()) {
                java.util.regex.Matcher one = java.util.regex.Pattern.compile("^\\$\\{([A-Za-z_][\\w]*)\\}$").matcher(v.asText());
                if (one.matches() && isAbsent(params.get(one.group(1)))) {
                    continue;
                }
            }
            out.set(f.getKey(), v.isObject() ? withoutAbsent(v, params) : v);
        }
        return out;
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
        request = withHeaders(request, binding, params, path);
        JsonNode sent = withoutReserved(params);
        if (binding.hasNonNull("query")) {
            JsonNode query = auditkit.Expr.fillLenient(binding.get("query"), sent);
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
                    ? sent : auditkit.Expr.fillLenient(withoutAbsent(binding.get("body"), params), sent);
            request = request.contentType(MediaType.APPLICATION_JSON).content(AuditConfig.JSON.writeValueAsBytes(body));
        }
        MockHttpServletResponse response;
        try {
            response = followed(mvc.perform(request).andReturn().getResponse(), binding, params, path);
        } catch (Exception e) {
            return failure(e);
        }
        return outcomeOf(response);
    }

    /**
     * An answer that sends the caller on to another address of this same application (a security filter
     * that redirects whoever is not signed in to a page saying so) is read as what is found there: that
     * is what the caller is told. A redirect to anywhere else is left as it is.
     */
    private MockHttpServletResponse followed(MockHttpServletResponse response, JsonNode binding, JsonNode params,
                                             String firstPath) throws Exception {
        for (int hop = 0; hop < 3; hop++) {
            int status = response.getStatus();
            String to = response.getRedirectedUrl();
            if (to == null || !(status == 301 || status == 302 || status == 303)) {
                break;
            }
            to = to.replaceFirst("^https?://localhost(:\\d+)?(?=/)", "").replaceFirst(";[^?#]*", "");
            if (!to.startsWith("/") || to.startsWith("//")) {
                break;
            }
            java.net.URI next;
            try {
                next = java.net.URI.create(to);
            } catch (IllegalArgumentException e) {
                break;
            }
            response = mvc.perform(withHeaders(MockMvcRequestBuilders.get(next), binding, params, firstPath)).andReturn().getResponse();
        }
        return response;
    }

    /**
     * How a call ended, from its answer. By the HTTP status; and for an application that always answers
     * 200 and says in the body whether it refused (harness.json result_code), by that code.
     */
    private Outcome outcomeOf(MockHttpServletResponse response) throws Exception {
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
            if (resultPath != null && json != null && json.isObject()) {
                JsonNode value = json.at(resultPath);
                if (value.isValueNode() && !value.isNull()) {
                    String code = value.asText();
                    if (resultAllowed.contains(code)) {
                        return new Outcome(Outcome.ALLOWED, null, status, json, null);
                    }
                    if (resultError.contains(code)) {
                        return new Outcome(Outcome.ERROR, code, status, json, "result code " + code);
                    }
                    return new Outcome(Outcome.REJECTED, code, status, json, null);
                }
            }
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
