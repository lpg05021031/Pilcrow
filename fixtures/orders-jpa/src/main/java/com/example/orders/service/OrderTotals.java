package com.example.orders.service;

import com.example.orders.domain.LineStatus;
import com.example.orders.domain.OrderHeader;
import com.example.orders.domain.OrderLine;
import com.example.orders.domain.OrderStatus;
import java.math.BigDecimal;
import java.util.Collection;

/** The header values that are derived from the lines: the total amount and the rolled-up status. */
public final class OrderTotals {

    private OrderTotals() {
    }

    public static BigDecimal total(Collection<OrderLine> lines) {
        BigDecimal sum = BigDecimal.ZERO.setScale(2);
        for (OrderLine line : lines) {
            sum = sum.add(line.amount());
        }
        return sum;
    }

    /**
     * An open order whose lines are all cancelled is cancelled. A confirmed order whose lines are all
     * closed or cancelled, with at least one closed, is closed. Otherwise the status stays.
     */
    public static OrderStatus rollup(OrderStatus current, Collection<LineStatus> lineStatuses) {
        if (current != OrderStatus.DRAFT && current != OrderStatus.CONFIRMED) {
            return current;
        }
        if (lineStatuses.isEmpty()) {
            return current;
        }
        boolean allCancelled = lineStatuses.stream().allMatch(s -> s == LineStatus.CANCELLED);
        if (allCancelled) {
            return OrderStatus.CANCELLED;
        }
        boolean noneOpen = lineStatuses.stream().noneMatch(s -> s == LineStatus.OPEN);
        if (current == OrderStatus.CONFIRMED && noneOpen) {
            return OrderStatus.CLOSED;
        }
        return current;
    }

    public static void apply(OrderHeader header) {
        header.setTotalAmount(total(header.getLines()));
        header.setStatus(rollup(header.getStatus(), header.getLines().stream().map(OrderLine::getStatus).toList()));
    }
}
