package com.example.orders.messaging;

import com.example.orders.domain.OutboxEvent;
import com.example.orders.domain.OutboxStatus;
import com.example.orders.repo.OutboxEventRepository;
import java.time.Clock;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

/**
 * Sends recorded events in order and marks them sent. Delivery is at least once: an event sent just
 * before a failure is sent again on the next run.
 */
@Component
public class OutboxRelay {

    private final OutboxEventRepository outbox;
    private final OrderEventPublisher publisher;
    private final Clock clock;

    public OutboxRelay(OutboxEventRepository outbox, OrderEventPublisher publisher, Clock clock) {
        this.outbox = outbox;
        this.publisher = publisher;
        this.clock = clock;
    }

    @Scheduled(fixedDelayString = "${orders.outbox.delay-ms:1000}")
    @Transactional
    public void relay() {
        for (OutboxEvent event : outbox.findTop100ByStatusOrderByIdAsc(OutboxStatus.NEW)) {
            publisher.publish(event.getType(), event.getPayload());
            event.markSent(clock.instant());
        }
    }
}
