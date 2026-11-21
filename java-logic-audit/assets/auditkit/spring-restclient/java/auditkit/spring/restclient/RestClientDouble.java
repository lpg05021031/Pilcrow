package auditkit.spring.restclient;

import auditkit.spring.HttpDouble;
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

    /** Sends one request through the application's own builder and reports whether the double saw it. */
    public static boolean selfTest(ApplicationContext context) {
        int before = HttpDouble.requests().size();
        try {
            context.getBean(RestClient.Builder.class).build().get()
                    .uri("http://audit.invalid/java-logic-audit/self-test").retrieve().toBodilessEntity();
        } catch (RestClientException e) {
            // an unconfigured request is answered 501, which the client reports as an error: expected here
        }
        return HttpDouble.requests().size() == before + 1;
    }
}
