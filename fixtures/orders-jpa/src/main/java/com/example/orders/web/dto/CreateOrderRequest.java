package com.example.orders.web.dto;

import jakarta.validation.Valid;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotEmpty;
import jakarta.validation.constraints.NotNull;
import java.time.LocalDate;
import java.util.List;

public record CreateOrderRequest(
        @NotBlank String orderNo,
        @NotBlank String customerId,
        @NotEmpty @Valid List<Line> lines) {

    /** A line is ordered as one or more deliveries. Its quantity is the sum of its schedule lines. */
    public record Line(@NotBlank String sku, @NotEmpty @Valid List<Schedule> schedules) {

        public int qty() {
            return schedules.stream().mapToInt(Schedule::qty).sum();
        }
    }

    public record Schedule(@Min(1) int qty, @NotNull LocalDate deliveryDate) {
    }
}
