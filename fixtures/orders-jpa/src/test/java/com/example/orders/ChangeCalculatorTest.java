package com.example.orders;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.example.orders.domain.ChangeType;
import com.example.orders.domain.LineStatus;
import com.example.orders.domain.OrderHeader;
import com.example.orders.domain.OrderLine;
import com.example.orders.domain.OrderStatus;
import com.example.orders.domain.ScheduleLine;
import com.example.orders.domain.ScheduleStatus;
import com.example.orders.service.BusinessException;
import com.example.orders.service.ChangeCalculator;
import com.example.orders.service.OrderTotals;
import java.math.BigDecimal;
import java.time.Instant;
import java.time.LocalDate;
import org.junit.jupiter.api.Test;

class ChangeCalculatorTest {

    private static final LocalDate TODAY = LocalDate.of(2026, 6, 1);

    private static OrderLine line(int qty) {
        OrderHeader header = new OrderHeader("SO-1", "C-1", Instant.parse("2026-06-01T00:00:00Z"));
        header.setStatus(OrderStatus.CONFIRMED);
        OrderLine line = new OrderLine(header, 1, "SKU-1", qty, new BigDecimal("10.00"));
        line.addSchedule(new ScheduleLine(line, 1, qty, TODAY.plusDays(7)));
        header.addLine(line);
        OrderTotals.apply(header);
        return line;
    }

    @Test
    void reducingQuantityTakesFromThePlannedSchedule() {
        OrderLine line = line(100);
        ChangeCalculator.checkAllowed(line.getHeader(), line, ChangeType.QTY, 40, null, TODAY);
        ChangeCalculator.Effect effect = ChangeCalculator.effect(line.getHeader(), line, ChangeType.QTY, 40, null,
                new BigDecimal("10.00"));

        assertThat(effect.newQty()).isEqualTo(40);
        assertThat(effect.schedules()).singleElement().satisfies(s -> assertThat(s.qty()).isEqualTo(40));
        assertThat(effect.newLineAmount()).isEqualByComparingTo("400.00");
        assertThat(effect.amountDelta()).isEqualByComparingTo("-600.00");
        assertThat(effect.newOrderTotal()).isEqualByComparingTo("400.00");
    }

    @Test
    void quantityBelowShippedIsRefused() {
        OrderLine line = line(100);
        line.setShippedQty(60);
        assertThatThrownBy(() -> ChangeCalculator.checkAllowed(line.getHeader(), line, ChangeType.QTY, 40, null, TODAY))
                .isInstanceOf(BusinessException.class).hasMessage("QTY_BELOW_SHIPPED");
    }

    @Test
    void cancellingALineCancelsItsPlannedSchedulesAndZeroesItsAmount() {
        OrderLine line = line(100);
        ChangeCalculator.checkAllowed(line.getHeader(), line, ChangeType.CANCEL, null, null, TODAY);
        ChangeCalculator.Effect effect = ChangeCalculator.effect(line.getHeader(), line, ChangeType.CANCEL, null, null,
                line.getUnitPrice());

        assertThat(effect.newLineStatus()).isEqualTo(LineStatus.CANCELLED);
        assertThat(effect.schedules()).allSatisfy(s -> assertThat(s.status()).isEqualTo(ScheduleStatus.CANCELLED));
        assertThat(effect.newLineAmount()).isEqualByComparingTo("0.00");
        assertThat(effect.newOrderStatus()).isEqualTo(OrderStatus.CANCELLED);
    }

    @Test
    void aDateInThePastIsRefused() {
        OrderLine line = line(100);
        assertThatThrownBy(() -> ChangeCalculator.checkAllowed(line.getHeader(), line, ChangeType.DATE, null,
                TODAY.minusDays(1), TODAY))
                .isInstanceOf(BusinessException.class).hasMessage("DATE_IN_PAST");
    }
}
