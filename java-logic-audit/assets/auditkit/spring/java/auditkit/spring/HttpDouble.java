package auditkit.spring;

import auditkit.AuditConfig;
import auditkit.EdgeDoubles;
import auditkit.EffectRecorder;
import com.fasterxml.jackson.databind.JsonNode;
import java.io.IOException;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.client.ClientHttpRequest;
import org.springframework.http.client.ClientHttpRequestFactory;
import org.springframework.http.client.ClientHttpResponse;
import org.springframework.mock.http.client.MockClientHttpRequest;
import org.springframework.mock.http.client.MockClientHttpResponse;

/**
 * Stands in for every HTTP service the application calls. The application's own client code still
 * runs (URLs, headers, serialization); only the network is replaced. Answers come from the
 * http_doubles rules in harness.json; a request no rule covers is answered 501 and marked unconfigured.
 */
public final class HttpDouble implements ClientHttpRequestFactory {

    /** One request the application sent. */
    public static final class Request {
        public final String method;
        public final String url;
        public final String body;
        public final int status;
        public final boolean unconfigured;

        Request(String method, String url, String body, int status, boolean unconfigured) {
            this.method = method;
            this.url = url;
            this.body = body;
            this.status = status;
            this.unconfigured = unconfigured;
        }
    }

    private static final List<Request> REQUESTS = Collections.synchronizedList(new ArrayList<Request>());
    private static volatile EdgeDoubles.FaultHook faultHook;

    public static List<Request> requests() {
        synchronized (REQUESTS) {
            return new ArrayList<Request>(REQUESTS);
        }
    }

    public static void clear() {
        REQUESTS.clear();
    }

    public static void setFaultHook(EdgeDoubles.FaultHook hook) {
        faultHook = hook;
    }

    @Override
    public ClientHttpRequest createRequest(final URI uri, final HttpMethod httpMethod) throws IOException {
        return new MockClientHttpRequest(httpMethod, uri) {
            @Override
            protected ClientHttpResponse executeInternal() throws IOException {
                String method = httpMethod.name();
                String url = uri.toString();
                EdgeDoubles.FaultHook hook = faultHook;
                if (hook != null) {
                    RuntimeException failure = hook.before("http", method + " " + url);
                    if (failure != null) {
                        throw failure;
                    }
                }
                try {
                    auditkit.Faults.at(auditkit.Faults.EXT, method + " " + url);
                } catch (auditkit.Faults.Injected e) {
                    throw new IOException(e.getMessage());        // what a connection that drops looks like to the client
                }
                int status = 501;
                String body = "{}";
                boolean unconfigured = true;
                for (JsonNode rule : AuditConfig.load().nodes("http_doubles")) {
                    boolean methodOk = !rule.has("method") || rule.get("method").asText().equalsIgnoreCase(method);
                    boolean urlOk = !rule.has("url") || url.matches(rule.get("url").asText());
                    if (methodOk && urlOk) {
                        status = rule.has("status") ? rule.get("status").asInt() : 200;
                        body = rule.has("body") ? rule.get("body").toString() : "";
                        unconfigured = false;
                        break;
                    }
                }
                REQUESTS.add(new Request(method, url, getBodyAsString(), status, unconfigured));
                Map<String, Object> more = new LinkedHashMap<String, Object>();
                more.put("double", "http");
                more.put("status", status);
                if (unconfigured) {
                    more.put("unconfigured", true);
                }
                EffectRecorder.add("EXT", null, method + " " + url, more);
                MockClientHttpResponse response =
                        new MockClientHttpResponse(body.getBytes(StandardCharsets.UTF_8), HttpStatus.valueOf(status));
                response.getHeaders().setContentType(MediaType.APPLICATION_JSON);
                return response;
            }
        };
    }
}
