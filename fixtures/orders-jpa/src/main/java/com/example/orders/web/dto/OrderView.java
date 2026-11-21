package com.example.orders.web.dto;

import com.example.orders.domain.LineStatus;
import com.example.orders.domain.OrderHeader;
import com.example.orders.domain.OrderLine;
import com.example.orders.domain.OrderStatus;
import com.example.orders.domain.ScheduleLine;
import com.example.orders.domain.ScheduleStatus;
import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;

public record OrderView(Long id, String orderNo, String customerId, OrderStatus status, BigDecimal totalAmount,
                        List<LineView> lines) {

    public record LineView(Long id, int lineNo, String sku, int qty, int shippedQty, BigDecimal unitPrice,
                           LineStatus status, BigDecimal amount, int revision, List<ScheduleView> schedules) {
    }

    public record ScheduleView(Long id, int seqNo, int qty, LocalDate deliveryDate, ScheduleStatus status) {
    }

    public static OrderView of(OrderHeader header) {
        return new OrderView(header.getId(), header.getOrderNo(), header.getCustomerId(), header.getStatus(),
                header.getTotalAmount(), header.getLines().stream().map(OrderView::line).toList());
    }

    private static LineView line(OrderLine line) {
        return new LineView(line.getId(), line.getLineNo(), line.getSku(), line.getQty(), line.getShippedQty(),
                line.getUnitPrice(), line.getStatus(), line.amount(), line.getRevision(),
                line.getSchedules().stream().map(OrderView::schedule).toList());
    }

    private static ScheduleView schedule(ScheduleLine s) {
        return new ScheduleView(s.getId(), s.getSeqNo(), s.getQty(), s.getDeliveryDate(), s.getStatus());
    }
}
