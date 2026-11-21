package com.example.orders.web.dto;

import com.example.orders.domain.ChangeType;
import jakarta.validation.constraints.NotNull;
import java.time.LocalDate;

public record ChangeCommand(@NotNull ChangeType type, Integer newQty, LocalDate newDate) {
}
