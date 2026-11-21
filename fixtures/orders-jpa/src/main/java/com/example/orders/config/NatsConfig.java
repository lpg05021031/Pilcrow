package com.example.orders.config;

import io.nats.client.Connection;
import io.nats.client.Nats;
import java.io.IOException;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
@ConditionalOnProperty(name = "orders.nats.enabled", havingValue = "true", matchIfMissing = true)
public class NatsConfig {

    @Bean(destroyMethod = "close")
    public Connection natsConnection(@Value("${orders.nats.url}") String url) throws IOException, InterruptedException {
        return Nats.connect(url);
    }
}
