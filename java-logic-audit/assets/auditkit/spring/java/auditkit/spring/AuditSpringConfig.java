package auditkit.spring;

import auditkit.AuditConfig;
import auditkit.ConnectionGuard;
import auditkit.EdgeDoubles;
import auditkit.EffectRecorder;
import com.fasterxml.jackson.databind.JsonNode;
import java.util.function.Supplier;
import javax.sql.DataSource;
import org.springframework.beans.BeansException;
import org.springframework.beans.factory.config.BeanFactoryPostProcessor;
import org.springframework.beans.factory.config.BeanPostProcessor;
import org.springframework.beans.factory.config.ConfigurableListableBeanFactory;
import org.springframework.beans.factory.support.BeanDefinitionRegistry;
import org.springframework.beans.factory.support.RootBeanDefinition;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/** What the audit adds to the application context. Nothing here is specific to one application. */
@Configuration
public class AuditSpringConfig {

    static final String SCHEDULED_PROCESSOR = "org.springframework.context.annotation.internalScheduledAnnotationProcessor";

    /**
     * Replaces the bean definition of each outside system named in harness.json with a recording
     * double, before the real bean is ever created, and removes the processor that would start
     * scheduled tasks: the audit calls scheduled methods itself, one at a time.
     */
    @Bean
    public static BeanFactoryPostProcessor auditDoubles() {
        return new BeanFactoryPostProcessor() {
            @Override
            public void postProcessBeanFactory(ConfigurableListableBeanFactory beanFactory) throws BeansException {
                BeanDefinitionRegistry registry = (BeanDefinitionRegistry) beanFactory;
                if (registry.containsBeanDefinition(SCHEDULED_PROCESSOR)) {
                    registry.removeBeanDefinition(SCHEDULED_PROCESSOR);
                }
                for (JsonNode node : AuditConfig.load().nodes("doubles")) {
                    final Class<?> type = AuditContext.forName(node.get("type").asText());
                    String kind = node.has("kind") ? node.get("kind").asText() : "external";
                    final Object mock = EdgeDoubles.create(type, kind);
                    String[] names = beanFactory.getBeanNamesForType(type, true, false);
                    if (names.length == 0) {
                        registry.registerBeanDefinition("auditDouble." + type.getName(), definition(type, mock));
                    }
                    for (String name : names) {
                        registry.removeBeanDefinition(name);
                        registry.registerBeanDefinition(name, definition(type, mock));
                    }
                }
            }
        };
    }

    @SuppressWarnings("unchecked")
    private static RootBeanDefinition definition(Class<?> type, final Object mock) {
        RootBeanDefinition def = new RootBeanDefinition((Class<Object>) type, new Supplier<Object>() {
            @Override
            public Object get() {
                return mock;
            }
        });
        def.setPrimary(true);
        return def;
    }

    /** Checks where each data source points, then wraps it so that every statement is seen. */
    @Bean
    public static BeanPostProcessor auditDataSourceProxy() {
        return new BeanPostProcessor() {
            @Override
            public Object postProcessBeforeInitialization(Object bean, String beanName) {
                return bean;
            }

            @Override
            public Object postProcessAfterInitialization(Object bean, String beanName) {
                if (bean instanceof DataSource && !(bean instanceof net.ttddyy.dsproxy.support.ProxyDataSource)) {
                    ConnectionGuard.check((DataSource) bean);
                    return EffectRecorder.wrap((DataSource) bean);
                }
                return bean;
            }
        };
    }
}
