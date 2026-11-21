package com.example.orders.service;

import com.example.orders.domain.OutboxEvent;
import com.example.orders.repo.OutboxEventRepository;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.time.Clock;
import java.util.Map;
import org.springframework.stereotype.Component;

/** Records an event in the same transaction as the change it describes. The relay sends it later. */
@Component
public class OutboxWriter {

    private final OutboxEventRepository outbox;
    private final ObjectMapper mapper;
    private final Clock clock;

    public OutboxWriter(OutboxEventRepository outbox, ObjectMapper mapper, Clock clock) {
        this.outbox = outbox;
        this.mapper = mapper;
        this.clock = clock;
    }

    public void add(String type, Long aggregateId, Map<String, Object> payload) {
        try {
            outbox.save(new OutboxEvent(type, aggregateId, mapper.writeValueAsString(payload), clock.instant()));
        } catch (JsonProcessingException e) {
            throw new IllegalStateException("event payload cannot be written", e);
        }
    }
}
