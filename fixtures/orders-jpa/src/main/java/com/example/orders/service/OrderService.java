package com.example.orders.service;

import com.example.orders.domain.LineStatus;
import com.example.orders.domain.OrderHeader;
import com.example.orders.domain.OrderLine;
import com.example.orders.domain.OrderStatus;
import com.example.orders.domain.ScheduleLine;
import com.example.orders.domain.ScheduleStatus;
import com.example.orders.pricing.PricingClient;
import com.example.orders.repo.OrderHeaderRepository;
import com.example.orders.web.dto.CreateOrderRequest;
import com.example.orders.web.dto.OrderView;
import java.math.BigDecimal;
import java.time.Clock;
import java.time.LocalDate;
import java.util.Map;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class OrderService {

    private final OrderHeaderRepository headers;
    private final PricingClient pricing;
    private final OutboxWriter outbox;
    private final Clock clock;

    public OrderService(OrderHeaderRepository headers, PricingClient pricing, OutboxWriter outbox, Clock clock) {
        this.headers = headers;
        this.pricing = pricing;
        this.outbox = outbox;
        this.clock = clock;
    }

    @Transactional
    public OrderView create(CreateOrderRequest request) {
        if (headers.existsByOrderNo(request.orderNo())) {
            throw new BusinessException("DUPLICATE_ORDER_NO");
        }
        LocalDate today = LocalDate.now(clock);
        OrderHeader header = new OrderHeader(request.orderNo(), request.customerId(), clock.instant());
        int lineNo = 1;
        for (CreateOrderRequest.Line requested : request.lines()) {
            if (requested.schedules().stream().anyMatch(s -> s.deliveryDate().isBefore(today))) {
                throw new BusinessException("DATE_IN_PAST");
            }
            BigDecimal unitPrice = pricing.quote(requested.sku(), requested.qty()).unitPrice();
            OrderLine line = new OrderLine(header, lineNo++, requested.sku(), requested.qty(), unitPrice);
            int seqNo = 1;
            for (CreateOrderRequest.Schedule schedule : requested.schedules()) {
                line.addSchedule(new ScheduleLine(line, seqNo++, schedule.qty(), schedule.deliveryDate()));
            }
            header.addLine(line);
        }
        OrderTotals.apply(header);
        return OrderView.of(headers.save(header));
    }

    @Transactional(readOnly = true)
    public OrderView get(Long id) {
        return OrderView.of(headers.findById(id).orElseThrow(() -> new NotFoundException("order", id)));
    }

    @Transactional
    public OrderView confirm(Long id) {
        OrderHeader header = lock(id);
        if (header.getStatus() != OrderStatus.DRAFT) {
            throw new BusinessException("ORDER_NOT_DRAFT");
        }
        if (header.getLines().stream().noneMatch(l -> l.getStatus() == LineStatus.OPEN)) {
            throw new BusinessException("NO_OPEN_LINE");
        }
        header.setStatus(OrderStatus.CONFIRMED);
        outbox.add("order.confirmed", header.getId(), Map.of("orderId", header.getId(), "orderNo", header.getOrderNo()));
        return OrderView.of(header);
    }

    /** An order can be cancelled as a whole only while nothing on it has shipped. */
    @Transactional
    public OrderView cancel(Long id) {
        OrderHeader header = lock(id);
        if (header.getStatus() != OrderStatus.DRAFT && header.getStatus() != OrderStatus.CONFIRMED) {
            throw new BusinessException("ORDER_NOT_OPEN");
        }
        if (header.getLines().stream().anyMatch(l -> l.getShippedQty() > 0)) {
            throw new BusinessException("ORDER_ALREADY_SHIPPED");
        }
        for (OrderLine line : header.getLines()) {
            if (line.getStatus() == LineStatus.OPEN) {
                line.setStatus(LineStatus.CANCELLED);
                for (ScheduleLine schedule : line.getSchedules()) {
                    if (schedule.getStatus() == ScheduleStatus.PLANNED) {
                        schedule.setStatus(ScheduleStatus.CANCELLED);
                    }
                }
                line.touch();
            }
        }
        header.setStatus(OrderStatus.CANCELLED);
        header.setTotalAmount(OrderTotals.total(header.getLines()));
        outbox.add("order.cancelled", header.getId(), Map.of("orderId", header.getId(), "orderNo", header.getOrderNo()));
        return OrderView.of(header);
    }

    /** Re-derives the header's total and status from its lines. Used by the nightly closing job. */
    @Transactional
    public void rollup(Long id) {
        OrderTotals.apply(lock(id));
    }

    private OrderHeader lock(Long id) {
        return headers.findByIdForUpdate(id).orElseThrow(() -> new NotFoundException("order", id));
    }
}
