package com.example.orders.service;

import com.example.orders.domain.ChangeType;
import com.example.orders.domain.LineStatus;
import com.example.orders.domain.OrderHeader;
import com.example.orders.domain.OrderLine;
import com.example.orders.domain.OrderStatus;
import com.example.orders.domain.ScheduleLine;
import com.example.orders.domain.ScheduleStatus;
import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.List;

/**
 * Decides whether a change to an order line is allowed and what it would do.
 * Preview and commit both go through here, so what the preview shows is what the commit applies.
 * Nothing in this class reads or writes the database.
 */
public final class ChangeCalculator {

    private ChangeCalculator() {
    }

    /** One schedule line after the change. */
    public record ScheduleState(Long id, int seqNo, int qty, LocalDate deliveryDate, ScheduleStatus status) {
    }

    public record Effect(int newQty, BigDecimal newUnitPrice, LineStatus newLineStatus, List<ScheduleState> schedules,
                         BigDecimal newLineAmount, BigDecimal amountDelta, OrderStatus newOrderStatus,
                         BigDecimal newOrderTotal) {
    }

    public static void checkAllowed(OrderHeader header, OrderLine line, ChangeType type, Integer newQty,
                                    LocalDate newDate, LocalDate today) {
        if (header.getStatus() != OrderStatus.DRAFT && header.getStatus() != OrderStatus.CONFIRMED) {
            throw new BusinessException("ORDER_NOT_OPEN");
        }
        if (line.getStatus() != LineStatus.OPEN) {
            throw new BusinessException("LINE_NOT_OPEN");
        }
        switch (type) {
            case QTY -> {
                if (newQty == null || newQty < 1) {
                    throw new BusinessException("INVALID_QTY");
                }
                if (newQty < line.getShippedQty()) {
                    throw new BusinessException("QTY_BELOW_SHIPPED");
                }
                if (newQty == line.getQty()) {
                    throw new BusinessException("NO_CHANGE");
                }
            }
            case DATE -> {
                if (newDate == null) {
                    throw new BusinessException("INVALID_DATE");
                }
                if (newDate.isBefore(today)) {
                    throw new BusinessException("DATE_IN_PAST");
                }
                boolean unchanged = line.getSchedules().stream()
                        .filter(s -> s.getStatus() == ScheduleStatus.PLANNED)
                        .allMatch(s -> s.getDeliveryDate().equals(newDate));
                if (unchanged) {
                    throw new BusinessException("NO_CHANGE");
                }
            }
            case CANCEL -> {
                if (line.getShippedQty() > 0) {
                    throw new BusinessException("LINE_ALREADY_SHIPPED");
                }
            }
        }
    }

    /** Call only after {@link #checkAllowed}. For a quantity change, quotedUnitPrice is the price at the new quantity. */
    public static Effect effect(OrderHeader header, OrderLine line, ChangeType type, Integer newQty, LocalDate newDate,
                                BigDecimal quotedUnitPrice) {
        List<ScheduleState> schedules = new ArrayList<>();
        for (ScheduleLine s : line.getSchedules()) {
            schedules.add(new ScheduleState(s.getId(), s.getSeqNo(), s.getQty(), s.getDeliveryDate(), s.getStatus()));
        }
        int qty = line.getQty();
        BigDecimal unitPrice = line.getUnitPrice();
        LineStatus status = line.getStatus();

        switch (type) {
            case QTY -> {
                int delta = newQty - qty;
                if (delta > 0) {
                    increase(schedules, delta);
                } else {
                    decrease(schedules, -delta);
                }
                qty = newQty;
                unitPrice = quotedUnitPrice;
                if (qty == line.getShippedQty()) {
                    status = LineStatus.CLOSED;
                }
            }
            case DATE -> {
                for (int i = 0; i < schedules.size(); i++) {
                    ScheduleState s = schedules.get(i);
                    if (s.status() == ScheduleStatus.PLANNED) {
                        schedules.set(i, new ScheduleState(s.id(), s.seqNo(), s.qty(), newDate, s.status()));
                    }
                }
            }
            case CANCEL -> {
                status = LineStatus.CANCELLED;
                for (int i = 0; i < schedules.size(); i++) {
                    ScheduleState s = schedules.get(i);
                    if (s.status() == ScheduleStatus.PLANNED) {
                        schedules.set(i, new ScheduleState(s.id(), s.seqNo(), s.qty(), s.deliveryDate(),
                                ScheduleStatus.CANCELLED));
                    }
                }
            }
        }

        BigDecimal oldAmount = line.amount();
        BigDecimal newAmount = OrderLine.amountOf(status, qty, unitPrice);
        BigDecimal newTotal = BigDecimal.ZERO.setScale(2);
        List<LineStatus> statuses = new ArrayList<>();
        for (OrderLine other : header.getLines()) {
            boolean same = other == line;
            newTotal = newTotal.add(same ? newAmount : other.amount());
            statuses.add(same ? status : other.getStatus());
        }
        return new Effect(qty, unitPrice, status, List.copyOf(schedules), newAmount, newAmount.subtract(oldAmount),
                OrderTotals.rollup(header.getStatus(), statuses), newTotal);
    }

    /** Extra quantity goes to the last planned schedule line. An open line always has one. */
    private static void increase(List<ScheduleState> schedules, int delta) {
        for (int i = schedules.size() - 1; i >= 0; i--) {
            ScheduleState s = schedules.get(i);
            if (s.status() == ScheduleStatus.PLANNED) {
                schedules.set(i, new ScheduleState(s.id(), s.seqNo(), s.qty() + delta, s.deliveryDate(), s.status()));
                return;
            }
        }
        throw new IllegalStateException("an open line has no planned schedule line");
    }

    /** Quantity is taken from planned schedule lines, latest first. One reduced to nothing is cancelled. */
    private static void decrease(List<ScheduleState> schedules, int amount) {
        int remaining = amount;
        for (int i = schedules.size() - 1; i >= 0 && remaining > 0; i--) {
            ScheduleState s = schedules.get(i);
            if (s.status() != ScheduleStatus.PLANNED) {
                continue;
            }
            if (s.qty() > remaining) {
                schedules.set(i, new ScheduleState(s.id(), s.seqNo(), s.qty() - remaining, s.deliveryDate(), s.status()));
                remaining = 0;
            } else {
                remaining -= s.qty();
                schedules.set(i, new ScheduleState(s.id(), s.seqNo(), s.qty(), s.deliveryDate(),
                        ScheduleStatus.CANCELLED));
            }
        }
        if (remaining > 0) {
            throw new IllegalStateException("planned quantity is smaller than the reduction");
        }
    }
}
