package com.example.orders.messaging;

import io.nats.client.Connection;
import io.nats.client.Dispatcher;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.stereotype.Component;

@Component
@ConditionalOnProperty(name = "orders.nats.enabled", havingValue = "true", matchIfMissing = true)
public class NatsSubscriptions {

    public static final String SHIPMENTS = "orders.shipments";

    private final Connection connection;
    private final ShipmentListener shipmentListener;

    public NatsSubscriptions(Connection connection, ShipmentListener shipmentListener) {
        this.connection = connection;
        this.shipmentListener = shipmentListener;
    }

    @EventListener(ApplicationReadyEvent.class)
    public void subscribe() {
        Dispatcher dispatcher = connection.createDispatcher(shipmentListener);
        dispatcher.subscribe(SHIPMENTS);
    }
}
