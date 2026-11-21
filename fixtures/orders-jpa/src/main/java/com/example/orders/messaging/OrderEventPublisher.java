package com.example.orders.messaging;

import io.nats.client.Connection;
import java.nio.charset.StandardCharsets;
import org.springframework.stereotype.Component;

@Component
public class OrderEventPublisher {

    private final Connection connection;

    public OrderEventPublisher(Connection connection) {
        this.connection = connection;
    }

    public void publish(String type, String payload) {
        connection.publish("orders.events." + type, payload.getBytes(StandardCharsets.UTF_8));
    }
}
