package com.example.orders.web.dto;

import com.example.orders.domain.LineStatus;
import com.example.orders.domain.OrderStatus;
import com.example.orders.domain.ScheduleStatus;
import com.example.orders.service.ChangeCalculator;
import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;

/** What a change would do if it were committed now. */
public record ChangePreview(int newQty, BigDecimal newUnitPrice, LineStatus newLineStatus, BigDecimal newLineAmount,
                            BigDecimal amountDelta, OrderStatus newOrderStatus, BigDecimal newOrderTotal,
                            List<Schedule> schedules) {

    public record Schedule(int seqNo, int qty, LocalDate deliveryDate, ScheduleStatus status) {
    }

    public static ChangePreview of(ChangeCalculator.Effect e) {
        return new ChangePreview(e.newQty(), e.newUnitPrice(), e.newLineStatus(), e.newLineAmount(), e.amountDelta(),
                e.newOrderStatus(), e.newOrderTotal(),
                e.schedules().stream().map(s -> new Schedule(s.seqNo(), s.qty(), s.deliveryDate(), s.status())).toList());
    }
}
