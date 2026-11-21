package auditkit.spring.restclient;

import auditkit.AuditConfig;
import auditkit.spring.HttpDouble;
import com.fasterxml.jackson.databind.JsonNode;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.springframework.boot.web.client.RestClientCustomizer;
import org.springframework.context.ApplicationContext;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;

/** Puts the HTTP double behind every RestClient the application builds from the shared builder. */
public final class RestClientDouble {

    private RestClientDouble() {
    }

    @Configuration
    public static class Config {

        @Bean
        public RestClientCustomizer auditHttpDouble() {
            return new RestClientCustomizer() {
                @Override
                public void customize(RestClient.Builder builder) {
                    builder.requestFactory(new HttpDouble());
                }
            };
        }
    }

    /**
     * Sends requests through the application's own builder: one that no rule covers, to show the double
     * is in place, and the example of every rule in harness.json, to show the rule answers it.
     * Returns "reached" (boolean) and "examples" (one row per rule).
     */
    public static Map<String, Object> selfTest(ApplicationContext context) {
        RestClient client = context.getBean(RestClient.Builder.class).build();
        Map<String, Object> out = new LinkedHashMap<String, Object>();
        int before = HttpDouble.requests().size();
        send(client, "GET", "http://audit.invalid/java-logic-audit/self-test");
        out.put("reached", HttpDouble.requests().size() == before + 1);

        List<Map<String, Object>> examples = new ArrayList<Map<String, Object>>();
        for (JsonNode rule : AuditConfig.load().nodes("http_doubles")) {
            Map<String, Object> row = new LinkedHashMap<String, Object>();
            String example = rule.has("example") ? rule.get("example").asText() : null;
            row.put("url", rule.has("url") ? rule.get("url").asText() : null);
            row.put("example", example);
            if (example == null) {
                row.put("answered", null);
            } else {
                int at = HttpDouble.requests().size();
                send(client, rule.has("method") ? rule.get("method").asText() : "GET", example);
                List<HttpDouble.Request> seen = HttpDouble.requests();
                boolean answered = seen.size() == at + 1 && !seen.get(at).unconfigured
                        && seen.get(at).status == (rule.has("status") ? rule.get("status").asInt() : 200);
                row.put("answered", answered);
            }
            examples.add(row);
        }
        out.put("examples", examples);
        HttpDouble.clear();
        return out;
    }

    private static void send(RestClient client, String method, String url) {
        try {
            client.method(org.springframework.http.HttpMethod.valueOf(method.toUpperCase())).uri(url)
                    .retrieve().toBodilessEntity();
        } catch (RestClientException e) {
            // an error status is reported by the client as an exception: what was sent is still recorded
        }
    }
}
