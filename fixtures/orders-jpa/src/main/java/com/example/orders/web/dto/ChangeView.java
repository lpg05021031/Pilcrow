package com.example.orders.web.dto;

import com.example.orders.domain.ChangeRequest;
import com.example.orders.domain.ChangeStatus;
import com.example.orders.domain.ChangeType;
import java.math.BigDecimal;
import java.time.LocalDate;

public record ChangeView(Long id, Long lineId, ChangeType type, Integer newQty, LocalDate newDate, ChangeStatus status,
                         int lineRevision, BigDecimal amountDelta) {

    public static ChangeView of(ChangeRequest c) {
        return new ChangeView(c.getId(), c.getLineId(), c.getType(), c.getNewQty(), c.getNewDate(), c.getStatus(),
                c.getLineRevision(), c.getAmountDelta());
    }
}
