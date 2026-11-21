package auditkit.spring;

import auditkit.AuditConfig;
import com.fasterxml.jackson.databind.JsonNode;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import org.springframework.boot.builder.SpringApplicationBuilder;
import org.springframework.context.ConfigurableApplicationContext;

/**
 * Starts the application once per JVM, with the audit's own configuration added: a proxy around the
 * data source, doubles for the outside systems named in harness.json, and no scheduled tasks or
 * message listeners running on their own.
 */
public final class AuditContext {

    private static ConfigurableApplicationContext context;

    private AuditContext() {
    }

    public static synchronized ConfigurableApplicationContext get() {
        if (context != null) {
            return context;
        }
        AuditConfig config = AuditConfig.load();
        List<Class<?>> sources = new ArrayList<Class<?>>();
        sources.add(forName(config.text("application")));
        sources.add(AuditSpringConfig.class);
        for (String name : config.texts("reuse_test_config")) {
            sources.add(forName(name));
        }
        addIfPresent(sources, "auditkit.spring.restclient.RestClientDouble$Config");

        List<String> args = new ArrayList<String>();
        args.add("--server.port=0");
        args.add("--spring.main.banner-mode=off");
        // Message listeners are driven by the audit, one message at a time; they must not consume on their own.
        args.add("--spring.kafka.listener.auto-startup=false");
        args.add("--spring.rabbitmq.listener.simple.auto-startup=false");
        args.add("--spring.rabbitmq.listener.direct.auto-startup=false");
        args.add("--spring.jms.listener.auto-startup=false");
        for (Map.Entry<String, String> e : config.map("properties").entrySet()) {
            args.add("--" + e.getKey() + "=" + e.getValue());
        }
        // A database offered by a person: its address and credentials come from the environment only.
        // They are set under spring.datasource and under every other prefix harness.json names: a connection pool
        // with its own starter reads its own properties (spring.datasource.druid, for one).
        String url = System.getenv("AUDIT_DB_URL");
        if (url != null && !url.trim().isEmpty()) {
            List<String> prefixes = new ArrayList<String>();
            prefixes.add("spring.datasource");
            for (JsonNode p : config.harness().path("database").path("property_prefixes")) {
                if (!prefixes.contains(p.asText())) {
                    prefixes.add(p.asText());
                }
            }
            String user = System.getenv("AUDIT_DB_USER");
            String password = System.getenv("AUDIT_DB_PASSWORD");
            for (String prefix : prefixes) {
                args.add("--" + prefix + ".url=" + url.trim());
                if (user != null) {
                    args.add("--" + prefix + ".username=" + user);
                }
                if (password != null) {
                    args.add("--" + prefix + ".password=" + password);
                }
            }
        }

        auditkit.DbReset.beforeStart();        // an application that reads its tables while starting needs them now
        SpringApplicationBuilder builder = new SpringApplicationBuilder(sources.toArray(new Class<?>[0]));
        List<String> profiles = config.texts("profiles");
        if (!profiles.isEmpty()) {
            builder.profiles(profiles.toArray(new String[0]));
        }
        context = builder.run(args.toArray(new String[0]));
        return context;
    }

    static Class<?> forName(String name) {
        try {
            return Class.forName(name);
        } catch (ClassNotFoundException e) {
            throw new IllegalStateException("class named in harness.json not found: " + name, e);
        }
    }

    private static void addIfPresent(List<Class<?>> sources, String name) {
        try {
            sources.add(Class.forName(name));
        } catch (ClassNotFoundException e) {
            // that module of the harness is not installed here
        }
    }
}
