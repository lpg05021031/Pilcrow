package com.example.orders.service;

import com.example.orders.domain.LineStatus;
import com.example.orders.domain.OrderHeader;
import com.example.orders.domain.OrderLine;
import com.example.orders.domain.OrderStatus;
import com.example.orders.domain.ProcessedMessage;
import com.example.orders.domain.ScheduleLine;
import com.example.orders.domain.ScheduleStatus;
import com.example.orders.repo.OrderHeaderRepository;
import com.example.orders.repo.ProcessedMessageRepository;
import com.example.orders.repo.ScheduleLineRepository;
import java.time.Clock;
import java.util.Map;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class ShipmentService {

    private final OrderHeaderRepository headers;
    private final ScheduleLineRepository schedules;
    private final ProcessedMessageRepository processed;
    private final OutboxWriter outbox;
    private final Clock clock;

    public ShipmentService(OrderHeaderRepository headers, ScheduleLineRepository schedules,
                           ProcessedMessageRepository processed, OutboxWriter outbox, Clock clock) {
        this.headers = headers;
        this.schedules = schedules;
        this.processed = processed;
        this.outbox = outbox;
        this.clock = clock;
    }

    /**
     * Records that one schedule line shipped in full. A message already applied is ignored.
     *
     * @return true when the shipment was applied, false when the message had been applied before
     */
    @Transactional
    public boolean apply(String messageId, Long scheduleId) {
        Long headerId = schedules.findHeaderId(scheduleId)
                .orElseThrow(() -> new NotFoundException("schedule", scheduleId));
        OrderHeader header = headers.findByIdForUpdate(headerId)
                .orElseThrow(() -> new NotFoundException("order", headerId));
        if (processed.existsById(messageId)) {
            return false;
        }
        ScheduleLine schedule = null;
        OrderLine line = null;
        for (OrderLine candidate : header.getLines()) {
            for (ScheduleLine s : candidate.getSchedules()) {
                if (s.getId().equals(scheduleId)) {
                    schedule = s;
                    line = candidate;
                }
            }
        }
        if (schedule == null) {
            throw new NotFoundException("schedule", scheduleId);
        }
        if (header.getStatus() != OrderStatus.CONFIRMED) {
            throw new BusinessException("ORDER_NOT_CONFIRMED");
        }
        if (line.getStatus() != LineStatus.OPEN) {
            throw new BusinessException("LINE_NOT_OPEN");
        }
        if (schedule.getStatus() != ScheduleStatus.PLANNED) {
            throw new BusinessException("SCHEDULE_NOT_PLANNED");
        }

        schedule.setStatus(ScheduleStatus.SHIPPED);
        line.setShippedQty(line.getShippedQty() + schedule.getQty());
        if (line.getShippedQty() == line.getQty()) {
            line.setStatus(LineStatus.CLOSED);
        }
        line.touch();
        OrderTotals.apply(header);
        processed.save(new ProcessedMessage(messageId, clock.instant()));
        outbox.add("order.line.shipped", header.getId(), Map.of(
                "orderId", header.getId(),
                "lineId", line.getId(),
                "scheduleId", schedule.getId(),
                "shippedQty", line.getShippedQty()));
        return true;
    }
}
