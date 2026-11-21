package com.example.orders.support;

import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.context.annotation.Bean;
import org.testcontainers.containers.PostgreSQLContainer;

/**
 * The database for integration tests: a PostgreSQL container by default. Where Docker is not
 * available, run with -Dorders.test.external-db=true and point spring.datasource.* at a local
 * PostgreSQL instead.
 */
@TestConfiguration(proxyBeanMethods = false)
public class PostgresTestConfig {

    @Bean
    @ServiceConnection
    @ConditionalOnProperty(name = "orders.test.external-db", havingValue = "false", matchIfMissing = true)
    PostgreSQLContainer<?> postgres() {
        return new PostgreSQLContainer<>("postgres:16-alpine");
    }
}
