package com.example.orders.messaging;

import com.example.orders.service.BusinessException;
import com.example.orders.service.NotFoundException;
import com.example.orders.service.ShipmentService;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.nats.client.Message;
import io.nats.client.MessageHandler;
import java.io.IOException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

@Component
public class ShipmentListener implements MessageHandler {

    private static final Logger log = LoggerFactory.getLogger(ShipmentListener.class);

    private final ShipmentService shipments;
    private final ObjectMapper mapper;

    public ShipmentListener(ShipmentService shipments, ObjectMapper mapper) {
        this.shipments = shipments;
        this.mapper = mapper;
    }

    @Override
    public void onMessage(Message msg) throws InterruptedException {
        ShipmentMessage shipment;
        try {
            shipment = mapper.readValue(msg.getData(), ShipmentMessage.class);
        } catch (IOException e) {
            log.warn("dropping unreadable shipment message");
            return;
        }
        handle(shipment);
    }

    /**
     * A shipment the business rules refuse can never succeed later, so it is logged and dropped.
     * Any other failure propagates so that the message is delivered again.
     */
    public void handle(ShipmentMessage shipment) {
        try {
            boolean applied = shipments.apply(shipment.messageId(), shipment.scheduleId());
            if (!applied) {
                log.info("shipment message {} was already applied", shipment.messageId());
            }
        } catch (BusinessException e) {
            log.warn("shipment message {} refused: {}", shipment.messageId(), e.getCode());
        } catch (NotFoundException e) {
            log.warn("shipment message {} refused: {}", shipment.messageId(), e.getMessage());
        }
    }
}
