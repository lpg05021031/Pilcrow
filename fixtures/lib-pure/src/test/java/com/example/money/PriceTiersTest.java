package com.example.money;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import org.junit.jupiter.api.Test;

class PriceTiersTest {

    private final PriceTiers tiers = new PriceTiers()
            .add(1, Money.of("10.00", "USD"))
            .add(100, Money.of("9.00", "USD"));

    @Test
    void theHighestTierReachedSetsThePrice() {
        assertEquals(Money.of("10", "USD"), tiers.unitPriceFor(99));
        assertEquals(Money.of("9", "USD"), tiers.unitPriceFor(100));
        assertEquals(Money.of("900.00", "USD"), tiers.totalFor(100));
    }

    @Test
    void aQuantityBelowEveryTierIsRefused() {
        assertThrows(IllegalArgumentException.class, () -> tiers.unitPriceFor(0));
    }
}
